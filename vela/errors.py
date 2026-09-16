"""A record of what went wrong, so a failure is something you can look at.

Fingerprint-merging, the list filters and `resolve` follow ServerKit's
`error_log_service.py` (MIT, same owner). ServerKit stores rows through
SQLAlchemy against the panel's database; Vela keeps its own
`diagnostics.sqlite`, separate from app data, so clearing diagnostics can never
touch what an app saved.

Nothing here leaves the computer. Recording an error must never break the code
path that raised it, so every failure below is swallowed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

MAX_MESSAGE = 2000
MAX_TRACEBACK = 20_000
MAX_TYPE = 200
MAX_ENDPOINT = 400
PAGE_SIZE = 25

# Retention: whichever comes first. A diagnostics file is a convenience, not an
# archive, and it must not grow without bound on a server nobody is watching.
RETENTION_DAYS = 30
RETENTION_ROWS = 500

# The dashboard may report at most this many errors a minute. A render loop
# that throws on every frame would otherwise write thousands of rows.
CLIENT_LIMIT_PER_MINUTE = 20

SOURCES = ("server", "dashboard", "app")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL,
    source TEXT NOT NULL,
    type TEXT,
    message TEXT NOT NULL,
    traceback TEXT,
    endpoint TEXT,
    count INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS errors_fingerprint ON errors (fingerprint, resolved);
CREATE INDEX IF NOT EXISTS errors_last_seen ON errors (last_seen DESC);
"""


