"""HTTP surface for desktops.

Hub-authenticated through the existing middleware, like every other `/api`
route: an app iframe's session can never create or delete a workspace.

The compatibility routes live in `vela/api.py` beside the ones they replace, so
that `/api/desk`, `/api/settings` and `/api/wallpaper` keep their exact shapes
while reading and writing the same desktop this router serves.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..agent_runs.artifacts import MAX_UPLOAD_BYTES as MAX_ARTIFACT_UPLOAD
from ..app_storage import AppServiceError
from ..desk import DeskError
from ..wallpaper import MAX_WALLPAPER_BYTES, WallpaperError
from .models import MAX_NAME, DesktopConflict, DesktopError


class CreateDesktop(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME)


class RenameDesktop(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=MAX_NAME)
    revision: int = Field(ge=0)


class SaveBoards(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    boards: dict[str, Any]


class SaveAppearance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int | None = Field(default=None, ge=0)
    wallpaper: str | None = Field(default=None, max_length=40)
    dim: bool | None = None
    labels: bool | None = None


class OpenView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(max_length=16)
    # What this view points at. Each kind reads its own field and nothing else,
    # so sending both cannot turn one kind of view into another.
    appId: str | None = Field(default=None, max_length=80)
    surface: str | None = Field(default=None, max_length=40)
    url: str | None = Field(default=None, max_length=2000)
    title: str = Field(default="", max_length=120)
    state: dict[str, Any] | None = None
    bounds: dict[str, Any] | None = None
    # A second window of something already open, rather than bringing the first
    # one forward. Off by default: opening Notes usually means "show me Notes".
    newView: bool = False


class UpdateView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=120)
    state: dict[str, Any] | None = None
    bounds: dict[str, Any] | None = None
    restoreBounds: dict[str, Any] | None = None
    minimized: bool | None = None
    raise_: bool = Field(default=False, alias="raise")


class SelectView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    viewId: str | None = Field(default=None, max_length=64)


class SavePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    apps: list[str] = Field(default_factory=list, max_length=64)
    sites: list[Any] = Field(default_factory=list, max_length=64)
    approvals: str = Field(default="ask", max_length=16)
    actionScopes: list[dict[str, Any]] = Field(default_factory=list, max_length=128)
    budget: dict[str, Any] | None = None


class IssueGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    effect: str = Field(max_length=20)
    appId: str = Field(max_length=80)
    runId: str | None = Field(default=None, max_length=64)
    # The exact request this covers. Without one the grant covers any request of
    # its class for that app, which is a bigger decision and is why approving a
    # particular change sends one.
    requestDigest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    scope: dict[str, Any] | None = None
    seconds: int = Field(default=3600, ge=1, le=86400)


class SubmitTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruction: str = Field(min_length=1, max_length=4000)
    #: The caller's own id for this submission, so a retried request joins the
    #: queue once. A double-tapped button is not two tasks.
    clientRequestId: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=120)


class ControlTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern=r"^(pause|resume|stop)$")


class TakeOver(BaseModel):
    model_config = ConfigDict(extra="forbid")
    viewId: str | None = Field(default=None, max_length=64)


class ViewerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern=r"^(click|move|scroll|key|text)$")
    viewId: str | None = Field(default=None, max_length=64)
    point: dict[str, Any] | None = None
    button: str | None = Field(default=None, max_length=8)
    clickCount: int | None = Field(default=None, ge=1, le=2)
    dx: float | None = None
    dy: float | None = None
    key: str | None = Field(default=None, max_length=32)
    #: Text the person chose and sent. Vela never reads the host clipboard.
    text: str | None = Field(default=None, max_length=4000)
    #: When the picture this was decided from was taken.
    frameAt: float | None = None


class ResolveApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern=r"^(approve|deny)$")
    #: The digest of what was on screen when the person decided. Sent back so a
    #: prompt that was replaced between being rendered and being answered
    #: resolves nothing.
    requestDigest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    #: Approving this one thing, or approving this kind of thing for a while.
    #: Two different decisions, never the same button.
    scopeFuture: bool = False


class OpenAgentSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    viewId: str = Field(max_length=64)
    runId: str = Field(min_length=1, max_length=64)
    actorId: str | None = Field(default=None, max_length=64)


class SaveLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    arrangement: str | None = Field(default=None, max_length=16)
    maximizedView: str | None = Field(default=None, max_length=64)
    primaryView: str | None = Field(default=None, max_length=64)
    secondaryView: str | None = Field(default=None, max_length=64)
    selectedView: str | None = Field(default=None, max_length=64)
    dividerRatio: float | None = None
    # Which panes were deliberately emptied, as opposed to simply not sent.
    clear: list[str] = Field(default_factory=list, max_length=4)


def conflict(exc: DesktopConflict) -> HTTPException:
    """409 with the revision to reload, the same header `/api/desk` has used."""
    return HTTPException(
        status_code=409,
        detail=exc.detail,
        headers={"X-Vela-Desk-Revision": str(exc.revision)},
    )


def router(desktops, *, runs=None) -> APIRouter:
    """The desktop routes, and — when a run service exists — its task routes.

    `runs` is optional so the desktop surface can be mounted on its own; without
    it the task routes answer 503 rather than being absent, which is easier to
    diagnose than a 404 that looks like a typo.
    """
    api = APIRouter(prefix="/api/desktops", tags=["desktops"])

    def _fail(exc: DesktopError) -> HTTPException:
        if isinstance(exc, DesktopConflict):
            return conflict(exc)
        return HTTPException(status_code=exc.status, detail=exc.detail)

    @api.get("")
    def list_desktops() -> dict:
        return desktops.list()

    @api.post("", status_code=201)
    def create_desktop(payload: CreateDesktop | None = None) -> dict:
        try:
            return desktops.create((payload.name if payload else None))
        except DesktopError as exc:
            raise _fail(exc)

    # Declared before "/{desktop_id}" so it is not shadowed by it.
    @api.get("/runtime")
    def runtime_status() -> dict:
        return desktops.runtime_status()

    @api.get("/models")
    async def usable_models() -> dict:
        """Which models on this computer could actually run a task."""
        return await _runs().models()

    @api.get("/attention")
    def attention() -> dict:
        """What is working and what needs you, per agent desktop."""
        return _runs().attention()

    # --------------------------------------------------------- what is kept --

    @api.get("/retention")
    def retention() -> dict:
        """What agent desktops keep, for how long, and how much of it there is."""
        return _runs().retention.status()

    @api.post("/retention/sweep")
    def sweep_retention() -> dict:
        """Remove what has expired, now.

        The same sweep that runs on the way up and periodically after. Offered
        as a button because a cleanup that has been failing quietly is exactly
        the thing somebody wants to be able to run and watch.
        """
        return _runs().retention.sweep()

    @api.get("/{desktop_id}")
    def get_desktop(desktop_id: str) -> dict:
        try:
            return desktops.get(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)

    @api.patch("/{desktop_id}")
    def rename_desktop(desktop_id: str, payload: RenameDesktop) -> dict:
        try:
            return desktops.rename(desktop_id, payload.name, payload.revision)
        except DesktopError as exc:
            raise _fail(exc)

    @api.delete("/{desktop_id}")
    def delete_desktop(desktop_id: str) -> dict:
        try:
            result = desktops.delete(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)
        # Its tasks go with it. A run record naming a workspace that no longer
        # exists is a record nobody can act on.
        if runs is not None:
            runs.forget_desktop(desktop_id)
        return result

    @api.get("/{desktop_id}/boards")
    def get_boards(desktop_id: str) -> dict:
        try:
            return desktops.boards(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)

    @api.put("/{desktop_id}/boards")
    def put_boards(desktop_id: str, payload: SaveBoards) -> dict:
        try:
            return desktops.save_boards(desktop_id, payload.boards, payload.revision)
        except DesktopConflict as exc:
            raise conflict(exc)
        except DesktopError as exc:
            raise _fail(exc)
        except DeskError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @api.get("/{desktop_id}/appearance")
    def get_appearance(desktop_id: str) -> dict:
        try:
            return desktops.appearance(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)

    @api.put("/{desktop_id}/appearance")
    def put_appearance(desktop_id: str, payload: SaveAppearance) -> dict:
        patch = payload.model_dump(exclude_none=True)
        patch.pop("revision", None)
        try:
            return desktops.save_appearance(desktop_id, patch, payload.revision)
        except DesktopError as exc:
            raise _fail(exc)

    # Views and layout. Opening a window is not saving an arrangement, so only
    # the layout route carries a revision; selecting and moving windows happen
    # constantly and must not conflict with a drag someone else is finishing.

    @api.get("/{desktop_id}/views")
    def get_views(desktop_id: str) -> dict:
        try:
            return desktops.views(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)

    @api.post("/{desktop_id}/views", status_code=201)
    def open_view(desktop_id: str, payload: OpenView) -> dict:
        try:
            return desktops.open_view(
                desktop_id,
                payload.kind,
                {"appId": payload.appId, "surface": payload.surface, "url": payload.url},
                title=payload.title,
                state=payload.state,
                bounds=payload.bounds,
                reuse=not payload.newView,
            )
        except DesktopError as exc:
            raise _fail(exc)

    @api.patch("/{desktop_id}/views/{view_id}")
    def update_view(desktop_id: str, view_id: str, payload: UpdateView) -> dict:
        patch = payload.model_dump(by_alias=True, exclude_unset=True)
        try:
            return desktops.update_view(desktop_id, view_id, patch)
        except DesktopError as exc:
            raise _fail(exc)

    @api.delete("/{desktop_id}/views/{view_id}")
    def close_view(desktop_id: str, view_id: str) -> dict:
        try:
            return desktops.close_view(desktop_id, view_id)
        except DesktopError as exc:
            raise _fail(exc)

    @api.post("/{desktop_id}/selected-view")
    def select_view(desktop_id: str, payload: SelectView) -> dict:
        try:
            return desktops.select_view(desktop_id, payload.viewId)
        except DesktopError as exc:
            raise _fail(exc)

    @api.get("/{desktop_id}/layout")
    def get_layout(desktop_id: str) -> dict:
        try:
            return desktops.layout(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)

    @api.put("/{desktop_id}/layout")
    def put_layout(desktop_id: str, payload: SaveLayout) -> dict:
        patch = payload.model_dump(exclude_unset=True)
        patch.pop("revision", None)
        # An emptied pane is a deliberate choice, and `exclude_unset` cannot
        # tell "I did not mention this" from "I want this cleared".
        for key in patch.pop("clear", []):
            patch[key] = None
        try:
            return desktops.save_layout(desktop_id, patch, payload.revision)
        except DesktopError as exc:
            raise _fail(exc)

    # What this desktop may touch, and what it has actually been allowed to do.
    # Configuration and authority are separate on purpose: changing the first
    # removes every instance of the second, because narrowing what an agent may
    # do has to take effect now rather than when something is next re-checked.

    @api.get("/{desktop_id}/policy")
    def get_policy(desktop_id: str) -> dict:
        try:
            return desktops.policy(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)

    @api.put("/{desktop_id}/policy")
    def put_policy(desktop_id: str, payload: SavePolicy) -> dict:
        document = payload.model_dump(exclude_unset=True)
        document.pop("revision", None)
        try:
            return desktops.save_policy(desktop_id, document, payload.revision)
        except DesktopError as exc:
            raise _fail(exc)

    @api.post("/{desktop_id}/enable-agent")
    async def enable_agent(desktop_id: str) -> dict:
        try:
            return await desktops.enable_agent(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)

    @api.post("/{desktop_id}/disable-agent")
    async def disable_agent(desktop_id: str) -> dict:
        try:
            return await desktops.disable_agent(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)

    @api.get("/{desktop_id}/grants")
    def list_grants(desktop_id: str) -> dict:
        try:
            return desktops.list_grants(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)

    @api.post("/{desktop_id}/grants", status_code=201)
    def issue_grant(desktop_id: str, payload: IssueGrant) -> dict:
        try:
            return desktops.grant(desktop_id, payload.model_dump())
        except DesktopError as exc:
            raise _fail(exc)

    @api.delete("/{desktop_id}/grants/{grant_id}")
    def revoke_grant(desktop_id: str, grant_id: str) -> dict:
        try:
            return desktops.revoke_grant(desktop_id, grant_id)
        except DesktopError as exc:
            raise _fail(exc)

    @api.delete("/{desktop_id}/grants")
    def revoke_all(desktop_id: str, runId: str | None = None) -> dict:
        try:
            if runId:
                return desktops.revoke_run(desktop_id, runId)
            desktops.get(desktop_id)
            return desktops.revoke_desktop(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)

    @api.get("/{desktop_id}/approvals")
    def list_approvals(desktop_id: str) -> dict:
        """What this desktop is waiting for you to answer."""
        try:
            desktops.get(desktop_id)
            return {"approvals": desktops.approvals.pending(desktop_id)}
        except DesktopError as exc:
            raise _fail(exc)

    @api.post("/{desktop_id}/approvals/{request_id}")
    def resolve_approval(desktop_id: str, request_id: str, payload: ResolveApproval) -> dict:
        """Approve or deny one pending change.

        Owner-authenticated, like every route in this file. Nothing inside an
        agent's browser can reach it, which is what makes "only owner controls
        resolve approvals" a fact about the system rather than a claim about a
        page.
        """
        try:
            desktops.get(desktop_id)
            return desktops.approvals.resolve(
                request_id,
                payload.decision,
                desktop_id=desktop_id,
                expected_digest=payload.requestDigest,
                scope_future=bool(payload.scopeFuture),
            )
        except DesktopError as exc:
            raise _fail(exc)

    @api.post("/{desktop_id}/agent-sessions", status_code=201)
    def open_agent_session(desktop_id: str, payload: OpenAgentSession) -> dict:
        try:
            return desktops.open_agent_session(desktop_id, payload.model_dump())
        except DesktopError as exc:
            raise _fail(exc)

    @api.get("/{desktop_id}/wallpaper")
    def get_wallpaper(desktop_id: str):
        try:
            path, media_type = desktops.wallpaper_file(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)
        # It changes only when the user replaces it, and the page asks for it
        # again on every desk load, so it is worth caching in the browser.
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-cache"})

    @api.put("/{desktop_id}/wallpaper")
    async def put_wallpaper(desktop_id: str, request: Request) -> dict:
        # Raw bytes with the type in the header, the same shape as the existing
        # wallpaper route, so the server needs no multipart parser for one picture.
        try:
            extension = desktops.extension_for(request.headers.get("content-type", ""))
            content = bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > MAX_WALLPAPER_BYTES:
                    raise WallpaperError(413, "A wallpaper is at most 8 MB")
            return desktops.save_wallpaper(desktop_id, bytes(content), extension)
        except WallpaperError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail)
        except DesktopError as exc:
            raise _fail(exc)

    @api.delete("/{desktop_id}/wallpaper")
    def delete_wallpaper(desktop_id: str) -> dict:
        try:
            return desktops.remove_wallpaper(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)

    # -------------------------------------------------- watch and control --

    @api.get("/{desktop_id}/viewer")
    def viewer_status(desktop_id: str) -> dict:
        """Who is watching, and who — if anyone — is typing."""
        try:
            return _runs().viewer.status(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)

    @api.get("/{desktop_id}/views/{view_id}/frame")
    async def view_frame(desktop_id: str, view_id: str, maxAgeMs: int = 400) -> dict:
        """A recent picture of one view — the view, never the whole desktop.

        A desktop capture would contain the owner's approval prompt, which is
        exactly what must never be in anything the agent's side can influence.
        """
        try:
            frame = await _runs().viewer.frame(desktop_id, view_id, max_age_ms=maxAgeMs)
        except DesktopError as exc:
            raise _fail(exc)
        return {key: value for key, value in frame.items() if key != "file"}

    @api.get("/{desktop_id}/views/{view_id}/frame/{digest}")
    def view_frame_bytes(desktop_id: str, view_id: str, digest: str):
        """The bytes, behind owner authentication and never cached."""
        try:
            path = _runs().viewer.frame_file(desktop_id, view_id, digest)
        except DesktopError as exc:
            raise _fail(exc)
        return FileResponse(
            path,
            media_type="image/png",
            headers={
                "Cache-Control": "no-store, private",
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @api.post("/{desktop_id}/takeover", status_code=201)
    async def take_over(desktop_id: str, payload: TakeOver | None = None) -> dict:
        """Stop the agent, change the generation, then hand control over."""
        try:
            return await _runs().viewer.take_over(
                desktop_id, view_id=payload.viewId if payload else None
            )
        except DesktopError as exc:
            raise _fail(exc)
        except AppServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail)

    @api.post("/{desktop_id}/takeover/{lease_id}/input")
    async def send_input(desktop_id: str, lease_id: str, payload: ViewerInput) -> dict:
        try:
            return await _runs().viewer.send_input(
                desktop_id, lease_id, payload.model_dump(exclude_none=True)
            )
        except DesktopError as exc:
            raise _fail(exc)
        except AppServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail)

    @api.delete("/{desktop_id}/takeover/{lease_id}")
    def release_control(desktop_id: str, lease_id: str) -> dict:
        """Give control back. The task stays paused until you say carry on."""
        try:
            return _runs().viewer.release(desktop_id, lease_id)
        except DesktopError as exc:
            raise _fail(exc)

    # ------------------------------------------------------------- files --

    @api.get("/{desktop_id}/files")
    def list_files(desktop_id: str, runId: str | None = None) -> dict:
        """What this desktop was given, what it came back with, and the limits."""
        try:
            return _runs().files(desktop_id, run_id=runId)
        except DesktopError as exc:
            raise _fail(exc)
        except AppServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail)

    @api.post("/{desktop_id}/files", status_code=201)
    async def add_file(desktop_id: str, request: Request) -> dict:
        """A file the owner chose, staged for a task to attach.

        Streamed with a cap rather than read whole: the limit has to bound what
        is written, not describe what was. The name is a label — the bytes are
        stored under one Vela generated.
        """
        name = request.headers.get("x-vela-filename") or "file"
        media_type = (request.headers.get("content-type") or "").split(";")[0].strip()
        run_id = request.headers.get("x-vela-run") or None
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_ARTIFACT_UPLOAD:
            raise HTTPException(status_code=413, detail="That file is too large to attach.")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_ARTIFACT_UPLOAD:
                raise HTTPException(status_code=413, detail="That file is too large to attach.")
        try:
            desktops.store.get(desktop_id)
            return desktops.artifacts.accept_upload(
                desktop_id, [bytes(body)], name=name, media_type=media_type, run_id=run_id
            )
        except DesktopError as exc:
            raise _fail(exc)
        except AppServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail)

    @api.get("/{desktop_id}/files/{artifact_id}")
    def read_file(desktop_id: str, artifact_id: str):
        """The bytes, behind owner authentication, offered as a download.

        Never rendered inline. A file that arrived from a website is not
        something to open in the dashboard's own origin.
        """
        try:
            desktops.store.get(desktop_id)
            path, record = desktops.artifacts.file(desktop_id, artifact_id)
        except DesktopError as exc:
            raise _fail(exc)
        except AppServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail)
        return FileResponse(
            path,
            media_type="application/octet-stream",
            filename=record["name"],
            headers={
                "Cache-Control": "no-store, private",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    @api.delete("/{desktop_id}/files/{artifact_id}")
    def remove_file(desktop_id: str, artifact_id: str) -> dict:
        try:
            desktops.store.get(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)
        if not desktops.artifacts.remove(desktop_id, artifact_id):
            raise HTTPException(status_code=404, detail="That file is not on this desktop.")
        return {"ok": True}

    @api.delete("/{desktop_id}/files")
    def clear_unresolved(desktop_id: str, digest: str | None = None) -> dict:
        """Say that a submission nobody could confirm has been checked.

        An owner action on purpose. The only thing that can establish what
        happened on somebody else's server is a person looking, and a timer is
        not a person looking.
        """
        try:
            return desktops.resolve_uncertain(desktop_id, digest)
        except DesktopError as exc:
            raise _fail(exc)

    # --------------------------------------------------- website sessions --

    @api.get("/{desktop_id}/session")
    def website_session(desktop_id: str) -> dict:
        """What sign-ins are being kept for this desktop, as counts."""
        try:
            return desktops.session_state(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)

    @api.post("/{desktop_id}/session")
    async def keep_website_session(desktop_id: str) -> dict:
        """Keep this desktop's signed-in websites for its next browser."""
        try:
            return await desktops.remember_session(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)

    @api.delete("/{desktop_id}/session")
    async def erase_website_session(desktop_id: str) -> dict:
        """Erase them, from the file and from the browser that is open."""
        try:
            return await desktops.forget_session(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)

    # ------------------------------------------------------------- tasks --

    def _runs():
        if runs is None:
            raise HTTPException(status_code=503, detail="Agent tasks are not available yet.")
        return runs

    @api.post("/{desktop_id}/tasks", status_code=202)
    async def submit_task(desktop_id: str, payload: SubmitTask) -> dict:
        """Queue one instruction. 202: accepted, not finished.

        The task is the server's from here. Closing this page, losing the
        network or switching desktops does not touch it.
        """
        try:
            return await _runs().submit(desktop_id, payload.model_dump())
        except DesktopError as exc:
            raise _fail(exc)
        except AppServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail)

    @api.get("/{desktop_id}/tasks")
    def list_tasks(desktop_id: str, limit: int = 50, offset: int = 0) -> dict:
        try:
            return _runs().list(desktop_id, limit=limit, offset=offset)
        except DesktopError as exc:
            raise _fail(exc)

    @api.get("/{desktop_id}/tasks/{run_id}")
    def get_task(desktop_id: str, run_id: str) -> dict:
        try:
            return _runs().get(desktop_id, run_id)
        except DesktopError as exc:
            raise _fail(exc)
        except AppServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail)

    @api.post("/{desktop_id}/tasks/{run_id}/control")
    async def control_task(desktop_id: str, run_id: str, payload: ControlTask) -> dict:
        try:
            return await _runs().control(desktop_id, run_id, payload.action)
        except DesktopError as exc:
            raise _fail(exc)
        except AppServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail)

    @api.post("/{desktop_id}/tasks/{run_id}/retry", status_code=201)
    async def retry_task(desktop_id: str, run_id: str) -> dict:
        """Ask for the same thing again, as a new task.

        Never a resumption: what the old run did, it did. This queues a fresh
        attempt and records which one it came from.
        """
        try:
            return await _runs().retry(desktop_id, run_id)
        except DesktopError as exc:
            raise _fail(exc)
        except AppServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail)

    @api.post("/{desktop_id}/queue/resume")
    async def resume_queue(desktop_id: str) -> dict:
        """Carry on with what is queued, after seeing why it stopped."""
        try:
            return await _runs().resume_queue(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)
        except AppServiceError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail)

    @api.get("/{desktop_id}/events")
    def read_events(desktop_id: str, after: int = 0, limit: int = 200) -> dict:
        """Everything after a cursor the viewer already has.

        Polled rather than streamed for now: a numbered stream with a cursor
        recovers from a dropped connection on its own, and adding a transport
        that needs its own authentication is Phase 10's work, not this one's.
        """
        try:
            return _runs().events(desktop_id, after=after, limit=limit)
        except DesktopError as exc:
            raise _fail(exc)

    return api
