"""Serving apps under /apps/{id}/ — the hub is the environment, same-origin.

Web apps (manifest "web" entry) are served statically from the installed copy.
Process apps are reverse-proxied to http://127.0.0.1:{port}/ while running.

Routes registered here must be mounted BEFORE the SPA fallback so /apps/{id}/...
requests are never swallowed by the frontend's index.html. The backend only
claims /apps/{id}/... for KNOWN app ids; bare /apps belongs to the SPA.
"""

import json
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from .errors_http import Conflict, NotAllowed, NotFound, Upstream
from .manifest import Manifest
from .pwa import build_service_worker, build_web_manifest
from .registry import Registry
from .state import StateStore, pid_alive

_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]

_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)
_REQUEST_HEADER_DENYLIST = _HOP_BY_HOP | {"host", "content-length", "cookie", "authorization"}
_RESPONSE_HEADER_DENYLIST = _HOP_BY_HOP | {"content-length", "set-cookie"}


def mount_webapps(app: FastAPI, registry: Registry, state: StateStore) -> None:
    client = httpx.AsyncClient(follow_redirects=False, timeout=None)

    @app.on_event("shutdown")
    async def _close_client() -> None:
        await client.aclose()

    def get_manifest_or_404(app_id: str) -> Manifest:
        manifest = registry.get(app_id)
        if manifest is None:
            raise NotFound(f"unknown app: {app_id}", code="apps.unknown")
        return manifest

    def serve_static(app_id: str, manifest: Manifest, file_path: str) -> Response:
        if not registry.is_installed(app_id):
            raise NotFound(f"app {app_id} is not installed", code="apps.not_installed")
        base = registry.installed_path(app_id).resolve()
        if not file_path:
            file_path = manifest.web.entry
        if file_path == "manifest.webmanifest":
            return Response(
                content=json.dumps(build_web_manifest(manifest), indent=2),
                media_type="application/manifest+json",
            )
        if file_path == "sw.js":
            if manifest.schema_version == 2:
                raise NotFound("v2 views do not support service workers",
                               code="apps.no_service_worker")
            return Response(
                content=build_service_worker(manifest, base),
                media_type="application/javascript",
            )
        target = (base / file_path).resolve()
        if not target.is_file() or not target.is_relative_to(base):
            raise NotFound(f"no such file in app {app_id}: {file_path}",
                           code="apps.file_unknown")
        return FileResponse(target)

    async def proxy_request(app_id: str, file_path: str, request: Request) -> Response:
        entry = state.get(app_id)
        running = bool(entry and pid_alive(entry.get("pid", -1), entry.get("pid_ctime")))
        if not running:
            raise Conflict(f"app {app_id} is not running", code="apps.not_running")
        port = entry.get("port")
        if port is None:
            raise NotFound(f"app {app_id} does not serve HTTP", code="apps.no_http")

        headers = [
            (name, value)
            for name, value in request.headers.items()
            if name.lower() not in _REQUEST_HEADER_DENYLIST
        ]
        upstream_request = client.build_request(
            request.method,
            f"http://127.0.0.1:{port}/{file_path}",
            params=httpx.QueryParams(request.url.query),
            headers=headers,
            content=request.stream(),
        )
        try:
            upstream = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            raise Upstream(f"app {app_id} is unreachable: {exc}",
                           code="apps.unreachable") from exc
        response_headers = {
            name: value
            for name, value in upstream.headers.items()
            if name.lower() not in _RESPONSE_HEADER_DENYLIST
        }
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=response_headers,
            background=BackgroundTask(upstream.aclose),
        )

    async def dispatch(app_id: str, file_path: str, request: Request) -> Response:
        # registry.get is installed-first, so apps keep working when the
        # source folder under apps/ is gone.
        manifest = get_manifest_or_404(app_id)
        if not registry.is_installed(app_id):
            raise NotFound(f"app {app_id} is not installed", code="apps.not_installed")
        if file_path == "_vela/sdk.js" and manifest.schema_version == 2:
            response = FileResponse(Path(__file__).resolve().parent / "assets/sdk.js", media_type="application/javascript")
        elif manifest.web is not None:
            if manifest.active_runtime(registry.platform) == "process" and not state.is_running(app_id):
                raise Conflict("Required app process is stopped", code="apps.process_stopped")
            # v2 static assets may require a managed process to be running.
            if request.method not in ("GET", "HEAD"):
                raise NotAllowed("web apps are served statically", code="apps.static_only")
            response = serve_static(app_id, manifest, file_path)
        else:
            response = await proxy_request(app_id, file_path, request)
        if manifest.schema_version == 2:
            # Enforced on the response too, including direct/new-tab visits.
            # No allow-same-origin: every document receives an opaque origin.
            response.headers["Content-Security-Policy"] = (
                "sandbox allow-scripts; default-src 'none'; "
                "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; font-src 'self'; connect-src 'none'; "
                "frame-src 'none'; worker-src 'none'; form-action 'none'; base-uri 'none'"
            )
            response.headers["Cache-Control"] = "no-store"
            response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    @app.get("/apps/{app_id}", include_in_schema=False)
    def app_root(app_id: str) -> RedirectResponse:
        get_manifest_or_404(app_id)
        return RedirectResponse(url=f"/apps/{app_id}/", status_code=307)

    @app.api_route("/apps/{app_id}/", methods=_METHODS, include_in_schema=False)
    async def app_entry(app_id: str, request: Request) -> Response:
        return await dispatch(app_id, "", request)

    @app.api_route("/apps/{app_id}/{file_path:path}", methods=_METHODS, include_in_schema=False)
    async def app_file(app_id: str, file_path: str, request: Request) -> Response:
        return await dispatch(app_id, file_path, request)
