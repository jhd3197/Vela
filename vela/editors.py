"""Workspace files and a plain-text editor inside the bot's own computer, and nowhere else.

A controller-mode Vela is one bot's computer, and the bot's files live in its
workspace volume. This module is how a controller and its agent browse, read
and edit those files, and how one of them opens in a plain-text editor window
on an agent desktop. Everywhere else the rule stands exactly as it always has:
there is no workspace share outside controller mode, no editor view, and no
path from an agent to a file location (see docs/AGENT-DESKTOPS.md).

Three decisions shape this file.

**The workspace is a share; nothing here forks the path rules.** The built-in
Workspace share (`vela/files.py`) is the single root every operation resolves
against, through the same `resolve` that guards every share: `..`, an absolute
path, a drive letter and a symlink pointing out all refuse the same way, and
deletes through the Files app go to the same trash. The editor and the tools
add only what a share does not know about: UTF-8 text, size caps and a
revision.

**The revision is the file's content hash; a save names the revision it
expected.** Reading answers the SHA-256 of what was on disk, and a write may
carry that hash back. When the hash no longer matches, somebody saved first
and the write is refused with a sentence instead of landing on top of them.
Writes are atomic — a temporary name beside the destination, then a replace —
so a crash mid-save never leaves a half-written file pretending to be whole.

**The editor is a view, watched like any other; a person edits under the
lease.** An editor window is a view of the `editor` kind on the desktop, so
the frame route, the lease and the take-over rules apply unchanged. A frame
is text — the file's current content plus the revision — not a picture, the
same approach the terminal uses. Input from a person is whole-text: the one
holding the lease sends the file's new content, and the lease is the guard,
exactly as it is for a terminal's keystrokes. Unlike a terminal an editor
keeps no process: a run ending leaves its editor windows open, and nothing
here outlives the desktop that owns the window.
"""

from __future__ import annotations

import contextlib
import hashlib
import threading
import time
from pathlib import Path
from typing import Any

from .desktops.models import DesktopError, validate_view_id
from .files import WORKSPACE_SHARE_ID, FileError
from .logging_setup import audit
from .terminals import clip_output

#: Largest file the editor opens. Matches the surface contract's document cap:
#: a plain-text window is for files a person reads whole, not logs a bot
#: pages through with `workspace_read` or the terminal.
MAX_FILE_BYTES = 1024 * 1024

#: Largest save the editor and the workspace tools accept, whatever the caller
#: sends. Bounds one request's write, not what the workspace can hold.
MAX_WRITE_BYTES = 1024 * 1024

#: Largest file `workspace_read` reads into memory at once. Bigger files are
#: still reachable — a terminal `head`/`tail`/`sed` pages them the way a
#: shell always has.
MAX_READ_BYTES = 4 * 1024 * 1024

#: Most characters one read answers with. Beyond it the answer is head and
#: tail around an `…[output cut]…` marker, the same shape terminal answers
#: use, so a long answer never pretends to be complete.
MAX_READ_CHARS = 16_000

#: Longest workspace-relative path accepted, in characters.
MAX_PATH = 400

#: Longest expected-revision string: a SHA-256 in hex.
MAX_REVISION = 64


def revision_of(content: bytes) -> str:
    """The revision a file's bytes hash to. Changes with the content only."""
    return hashlib.sha256(content).hexdigest()


class Editor:
    """One workspace file open in an editor window.

    The file on disk is the truth; this caches what the window shows and
    refreshes from a stat before answering, so a frame is cheap while nothing
    changes and honest the moment something does.
    """

    def __init__(self, view_id: str, desktop_id: str, path: str, snapshot: dict[str, Any]):
        self.view_id = view_id
        self.desktop_id = desktop_id
        #: The workspace-relative path, posix separators, the source the view
        #: row remembers the window by.
        self.path = path
        self.title = Path(path).name or path
        self.created_at = time.time()
        self._mtime_ns = snapshot["mtime_ns"]
        self._size = snapshot["size"]
        self.text = snapshot["text"]
        self.revision = snapshot["revision"]

    def frame(self) -> dict[str, Any]:
        """What a watcher sees: the file's text and its revision."""
        return {
            "text": self.text,
            "revision": self.revision,
            "capturedAt": time.time(),
        }


