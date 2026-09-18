"""Everything that answers a path no other router claimed.

Registered last, deliberately. `/api/{unknown_path}` must come after every real
API route or it would swallow them, and the SPA fallback must come after
`/apps/{app_id}/...` or an app request would be answered with the dashboard's
index.html. `vela/router_registry.py` is where that order is stated.
"""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..errors_http import NotFound


def router(config) -> APIRouter:
    api = APIRouter(tags=["fallback"])

    # The bundled wallpapers are downloaded into the data directory on first
    # start rather than shipped in web/dist (see vela/bundled_wallpapers.py),
    # so they are served from there. Registered before the SPA fallback, which
    # must never swallow them.
    wallpapers_root = config.data_dir / "wallpapers"

    @api.get("/wallpapers/{file_path:path}", include_in_schema=False)
    def bundled_wallpaper(file_path: str) -> FileResponse:
        root = wallpapers_root.resolve()
        candidate = (root / file_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        raise NotFound("Unknown wallpaper", code="wallpapers.unknown")

    @api.api_route("/api/{unknown_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    def unknown_api(unknown_path: str):
        raise NotFound("Unknown API endpoint", code="api.unknown_endpoint")

    index = config.web_dist / "index.html"
    if config.web_dist.is_dir() and index.is_file():
        dist_root = config.web_dist.resolve()

        # The page an agent desktop's browser loads. A separate entry point, not
        # the dashboard with pieces hidden: hiding is a rendering decision and
        # this has to be a structural one. It is the only path under this prefix,
        # which is what the managed browser's network policy allows.
        @api.get("/agent-host/{full_path:path}", include_in_schema=False)
        def agent_host(full_path: str) -> FileResponse:
            page = config.web_dist / "agent-host.html"
            if not page.is_file():
                raise NotFound("The agent host was not built", code="agent_host.missing")
            return FileResponse(page)

        @api.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> FileResponse:
            candidate = (config.web_dist / full_path).resolve()
            if full_path and candidate.is_file() and candidate.is_relative_to(dist_root):
                return FileResponse(candidate)
            return FileResponse(index)
    else:

        @api.get("/", include_in_schema=False)
        def root() -> dict:
            return {"name": "vela", "api": "/api", "docs": "/docs"}

    return api
