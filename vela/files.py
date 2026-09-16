"""Browsing the folders the user chose to share, and nothing else.

Vela runs on someone's own computer, which means the engine can read everything
that person can. That makes a file browser the most dangerous surface in the
hub, and the rule that keeps it safe is narrow and absolute:

    **A path is served only when it resolves inside a configured share.**

Every request names a share by id and a path relative to it. The path is
resolved against the share's real location, symlinks and all, and compared with
that location before anything is opened. `..`, an absolute path, a Windows
drive letter, a UNC path and a symlink pointing out of the share all fail the
same comparison. There is no route that takes a whole path, so there is nothing
to trick into leaving.

Deletes go to `<data_dir>/trash` rather than removing anything, and are cleared
after thirty days. Uploads are streamed with a size cap so a browser cannot fill
the disk in one request. Everything that changes a file is written to audit.log.
"""

from __future__ import annotations

import os
import shutil
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .logging_setup import audit

#: The largest upload accepted, streamed rather than held in memory.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024

#: How long a deleted file stays in the trash before it is cleared.
TRASH_DAYS = 30

#: The most entries one listing returns. A folder with a hundred thousand files
#: is real; sending all of it to a browser is not.
MAX_ENTRIES = 5000

#: Characters no file Vela creates may contain, on any platform. Windows
#: refuses most of these outright; refusing them everywhere keeps a share
#: readable when it is copied between machines.
_FORBIDDEN = set('<>:"/\\|?*') | {chr(code) for code in range(32)}

#: Names Windows will not open whatever the extension.
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{n}" for n in range(1, 10)),
    *(f"lpt{n}" for n in range(1, 10)),
}

#: What a browser can preview inline, by extension. Everything else downloads.
_KINDS = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif"},
    "video": {".mp4", ".webm", ".mov", ".mkv"},
    "audio": {".mp3", ".wav", ".ogg", ".flac", ".m4a"},
    "pdf": {".pdf"},
    "text": {
        ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".csv", ".log",
        ".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".scss", ".html", ".xml", ".sh",
    },
    "archive": {".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar"},
}


