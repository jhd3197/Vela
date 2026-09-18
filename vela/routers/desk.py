"""The default desktop under its original names, plus the weather line.

`/api/desk` and `/api/wallpaper` are aliases, not a second store: they read and
write exactly what `/api/desktops/{id}/boards` and `/api/desktops/{id}/wallpaper`
do, revision included, so a dashboard that has not learned about desktops yet
keeps working.
"""

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import FileResponse

from ..wallpaper import MAX_WALLPAPER_BYTES, WallpaperError


def router(desktops, weather) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["desk"])

    # The wallpaper routes are the default desktop's picture under their
    # original names. The image itself lives in the desktop asset store, named
    # by its own digest, so two desktops can draw the same photo and changing
    # one of them cannot delete it from under the other.
    @api.get("/wallpaper")
    def get_wallpaper():
        path, media_type = desktops.wallpaper_file(desktops.default_id())
        # It changes only when the user replaces it, and the page asks for it
        # again on every desk load, so it is worth caching in the browser.
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-cache"})

    @api.put("/wallpaper")
    async def put_wallpaper(request: Request):
        # Raw bytes with the type in the header, the same shape as the release
        # upload, so the server needs no multipart parser for one picture.
        extension = desktops.extension_for(request.headers.get("content-type", ""))
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_WALLPAPER_BYTES:
                raise WallpaperError(413, "A wallpaper is at most 8 MB")
        return desktops.save_wallpaper(desktops.default_id(), bytes(content), extension)

    @api.delete("/wallpaper")
    def delete_wallpaper() -> dict:
        return desktops.remove_wallpaper(desktops.default_id())

    # `/api/desk` is the default desktop's boards under its original name. It
    # is an alias, not a second store: the dashboard that has not learned about
    # desktops yet reads and writes exactly what `/api/desktops/{id}/boards`
    # does, revision included.
    @api.get("/desk")
    def get_desk() -> dict:
        return desktops.boards(desktops.default_id())

    @api.put("/desk")
    def put_desk(payload: dict[str, Any] = Body(...)) -> dict:
        # Nothing is translated here. A `DesktopConflict` — someone else saved
        # first — is a 409 carrying the revision to reload on its own header, so
        # the dashboard says so rather than overwriting an arrangement it never
        # saw; a `DeskError` is a 422. Both reach the one error handler.
        return desktops.save_boards(
            desktops.default_id(), payload.get("boards"), payload.get("revision")
        )

    @api.get("/weather")
    def get_weather() -> dict:
        """The desk's weather line. Makes no request while the switch is off."""
        return weather.current()

    @api.post("/weather/locate")
    def locate_weather(payload: dict[str, Any] = Body(...)) -> dict:
        """Turn a typed place into coordinates, once, so the place is not stored."""
        return weather.locate(str(payload.get("place") or ""))

    return api
