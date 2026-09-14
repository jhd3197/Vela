"""FastAPI app wiring the registry, state store, and platform runner together."""

import asyncio
import json
import tempfile
import os
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field, ConfigDict

from . import __version__
from .assistant import Assistant, AssistantError
from .backups import BackupError, BackupStore
from .config import Config, load_config
from .manifest import SUPPORTED_PLATFORMS
from .notify import Notifier, NotifyError, NotifyScheduler
from .registry import Registry
from .runners import current_platform, get_runner
from .settings import SettingsStore
from .state import StateStore
from .webapps import mount_webapps
from .auth import Auth
from .app_storage import AppStorage, AppServiceError
from .app_services import AppServices
from .lifecycle import Lifecycle, LifecycleError
from .connections import Connections
from .catalog import Catalog
from .releases import Releases
from .actions import Actions
from .package_files import MAX_BYTES


_SETTINGS_KEYS = {"theme", "chat_model", "chat_history", "ntfy_config"}


class NotifyPublishRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    message: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=5)
    priority: int = Field(default=3, ge=1, le=5)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    conversationId: str | None = None


class StorageWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: Any
    revision: int = Field(ge=0)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=1, max_length=256)


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str = Field(min_length=1, max_length=64)
    payload: dict = Field(default_factory=dict)


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0)


class ReleasePrepare(BaseModel):
    model_config = ConfigDict(extra='forbid')
    folder: str | None = Field(default=None, max_length=4096)
    app_id: str | None = Field(default=None, pattern=r'^[a-z0-9]+(-[a-z0-9]+)*$')
    rollback: str | None = Field(default=None, pattern=r'^[a-f0-9-]{36}$')


class ReleaseApproval(BaseModel):
    model_config = ConfigDict(extra='forbid')
    capabilities: list[str]
    operations: list[str]


