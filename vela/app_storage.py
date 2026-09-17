"""Transactional installation identities and revisioned JSON documents.

Code lives in installed/; data survives uninstall in app-data.sqlite.
No caller-controlled paths or app IDs are accepted by document operations.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path


class AppServiceError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class AppStorage:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS installations (
                    app_id TEXT PRIMARY KEY, identity TEXT UNIQUE NOT NULL, active INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    app_id TEXT PRIMARY KEY, value TEXT NOT NULL,
                    revision INTEGER NOT NULL, schema_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY, app_id TEXT NOT NULL, value TEXT NOT NULL,
                    revision INTEGER NOT NULL, schema_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL, reason TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS migrations (
                    app_id TEXT NOT NULL, digest TEXT NOT NULL, original TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY (app_id, digest)
                );
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def activate(self, app_id: str) -> str:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM installations WHERE app_id=?", (app_id,)).fetchone()
            if row and row["active"]:
                return row["identity"]
            identity = str(uuid.uuid4())
            db.execute("INSERT OR REPLACE INTO installations VALUES (?, ?, 1)", (app_id, identity))
            return identity

    def installation(self, app_id: str) -> str | None:
        """The active installation identity, or None — without creating one.

        `activate` is the write path an app session takes. Callers that only
        want to know which installation is current — an open window binding
        itself to one, say — must not bring a removed app back by asking.
        """
        with self.connection() as db:
            row = db.execute(
                "SELECT identity FROM installations WHERE app_id=? AND active=1", (app_id,)
            ).fetchone()
        return row["identity"] if row else None

    def deactivate(self, app_id: str):
        with self.connection() as db:
            db.execute("UPDATE installations SET active=0 WHERE app_id=?", (app_id,))

    def _app_id(self, db, identity):
        row = db.execute("SELECT app_id FROM installations WHERE identity=? AND active=1", (identity,)).fetchone()
        if not row:
            raise AppServiceError(401, "Installation identity is no longer active")
        return row["app_id"]

    def read(self, identity: str, schema_version: int) -> dict:
        with self.connection() as db:
            app_id = self._app_id(db, identity)
            row = db.execute("SELECT * FROM documents WHERE app_id=?", (app_id,)).fetchone()
            if row and row["schema_version"] != schema_version:
                raise AppServiceError(409, "Stored data needs an explicit schema migration")
            return {"value": json.loads(row["value"]) if row else None,
                    "revision": row["revision"] if row else 0, "schemaVersion": schema_version}

    def write(self, identity: str, value, revision: int, schema_version: int, quota: int,
              *, snapshot_reason=None, migration=None) -> dict:
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (ValueError, TypeError, RecursionError) as exc:
            raise AppServiceError(422, "Value must be finite JSON") from exc
        if len(encoded.encode("utf-8")) > quota:
            raise AppServiceError(413, "App storage quota exceeded")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            app_id = self._app_id(db, identity)
            row = db.execute("SELECT * FROM documents WHERE app_id=?", (app_id,)).fetchone()
            if migration and db.execute("SELECT 1 FROM migrations WHERE app_id=? AND digest=?", (app_id, migration[0])).fetchone():
                return {"value": json.loads(row["value"]) if row else None, "revision": row["revision"] if row else 0,
                        "schemaVersion": schema_version, "alreadyImported": True}
            if row and row["schema_version"] != schema_version:
                raise AppServiceError(409, "Stored data needs an explicit schema migration")
            if revision != (row["revision"] if row else 0):
                raise AppServiceError(409, "Storage revision conflict; read the latest document before retrying")
            if snapshot_reason and row:
                self._snapshot(db, app_id, row, snapshot_reason)
            if migration:
                db.execute("INSERT INTO migrations VALUES (?, ?, ?, ?)",
                           (app_id, migration[0], migration[1], datetime.now(timezone.utc).isoformat()))
            db.execute("INSERT OR REPLACE INTO documents VALUES (?, ?, ?, ?)",
                       (app_id, encoded, revision + 1, schema_version))
            return {"value": value, "revision": revision + 1, "schemaVersion": schema_version}

    def _snapshot(self, db, app_id, row, reason):
        snapshot_id = str(uuid.uuid4())
        db.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (snapshot_id, app_id, row["value"], row["revision"], row["schema_version"], datetime.now(timezone.utc).isoformat(), reason))
        # Bound snapshot storage. Migration originals are separately retained.
        db.execute("DELETE FROM snapshots WHERE app_id=? AND id NOT IN (SELECT id FROM snapshots WHERE app_id=? ORDER BY created_at DESC LIMIT 20)", (app_id, app_id))
        return {"id": snapshot_id, "revision": row["revision"]}

    def snapshot(self, identity):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            app_id = self._app_id(db, identity)
            row = db.execute("SELECT * FROM documents WHERE app_id=?", (app_id,)).fetchone()
            if not row:
                raise AppServiceError(404, "No app data to back up yet")
            return self._snapshot(db, app_id, row, "Manual backup")

    def snapshots(self, identity):
        with self.connection() as db:
            app_id = self._app_id(db, identity)
            return [dict(row) for row in db.execute("SELECT id, revision, schema_version AS schemaVersion, created_at, reason FROM snapshots WHERE app_id=? ORDER BY created_at DESC", (app_id,))]

    def get_snapshot(self, identity, snapshot_id):
        with self.connection() as db:
            app_id = self._app_id(db, identity)
            row = db.execute("SELECT * FROM snapshots WHERE app_id=? AND id=?", (app_id, snapshot_id)).fetchone()
            if not row:
                raise AppServiceError(404, "App backup not found")
            return {"value": json.loads(row["value"]), "schemaVersion": row["schema_version"]}

    def migration_done(self, identity, digest):
        with self.connection() as db:
            app_id = self._app_id(db, identity)
            return bool(db.execute("SELECT 1 FROM migrations WHERE app_id=? AND digest=?", (app_id, digest)).fetchone())
