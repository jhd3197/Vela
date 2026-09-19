"""What Vela remembers about a managed app, and where its files live.

Five facts are kept apart on purpose, because conflating any two of them is how
a hosting layer starts lying to the person using it:

- **Identity**: which installation this is, and which generation of it. The
  identity survives updates; the generation changes whenever the code does, and
  that is what expires gateway access reviewed against the old code.
- **Intent**: whether the user wants this running, and whether it should come
  back with Vela. An explicit Stop is a decision, not a symptom, and it survives
  a restart.
- **Release**: which package and which extracted artifact are active, by digest,
  plus the earlier ones still retained for recovery.
- **Operation**: the one exclusive thing happening to this app right now, with a
  durable journal so an interrupted update is recoverable rather than mysterious.
- **Observed state**: the process itself. That belongs to the supervisor, which
  keeps it in its own file; nothing here pretends to know whether a program is
  answering.

The SQLite commit is the decision point, exactly as `vela/releases.py` does it.
The journal file is written and flushed *before* anything on disk is swapped, so
a crash between the two is recoverable in one direction rather than ambiguous.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..errors_http import Conflict, NotFound

_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

#: Directory names under an app's own managed root. `code/` is replaced by an
#: update; `data/` never is.
CODE_DIR = "code"
DATA_DIR = "data"
RELEASES_DIR = "releases"
SNAPSHOTS_DIR = "snapshots"
PACKAGE_DIR = "package"
JOURNAL_FILE = "journal.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_app_id(app_id: str) -> str:
    """An app id that may be joined onto a path, or a refusal.

    Every managed directory is built from this, so it is checked at the edge
    rather than trusted because it came out of the database.
    """
    if not isinstance(app_id, str) or not _ID_RE.match(app_id) or len(app_id) > 48:
        raise NotFound(f"unknown managed app: {app_id!r}", code="managed.unknown")
    return app_id


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Where one managed app keeps its things."""

    root: Path

    @property
    def package(self) -> Path:
        """The reviewed Vela package: `app.json`, icon, upstream licence."""
        return self.root / PACKAGE_DIR

    @property
    def code(self) -> Path:
        """The active extracted release. Replaced whole by an update."""
        return self.root / CODE_DIR

    @property
    def data(self) -> Path:
        """The app's own persistent storage root. Never replaced, never erased
        by an update or a removal."""
        return self.root / DATA_DIR

    @property
    def releases(self) -> Path:
        return self.root / RELEASES_DIR

    @property
    def snapshots(self) -> Path:
        return self.root / SNAPSHOTS_DIR

    @property
    def journal(self) -> Path:
        return self.root / JOURNAL_FILE

    def release(self, release_id: str) -> Path:
        return self.releases / _safe_uuid(release_id)

    def snapshot(self, snapshot_id: str) -> Path:
        return self.snapshots / _safe_uuid(snapshot_id)

    def data_directory(self, relative: str) -> Path:
        """The directory the manifest declared, resolved inside `data/`.

        Checked rather than joined: the manifest is validated before it gets
        here, and this is still the last place before a recursive copy or a
        delete, so it proves the result is inside the app's own storage.
        """
        target = (self.data / relative).resolve()
        root = self.data.resolve()
        if target != root and not target.is_relative_to(root):
            raise Conflict(
                "That app's data directory is not inside its own storage",
                code="managed.data_outside_app",
            )
        return target


def _safe_uuid(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f-]{36}", value):
        raise NotFound(f"unknown record: {value!r}", code="managed.unknown_record")
    return value


