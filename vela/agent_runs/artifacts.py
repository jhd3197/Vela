"""Files a task was given, and files a task came back with.

The rule this file exists to enforce: **an agent never names a path.** It is
given artifact ids. A file gets into the staging area two ways — the owner
picked it in their own dashboard, or the controlled browser downloaded it from
an approved site — and either way Vela chose the name it is stored under, the
directory it went into and how long it stays. There is no route that takes a
path from a task, so there is nothing to point somewhere else.

What that buys, concretely: no path traversal, because nothing concatenates a
supplied name; no surprise executable, because an artifact is never launched and
its stored name carries an extension from a short list; no silent overwrite,
because every stored name is generated and unique; no archive quietly becoming a
directory of files, because nothing here extracts anything.

Quotas are per upload, per download, per task and per server, in that order, and
each one is checked before the bytes are accepted rather than after. The limits
are the plan's proposed starting numbers — 25 MiB, 100 MiB, 250 MiB — and they
are displayed before a transfer rather than discovered by one failing.

Artifacts expire. A task's working files are not a filing cabinet, and a
directory of somebody's downloads that nobody swept would eventually be the most
interesting thing in the data directory.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import time
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..app_storage import AppServiceError

#: The plan's proposed starting limits, in bytes. Numbers to measure and tune,
#: not claims about what any machine can do.
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_TASK_BYTES = 250 * 1024 * 1024

#: Everything every desktop holds at once. The backstop that stops a hundred
#: small tasks doing what one big one is not allowed to.
MAX_SERVER_BYTES = 2 * 1024 * 1024 * 1024

#: How long an artifact stays before the sweep removes it.
ARTIFACT_TTL_SECONDS = 7 * 24 * 3600

#: Most artifacts one desktop holds, whatever their size.
MAX_PER_DESKTOP = 200

#: Extensions an artifact may be stored under. Anything else is stored as
#: `.bin`, which is not a claim about the contents — it is a refusal to give a
#: file a name the host operating system would treat as a program.
SAFE_EXTENSIONS = frozenset(
    {
        ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".log",
        ".pdf", ".rtf", ".odt", ".ods", ".odp",
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif",
        ".mp3", ".wav", ".ogg", ".flac", ".m4a",
        ".mp4", ".webm", ".mov", ".mkv",
        ".zip", ".gz", ".tar", ".tgz", ".7z",
        ".ics", ".vcf", ".eml",
    }
)

#: Where an artifact came from. Kept because the answer changes what a person
#: should think of it: something they chose is not the same as something a
#: website handed to a browser an agent was driving.
SOURCES = ("upload", "download", "result")

#: Characters a stored or displayed name never contains. A superset of what
#: Windows refuses, so one name is safe on every platform Vela runs on.
_FORBIDDEN_IN_NAME = frozenset('<>:"|?*') | {"\\", "/"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_artifacts (
  id TEXT PRIMARY KEY,
  desktop_id TEXT NOT NULL,
  run_id TEXT,
  source TEXT NOT NULL,
  name TEXT NOT NULL,
  stored_name TEXT NOT NULL,
  media_type TEXT NOT NULL DEFAULT '',
  bytes INTEGER NOT NULL,
  digest TEXT NOT NULL,
  origin TEXT,
  view_id TEXT,
  created_at TEXT NOT NULL,
  expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_artifacts_by_desktop ON agent_artifacts (desktop_id);
CREATE INDEX IF NOT EXISTS agent_artifacts_by_run ON agent_artifacts (run_id);
"""


class ArtifactError(AppServiceError):
    """A refusal a person and a run are both told about in the same words."""


def display_name(value: Any, *, fallback: str = "file") -> str:
    """A name safe to show, derived from one nothing here trusts.

    Used for the label only. The bytes are stored under a generated name, so
    even a name that survives this is never a path — and a name that arrived
    from a website is never a name anything opens by.
    """
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    # A website's suggested filename may contain directories, a drive letter or
    # a device name. Only the last segment is a candidate, and even that is
    # stripped of anything a filesystem would treat specially.
    text = text.replace("\\", "/").split("/")[-1]
    cleaned = "".join(
        character
        for character in text
        if character.isprintable() and character not in _FORBIDDEN_IN_NAME
    ).strip(" .")
    if not cleaned or cleaned in (".", ".."):
        return fallback
    return cleaned[:120]


def stored_extension(name: Any) -> str:
    """The extension the bytes are stored under: one from the list, or `.bin`."""
    suffix = Path(display_name(name)).suffix.lower()
    return suffix if suffix in SAFE_EXTENSIONS else ".bin"


