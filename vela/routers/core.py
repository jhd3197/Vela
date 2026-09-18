"""Signing in, and what this server is.

Every route here answers about the server itself rather than about something
stored in it, which is why the dashboard can call them before it knows whether
anything is installed.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import __version__
from ..config import dir_size
from ..manifest import SUPPORTED_PLATFORMS


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)


def router(auth, registry, connected_apps, config, platform) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["core"])

    @api.get("/session")
    def hub_session(request: Request):
        return {"token": auth.bootstrap(request), "remote": auth.is_remote_request(request)}

    @api.post("/login")
    def login(payload: LoginRequest, request: Request):
        token = auth.login(request, payload.password)
        response = JSONResponse({"token": token})
        response.set_cookie("__Host-vela-session", token, max_age=43200, secure=True, httponly=True, samesite="strict", path="/")
        return response

    @api.post("/logout")
    def logout(request: Request):
        auth.logout(request)
        response = JSONResponse({"ok": True})
        response.delete_cookie("__Host-vela-session", secure=True, httponly=True, samesite="strict", path="/")
        return response

    @api.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @api.get("/platforms")
    def platforms() -> dict:
        return {"current": platform, "supported": list(SUPPORTED_PLATFORMS)}

    @api.get("/engine")
    def engine() -> dict:
        apps = registry.list_apps() + connected_apps.list_apps()
        return {
            "status": "running",
            "engine": "local",
            "version": __version__,
            "endpoint": "http://127.0.0.1:7700",
            "apps_installed": sum(1 for a in apps if a["installed"]),
            "apps_running": sum(1 for a in apps if a["running"]),
            "storage_bytes": dir_size(config.data_dir),
            "data_dir": str(config.data_dir),
        }

    return api
