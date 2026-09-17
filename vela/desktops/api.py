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


def router(desktops) -> APIRouter:
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
            return desktops.delete(desktop_id)
        except DesktopError as exc:
            raise _fail(exc)

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

    return api
