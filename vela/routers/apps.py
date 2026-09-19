"""Installed apps: what is there, and starting and stopping it."""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..errors_http import NotFound


def router(registry, connected_apps, lifecycle, usage, snooze, managed) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["apps"])

    def get_manifest_or_404(app_id: str):
        manifest = registry.get(app_id)
        if manifest is None:
            raise NotFound(f"unknown app: {app_id}", code="apps.unknown")
        return manifest

    def managed_icon(app_id: str) -> FileResponse:
        """A managed app's icon, out of its own reviewed package."""
        manifest = managed.manifest(app_id)
        if not manifest.icon:
            raise NotFound(f"app {app_id} has no icon", code="apps.no_icon")
        root = managed.store.paths(app_id).release(
            managed.store.require(app_id)["release_id"]) / "package"
        icon_path = (root / manifest.icon).resolve()
        if not icon_path.is_file() or not icon_path.is_relative_to(root.resolve()):
            raise NotFound(f"icon not found for app: {app_id}", code="apps.icon_missing")
        return FileResponse(icon_path)

    @api.get("/apps")
    def list_apps() -> dict:
        # One list, three profiles. A managed web app is an app the person
        # installed, so leaving it out of the Library would mean two places to
        # look for the same kind of thing.
        return {"apps": registry.list_apps() + managed.list_apps()
                + connected_apps.list_apps()}

    @api.get("/apps/{app_id}")
    def get_app(app_id: str) -> dict:
        if connected_apps.owns(app_id): return connected_apps.get(app_id)
        if managed.owns(app_id): return managed.describe(app_id)
        description = registry.describe(app_id)
        if description is None:
            raise NotFound(f"unknown app: {app_id}", code="apps.unknown")
        return description

    @api.get("/apps/{app_id}/icon")
    def get_icon(app_id: str) -> FileResponse:
        if managed.owns(app_id):
            return managed_icon(app_id)
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
        # Removing a managed app keeps its data; `/api/managed/{id}` is where
        # erasing it can be asked for, deliberately and separately.
        if managed.owns(app_id):
            result = managed.remove(app_id)
            usage.forget(app_id)
            snooze.forget(app_id)
            return result
        result = lifecycle.uninstall_app(app_id)
        # Removing an app removes the record of having opened it, rather than
        # leaving it to age out of the Frequent window over the next month.
        usage.forget(app_id)
        snooze.forget(app_id)
        return result

    @api.post("/apps/{app_id}/launch")
    def launch_app(app_id: str) -> dict:
        # A managed app is started here and *entered* through its own address,
        # which needs a launch ticket. `/api/managed/{id}/launch` issues one;
        # this endpoint starts the service so the older shape keeps working.
        if managed.owns(app_id): return managed.start(app_id)
        return lifecycle.launch_app(app_id)

    @api.post("/apps/{app_id}/stop")
    def stop_app(app_id: str) -> dict:
        if managed.owns(app_id): return managed.stop(app_id)
        return lifecycle.stop_app(app_id)

    @api.get("/apps/{app_id}/status")
    def app_status(app_id: str) -> dict:
        if connected_apps.owns(app_id): return connected_apps.get(app_id)
        if managed.owns(app_id): return managed.status(app_id)
        return lifecycle.app_status(app_id)

    @api.post("/apps/{app_id}/upgrade")
    def upgrade_legacy(app_id: str):
        return lifecycle.upgrade_legacy(app_id)

    return api