class FileError(Exception):
    """A refused request, with the status and the reason to show the user."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def kind_of(name: str) -> str:
    suffix = Path(name).suffix.lower()
    for kind, suffixes in _KINDS.items():
        if suffix in suffixes:
            return kind
    return "file"


def validate_name(name: str) -> str:
    """One path segment the user typed, or a reason it cannot be used.

    This is for names Vela *creates* — a new folder, the far end of a rename.
    Names that already exist on disk are listed as they are.
    """
    # Normalising first means "é" typed two ways cannot become two files that
    # look identical in the listing.
    name = unicodedata.normalize("NFC", (name or "").strip())
    if not name:
        raise FileError(422, "A name cannot be empty.")
    if len(name.encode("utf-8")) > 255:
        raise FileError(422, "That name is too long.")
    if name in {".", ".."}:
        raise FileError(422, "That name is not allowed.")
    bad = sorted(_FORBIDDEN & set(name))
    if bad:
        shown = " ".join(character for character in bad if character.isprintable())
        raise FileError(422, f"A name cannot contain {shown or 'control characters'}.")
    if name.split(".")[0].lower() in _RESERVED:
        raise FileError(422, f"“{name}” is a name Windows reserves.")
    if name.endswith((" ", ".")):
        raise FileError(422, "A name cannot end with a space or a dot.")
    return name


def validate_shares(value: Any, data_dir: Path) -> list[dict[str, Any]]:
    """Check a `settings.files.shares` patch.

    A share has to be a folder that exists right now: offering to browse
    somewhere Vela cannot read is worse than refusing to store it.
    """
    if not isinstance(value, list):
        raise ValueError("shares must be a list")
    if len(value) > 20:
        raise ValueError("at most 20 shares")
    out: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError("each share is an object with an id, a label and a path")
        share_id = str(entry.get("id") or "").strip().lower()
        if not share_id or not all(ch.isalnum() or ch in "-_" for ch in share_id):
            raise ValueError("a share id is letters, digits, dashes or underscores")
        if len(share_id) > 40:
            raise ValueError("a share id is at most 40 characters")
        if share_id in seen_ids:
            raise ValueError(f"{share_id} is listed twice")
        path = str(entry.get("path") or "").strip()
        if not path:
            raise ValueError("each share needs a path")
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"{path} is not a folder on this computer")
        # Vela's own data directory is not a share: its contents are Vela's
        # working files, and a browser that can delete them is a way to lose
        # every app's data at once.
        if resolved == data_dir.resolve() or data_dir.resolve() in resolved.parents:
            if resolved != (data_dir / "shares").resolve() and (data_dir / "shares").resolve() not in resolved.parents:
                raise ValueError("Vela's own data folder cannot be shared")
        if str(resolved) in seen_paths:
            raise ValueError(f"{path} is listed twice")
        seen_ids.add(share_id)
        seen_paths.add(str(resolved))
        out.append(
            {
                "id": share_id,
                "label": (str(entry.get("label") or "").strip() or resolved.name or share_id)[:60],
                "path": str(resolved),
                "writable": bool(entry.get("writable", True)),
            }
        )
    return out


class Files:
    """The configured shares, and safe access to what is inside them."""

    def __init__(self, config, settings):
        self._config = config
        self._settings = settings

    # ------------------------------------------------------------- shares --

    @property
    def trash_dir(self) -> Path:
        return self._config.data_dir / "trash"

    def default_share_dir(self) -> Path:
        return self._config.data_dir / "shares" / "downloads"

    def shares(self) -> list[dict[str, Any]]:
        """The configured shares, each marked with whether it is reachable now.

        A share on a drive that is not plugged in is listed and marked, not
        hidden: the user configured it and should see why it is not working.
        """
        stored = (self._settings.get("files") or {}).get("shares")
        if not isinstance(stored, list) or not stored:
            default = self.default_share_dir()
            default.mkdir(parents=True, exist_ok=True)
            stored = [
                {"id": "downloads", "label": "Downloads", "path": str(default), "writable": True}
            ]
        out = []
        for entry in stored:
            if not isinstance(entry, dict):
                continue
            path = Path(str(entry.get("path") or ""))
            out.append(
                {
                    "id": str(entry.get("id") or ""),
                    "label": str(entry.get("label") or ""),
                    "path": str(path),
                    "writable": bool(entry.get("writable", True)),
                    "reachable": path.is_dir(),
                }
            )
        return out

    def _share(self, share_id: str) -> dict[str, Any]:
        for share in self.shares():
            if share["id"] == share_id:
                if not share["reachable"]:
                    raise FileError(409, f"{share['label']} is not available right now.")
                return share
        raise FileError(404, "No such share.")

    def _writable(self, share: dict[str, Any]) -> None:
        if not share["writable"]:
            raise FileError(403, f"{share['label']} is read-only.")

    # -------------------------------------------------------------- paths --

    def resolve(self, share_id: str, relative: str = "") -> tuple[dict[str, Any], Path]:
        """The real path for a request, or a refusal.

        This is the whole security boundary. Everything that touches the disk
        goes through it, and it answers the same way for `..`, an absolute path,
        a drive letter, a UNC path and a symlink that points out of the share:
        the resolved path is not inside the share, so it is not served.
        """
        share = self._share(share_id)
        root = Path(share["path"]).resolve()
        parts = []
        for part in PurePosixPath((relative or "").replace("\\", "/")).parts:
            if part in ("", "."):
                continue
            if part == "..":
                # Refused rather than clamped: a request that tried to leave is
                # not a request for the folder above, it is a mistake or an
                # attack, and either is worth saying out loud.
                raise FileError(403, "That path is outside the share.")
            if part.endswith(":") or part.startswith("/"):
                raise FileError(403, "That path is outside the share.")
            parts.append(part)
        target = root.joinpath(*parts)
        try:
            # `strict=False` so a name that does not exist yet (a new folder, an
            # upload's destination) still resolves and is still checked.
            resolved = target.resolve(strict=False)
        except OSError as exc:
            raise FileError(400, "That path cannot be read.") from exc
        if resolved != root and root not in resolved.parents:
            raise FileError(403, "That path is outside the share.")
        return share, resolved

    # ------------------------------------------------------------ listing --

    def _entry(self, path: Path, root: Path) -> dict[str, Any] | None:
        try:
            stat = path.stat()
        except OSError:
            # A file that vanished between the scan and the stat, or one this
            # process may not read. It is left out rather than shown broken.
            return None
        is_dir = path.is_dir()
        return {
            "name": path.name,
            "path": str(path.relative_to(root)).replace(os.sep, "/"),
            "kind": "folder" if is_dir else kind_of(path.name),
            "size": None if is_dir else int(stat.st_size),
            "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(
                timespec="seconds"
            ),
        }

    def list(self, share_id: str, relative: str = "") -> dict[str, Any]:
        share, target = self.resolve(share_id, relative)
        root = Path(share["path"]).resolve()
        if not target.is_dir():
            raise FileError(404, "No such folder.")
        entries = []
        truncated = False
        try:
            with os.scandir(target) as scan:
                for item in scan:
                    if len(entries) >= MAX_ENTRIES:
                        truncated = True
                        break
                    entry = self._entry(Path(item.path), root)
                    if entry:
                        entries.append(entry)
        except PermissionError as exc:
            raise FileError(403, "Vela cannot read that folder.") from exc
        except OSError as exc:
            raise FileError(400, "That folder cannot be read.") from exc
        # Folders first, then by name, the way a file manager orders things.
        entries.sort(key=lambda item: (item["kind"] != "folder", item["name"].lower()))
        return {
            "share": {k: share[k] for k in ("id", "label", "writable")},
            "path": str(target.relative_to(root)).replace(os.sep, "/") if target != root else "",
            "entries": entries,
            "truncated": truncated,
        }

    def open_file(self, share_id: str, relative: str) -> tuple[Path, str]:
        """A real file inside the share, for downloading or previewing."""
        _, target = self.resolve(share_id, relative)
        if not target.is_file():
            raise FileError(404, "No such file.")
        return target, kind_of(target.name)

    # ------------------------------------------------------------ changes --

    def mkdir(self, share_id: str, relative: str, name: str, *, actor: str = "local") -> dict:
        share, parent = self.resolve(share_id, relative)
        self._writable(share)
        if not parent.is_dir():
            raise FileError(404, "No such folder.")
        target = parent / validate_name(name)
        # Re-checked through `resolve` so a name can never reintroduce a path.
        _, checked = self.resolve(share_id, f"{relative}/{target.name}".strip("/"))
        if checked.exists():
            raise FileError(409, f"“{target.name}” already exists.")
        try:
            checked.mkdir()
        except OSError as exc:
            raise FileError(400, "That folder could not be created.") from exc
        audit("files", f"mkdir share={share_id} name={target.name}", actor=actor)
        return {"ok": True, "name": target.name}

    def rename(self, share_id: str, relative: str, name: str, *, actor: str = "local") -> dict:
        share, target = self.resolve(share_id, relative)
        self._writable(share)
        if not target.exists():
            raise FileError(404, "That is no longer there.")
        root = Path(share["path"]).resolve()
        if target == root:
            raise FileError(403, "A share cannot be renamed here.")
        clean = validate_name(name)
        destination = target.parent / clean
        _, checked = self.resolve(
            share_id,
            str((target.parent / clean).relative_to(root)).replace(os.sep, "/"),
        )
        if checked != destination:
            raise FileError(403, "That path is outside the share.")
        if destination.exists():
            raise FileError(409, f"“{clean}” already exists.")
        try:
            target.rename(destination)
        except OSError as exc:
            raise FileError(400, "That could not be renamed.") from exc
        audit("files", f"rename share={share_id} to={clean}", actor=actor)
        return {"ok": True, "name": clean}

    def move(self, share_id: str, relative: str, into: str, *, actor: str = "local") -> dict:
        share, target = self.resolve(share_id, relative)
        self._writable(share)
        if not target.exists():
            raise FileError(404, "That is no longer there.")
        _, folder = self.resolve(share_id, into)
        if not folder.is_dir():
            raise FileError(404, "No such folder.")
        if target == folder or target in folder.parents:
            raise FileError(422, "A folder cannot be moved inside itself.")
        destination = folder / target.name
        if destination.exists():
            raise FileError(409, f"“{target.name}” is already there.")
        try:
            shutil.move(str(target), str(destination))
        except OSError as exc:
            raise FileError(400, "That could not be moved.") from exc
        audit("files", f"move share={share_id} name={target.name}", actor=actor)
        return {"ok": True, "name": target.name}

    def delete(self, share_id: str, relative: str, *, actor: str = "local") -> dict:
        """Move something to the trash. Nothing here removes anything."""
        share, target = self.resolve(share_id, relative)
        self._writable(share)
        root = Path(share["path"]).resolve()
        if target == root:
            raise FileError(403, "A share cannot be deleted here.")
        if not target.exists():
            raise FileError(404, "That is no longer there.")
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        # The stamp and a counter keep two deletes of the same name apart.
        destination = self.trash_dir / f"{stamp}-{target.name}"
        counter = 2
        while destination.exists():
            destination = self.trash_dir / f"{stamp}-{counter}-{target.name}"
            counter += 1
        try:
            shutil.move(str(target), str(destination))
        except OSError as exc:
            raise FileError(400, "That could not be deleted.") from exc
        audit("files", f"delete share={share_id} name={target.name}", actor=actor)
        return {"ok": True, "trashed": destination.name}

    # -------------------------------------------------------------- trash --

    def sweep_trash(self, *, now: float | None = None) -> int:
        """Clear anything in the trash older than thirty days. Returns the count."""
        if not self.trash_dir.is_dir():
            return 0
        cutoff = (now if now is not None else time.time()) - TRASH_DAYS * 86400
        removed = 0
        for entry in self.trash_dir.iterdir():
            try:
                if entry.stat().st_mtime > cutoff:
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
        return removed

    def trash(self) -> dict[str, Any]:
        """What is waiting in the trash, and when it goes."""
        if not self.trash_dir.is_dir():
            return {"entries": [], "days": TRASH_DAYS}
        entries = []
        for entry in sorted(self.trash_dir.iterdir(), reverse=True):
            try:
                stat = entry.stat()
            except OSError:
                continue
            deleted = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            entries.append(
                {
                    "name": entry.name,
                    "kind": "folder" if entry.is_dir() else kind_of(entry.name),
                    "size": None if entry.is_dir() else int(stat.st_size),
                    "deleted": deleted.isoformat(timespec="seconds"),
                    "clearedAfter": (deleted + timedelta(days=TRASH_DAYS)).isoformat(
                        timespec="seconds"
                    ),
                }
            )
        return {"entries": entries[:MAX_ENTRIES], "days": TRASH_DAYS}

    # ------------------------------------------------------------- upload --

    def save_upload(
        self,
        share_id: str,
        relative: str,
        name: str,
        chunks: Iterable[bytes],
        *,
        actor: str = "local",
    ) -> dict:
        """Stream one upload into a share, refusing it past the size cap.

        Written to a temporary name beside the destination and moved into place
        only once it is whole, so a failed upload never leaves something that
        looks like a finished file.
        """
        share, folder = self.resolve(share_id, relative)
        self._writable(share)
        if not folder.is_dir():
            raise FileError(404, "No such folder.")
        clean = validate_name(name)
        root = Path(share["path"]).resolve()
        _, destination = self.resolve(
            share_id, str((folder / clean).relative_to(root)).replace(os.sep, "/")
        )
        if destination.exists():
            raise FileError(409, f"“{clean}” already exists.")
        partial = destination.with_name(f".{clean}.vela-part")
        written = 0
        try:
            with partial.open("wb") as handle:
                for chunk in chunks:
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES:
                        raise FileError(413, "That file is larger than 2 GB.")
                    handle.write(chunk)
        except FileError:
            partial.unlink(missing_ok=True)
            raise
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise FileError(400, "That file could not be saved.") from exc
        try:
            partial.replace(destination)
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise FileError(400, "That file could not be saved.") from exc
        audit("files", f"upload share={share_id} name={clean} bytes={written}", actor=actor)
        return {"ok": True, "name": clean, "bytes": written}