class ActionGrant(BaseModel):
    model_config = ConfigDict(extra='forbid')
    app: str = Field(pattern=r'^[a-z0-9]+(-[a-z0-9]+)*$')
    action: str = Field(pattern=r'^[a-z][a-z0-9-]*$')
    allow: bool
    sourceContract: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    targetContract: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class ActionCall(BaseModel):
    model_config = ConfigDict(extra='forbid')
    app: str = Field(pattern=r'^[a-z0-9]+(-[a-z0-9]+)*$')
    action: str = Field(pattern=r'^[a-z][a-z0-9-]*$')
    input: dict
    key: str = Field(min_length=8, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')


def _dir_size(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                pass
    return total


def create_app(config: Config | None = None, *, connection_transport=None) -> FastAPI:
    config = config or load_config()
    state = StateStore(config.state_file)
    platform = current_platform()
    registry = Registry(config, state, platform)
    runner = get_runner(platform)
    settings = SettingsStore(config.settings_file)
    notifier = Notifier(settings)
    scheduler = NotifyScheduler(notifier, registry, config)
    assistant = Assistant(settings, registry, state, config)
    backups = BackupStore(config)
    auth = Auth(config)
    storage = AppStorage(config.data_dir / "app-data.sqlite")
    app_services = AppServices(registry, auth, storage)
    connections = Connections(registry, storage, transport=connection_transport)
    lifecycle = Lifecycle(config, registry, state, runner, platform, auth, storage)
    catalog = Catalog(config)
    registry.catalog = catalog
    releases = Releases(config, lifecycle, app_services, catalog)
    actions = Actions(lifecycle, app_services)

    app = FastAPI(title="vela", version=__version__)
    app.middleware("http")(auth.middleware)

    @app.exception_handler(AppServiceError)
    async def app_error(request, exc):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    @app.exception_handler(LifecycleError)
    async def lifecycle_error(request, exc):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.get('/api/catalog')
    def catalog_status():
        return catalog.status()

    @app.get('/api/apps/{app_id}/actions')
    def action_status(app_id: str):
        with lifecycle.lock: return actions.status(app_id)

    @app.put('/api/apps/{app_id}/actions/grant')
    def action_grant(app_id: str, payload: ActionGrant):
        return actions.grant(app_id, payload.app, payload.action, payload.allow, payload.sourceContract, payload.targetContract)

    @app.get('/api/apps/{app_id}/actions/history')
    def action_history(app_id: str):
        return actions.history(app_id)

    @app.get('/api/app/actions')
    def own_actions(request: Request):
        with lifecycle.lock: return actions.status(request.state.app_session['app_id'])

    @app.post('/api/app/actions/invoke')
    def invoke_action(request: Request, payload: ActionCall):
        return actions.invoke(request.state.app_session, payload.app, payload.action, payload.input, payload.key)

    @app.post('/api/catalog/refresh')
    def refresh_catalog():
        with lifecycle.lock:
            return catalog.refresh()

    @app.post('/api/releases/prepare')
    def prepare_release(payload: ReleasePrepare):
        if bool(payload.folder) == bool(payload.app_id) or (payload.rollback and not payload.app_id):
            raise AppServiceError(422, 'Select a folder or catalog app, optionally a rollback for that app')
        return releases.prepare(**payload.model_dump())

    @app.post('/api/releases/upload')
    async def upload_release(request: Request):
        directory = config.data_dir / 'staging'
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(suffix='.zip', dir=directory)
        try:
            total = 0
            with os.fdopen(descriptor, 'wb') as output:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > MAX_BYTES: raise AppServiceError(413, 'Release archive exceeds 32 MiB')
                    output.write(chunk)
            return releases.prepare(archive=name)
        finally:
            Path(name).unlink(missing_ok=True)

    @app.post('/api/releases/{review}/commit')
    def commit_release(review: str, payload: ReleaseApproval):
        return releases.commit(review, payload.capabilities, payload.operations)

    @app.delete('/api/releases/{review}')
    def cancel_release(review: str):
        return releases.cancel(review)

    @app.get('/api/apps/{app_id}/releases')
    def release_history(app_id: str):
        return releases.history(app_id)

    @app.get("/api/session")
    def hub_session(request: Request):
        return {"token": auth.bootstrap(request), "remote": auth.remote}

    @app.post("/api/login")
    def login(payload: LoginRequest, request: Request):
        token = auth.login(request, payload.password)
        response = JSONResponse({"token": token})
        response.set_cookie("__Host-vela-session", token, max_age=43200, secure=True, httponly=True, samesite="strict", path="/")
        return response

    @app.post("/api/logout")
    def logout(request: Request):
        auth.logout(request)
        response = JSONResponse({"ok": True})
        response.delete_cookie("__Host-vela-session", secure=True, httponly=True, samesite="strict", path="/")
        return response

    @app.post("/api/apps/{app_id}/session")
    def open_app_session(app_id: str, request: Request):
        with lifecycle.lock:
            return app_services.open(app_id, request.state.hub_token)

    @app.delete("/api/app/session")
    def close_app_session(request: Request):
        auth.revoke(request.headers.get("authorization", "").removeprefix("Bearer "))
        return {"ok": True}

    @app.get("/api/app/storage")
    def read_app_storage(request: Request):
        with lifecycle.lock:
            return app_services.read(request.state.app_session)

    @app.put("/api/app/storage")
    def write_app_storage(payload: StorageWrite, request: Request):
        with lifecycle.lock:
            return app_services.write(request.state.app_session, payload.value, payload.revision)

    @app.get("/api/app/storage/snapshots")
    def list_app_snapshots(request: Request):
        with lifecycle.lock:
            return app_services.snapshots(request.state.app_session)

    @app.post("/api/app/storage/snapshots")
    def create_app_snapshot(request: Request):
        with lifecycle.lock:
            return app_services.snapshot(request.state.app_session)

    @app.post("/api/app/storage/snapshots/{snapshot_id}/restore")
    def restore_app_snapshot(snapshot_id: str, payload: RevisionRequest, request: Request):
        with lifecycle.lock:
            return app_services.restore(request.state.app_session, snapshot_id, payload.revision)

    @app.post("/api/apps/{app_id}/migration/preview")
    def preview_migration(app_id: str, payload: StorageWrite):
        with lifecycle.lock:
            return app_services.migrate(app_id, payload.value, payload.revision)

    @app.post("/api/apps/{app_id}/migration")
    def migrate_app(app_id: str, payload: StorageWrite):
        with lifecycle.lock:
            return app_services.migrate(app_id, payload.value, payload.revision, commit=True)

    @app.post("/api/apps/{app_id}/upgrade")
    def upgrade_legacy(app_id: str):
        return lifecycle.upgrade_legacy(app_id)

    @app.get("/api/apps/{app_id}/connection")
    def connection_status(app_id: str):
        return connections.status(app_id)

    @app.put("/api/apps/{app_id}/connection")
    async def bind_connection(app_id: str, payload: ConnectionRequest):
        return await connections.bind(app_id, payload.endpoint)

    @app.delete("/api/apps/{app_id}/connection")
    def disconnect_connection(app_id: str):
        return connections.disconnect(app_id)

    @app.get("/api/app/connection")
    def app_connection(request: Request):
        session = request.state.app_session
        if "connections" not in session["capabilities"]: raise AppServiceError(403, "Connections capability was not granted")
        connections._binding(session["installationId"])
        return connections.status(session["app_id"])

    @app.post("/api/app/connection/invoke")
    async def invoke_connection(payload: OperationRequest, request: Request):
        return await connections.invoke(request.state.app_session, payload.operation, payload.payload)

    def get_manifest_or_404(app_id: str):
        manifest = registry.get(app_id)
        if manifest is None:
            raise HTTPException(status_code=404, detail=f"unknown app: {app_id}")
        return manifest

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/api/platforms")
    def platforms() -> dict:
        return {"current": platform, "supported": list(SUPPORTED_PLATFORMS)}

    @app.get("/api/engine")
    def engine() -> dict:
        apps = registry.list_apps()
        return {
            "status": "running",
            "engine": "local",
            "endpoint": "http://127.0.0.1:7700",
            "apps_installed": sum(1 for a in apps if a["installed"]),
            "apps_running": sum(1 for a in apps if a["running"]),
            "storage_bytes": _dir_size(config.data_dir),
            "data_dir": str(config.data_dir),
        }

    @app.get("/api/apps")
    def list_apps() -> dict:
        return {"apps": registry.list_apps()}

    @app.get("/api/apps/{app_id}")
    def get_app(app_id: str) -> dict:
        description = registry.describe(app_id)
        if description is None:
            raise HTTPException(status_code=404, detail=f"unknown app: {app_id}")
        return description

    @app.get("/api/apps/{app_id}/icon")
    def get_icon(app_id: str) -> FileResponse:
        manifest = get_manifest_or_404(app_id)
        if not manifest.icon:
            raise HTTPException(status_code=404, detail=f"app {app_id} has no icon")
        icon_path = (manifest.path / manifest.icon).resolve()
        if not icon_path.is_file() or not icon_path.is_relative_to(manifest.path.resolve()):
            raise HTTPException(status_code=404, detail=f"icon not found for app: {app_id}")
        return FileResponse(icon_path)

    @app.post("/api/apps/{app_id}/install")
    def install_app(app_id: str) -> dict:
        return lifecycle.install_app(app_id)

    @app.delete("/api/apps/{app_id}")
    def uninstall_app(app_id: str) -> dict:
        return lifecycle.uninstall_app(app_id)

    @app.post("/api/apps/{app_id}/launch")
    def launch_app(app_id: str) -> dict:
        return lifecycle.launch_app(app_id)

    @app.post("/api/apps/{app_id}/stop")
    def stop_app(app_id: str) -> dict:
        return lifecycle.stop_app(app_id)

    @app.get("/api/apps/{app_id}/status")
    def app_status(app_id: str) -> dict:
        return lifecycle.app_status(app_id)

    @app.on_event("startup")
    async def start_scheduler() -> None:
        scheduler.start()

    @app.on_event("shutdown")
    async def stop_scheduler() -> None:
        await scheduler.stop()

    @app.get("/api/settings")
    def get_settings() -> dict:
        return settings.public_view()

    @app.patch("/api/settings")
    def patch_settings(payload: dict[str, Any] = Body(...)) -> dict:
        settings.patch({key: value for key, value in payload.items() if key in _SETTINGS_KEYS})
        return {"ok": True}

    @app.post("/api/notify/test")
    async def notify_test(payload: dict[str, Any] | None = Body(None)) -> dict:
        if payload and isinstance(payload.get("ntfy_config"), dict):
            settings.patch({"ntfy_config": payload["ntfy_config"]})
        try:
            receipt = await notifier.publish(
                "Vela",
                "Notification test — if you can read this, delivery is working.",
                tags=["bell"],
                kind="test",
            )
        except NotifyError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return {"ok": True, **receipt}

    @app.post("/api/notify/publish")
    async def notify_publish(payload: NotifyPublishRequest) -> dict:
        try:
            receipt = await notifier.publish(
                payload.title, payload.message, tags=payload.tags, priority=payload.priority
            )
        except NotifyError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        return {"ok": True, **receipt}

    @app.get("/api/notifications")
    def list_notifications() -> dict:
        return {"notifications": notifier.recent()}

    @app.get("/api/ai/status")
    async def ai_status() -> dict:
        return await assistant.status()

    @app.post("/api/chat")
    async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
        if (
            not payload.messages
            or payload.messages[-1].role != "user"
            or not payload.messages[-1].content.strip()
        ):
            raise HTTPException(status_code=400, detail="messages must end with a user message")
        text = payload.messages[-1].content.strip()
        client_id = request.client.host if request.client else "local"
        queue: asyncio.Queue[dict | None] = asyncio.Queue()

        def emit(event: dict) -> None:
            queue.put_nowait(event)

        async def run() -> None:
            try:
                await assistant.run(client_id, payload.conversationId, text, emit)
            except AssistantError as exc:
                emit({"error": str(exc)})
            except Exception:
                emit({"error": "The assistant failed unexpectedly. Please retry."})
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(run())

        async def stream():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if event is None:
                        break
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                task.cancel()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/backups")
    def list_backups() -> dict:
        return {"backups": backups.list()}

    @app.post("/api/backups", status_code=201)
    def create_backup() -> dict:
        try:
            return backups.create()
        except BackupError as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/api/backups/{name}/verify")
    def verify_backup(name: str) -> dict:
        try:
            return backups.verify(name)
        except BackupError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    # /apps/* is matched before the SPA fallback below; the fallback must
    # never swallow app requests.
    mount_webapps(app, registry, state)

    @app.api_route("/api/{unknown_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    def unknown_api(unknown_path: str):
        raise HTTPException(status_code=404, detail="Unknown API endpoint")

    index = config.web_dist / "index.html"
    if config.web_dist.is_dir() and index.is_file():
        dist_root = config.web_dist.resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> FileResponse:
            candidate = (config.web_dist / full_path).resolve()
            if full_path and candidate.is_file() and candidate.is_relative_to(dist_root):
                return FileResponse(candidate)
            return FileResponse(index)
    else:

        @app.get("/", include_in_schema=False)
        def root() -> dict:
            return {"name": "vela", "api": "/api", "docs": "/docs"}

    return app


app = create_app()