class ManagedStore:
    """`managed.sqlite`: installations, releases, snapshots and the journal."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "managed"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "managed.sqlite"
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS installations (
                    app_id TEXT PRIMARY KEY,
                    installation_id TEXT UNIQUE NOT NULL,
                    generation INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    manifest TEXT NOT NULL,
                    package_digest TEXT NOT NULL,
                    artifact_digest TEXT NOT NULL,
                    release_id TEXT NOT NULL,
                    desired TEXT NOT NULL,
                    start_with_vela INTEGER NOT NULL,
                    installed_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS releases (
                    id TEXT PRIMARY KEY,
                    app_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    manifest TEXT NOT NULL,
                    package_digest TEXT NOT NULL,
                    artifact_digest TEXT NOT NULL,
                    artifact TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    retained INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY,
                    app_id TEXT NOT NULL,
                    release_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    files INTEGER NOT NULL,
                    digest TEXT NOT NULL,
                    note TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS operations (
                    app_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    release_id TEXT,
                    snapshot_id TEXT,
                    started_at TEXT NOT NULL
                );
                """
            )

    # ------------------------------------------------------------ plumbing --

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def paths(self, app_id: str) -> AppPaths:
        return AppPaths(self.root / safe_app_id(app_id))

    # ------------------------------------------------------- installations --

    def get(self, app_id: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM installations WHERE app_id=?", (safe_app_id(app_id),)
            ).fetchone()
        return _row(row)

    def require(self, app_id: str) -> dict[str, Any]:
        record = self.get(app_id)
        if record is None:
            raise NotFound(f"{app_id} is not installed", code="managed.not_installed")
        return record

    def all(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM installations ORDER BY name COLLATE NOCASE").fetchall()
        return [_row(row) for row in rows]

    def ids(self) -> list[str]:
        return [record["app_id"] for record in self.all()]

    def create_or_replace(
        self,
        *,
        app_id: str,
        manifest: dict[str, Any],
        package_digest: str,
        artifact_digest: str,
        release_id: str,
        start_with_vela: bool,
    ) -> dict[str, Any]:
        """Write the installation record, keeping identity across an update.

        The installation id is created once and kept; the generation moves every
        time the code does. Sessions, tickets and open windows are bound to the
        generation, so replacing the code is what ends authority that was
        reviewed against the code being replaced.
        """
        app_id = safe_app_id(app_id)
        stamp = now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM installations WHERE app_id=?", (app_id,)
            ).fetchone()
            installation_id = existing["installation_id"] if existing else str(uuid.uuid4())
            generation = (existing["generation"] + 1) if existing else 1
            desired = existing["desired"] if existing else "stopped"
            db.execute(
                "INSERT OR REPLACE INTO installations (app_id, installation_id, generation, name,"
                " version, manifest, package_digest, artifact_digest, release_id, desired,"
                " start_with_vela, installed_at, updated_at, last_error)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (
                    app_id,
                    installation_id,
                    generation,
                    manifest["name"],
                    manifest["version"],
                    json.dumps(manifest, ensure_ascii=False),
                    package_digest,
                    artifact_digest,
                    release_id,
                    desired,
                    int(start_with_vela if existing is None else bool(existing["start_with_vela"])),
                    existing["installed_at"] if existing else stamp,
                    stamp,
                ),
            )
            row = db.execute("SELECT * FROM installations WHERE app_id=?", (app_id,)).fetchone()
        return _row(row)

    def set_desired(self, app_id: str, desired: str) -> dict[str, Any]:
        if desired not in ("running", "stopped"):
            raise Conflict(f"unknown desired state: {desired!r}", code="managed.bad_intent")
        with self.connection() as db:
            db.execute(
                "UPDATE installations SET desired=?, updated_at=? WHERE app_id=?",
                (desired, now(), safe_app_id(app_id)),
            )
        return self.require(app_id)

    def set_start_with_vela(self, app_id: str, enabled: bool) -> dict[str, Any]:
        with self.connection() as db:
            db.execute(
                "UPDATE installations SET start_with_vela=?, updated_at=? WHERE app_id=?",
                (int(bool(enabled)), now(), safe_app_id(app_id)),
            )
        return self.require(app_id)

    def set_error(self, app_id: str, message: str | None) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE installations SET last_error=?, updated_at=? WHERE app_id=?",
                (message, now(), safe_app_id(app_id)),
            )

    def forget(self, app_id: str) -> None:
        """Remove the installation record. Releases and snapshots outlive it
        only if the caller keeps them; removal with retained data does."""
        with self.connection() as db:
            db.execute("DELETE FROM installations WHERE app_id=?", (safe_app_id(app_id),))
            db.execute("DELETE FROM operations WHERE app_id=?", (app_id,))

    # ------------------------------------------------------------ releases --

    def add_release(
        self,
        *,
        release_id: str,
        app_id: str,
        manifest: dict[str, Any],
        package_digest: str,
        artifact_digest: str,
        artifact: dict[str, Any],
        source: dict[str, Any],
    ) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO releases (id, app_id, version, manifest, package_digest,"
                " artifact_digest, artifact, source, created_at, retained) VALUES (?,?,?,?,?,?,?,?,?,1)",
                (
                    _safe_uuid(release_id),
                    safe_app_id(app_id),
                    manifest["version"],
                    json.dumps(manifest, ensure_ascii=False),
                    package_digest,
                    artifact_digest,
                    json.dumps(artifact, ensure_ascii=False),
                    json.dumps(source, ensure_ascii=False),
                    now(),
                ),
            )

    def release(self, app_id: str, release_id: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM releases WHERE id=? AND app_id=?",
                (_safe_uuid(release_id), safe_app_id(app_id)),
            ).fetchone()
        return _row(row)

    def releases_for(self, app_id: str) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM releases WHERE app_id=? ORDER BY created_at DESC, rowid DESC",
                (safe_app_id(app_id),),
            ).fetchall()
        return [_row(row) for row in rows]

    def drop_release(self, app_id: str, release_id: str) -> None:
        with self.connection() as db:
            db.execute(
                "DELETE FROM releases WHERE id=? AND app_id=?",
                (_safe_uuid(release_id), safe_app_id(app_id)),
            )

    # ----------------------------------------------------------- snapshots --

    def add_snapshot(self, record: dict[str, Any]) -> dict[str, Any]:
        with self.connection() as db:
            db.execute(
                "INSERT INTO snapshots (id, app_id, release_id, version, kind, created_at, size,"
                " files, digest, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    _safe_uuid(record["id"]),
                    safe_app_id(record["app_id"]),
                    record["release_id"],
                    record["version"],
                    record["kind"],
                    record.get("created_at") or now(),
                    int(record["size"]),
                    int(record["files"]),
                    record["digest"],
                    record.get("note", ""),
                ),
            )
        return self.snapshot(record["app_id"], record["id"])

    def snapshot(self, app_id: str, snapshot_id: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM snapshots WHERE id=? AND app_id=?",
                (_safe_uuid(snapshot_id), safe_app_id(app_id)),
            ).fetchone()
        return _row(row)

    def snapshots_for(self, app_id: str) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM snapshots WHERE app_id=? ORDER BY created_at DESC, rowid DESC",
                (safe_app_id(app_id),),
            ).fetchall()
        return [_row(row) for row in rows]

    def drop_snapshot(self, app_id: str, snapshot_id: str) -> None:
        with self.connection() as db:
            db.execute(
                "DELETE FROM snapshots WHERE id=? AND app_id=?",
                (_safe_uuid(snapshot_id), safe_app_id(app_id)),
            )

    # ----------------------------------------------------------- operations --

    def begin_operation(
        self, app_id: str, kind: str, detail: str = "", *, release_id: str | None = None
    ) -> None:
        """Claim the app's one exclusive operation, or refuse.

        Install, update, restore and remove change the same files. Two at once
        is not a race to win; it is a 423 with the name of what is already
        running.
        """
        app_id = safe_app_id(app_id)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM operations WHERE app_id=?", (app_id,)).fetchone()
            if row is not None:
                raise Conflict(
                    f"{row['kind']} is already running for this app; wait for it to finish",
                    code="managed.operation_in_progress",
                    status=423,
                )
            db.execute(
                "INSERT INTO operations (app_id, kind, state, detail, release_id, snapshot_id,"
                " started_at) VALUES (?,?,?,?,?,NULL,?)",
                (app_id, kind, "running", detail, release_id, now()),
            )

    def update_operation(self, app_id: str, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{name}=?" for name in fields)
        with self.connection() as db:
            db.execute(
                f"UPDATE operations SET {assignments} WHERE app_id=?",
                (*fields.values(), safe_app_id(app_id)),
            )

    def end_operation(self, app_id: str) -> None:
        with self.connection() as db:
            db.execute("DELETE FROM operations WHERE app_id=?", (safe_app_id(app_id),))

    def operation(self, app_id: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM operations WHERE app_id=?", (safe_app_id(app_id),)
            ).fetchone()
        return _row(row)

    def operations(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            return [_row(row) for row in db.execute("SELECT * FROM operations").fetchall()]

    # -------------------------------------------------------------- journal --

    def write_journal(self, app_id: str, record: dict[str, Any]) -> None:
        """Record what is about to happen, durably, before it happens.

        `fsync` on the file and on its directory. Without the second one a crash
        can lose the journal entry on a filesystem that has already written the
        swapped directory, which is the ordering this whole mechanism exists to
        prevent.
        """
        paths = self.paths(app_id)
        paths.root.mkdir(parents=True, exist_ok=True)
        temporary = paths.journal.with_suffix(".tmp")
        payload = json.dumps({**record, "app_id": app_id, "written_at": now()}, ensure_ascii=False)
        with temporary.open("w", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(paths.journal)
        try:
            handle = os.open(paths.root, os.O_RDONLY)
        except OSError:
            return  # Windows cannot open a directory for fsync; the replace is atomic there.
        try:
            os.fsync(handle)
        except OSError:
            pass
        finally:
            os.close(handle)

    def read_journal(self, app_id: str) -> dict[str, Any] | None:
        try:
            return json.loads(self.paths(app_id).journal.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def clear_journal(self, app_id: str) -> None:
        self.paths(app_id).journal.unlink(missing_ok=True)

    def journals(self) -> list[tuple[str, dict[str, Any]]]:
        """Every unfinished operation this server can find on the way up."""
        found = []
        if not self.root.is_dir():
            return found
        for folder in sorted(self.root.iterdir()):
            if not folder.is_dir() or not (folder / JOURNAL_FILE).is_file():
                continue
            try:
                app_id = safe_app_id(folder.name)
            except NotFound:
                continue
            record = self.read_journal(app_id)
            if record is not None:
                found.append((app_id, record))
        return found


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    record = dict(row)
    for key in ("manifest", "source", "artifact"):
        if isinstance(record.get(key), str):
            try:
                record[key] = json.loads(record[key])
            except ValueError:
                pass
    if "start_with_vela" in record:
        record["start_with_vela"] = bool(record["start_with_vela"])
    return record
