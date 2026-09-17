"""What an agent has actually been allowed to do, checked where it commits.

A grant is not a preference. It is a row that has to be there at the moment the
effect is written, and it lives in the same database the effect writes to for
exactly that reason: revoking it and committing the effect contend for the same
write lock, so one of them wins and neither half-happens. Keeping grants beside
the desktop's configuration would have meant two databases and two transactions
and a window between them, and "we check, then we write" is not a sentence worth
having in the part of a system that decides what an agent may change.

Every grant names the installation and the manifest it was reviewed against.
Reinstall the app and the grant stops matching; change the app and it stops
matching. A grant for a specific request also carries that request's digest, so
approving one thing is not approving a different thing that arrives afterwards.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from ..app_storage import AppServiceError
from .effects import EFFECTFUL, EFFECT_CLASSES

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_grants (
  id TEXT PRIMARY KEY,
  desktop_id TEXT NOT NULL,
  run_id TEXT,
  effect TEXT NOT NULL,
  app_id TEXT NOT NULL,
  installation_id TEXT NOT NULL,
  contract TEXT NOT NULL,
  request_digest TEXT,
  scope TEXT NOT NULL DEFAULT '{}',
  expires_at REAL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_grants_by_desktop ON agent_grants (desktop_id);
CREATE INDEX IF NOT EXISTS agent_grants_by_app ON agent_grants (app_id);
"""

#: Longest a grant can outlive the moment it was given. A grant that never
#: expired would be a decision somebody made once becoming a standing
#: permission nobody remembers giving.
MAX_GRANT_SECONDS = 24 * 3600


class Grants:
    """Agent grants, stored beside the app data they authorize changes to."""

    def __init__(self, storage):
        self.storage = storage
        with self.storage.connection() as db:
            db.executescript(SCHEMA)

    # ------------------------------------------------------------ issuing --

    def issue(
        self,
        *,
        desktop_id: str,
        effect: str,
        app_id: str,
        installation_id: str,
        contract: str,
        run_id: str | None = None,
        request_digest: str | None = None,
        scope: dict[str, Any] | None = None,
        seconds: int = 3600,
    ) -> dict[str, Any]:
        """Record one authorization. Returns the grant as the owner sees it."""
        if effect not in EFFECT_CLASSES:
            raise AppServiceError(422, "That is not something a grant can cover.")
        if effect not in EFFECTFUL:
            # Reading is covered by the desktop allowing the app at all. A grant
            # for it would be a permission that means nothing, which is worse
            # than no permission at all.
            raise AppServiceError(422, "Reading does not need a grant.")
        seconds = max(1, min(int(seconds), MAX_GRANT_SECONDS))
        grant_id = str(uuid.uuid4())
        row = (
            grant_id,
            desktop_id,
            run_id,
            effect,
            app_id,
            installation_id,
            contract,
            request_digest,
            json.dumps(scope or {}, sort_keys=True, separators=(",", ":")),
            time.time() + seconds,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        with self.storage.connection() as db:
            db.execute("INSERT INTO agent_grants VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
        return self._describe(row)

    def list(self, desktop_id: str) -> list[dict[str, Any]]:
        with self.storage.connection() as db:
            rows = db.execute(
                "SELECT * FROM agent_grants WHERE desktop_id=? ORDER BY created_at DESC",
                (desktop_id,),
            ).fetchall()
        now = time.time()
        return [self._describe(tuple(row)) for row in rows if (row["expires_at"] or 0) > now]

    # --------------------------------------------------------- revocation --

    def revoke(self, grant_id: str) -> bool:
        with self.storage.connection() as db:
            return bool(db.execute("DELETE FROM agent_grants WHERE id=?", (grant_id,)).rowcount)

    def revoke_desktop(self, desktop_id: str) -> int:
        """Everything this desktop was allowed to change. Used by Stop, by a
        policy change and by deleting the desktop."""
        with self.storage.connection() as db:
            return db.execute(
                "DELETE FROM agent_grants WHERE desktop_id=?", (desktop_id,)
            ).rowcount

    def revoke_run(self, desktop_id: str, run_id: str) -> int:
        with self.storage.connection() as db:
            return db.execute(
                "DELETE FROM agent_grants WHERE desktop_id=? AND run_id=?", (desktop_id, run_id)
            ).rowcount

    def revoke_app(self, app_id: str) -> int:
        """Used when an app is removed or replaced. Its grants named an
        installation that no longer exists, and a new one must not inherit
        them."""
        with self.storage.connection() as db:
            return db.execute("DELETE FROM agent_grants WHERE app_id=?", (app_id,)).rowcount

    def sweep(self) -> int:
        with self.storage.connection() as db:
            return db.execute(
                "DELETE FROM agent_grants WHERE expires_at IS NULL OR expires_at <= ?",
                (time.time(),),
            ).rowcount

    # -------------------------------------------------------- the check --

    def find(
        self,
        db,
        *,
        desktop_id: str,
        effect: str,
        app_id: str,
        installation_id: str,
        contract: str,
        run_id: str | None = None,
        request_digest: str | None = None,
        scope: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """A matching, unexpired grant, read on the caller's own connection.

        `db` is passed in rather than opened here so this can run *inside* the
        transaction that is about to write. That is the whole design: a grant
        revoked a moment earlier has already taken the write lock, so it either
        lands before this read — and there is nothing to find — or after the
        effect, which is then a committed effect and stays one.
        """
        rows = db.execute(
            "SELECT * FROM agent_grants WHERE desktop_id=? AND effect=? AND app_id=?",
            (desktop_id, effect, app_id),
        ).fetchall()
        now = time.time()
        wanted = json.dumps(scope or {}, sort_keys=True, separators=(",", ":"))
        for row in rows:
            if (row["expires_at"] or 0) <= now:
                continue
            if row["installation_id"] != installation_id or row["contract"] != contract:
                continue
            if row["run_id"] is not None and row["run_id"] != run_id:
                continue
            if row["request_digest"] is not None and row["request_digest"] != request_digest:
                continue
            # An empty stored scope covers any request of this class; a stored
            # one has to match exactly. Approving "save this note" is not
            # approving "save that other note".
            if row["scope"] not in ("{}", wanted):
                continue
            return self._describe(tuple(row))
        return None

    def require(self, db, *, detail: str | None = None, **binding) -> dict[str, Any]:
        """Like `find`, but refuses instead of returning None."""
        grant = self.find(db, **binding)
        if grant is None:
            raise AppServiceError(
                403,
                detail
                or "This agent has not been allowed to make that change on this desktop.",
            )
        return grant

    @staticmethod
    def _describe(row: tuple) -> dict[str, Any]:
        (
            grant_id,
            desktop_id,
            run_id,
            effect,
            app_id,
            installation_id,
            contract,
            request_digest,
            scope,
            expires_at,
            created_at,
        ) = row
        return {
            "id": grant_id,
            "desktopId": desktop_id,
            "runId": run_id,
            "effect": effect,
            "appId": app_id,
            "installationId": installation_id,
            "contract": contract,
            "requestDigest": request_digest,
            "scope": json.loads(scope),
            "expiresAt": expires_at,
            "createdAt": created_at,
        }