def fingerprint(source: str, type_: str | None, message: str, endpoint: str | None) -> str:
    """What makes two failures 'the same failure'.

    The message is cut at 200 characters so one error that embeds a changing
    id or timestamp still merges rather than filling the list with near
    duplicates.
    """
    raw = "|".join([source or "", type_ or "", (message or "")[:200], endpoint or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ErrorStore:
    """`diagnostics.sqlite`: errors merged by fingerprint, with retention."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._client_hits: list[float] = []
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path, timeout=5)
        connection.row_factory = sqlite3.Row
        if not self._ready:
            connection.executescript(_SCHEMA)
            connection.commit()
            self._ready = True
        return connection

    # ----------------------------------------------------------------- write

    def record(
        self,
        source: str,
        message: str,
        *,
        type_: str | None = None,
        traceback: str | None = None,
        endpoint: str | None = None,
    ) -> dict[str, Any] | None:
        """Record one failure, merging it into an unresolved twin if there is one.

        Returns the row, or None when the record could not be written — which
        is never allowed to matter to the caller.
        """
        if source not in SOURCES:
            source = "server"
        message = str(message or "")[:MAX_MESSAGE]
        if not message:
            return None
        type_ = str(type_)[:MAX_TYPE] if type_ else None
        traceback = str(traceback)[:MAX_TRACEBACK] if traceback else None
        endpoint = str(endpoint)[:MAX_ENDPOINT] if endpoint else None
        mark = fingerprint(source, type_, message, endpoint)
        now = datetime.now().isoformat(timespec="seconds")
        try:
            with self._lock:
                connection = self._connect()
                try:
                    # A resolved row is history: the same failure happening
                    # again is news, so it starts a new row rather than
                    # reopening one someone has already dealt with.
                    existing = connection.execute(
                        "SELECT * FROM errors WHERE fingerprint = ? AND resolved = 0",
                        (mark,),
                    ).fetchone()
                    if existing:
                        connection.execute(
                            "UPDATE errors SET count = count + 1, last_seen = ? WHERE id = ?",
                            (now, existing["id"]),
                        )
                        row_id = existing["id"]
                    else:
                        cursor = connection.execute(
                            "INSERT INTO errors (fingerprint, source, type, message, traceback,"
                            " endpoint, count, first_seen, last_seen, resolved)"
                            " VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 0)",
                            (mark, source, type_, message, traceback, endpoint, now, now),
                        )
                        row_id = cursor.lastrowid
                    connection.commit()
                    self._prune(connection)
                    row = connection.execute(
                        "SELECT * FROM errors WHERE id = ?", (row_id,)
                    ).fetchone()
                    return _as_dict(row) if row else None
                finally:
                    connection.close()
        except sqlite3.Error:
            # Recording a failure must never become a second failure.
            LOG.warning("could not record an error", exc_info=True)
            return None

    def accept_client_report(self) -> bool:
        """Whether the dashboard may report another error right now."""
        now = time.monotonic()
        with self._lock:
            self._client_hits = [hit for hit in self._client_hits if now - hit < 60]
            if len(self._client_hits) >= CLIENT_LIMIT_PER_MINUTE:
                return False
            self._client_hits.append(now)
            return True

    def _prune(self, connection: sqlite3.Connection) -> None:
        cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")
        connection.execute("DELETE FROM errors WHERE last_seen < ?", (cutoff,))
        connection.execute(
            "DELETE FROM errors WHERE id NOT IN ("
            " SELECT id FROM errors ORDER BY last_seen DESC, id DESC LIMIT ?)",
            (RETENTION_ROWS,),
        )
        connection.commit()

    # ------------------------------------------------------------------ read

    def list(
        self,
        *,
        source: str | None = None,
        resolved: bool | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = PAGE_SIZE,
    ) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        if source in SOURCES:
            where.append("source = ?")
            params.append(source)
        if resolved is not None:
            where.append("resolved = ?")
            params.append(1 if resolved else 0)
        if search:
            where.append("(message LIKE ? OR type LIKE ? OR endpoint LIKE ?)")
            like = f"%{search}%"
            params.extend([like, like, like])
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        connection = self._connect()
        try:
            total = connection.execute(
                f"SELECT COUNT(*) FROM errors{clause}", params
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT * FROM errors{clause} ORDER BY last_seen DESC, id DESC LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        finally:
            connection.close()
        return {
            "errors": [_as_dict(row) for row in rows],
            "total": total,
            "page": page,
            "pageSize": page_size,
        }

    def stats(self) -> dict[str, Any]:
        day = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
        connection = self._connect()
        try:
            unresolved = connection.execute(
                "SELECT COUNT(*) FROM errors WHERE resolved = 0"
            ).fetchone()[0]
            recent = connection.execute(
                "SELECT COUNT(*) FROM errors WHERE resolved = 0 AND last_seen >= ?", (day,)
            ).fetchone()[0]
            total = connection.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
            by_source = {
                row["source"]: row["n"]
                for row in connection.execute(
                    "SELECT source, COUNT(*) AS n FROM errors WHERE resolved = 0 GROUP BY source"
                ).fetchall()
            }
        finally:
            connection.close()
        return {
            "unresolved": unresolved,
            "lastDay": recent,
            "total": total,
            "bySource": by_source,
        }

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        """The newest rows, for the support bundle."""
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM errors ORDER BY last_seen DESC, id DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        finally:
            connection.close()
        return [_as_dict(row) for row in rows]

    # ---------------------------------------------------------------- change

    def resolve(self, error_id: int, resolved: bool = True) -> dict[str, Any] | None:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    "UPDATE errors SET resolved = ? WHERE id = ?",
                    (1 if resolved else 0, int(error_id)),
                )
                connection.commit()
                row = connection.execute(
                    "SELECT * FROM errors WHERE id = ?", (int(error_id),)
                ).fetchone()
            finally:
                connection.close()
        return _as_dict(row) if row else None

    def delete(self, error_id: int) -> bool:
        with self._lock:
            connection = self._connect()
            try:
                cursor = connection.execute("DELETE FROM errors WHERE id = ?", (int(error_id),))
                connection.commit()
                return cursor.rowcount > 0
            finally:
                connection.close()


def _as_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "fingerprint": row["fingerprint"],
        "source": row["source"],
        "type": row["type"],
        "message": row["message"],
        "traceback": row["traceback"],
        "endpoint": row["endpoint"],
        "count": row["count"],
        "firstSeen": row["first_seen"],
        "lastSeen": row["last_seen"],
        "resolved": bool(row["resolved"]),
    }


def json_default(value: Any) -> str:
    return json.dumps(value, indent=2, default=str)