class Editors:
    """Workspace files and the editor windows of every desktop.

    The lifecycle owner for editor views: it opens them through the desktop
    service, answers their frames, and closes them with the desktop — on
    delete, on turning the agent off, when Vela quiesces for a restore, and
    when Vela stops.
    """

    def __init__(self, desktops, files, config, *, log=None):
        self.desktops = desktops
        self._files = files
        self._config = config
        self._log = log or (lambda message: None)
        #: view id -> editor. In memory, like the views' live content; the
        #: view rows in the store are recovered lazily if a crash left one.
        self._all: dict[str, Editor] = {}
        #: One write at a time across the workspace, so the read-hash-compare
        #: inside a save cannot race another save.
        self._write_lock = threading.Lock()

    # ------------------------------------------------------------ enabled --

    @property
    def supported(self) -> bool:
        """The workspace and the editor exist only in controller mode."""
        return bool(self._config.controller_mode)

    def _require_supported(self) -> None:
        if not self._config.controller_mode:
            raise DesktopError(404, "The workspace and the editor exist only in controller mode.")

    def _emit(self, desktop_id: str, kind: str, payload: dict[str, Any]) -> None:
        runs = getattr(self.desktops, "runs", None)
        if runs is None:
            return
        with contextlib.suppress(Exception):
            runs.supervisor.emit(desktop_id, kind, payload)

    # -------------------------------------------------------------- paths --

    def _guard(self, call, *args, **kwargs):
        """A files-service call in the words this layer refuses in."""
        try:
            return call(*args, **kwargs)
        except FileError as exc:
            raise DesktopError(exc.status, str(exc)) from exc

    def _check_path(self, path: Any, *, what: str = "path") -> str:
        if not isinstance(path, str) or not path.strip():
            raise DesktopError(422, f"A {what} names a file or folder inside the workspace.")
        clean = path.strip().replace("\\", "/")
        if len(clean) > MAX_PATH:
            raise DesktopError(422, f"That {what} is too long.")
        return clean

    def _resolve(self, relative: str) -> Path:
        """The real path inside the workspace, or the share rules' refusal."""
        _, target = self._guard(self._files.resolve, WORKSPACE_SHARE_ID, relative)
        return target

    # ------------------------------------------------------------ snapshot --

    def _snapshot(self, relative: str, *, max_bytes: int) -> dict[str, Any]:
        """The file's text, revision and identity, checked whole.

        Refuses what an editor cannot show: a missing path, a folder, a file
        past the cap, bytes that are not UTF-8, and bytes that smell binary.
        """
        target = self._resolve(relative)
        if not target.is_file():
            raise DesktopError(404, "No such file.")
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise DesktopError(400, "That file cannot be read.") from exc
        if len(data) > max_bytes:
            raise DesktopError(
                413,
                f"That file is {len(data)} bytes; at most {max_bytes // 1024} KB is accepted here.",
            )
        return {**self._text_of(data, relative), "size": len(data), "mtime_ns": self._mtime(target)}

    @staticmethod
    def _mtime(target: Path) -> int:
        try:
            return target.stat().st_mtime_ns
        except OSError:
            return -1

    def _text_of(self, data: bytes, relative: str) -> dict[str, Any]:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DesktopError(415, "That file is not UTF-8 text.") from exc
        if "\x00" in text:
            raise DesktopError(415, "That file looks like binary, not text.")
        return {"text": text, "revision": revision_of(data)}

    def _refresh(self, editor: Editor) -> None:
        """Bring the cached snapshot up to date, or refuse if the file went."""
        target = self._resolve(editor.path)
        try:
            stat = target.stat()
        except OSError as exc:
            raise DesktopError(404, "That is no longer there.") from exc
        if (stat.st_mtime_ns, stat.st_size) == (editor._mtime_ns, editor._size):
            return
        snapshot = self._snapshot(editor.path, max_bytes=MAX_FILE_BYTES)
        editor._mtime_ns = snapshot["mtime_ns"]
        editor._size = snapshot["size"]
        editor.text = snapshot["text"]
        editor.revision = snapshot["revision"]

    # ------------------------------------------------------- files as data --

    def list(self, path: Any = None) -> dict[str, Any]:
        """The entries at a workspace folder, in the Files app's shape."""
        self._require_supported()
        relative = "" if path is None else self._check_path(path, what="folder")
        listing = self._guard(self._files.list, WORKSPACE_SHARE_ID, relative)
        return {
            "path": listing["path"],
            "entries": listing["entries"],
            "truncated": listing["truncated"],
        }

    def read(
        self,
        path: Any,
        *,
        offset: Any = None,
        limit: Any = None,
        max_bytes: int = MAX_READ_BYTES,
    ) -> dict[str, Any]:
        """A text file's content with its revision, sliced as asked."""
        self._require_supported()
        relative = self._check_path(path)
        target = self._resolve(relative)
        if not target.is_file():
            raise DesktopError(404, "No such file.")
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise DesktopError(400, "That file cannot be read.") from exc
        if len(data) > max_bytes:
            raise DesktopError(
                413,
                f"That file is {len(data)} bytes; at most {max_bytes // 1024} KB can be read at once.",
            )
        decoded = self._text_of(data, relative)
        text = decoded["text"]
        start = self._non_negative(offset, "offset")
        if limit is None:
            content = text
            if len(text) > MAX_READ_CHARS:
                content, _cut = clip_output(text, MAX_READ_CHARS)
                truncated = True
            else:
                truncated = False
        else:
            wanted = self._non_negative(limit, "limit")
            end = start + min(wanted, MAX_READ_CHARS)
            content = text[start:end]
            truncated = end < len(text)
        return {
            "path": relative,
            "content": content,
            "revision": decoded["revision"],
            "size": len(data),
            "truncated": truncated,
        }

    @staticmethod
    def _non_negative(value: Any, what: str) -> int:
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise DesktopError(422, f"{what} is a number of characters, zero or more.")
        return int(value)

    def write(
        self,
        path: Any,
        content: Any,
        *,
        expected_revision: Any = None,
        actor: str = "controller",
    ) -> dict[str, Any]:
        """Replace a workspace file atomically, refusing a stale expected revision.

        `expected_revision` is the revision a previous read answered; when it
        no longer matches what is on disk, somebody saved first and this is a
        409 instead of a write. Without one the write is taken as-is — creating
        a file is exactly a write with nothing to be stale against.
        """
        self._require_supported()
        relative = self._check_path(path)
        if not isinstance(content, str):
            raise DesktopError(422, "Writing needs the file's new text.")
        data = content.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            raise DesktopError(
                413, f"That save is {len(data)} bytes; at most {MAX_WRITE_BYTES // 1024} KB."
            )
        if expected_revision is not None and not (
            isinstance(expected_revision, str) and len(expected_revision) <= MAX_REVISION
        ):
            raise DesktopError(422, "A revision is what a read returned: the file's content hash.")
        target = self._resolve(relative)
        if target.is_dir():
            raise DesktopError(422, "That path is a folder, not a file.")
        parent = target.parent
        if not parent.is_dir():
            raise DesktopError(404, "No such folder.")
        with self._write_lock:
            current = None
            if target.exists():
                try:
                    current = target.read_bytes()
                except OSError as exc:
                    raise DesktopError(400, "That file cannot be read.") from exc
            current_revision = revision_of(current) if current is not None else None
            if expected_revision is not None and expected_revision != current_revision:
                raise DesktopError(
                    409,
                    "Somebody changed this file since it was read. Read it again, then save.",
                )
            partial = target.with_name(f".{target.name}.vela-part")
            try:
                partial.write_bytes(data)
                partial.replace(target)
            except OSError as exc:
                with contextlib.suppress(OSError):
                    partial.unlink()
                raise DesktopError(400, "That file could not be saved.") from exc
        audit("files", f"workspace-write path={relative} bytes={len(data)}", actor=actor)
        return {"path": relative, "revision": revision_of(data), "bytes": len(data)}

    # ------------------------------------------------------------ opening --

    def describe(self, editor: Editor) -> dict[str, Any]:
        return {
            "viewId": editor.view_id,
            "desktopId": editor.desktop_id,
            "path": editor.path,
            "title": editor.title,
            "revision": editor.revision,
            "size": editor._size,
            "createdAt": editor.created_at,
        }

    def open(self, desktop_id: str, path: Any, *, run_id: str | None = None) -> dict[str, Any]:
        """An editor window for this file, opening it or focusing the one open.

        The file is checked whole before the window exists: a window on a
        binary or missing file would be a broken thing to leave on the
        desktop, exactly the argument the terminal makes about its shell.
        """
        self._require_supported()
        desktop = self.desktops.store.get(desktop_id)
        if desktop["kind"] != "agent":
            raise DesktopError(409, "This desktop is not running an agent.")
        relative = self._check_path(path, what="file")
        snapshot = self._snapshot(relative, max_bytes=MAX_FILE_BYTES)
        for view in self.desktops.store.views(desktop_id):
            if view["kind"] == "editor" and view["source"] == relative:
                # The same file: bring its window forward rather than opening
                # a second editor for one file.
                self.desktops.update_view(
                    desktop_id, view["id"], {"minimized": False, "raise": True}
                )
                self.desktops.select_view(desktop_id, view["id"])
                editor = self._all.get(view["id"])
                if editor is None:
                    editor = Editor(view["id"], desktop_id, relative, snapshot)
                    self._all[view["id"]] = editor
                else:
                    self._refresh(editor)
                return self.describe(editor)
        view = self.desktops.open_editor_view(
            desktop_id,
            source=relative,
            title=Path(relative).name or relative,
            opened_by="agent" if run_id else "human",
        )
        editor = Editor(view["id"], desktop_id, relative, snapshot)
        self._all[view["id"]] = editor
        self._emit(
            desktop_id, "editor.opened", {"viewId": view["id"], "path": relative}
        )
        return self.describe(editor)

    # ---------------------------------------------------------- using them --

    def get(self, desktop_id: str, view_id: Any) -> Editor:
        """The editor behind a window, re-attaching one a crash left behind."""
        self.desktops.store.get(desktop_id)
        try:
            view_id = validate_view_id(view_id)
        except DesktopError:
            raise DesktopError(404, "That editor is no longer open.")
        editor = self._all.get(view_id)
        if editor is None or editor.desktop_id != desktop_id:
            view = next(
                (
                    item
                    for item in self.desktops.store.views(desktop_id)
                    if item["id"] == view_id and item["kind"] == "editor"
                ),
                None,
            )
            if view is None:
                raise DesktopError(404, "That editor is no longer open.")
            snapshot = self._snapshot(str(view["source"] or ""), max_bytes=MAX_FILE_BYTES)
            editor = Editor(view["id"], desktop_id, str(view["source"] or ""), snapshot)
            self._all[view_id] = editor
        return editor

    def save_view(self, desktop_id: str, view_id: Any, text: Any) -> dict[str, Any]:
        """A person's save, into the editor their lease holds.

        Whole-text, like the controller's PUT: the one holding the lease is
        the single writer, so no revision is asked of them — the lease is the
        guard, the same argument the terminal's keystrokes make.
        """
        editor = self.get(desktop_id, view_id)
        if not isinstance(text, str):
            raise DesktopError(422, "Editing sends the file's new text.")
        result = self.write(editor.path, text, actor="person")
        editor._mtime_ns = self._mtime(self._resolve(editor.path))
        editor._size = result["bytes"]
        editor.text = text
        editor.revision = result["revision"]
        return result

    # ------------------------------------------------------------ closing --

    def _close(self, editor: Editor) -> None:
        self._all.pop(editor.view_id, None)
        with contextlib.suppress(Exception):
            self.desktops.store.close_view(editor.view_id)
        self._emit(
            editor.desktop_id,
            "editor.closed",
            {"viewId": editor.view_id, "path": editor.path},
        )

    def close_by_view(self, desktop_id: str, view_id: Any) -> None:
        """The person closed the window: the editor goes with it."""
        try:
            view_id = validate_view_id(view_id)
        except DesktopError:
            return
        editor = self._all.get(view_id)
        if editor is not None and editor.desktop_id == desktop_id:
            self._close(editor)

    def close_desktop(self, desktop_id: str) -> int:
        closed = 0
        for editor in [e for e in self._all.values() if e.desktop_id == desktop_id]:
            self._close(editor)
            closed += 1
        return closed

    def stop_all(self) -> int:
        closed = 0
        for editor in list(self._all.values()):
            self._close(editor)
            closed += 1
        return closed

    # ------------------------------------------------------------ watching --

    def frame(self, desktop_id: str, view_id: Any) -> dict[str, Any]:
        editor = self.get(desktop_id, view_id)
        self._refresh(editor)
        return {
            "viewId": editor.view_id,
            "desktopId": desktop_id,
            "kind": "editor",
            "title": editor.title,
            "path": editor.path,
            **editor.frame(),
        }