class Artifacts:
    """Every staged file on this server, and the quotas they live under."""

    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "agent-artifacts"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = Path(data_dir) / "agent-artifacts.sqlite"
        with self.connection() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=ON")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # -------------------------------------------------------- the limits --

    def limits(self, desktop_id: str, run_id: str | None = None) -> dict[str, Any]:
        """What may be transferred, and how much room is left.

        Answered before a transfer so the interface can say "up to 25 MB" and a
        task can be told what it has, rather than either finding out from a
        failure halfway through.
        """
        used_task = self.used(desktop_id, run_id) if run_id else 0
        used_server = self.used()
        return {
            "maxUploadBytes": MAX_UPLOAD_BYTES,
            "maxDownloadBytes": MAX_DOWNLOAD_BYTES,
            "maxTaskBytes": MAX_TASK_BYTES,
            "maxServerBytes": MAX_SERVER_BYTES,
            "taskBytesUsed": used_task,
            "serverBytesUsed": used_server,
            "taskBytesFree": max(0, MAX_TASK_BYTES - used_task),
            "serverBytesFree": max(0, MAX_SERVER_BYTES - used_server),
            "expiresAfterSeconds": ARTIFACT_TTL_SECONDS,
        }

    def used(self, desktop_id: str | None = None, run_id: str | None = None) -> int:
        clauses, values = [], []
        if desktop_id is not None:
            clauses.append("desktop_id=?")
            values.append(desktop_id)
        if run_id is not None:
            clauses.append("run_id=?")
            values.append(run_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.connection() as db:
            row = db.execute(
                f"SELECT COALESCE(SUM(bytes), 0) AS total FROM agent_artifacts{where}", values
            ).fetchone()
        return int(row["total"])

    def _room_for(self, size: int, *, desktop_id: str, run_id: str | None, ceiling: int) -> None:
        if size <= 0:
            raise ArtifactError(422, "That file is empty.")
        if size > ceiling:
            raise ArtifactError(413, f"That file is larger than {_mib(ceiling)}.")
        if run_id and self.used(desktop_id, run_id) + size > MAX_TASK_BYTES:
            raise ArtifactError(
                413,
                f"This task has already used its {_mib(MAX_TASK_BYTES)} of file space. "
                "Remove a file it no longer needs, or start a new task.",
            )
        if self.used() + size > MAX_SERVER_BYTES:
            raise ArtifactError(
                507,
                f"Vela is holding its {_mib(MAX_SERVER_BYTES)} limit of task files. "
                "Remove some from the desktops that have them.",
            )

    # ------------------------------------------------------------ writing --

    def accept_upload(
        self,
        desktop_id: str,
        chunks: Iterable[bytes],
        *,
        name: Any,
        media_type: str = "",
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """A file the owner chose, streamed into the staging area.

        Streamed and bounded rather than read into memory: the cap has to be a
        cap on what is written, not on what is discovered afterwards to have
        been written.
        """
        self.sweep()
        label = display_name(name)
        artifact_id = uuid.uuid4().hex
        stored = artifact_id + stored_extension(label)
        folder = self._folder(desktop_id)
        partial = folder / (stored + ".part")
        written = 0
        digest = hashlib.sha256()
        try:
            with partial.open("wb") as handle:
                for chunk in chunks:
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES:
                        raise ArtifactError(
                            413, f"A file you attach can be up to {_mib(MAX_UPLOAD_BYTES)}."
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            self._room_for(written, desktop_id=desktop_id, run_id=run_id, ceiling=MAX_UPLOAD_BYTES)
            partial.replace(folder / stored)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        return self._record(
            artifact_id,
            desktop_id=desktop_id,
            run_id=run_id,
            source="upload",
            name=label,
            stored_name=stored,
            media_type=media_type,
            size=written,
            digest=digest.hexdigest(),
            origin=None,
            view_id=None,
        )

    def ingest_download(
        self,
        desktop_id: str,
        staged: Path,
        *,
        name: Any,
        run_id: str | None = None,
        origin: str | None = None,
        view_id: str | None = None,
        media_type: str = "",
    ) -> dict[str, Any]:
        """A file the controlled browser finished downloading.

        The worker has already written it to the staging directory Vela gave it
        and has already refused anything over the limit. This is where it stops
        being a loose file and becomes something with an id, an owner, a quota
        and an expiry — and where a partial download is discarded rather than
        handed to somebody as if it were whole.
        """
        self.sweep()
        staged = Path(staged)
        # The worker only ever writes inside the directory Vela handed it, and
        # this checks that rather than trusting it: a name from the other side of
        # a pipe is a name, not a promise.
        downloads = self.downloads_dir()
        try:
            resolved = staged.resolve(strict=True)
            resolved.relative_to(downloads.resolve())
        except (OSError, ValueError) as exc:
            raise ArtifactError(422, "That download is not where Vela put it.") from exc
        size = resolved.stat().st_size
        try:
            self._room_for(
                size, desktop_id=desktop_id, run_id=run_id, ceiling=MAX_DOWNLOAD_BYTES
            )
        except ArtifactError:
            resolved.unlink(missing_ok=True)
            raise
        label = display_name(name, fallback="download")
        artifact_id = uuid.uuid4().hex
        stored = artifact_id + stored_extension(label)
        destination = self._folder(desktop_id) / stored
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 256), b""):
                digest.update(block)
        shutil.move(str(resolved), str(destination))
        # Nothing here extracts an archive, marks anything executable or opens
        # what it just stored. A downloaded file is bytes with a label.
        _make_inert(destination)
        return self._record(
            artifact_id,
            desktop_id=desktop_id,
            run_id=run_id,
            source="download",
            name=label,
            stored_name=stored,
            media_type=media_type,
            size=size,
            digest=digest.hexdigest(),
            origin=origin,
            view_id=view_id,
        )

    def _record(self, artifact_id: str, **fields: Any) -> dict[str, Any]:
        now = time.time()
        row = (
            artifact_id,
            fields["desktop_id"],
            fields.get("run_id"),
            fields["source"],
            fields["name"],
            fields["stored_name"],
            fields.get("media_type") or "",
            int(fields["size"]),
            fields["digest"],
            fields.get("origin"),
            fields.get("view_id"),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            now + ARTIFACT_TTL_SECONDS,
        )
        with self.connection() as db:
            db.execute("INSERT INTO agent_artifacts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            self._trim(db, fields["desktop_id"])
        return self.get(fields["desktop_id"], artifact_id)

    def _trim(self, db, desktop_id: str) -> None:
        stale = db.execute(
            "SELECT id, desktop_id, stored_name FROM agent_artifacts WHERE desktop_id=? "
            "ORDER BY created_at DESC LIMIT -1 OFFSET ?",
            (desktop_id, MAX_PER_DESKTOP),
        ).fetchall()
        for row in stale:
            self._unlink(row["desktop_id"], row["stored_name"])
            db.execute("DELETE FROM agent_artifacts WHERE id=?", (row["id"],))

    # ------------------------------------------------------------ reading --

    def list(self, desktop_id: str, *, run_id: str | None = None, limit: int = 100) -> list[dict]:
        self.sweep()
        clauses, values = ["desktop_id=?"], [desktop_id]
        if run_id:
            clauses.append("run_id=?")
            values.append(run_id)
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM agent_artifacts WHERE " + " AND ".join(clauses)
                + " ORDER BY created_at DESC LIMIT ?",
                (*values, max(1, min(int(limit), 500))),
            ).fetchall()
        return [_describe(row) for row in rows]

    def get(self, desktop_id: str, artifact_id: str) -> dict[str, Any]:
        """One artifact, checked against the desktop asking for it.

        The desktop is part of the lookup rather than something verified
        afterwards. An id from another desktop is a 404 here — an artifact
        belongs to the workspace it was staged in, and a run on Desktop 2 has no
        business reading what Desktop 1 was given.
        """
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM agent_artifacts WHERE id=? AND desktop_id=?",
                (str(artifact_id or ""), desktop_id),
            ).fetchone()
        if row is None:
            raise ArtifactError(404, "That file is not on this desktop.")
        if row["expires_at"] <= time.time():
            self.remove(desktop_id, row["id"])
            raise ArtifactError(404, "That file has expired and was removed.")
        return _describe(row)

    def file(self, desktop_id: str, artifact_id: str) -> tuple[Path, dict[str, Any]]:
        """The bytes, and the record they belong to. Never a path from a caller."""
        record = self.get(desktop_id, artifact_id)
        path = self._folder(desktop_id) / record["storedName"]
        if not path.is_file():
            raise ArtifactError(404, "That file is no longer here.")
        return path, record

    # ----------------------------------------------------------- removing --

    def remove(self, desktop_id: str, artifact_id: str) -> bool:
        with self.connection() as db:
            row = db.execute(
                "SELECT stored_name FROM agent_artifacts WHERE id=? AND desktop_id=?",
                (artifact_id, desktop_id),
            ).fetchone()
            if row is None:
                return False
            db.execute("DELETE FROM agent_artifacts WHERE id=?", (artifact_id,))
        self._unlink(desktop_id, row["stored_name"])
        return True

    def forget_desktop(self, desktop_id: str) -> int:
        """Everything this desktop was given or came back with.

        Called when a desktop is deleted. An installed app's own data is not
        here and is not touched by this: these are staged transfers, not the
        user's documents.
        """
        with self.connection() as db:
            removed = db.execute(
                "DELETE FROM agent_artifacts WHERE desktop_id=?", (desktop_id,)
            ).rowcount
        shutil.rmtree(self._folder(desktop_id), ignore_errors=True)
        return removed

    def sweep(self) -> int:
        """Remove what has expired, and any file no row points at."""
        removed = 0
        with self.connection() as db:
            stale = db.execute(
                "SELECT id, desktop_id, stored_name FROM agent_artifacts WHERE expires_at<=?",
                (time.time(),),
            ).fetchall()
            for row in stale:
                self._unlink(row["desktop_id"], row["stored_name"])
                db.execute("DELETE FROM agent_artifacts WHERE id=?", (row["id"],))
                removed += 1
            known = {
                (row["desktop_id"], row["stored_name"])
                for row in db.execute("SELECT desktop_id, stored_name FROM agent_artifacts")
            }
        for folder in _subdirectories(self.root):
            for path in folder.iterdir():
                if not path.is_file():
                    continue
                if (folder.name, path.name) in known:
                    continue
                # A crash between writing bytes and writing the row leaves one
                # of these. Nothing references it, so nothing loses anything.
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
        return removed

    # ------------------------------------------------------------ staging --

    def downloads_dir(self) -> Path:
        """Where the worker is allowed to write a finished download.

        One directory, created by Vela, handed to the worker as an argument. The
        worker has no other writable location, and this is the only place
        `ingest_download` will accept a file from.
        """
        path = self.root / "incoming"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _folder(self, desktop_id: str) -> Path:
        # `desktop_id` is validated by the caller's service before it reaches
        # here, and is a generated identifier in every path that produces one.
        folder = self.root / str(desktop_id)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _unlink(self, desktop_id: str, stored_name: str) -> None:
        try:
            (self._folder(desktop_id) / stored_name).unlink(missing_ok=True)
        except OSError:
            pass


