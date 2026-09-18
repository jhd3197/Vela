"""Installed apps: what is there, and starting and stopping it."""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..errors_http import NotFound


def router(registry, connected_apps, lifecycle, usage, snooze) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["apps"])

    def get_manifest_or_404(app_id: str):
        manifest = registry.get(app_id)
        if manifest is None:
            raise NotFound(f"unknown app: {app_id}", code="apps.unknown")
        return manifest

    @api.get("/apps")
    def list_apps() -> dict:
        return {"apps": registry.list_apps() + connected_apps.list_apps()}

    @api.get("/apps/{app_id}")
    def get_app(app_id: str) -> dict:
        if connected_apps.owns(app_id): return connected_apps.get(app_id)
        description = registry.describe(app_id)
        if description is None:
            raise NotFound(f"unknown app: {app_id}", code="apps.unknown")
        return description

    @api.get("/apps/{app_id}/icon")
    def get_icon(app_id: str) -> FileResponse:
        manifest = get_manifest_or_404(app_id)
        if not manifest.icon:
            raise NotFound(f"app {app_id} has no icon", code="apps.no_icon")
        icon_path = (manifest.path / manifest.icon).resolve()
        if not icon_path.is_file() or not icon_path.is_relative_to(manifest.path.resolve()):
            raise NotFound(f"icon not found for app: {app_id}", code="apps.icon_missing")
        return FileResponse(icon_path)

    @api.post("/apps/{app_id}/install")
    def install_app(app_id: str) -> dict:
        return lifecycle.install_app(app_id)

    @api.delete("/apps/{app_id}")
    def uninstall_app(app_id: str) -> dict:
        result = lifecycle.uninstall_app(app_id)
        # Removing an app removes the record of having opened it, rather than
        # leaving it to age out of the Frequent window over the next month.
        usage.forget(app_id)
        snooze.forget(app_id)
        return result

    @api.post("/apps/{app_id}/launch")
    def launch_app(app_id: str) -> dict:
        return lifecycle.launch_app(app_id)

    @api.post("/apps/{app_id}/stop")
    def stop_app(app_id: str) -> dict:
        return lifecycle.stop_app(app_id)

    @api.get("/apps/{app_id}/status")
    def app_status(app_id: str) -> dict:
        if connected_apps.owns(app_id): return connected_apps.get(app_id)
        return lifecycle.app_status(app_id)

    @api.post("/apps/{app_id}/upgrade")
    def upgrade_legacy(app_id: str):
        return lifecycle.upgrade_legacy(app_id)

    return api
