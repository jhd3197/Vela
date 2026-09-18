"""Making, checking and restoring a backup of this server."""

import asyncio

from fastapi import APIRouter, Request

from ..backups import BackupError, describe_schedule
from ..errors_http import Conflict, NotFound, Precondition, VelaError
from ..logging_setup import request_actor


def router(backups, settings, lifecycle, auth, agent_runs) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["backups"])

    @api.get("/backups")
    def list_backups() -> dict:
        return {"backups": backups.list()}

    @api.post("/backups", status_code=201)
    def create_backup() -> dict:
        try:
            return backups.create()
        except BackupError as exc:
            raise VelaError(str(exc), code="backups.create_failed") from exc

    @api.post("/backups/{name}/verify")
    def verify_backup(name: str) -> dict:
        try:
            return backups.verify(name)
        except BackupError as exc:
            raise NotFound(str(exc), code="backups.unknown") from exc

    @api.get("/backups/stats")
    def backup_stats() -> dict:
        return {
            **backups.stats(),
            "schedule": describe_schedule((settings.get("backups") or {}).get("schedule")),
        }

    @api.post("/backups/{name}/restore")
    async def restore_backup(name: str, request: Request) -> dict:
        # Restoring replaces live files and stops running apps. It takes a
        # deliberate header so no stray link or retry can start one.
        if request.headers.get("x-vela-confirm") != "restore":
            raise Precondition("Confirm restoring this backup",
                               code="backups.confirm_restore")
        # Agent desktops are stopped before anything is replaced, and every
        # grant, session and browser they held goes with them. A task still
        # dispatching into app data that is being swapped underneath it is the
        # one thing a restore must not allow.
        quiesced = await agent_runs.quiesce()
        try:
            result = await asyncio.to_thread(
                backups.restore, name, lifecycle=lifecycle,
                actor=request_actor(auth, request),
            )
        except BackupError as exc:
            raise Conflict(str(exc), code="backups.restore_failed") from exc
        # What was in flight is interrupted, not resumed: the effects it already
        # had cannot be undone by starting it again.
        interrupted = agent_runs.prepare()["interrupted"]
        return {
            **result,
            "agentDesktopsStopped": quiesced["desktops"],
            "agentTasksInterrupted": interrupted,
        }

    return api
