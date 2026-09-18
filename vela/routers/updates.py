"""Checking for, installing and undoing a Vela update.

One anonymous request to the GitHub releases API, at most every six hours, and
only while the check is on. Replacing Vela with another copy of Vela is not
something a stray request may start, so applying and rolling back each take a
deliberate header.
"""

import asyncio

from fastapi import APIRouter, Request

from ..errors_http import Precondition
from ..updates import capability, rollback_available


def router(updates, update_job, config, request_shutdown, update_report_state) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["updates"])

    @api.get("/updates/report")
    def update_report() -> dict:
        return update_report_state() or {}

    # Updates. One anonymous request to the GitHub releases API, at most every
    # six hours, and only while the check is on.
    @api.get("/updates")
    def update_status() -> dict:
        return updates.status()

    @api.post("/updates/check")
    async def check_updates() -> dict:
        return await asyncio.to_thread(updates.check, force=True)

    @api.get("/updates/job")
    def update_job_state() -> dict:
        return {
            **update_job.state(),
            "rollback": rollback_available(config, capability()),
        }

    @api.post("/updates/apply")
    async def apply_update(request: Request) -> dict:
        # Replacing Vela with another copy of Vela is not something a stray
        # request may start.
        if request.headers.get("x-vela-confirm") != "update":
            raise Precondition("Confirm installing this update",
                               code="updates.confirm_required")
        return await asyncio.to_thread(update_job.apply, stop=request_shutdown)

    @api.post("/updates/rollback")
    async def rollback_update(request: Request) -> dict:
        if request.headers.get("x-vela-confirm") != "rollback":
            raise Precondition("Confirm going back", code="updates.confirm_rollback")
        return await asyncio.to_thread(update_job.rollback, stop=request_shutdown)

    return api
