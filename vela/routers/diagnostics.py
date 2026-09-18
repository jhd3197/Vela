"""Health checks, recorded failures, logs, support bundles and metrics.

The hub session is the gate — the auth middleware rejects anything else on
`/api/*`. "Show developer tools" decides what this browser shows, never what a
request may do, so it is not re-checked here. Nothing in this module sends
anything anywhere: a support bundle is built on this computer for the user to
share themselves.
"""

import asyncio
from typing import Any

from fastapi import APIRouter, Body, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..doctor import summarise
from ..errors import CLIENT_LIMIT_PER_MINUTE
from ..errors_http import NotFound, Precondition, Unprocessable, VelaError
from ..logging_setup import request_actor
from ..logs import DEFAULT_LINES
from ..usage import WINDOW_DAYS as USAGE_WINDOW_DAYS


class ClientError(BaseModel):
    """One failure the dashboard caught in the browser."""

    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)
    type: str | None = Field(default=None, max_length=200)
    stack: str | None = Field(default=None, max_length=20000)
    url: str | None = Field(default=None, max_length=400)


def router(doctor, updates, errors, support, logs, auth, system_metrics, usage) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["diagnostics"])

    # Logs. The hub session is the gate (the auth middleware rejects anything
    # else on /api/*); "Show developer tools" decides what this browser shows,
    # never what a request may do, so it is not re-checked here.
    # Health checks. The last sweep is served as-is so opening Settings does
    # not start thirteen checks; Run now is the deliberate action.
    @api.get("/doctor")
    def doctor_status() -> dict:
        last = doctor.last() or {"checks": [], "ranAt": None, "summary": summarise([])}
        # The desk reads one source for "is anything asking for me", so the
        # pending update rides along with the checks rather than costing the
        # desk a second request.
        status = updates.status()
        return {
            **last,
            "update": {
                "available": status["available"],
                "latest": status["latest"],
                "current": status["current"],
            },
        }

    @api.post("/doctor/run")
    async def doctor_run() -> dict:
        return await asyncio.to_thread(doctor.collect)

    @api.post("/doctor/{key}/repair")
    async def doctor_repair(key: str, request: Request) -> dict:
        result = await asyncio.to_thread(doctor.repair, key, actor=request_actor(auth, request))
        if result.get("check") is None:
            # No such check, or nothing registered to repair it.
            raise Unprocessable(result.get("detail", "That repair is not available."),
                                code="doctor.repair_unavailable")
        return result

    # Errors. The dashboard reports its own failures here; app frames are not
    # hooked, because an app's errors belong to the app.
    @api.get("/errors")
    def list_errors(source: str = "", resolved: str = "", search: str = "", page: int = 1) -> dict:
        wanted = None if resolved not in ("true", "false") else resolved == "true"
        return errors.list(source=source or None, resolved=wanted, search=search or None, page=page)

    @api.get("/errors/stats")
    def error_stats() -> dict:
        return errors.stats()

    @api.post("/errors/client", status_code=202)
    def report_client_error(payload: ClientError) -> dict:
        # A render loop that throws every frame must not be able to fill the
        # database. Over the cap Vela accepts the request and drops the report.
        if not errors.accept_client_report():
            return {"recorded": False, "reason": f"more than {CLIENT_LIMIT_PER_MINUTE} a minute"}
        row = errors.record(
            "dashboard",
            payload.message,
            type_=payload.type,
            traceback=payload.stack,
            endpoint=payload.url,
        )
        return {"recorded": bool(row)}

    @api.post("/errors/{error_id}/resolve")
    def resolve_error(error_id: int, payload: dict[str, Any] | None = Body(None)) -> dict:
        resolved = True if payload is None else bool(payload.get("resolved", True))
        row = errors.resolve(error_id, resolved)
        if row is None:
            raise NotFound("No such error", code="errors.unknown")
        return row

    @api.delete("/errors/{error_id}", status_code=204)
    def delete_error(error_id: int) -> Response:
        if not errors.delete(error_id):
            raise NotFound("No such error", code="errors.unknown")
        return Response(status_code=204)

    # Support bundles. Built on this computer, for the user to share
    # themselves; nothing here sends anything anywhere.
    @api.get("/support-bundle")
    def list_bundles() -> dict:
        return {"bundles": support.list()}

    @api.post("/support-bundle", status_code=201)
    async def create_bundle() -> dict:
        try:
            return await asyncio.to_thread(support.build)
        except OSError as exc:
            raise VelaError(f"Could not build the bundle: {exc}",
                            code="support.build_failed") from exc

    @api.get("/support-bundle/{name}")
    def download_bundle(name: str) -> FileResponse:
        try:
            path = support.path(name)
        except (FileNotFoundError, OSError) as exc:
            raise NotFound("No such bundle", code="support.unknown_bundle") from exc
        return FileResponse(path, media_type="application/zip", filename=name)

    @api.get("/logs")
    def list_logs() -> dict:
        return {"logs": logs.files()}

    @api.get("/logs/{name}")
    def read_log(name: str, lines: int = DEFAULT_LINES, from_end: bool = True,
                 pattern: str = "") -> dict:
        if pattern:
            return logs.search(name, pattern, lines=lines)
        return logs.read(name, lines=lines, from_end=from_end)

    @api.get("/logs/{name}/download")
    def download_log(name: str) -> FileResponse:
        return FileResponse(logs.path(name), media_type="text/plain", filename=name)

    @api.delete("/logs/{name}")
    def clear_log(name: str, request: Request) -> dict:
        # Clearing a log destroys evidence, so it takes a deliberate header
        # rather than a bare DELETE a stray link could produce.
        if request.headers.get("x-vela-confirm") != "clear":
            raise Precondition("Confirm clearing this log",
                               code="logs.confirm_required")
        return logs.clear(name, actor=request_actor(auth, request))

    @api.get("/system/metrics")
    def system_metrics_snapshot() -> dict:
        return system_metrics.snapshot()

    @api.get("/usage")
    def get_usage() -> dict:
        """Opens per app over the last 30 days, for the Launchpad's Frequent tab.

        Counted and kept on this computer only; nothing here is sent anywhere.
        """
        return {"totals": usage.totals(), "windowDays": USAGE_WINDOW_DAYS}

    @api.post("/usage/{app_id}")
    def record_usage(app_id: str) -> dict:
        return usage.record(app_id)

    return api
