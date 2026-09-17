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