def _subdirectories(root: Path) -> list[Path]:
    try:
        return [path for path in root.iterdir() if path.is_dir() and path.name != "incoming"]
    except OSError:
        return []


def _make_inert(path: Path) -> None:
    """Take the executable bit off, where a platform has one.

    Belt and braces: nothing in Vela launches an artifact, and an artifact is
    never given a name the shell would treat as a program. This is the third
    thing that would have to go wrong.
    """
    try:
        mode = path.stat().st_mode
        path.chmod(mode & ~0o111)
    except OSError:
        pass


def _mib(value: int) -> str:
    return f"{value // (1024 * 1024)} MB"


def _describe(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "desktopId": row["desktop_id"],
        "runId": row["run_id"],
        "source": row["source"],
        "name": row["name"],
        "storedName": row["stored_name"],
        "mediaType": row["media_type"],
        "bytes": int(row["bytes"]),
        "digest": row["digest"],
        "origin": row["origin"],
        "viewId": row["view_id"],
        "createdAt": row["created_at"],
        "expiresAt": row["expires_at"],
    }


def envelope(record: dict[str, Any]) -> dict[str, Any]:
    """What a run is told about an artifact: an id, a label and a size.

    Not the stored name, not the digest and not a path. A task needs to know the
    file exists and which one it is; everything else is the owner's view of it.
    """
    return {
        "artifactId": record["id"],
        "name": record["name"],
        "bytes": record["bytes"],
        "source": record["source"],
        "from": record.get("origin"),
    }


__all__ = [
    "ARTIFACT_TTL_SECONDS",
    "ArtifactError",
    "Artifacts",
    "MAX_DOWNLOAD_BYTES",
    "MAX_SERVER_BYTES",
    "MAX_TASK_BYTES",
    "MAX_UPLOAD_BYTES",
    "SAFE_EXTENSIONS",
    "display_name",
    "envelope",
    "stored_extension",
]
