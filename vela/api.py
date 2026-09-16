"""FastAPI app wiring the registry, state store, and platform runner together."""

import asyncio
import json
import tempfile
import os
import signal
import threading
import logging
import traceback
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel, Field, ConfigDict

from . import __version__
from .assistant import Assistant, AssistantError
from .bots import BUILTIN_BOT_ID, SELECTABLE_TOOLS, BotStore, builtin_profile
from .conversations import ConversationStore
from .rooms import Rooms
from .backups import KEEP_BACKUPS, BackupError, BackupStore, describe_schedule, validate_schedule
from .config import Config, load_config
from .desk import CORE_WIDGET_TYPES, DeskError, DeskStore
from .usage import WINDOW_DAYS as USAGE_WINDOW_DAYS, UsageStore
from .snooze import SnoozeStore
from .weather import Weather, WeatherError
from .doctor import Doctor, summarise
from .errors import CLIENT_LIMIT_PER_MINUTE, ErrorStore
from .support_bundle import SupportBundle
from .updates import (DEFAULT_UPDATES, MODES, UpdateChecker, UpdateError, UpdateJob,
                      capability, rollback_available, startup_report)
from .manifest import SUPPORTED_PLATFORMS
from .notify import Notifier, NotifyError, NotifyScheduler
from .registry import Registry
from .runners import current_platform, get_runner
from .settings import SettingsStore, normalize_identity, sanitize_pins
from .system_metrics import SystemMetrics, validate_volumes
from .state import StateStore
from .webapps import mount_webapps
from .wallpaper import MAX_WALLPAPER_BYTES, Wallpaper, WallpaperError
from .widgets import Widgets
from .auth import Auth
from .app_storage import AppStorage, AppServiceError
from .app_services import AppServices
from .lifecycle import Lifecycle, LifecycleError
from .logging_setup import audit, request_actor
from .logs import DEFAULT_LINES, LogError, LogStore
from .connections import Connections
from .catalog import Catalog
from .releases import Releases
from .actions import Actions
from .package_files import MAX_BYTES
from .connected_apps import ConnectedApps
from .phone_access import PhoneAccess
from .automations import Automations, router as automations_router


LOG = logging.getLogger(__name__)

_SETTINGS_KEYS = {"theme", "chat_model", "chat_history", "ntfy_config", "desk", "rail",
                  "backups", "updates", "identity"}


class ClientError(BaseModel):
    """One failure the dashboard caught in the browser."""

    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)
    type: str | None = Field(default=None, max_length=200)
    stack: str | None = Field(default=None, max_length=20000)
    url: str | None = Field(default=None, max_length=400)


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
    # Identifies one send, so a network retry resumes the same run rather than
    # starting a second one.
    requestId: str | None = Field(default=None, max_length=64)
    # Structured bot ids, resolved by the composer. Names are never trusted for
    # routing: a duplicate display name would otherwise misroute a message.
    recipients: list[str] = Field(default_factory=list, max_length=8)
    # Re-run only these bots, for retrying one that failed.
    only: list[str] = Field(default_factory=list, max_length=4)


class BotPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)
    icon: str = Field(default="sparkle", max_length=40)
    color: str = Field(default="indigo", max_length=20)
    instructions: str = Field(default="", max_length=8000)
    model: str = Field(default="", max_length=120)
    tools: list[str] = Field(default_factory=list, max_length=8)


class BotPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=200)
    icon: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=20)
    instructions: str | None = Field(default=None, max_length=8000)
    model: str | None = Field(default=None, max_length=120)
    tools: list[str] | None = Field(default=None, max_length=8)
    archived: bool | None = None


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str = Field(min_length=1, max_length=600)


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instructions: str = Field(default="", max_length=8000)
    model: str = Field(default="", max_length=120)
    name: str = Field(default="Preview", max_length=60)
    tools: list[str] = Field(default_factory=list, max_length=8)
    message: str = Field(min_length=1, max_length=2000)


class NewConversation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(default="direct", pattern="^(direct|room)$")
    botId: str = Field(default="vela", max_length=64)
    title: str | None = Field(default=None, max_length=120)
    purpose: str = Field(default="", max_length=1000)
    mode: str = Field(default="mention", pattern="^(mention|roundtable)$")
    leadBotId: str = Field(default="", max_length=64)
    botIds: list[str] = Field(default_factory=list, max_length=4)


class MembersPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    botIds: list[str] = Field(min_length=2, max_length=4)
    leadBotId: str = Field(default="", max_length=64)


class ConversationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=200)
    archived: bool | None = None
    draft: str | None = Field(default=None, max_length=4000)


class LegacyImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[ChatMessage] = Field(default_factory=list, max_length=500)


class StorageWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    value: Any
    revision: int = Field(ge=0)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)


class QuickEnrollRequest(BaseModel):
    """Enable or change app lock. The secret is validated by `vela.access`."""
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)
    method: str = Field(pattern="^(pin|pattern)$")
    # A six-digit string, or the drawn dot order. Never stored or logged.
    secret: str | list[int] = Field(union_mode="left_to_right")


class QuickTimeoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)
    timeout: int


class QuickDisableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=256)


class QuickUnlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    secret: str | list[int] | None = Field(default=None, union_mode="left_to_right")
    password: str | None = Field(default=None, max_length=256)


class PhoneAccessRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    address: str = Field(min_length=1, max_length=64)
    password: str = Field(default='', max_length=256)


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=1, max_length=256)


class ConnectedAppRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2048)
    color: str = Field(default='#9184d9', pattern=r'^#[0-9a-fA-F]{6}$')


class ConnectedAppUpdate(ConnectedAppRequest):
    revision: int = Field(ge=1)


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
    # The certificate belongs to phone access, so the metrics ask rather than
    # import: `secure` is read at snapshot time, after `phone_access` exists.
    system_metrics = SystemMetrics(
        config, settings, secure=lambda: bool(getattr(app.state, "phone_access", None) and app.state.phone_access.origin)
    )
    desk = DeskStore(config.data_dir / "desk.json")
    usage = UsageStore(config.data_dir / "usage.json")
    weather = Weather(settings)
    wallpaper = Wallpaper(config.data_dir)
    conversations = ConversationStore(config.data_dir / "chat.sqlite")
    bots = BotStore(config.data_dir / "chat.sqlite")
    assistant = Assistant(settings, registry, state, config, conversations, bots=bots)
    rooms = Rooms(conversations, bots, assistant)
    # A process that stopped mid-answer cannot finish it. Say so on the way back
    # up rather than showing a run that will never move again.
    conversations.interrupt_stale_runs()
    # Conversation id -> the task currently answering it, so a reloaded page can
    # stop a run it no longer holds the stream for.
    _live_runs: dict[str, asyncio.Task] = {}
    backups = BackupStore(config, keep=(settings.get("backups") or {}).get(
        "schedule", {}).get("keep", KEEP_BACKUPS))
    logs = LogStore(config.logs_dir)
    auth = Auth(config)
    storage = AppStorage(config.data_dir / "app-data.sqlite")
    connected_apps = ConnectedApps(storage)
    app_services = AppServices(registry, auth, storage)
    connections = Connections(registry, storage, transport=connection_transport)
    lifecycle = Lifecycle(config, registry, state, runner, platform, auth, storage)
    catalog = Catalog(config)
    registry.catalog = catalog
    releases = Releases(config, lifecycle, app_services, catalog)
    actions = Actions(lifecycle, app_services)
    snooze = SnoozeStore(config.data_dir / "snooze.json")
    widgets = Widgets(storage, registry, actions, snooze)
    automations = Automations(config, registry, actions, notifier, settings,
                              log=lambda message: print(f'[vela] {message}', flush=True))
    updates = UpdateChecker(config, __version__, settings=settings)
    doctor = Doctor(config, state=state, registry=registry, settings=settings,
                    backups=backups, assistant=assistant, catalog=catalog, updates=updates)
    errors = ErrorStore(config.data_dir / "diagnostics.sqlite")
    update_job = UpdateJob(config, updates, backups=backups)
    support = SupportBundle(config, version=__version__, registry=registry, settings=settings,
                            errors=errors, doctor=doctor, automations=automations)
    # The scheduler runs the sweep daily and once after startup, and announces
    # a check that newly fails.
    scheduler.attach_doctor(doctor)
    scheduler.attach_backups(backups, settings)
    scheduler.attach_updates(updates)
    scheduler.attach_update_job(update_job, registry=registry,
                                automations=automations, doctor=doctor)

    app = FastAPI(title="vela", version=__version__)
    app.middleware("http")(auth.middleware)
    app.include_router(automations_router(automations))
    app.state.automations = automations

    @app.on_event('startup')
    async def start_automations() -> None:
        await automations.start()

    @app.on_event('shutdown')
    async def stop_automations() -> None:
        await automations.stop()

    phone_access = PhoneAccess(app, config, auth)
    app.state.phone_access = phone_access

    @app.get('/api/phone-access')
    def phone_access_status():
        return phone_access.status()

    @app.post('/api/phone-access')
    async def enable_phone_access(payload: PhoneAccessRequest, request: Request):
        if auth.is_remote_request(request) or not auth.local_request(request):
            raise AppServiceError(403, 'Manage Wi-Fi access from the Vela computer')
        return await phone_access.start(payload.address, payload.password)

    @app.delete('/api/phone-access')
    async def disable_phone_access(request: Request):
        if auth.is_remote_request(request) or not auth.local_request(request):
            raise AppServiceError(403, 'Manage Wi-Fi access from the Vela computer')
        await phone_access.stop(disable=True)
        return phone_access.status()

    @app.on_event('startup')
    async def restore_phone_access():
        await phone_access.restore()

    @app.on_event('shutdown')
    async def stop_phone_access():
        await phone_access.stop()

    @app.exception_handler(AppServiceError)
    async def app_error(request, exc):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status)

    @app.exception_handler(LifecycleError)
    async def lifecycle_error(request, exc):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def record_unhandled(request, exc):
        # Record it, then answer the way an unhandled error is answered. The
        # record is a side effect: it must not change what the caller sees, and
        # a failure to write it must not replace the original failure.
        try:
            errors.record(
                "server",
                f"{exc}",
                type_=type(exc).__name__,
                traceback="".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                ),
                endpoint=request.url.path,
            )
        except Exception:  # noqa: BLE001 - never mask the original failure
            pass
        raise exc

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
        return {"token": auth.bootstrap(request), "remote": auth.is_remote_request(request)}

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

    # ----------------------------------------------------------- app lock
    #
    # Reauthentication for a session that is already signed in, enforced by the
    # middleware rather than by the page. See the local plan for the contract.

    @app.get("/api/security")
    def security_status(request: Request):
        return auth.quick_status(request)

    @app.post("/api/security/enroll")
    def security_enroll(payload: QuickEnrollRequest, request: Request):
        return auth.enroll_quick(request, payload.password, payload.method, payload.secret)

    @app.patch("/api/security")
    def security_timeout(payload: QuickTimeoutRequest, request: Request):
        return auth.update_quick(request, payload.password, payload.timeout)

    @app.delete("/api/security")
    def security_disable(payload: QuickDisableRequest, request: Request):
        return auth.disable_quick(request, payload.password)

    @app.post("/api/security/lock")
    def security_lock(request: Request):
        return auth.lock_now(request)

    @app.post("/api/security/unlock")
    def security_unlock(payload: QuickUnlockRequest, request: Request):
        if (payload.secret is None) == (payload.password is None):
            raise AppServiceError(422, "Send either the unlock code or the Vela password")
        return auth.unlock(request, secret=payload.secret, password=payload.password)

    @app.post("/api/security/activity")
    def security_activity(request: Request):
        return auth.record_activity(request)

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

    @app.put("/api/app/widgets/{widget_id}")
    def publish_widget(widget_id: str, request: Request, payload: dict[str, Any] = Body(...)):
        # App session only: an app publishes for itself and nothing else.
        return widgets.publish(request.state.app_session, widget_id, payload.get("summary"))

    @app.get("/api/apps/{app_id}/widgets")
    def app_widgets(app_id: str) -> dict:
        return widgets.for_app(app_id)

    @app.get("/api/widgets")
    def all_widgets() -> dict:
        return widgets.all()

    @app.post("/api/widgets/{app_id}/{widget_id}/snooze")
    def snooze_widget(app_id: str, widget_id: str) -> dict:
        """Put one widget's attention flag aside for eight hours.

        The summary is untouched and the app is told nothing: this only stops
        the desk's Needs you list and the rail's dot from showing it until the
        time is up.
        """
        try:
            return snooze.snooze(app_id, widget_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.delete("/api/widgets/{app_id}/{widget_id}/snooze")
    def wake_widget(app_id: str, widget_id: str) -> dict:
        return snooze.wake(app_id, widget_id)

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

    def _request_shutdown() -> None:
        """Ask the server to exit so the apply script can replace its files.

        The tray controller owns the real stop; without it (a console server)
        signalling the process is the equivalent. Either way the script is
        already running and waiting for this pid to go.
        """
        controller = getattr(app.state, "server_controller", None)
        if controller is not None:
            threading.Thread(target=controller.stop, daemon=True).start()
            return
        os.kill(os.getpid(), signal.SIGTERM)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/api/platforms")
    def platforms() -> dict:
        return {"current": platform, "supported": list(SUPPORTED_PLATFORMS)}

    @app.get("/api/engine")
    def engine() -> dict:
        apps = registry.list_apps() + connected_apps.list_apps()
        return {
            "status": "running",
            "engine": "local",
            "version": __version__,
            "endpoint": "http://127.0.0.1:7700",
            "apps_installed": sum(1 for a in apps if a["installed"]),
            "apps_running": sum(1 for a in apps if a["running"]),
            "storage_bytes": _dir_size(config.data_dir),
            "data_dir": str(config.data_dir),
        }

    @app.get("/api/apps")
    def list_apps() -> dict:
        return {"apps": registry.list_apps() + connected_apps.list_apps()}

    @app.post('/api/web-apps', status_code=201)
    def add_web_app(payload: ConnectedAppRequest, request: Request):
        return connected_apps.save(**payload.model_dump(), hub_origin=config.public_origin or str(request.base_url))

    @app.put('/api/web-apps/{app_id}')
    def edit_web_app(app_id: str, payload: ConnectedAppUpdate, request: Request):
        return connected_apps.save(**payload.model_dump(), app_id=app_id, hub_origin=config.public_origin or str(request.base_url))

    @app.delete('/api/web-apps/{app_id}')
    def remove_web_app(app_id: str, payload: RevisionRequest):
        return connected_apps.remove(app_id, payload.revision)

    @app.get("/api/apps/{app_id}")
    def get_app(app_id: str) -> dict:
        if connected_apps.owns(app_id): return connected_apps.get(app_id)
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
        result = lifecycle.uninstall_app(app_id)
        # Removing an app removes the record of having opened it, rather than
        # leaving it to age out of the Frequent window over the next month.
        usage.forget(app_id)
        snooze.forget(app_id)
        return result

    @app.post("/api/apps/{app_id}/launch")
    def launch_app(app_id: str) -> dict:
        return lifecycle.launch_app(app_id)

    @app.post("/api/apps/{app_id}/stop")
    def stop_app(app_id: str) -> dict:
        return lifecycle.stop_app(app_id)

    @app.get("/api/apps/{app_id}/status")
    def app_status(app_id: str) -> dict:
        if connected_apps.owns(app_id): return connected_apps.get(app_id)
        return lifecycle.app_status(app_id)

    @app.on_event("startup")
    async def report_update() -> None:
        # The process that applied the update is gone; its journal is the only
        # record of what it was doing.
        report = startup_report(config, __version__)
        if report is None:
            return
        app.state.update_report = report
        if report["outcome"] == "updated":
            audit("update", f"applied to={report['to']}")
        else:
            LOG.warning("the last update did not finish: %s", report)

    @app.get("/api/updates/report")
    def update_report() -> dict:
        return getattr(app.state, "update_report", None) or {}

    @app.on_event("startup")
    async def start_scheduler() -> None:
        scheduler.start()
        system_metrics.start()

    @app.on_event("shutdown")
    async def stop_scheduler() -> None:
        await scheduler.stop()
        await system_metrics.stop()

    @app.get("/api/system/metrics")
    def system_metrics_snapshot() -> dict:
        return system_metrics.snapshot()

    def _known_widget_types() -> set[str]:
        """Core types plus one per widget each installed app declares.

        A board may only name a type that exists right now, so uninstalling an
        app takes its widgets off the desk instead of leaving a frame that can
        never render."""
        known = set(CORE_WIDGET_TYPES)
        for summary in registry.list_apps():
            if not summary.get("installed"):
                continue
            for declared in summary.get("widgets") or []:
                widget_id = declared.get("id") if isinstance(declared, dict) else None
                if isinstance(widget_id, str) and widget_id:
                    known.add(f"{summary['id']}:{widget_id}")
        return known

    @app.get("/api/wallpaper")
    def get_wallpaper():
        path = wallpaper.path()
        if path is None:
            raise HTTPException(status_code=404, detail="No wallpaper is set")
        # It changes only when the user replaces it, and the page asks for it
        # again on every desk load, so it is worth caching in the browser.
        return FileResponse(
            path,
            media_type=wallpaper.media_type(path),
            headers={"Cache-Control": "no-cache"},
        )

    @app.put("/api/wallpaper")
    async def put_wallpaper(request: Request):
        # Raw bytes with the type in the header, the same shape as the release
        # upload, so the server needs no multipart parser for one picture.
        try:
            extension = wallpaper.extension_for(request.headers.get("content-type", ""))
            content = bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > MAX_WALLPAPER_BYTES:
                    raise WallpaperError(413, "A wallpaper is at most 8 MB")
            result = wallpaper.save(bytes(content), extension)
        except WallpaperError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail)
        settings.patch({"desk": {"wallpaper": "custom"}})
        return result

    @app.delete("/api/wallpaper")
    def delete_wallpaper() -> dict:
        result = wallpaper.remove()
        if (settings.get("desk") or {}).get("wallpaper") == "custom":
            settings.patch({"desk": {"wallpaper": "choroni"}})
        return result

    @app.get("/api/weather")
    def get_weather() -> dict:
        """The desk's weather line. Makes no request while the switch is off."""
        return weather.current()

    @app.post("/api/weather/locate")
    def locate_weather(payload: dict[str, Any] = Body(...)) -> dict:
        """Turn a typed place into coordinates, once, so the place is not stored."""
        try:
            return weather.locate(str(payload.get("place") or ""))
        except WeatherError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail)

    @app.get("/api/usage")
    def get_usage() -> dict:
        """Opens per app over the last 30 days, for the Launchpad's Frequent tab.

        Counted and kept on this computer only; nothing here is sent anywhere.
        """
        return {"totals": usage.totals(), "windowDays": USAGE_WINDOW_DAYS}

    @app.post("/api/usage/{app_id}")
    def record_usage(app_id: str) -> dict:
        return usage.record(app_id)

    @app.get("/api/desk")
    def get_desk() -> dict:
        return desk.load(_known_widget_types())

    @app.put("/api/desk")
    def put_desk(payload: dict[str, Any] = Body(...)) -> dict:
        try:
            return desk.save(
                payload.get("boards"), payload.get("revision"), _known_widget_types()
            )
        except ValueError as exc:
            # Someone else saved first. The dashboard reloads and says so
            # rather than overwriting an arrangement it never saw.
            raise HTTPException(
                status_code=409,
                detail="The desk changed somewhere else.",
                headers={"X-Vela-Desk-Revision": str(exc.args[0])},
            )
        except DeskError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/api/settings")
    def get_settings() -> dict:
        return settings.public_view()

    @app.patch("/api/settings")
    def patch_settings(payload: dict[str, Any] = Body(...)) -> dict:
        update = {key: value for key, value in payload.items() if key in _SETTINGS_KEYS}
        # A desk volume names a real folder on this computer, so it is checked
        # before it is stored rather than failing later inside a widget.
        desk = update.get("desk")
        if isinstance(desk, dict) and "volumes" in desk:
            try:
                desk["volumes"] = validate_volumes(desk["volumes"])
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        # The avatar letter follows the display name rather than being sent, so
        # an identity patch is normalised before it is stored.
        if "identity" in update:
            try:
                update["identity"] = normalize_identity(update["identity"])
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        # Rail pins are a list of app ids; store the shape, not the meaning —
        # the dashboard drops ids that no longer name a real app.
        rail = update.get("rail")
        if isinstance(rail, dict) and "pinned" in rail:
            if not isinstance(rail["pinned"], list):
                raise HTTPException(status_code=422, detail="rail.pinned must be a list of ids")
            rail["pinned"] = sanitize_pins(rail["pinned"])
        # A backup schedule names a time this computer will act on, so it is
        # checked before it is stored rather than failing quietly at 03:00.
        # Update preferences decide whether Vela makes a network request at
        # all, so a malformed patch must not quietly turn checking on.
        update_settings = update.get("updates")
        if isinstance(update_settings, dict):
            cleaned = {}
            if "check" in update_settings:
                cleaned["check"] = bool(update_settings["check"])
            if "mode" in update_settings:
                if update_settings["mode"] not in MODES:
                    raise HTTPException(status_code=422, detail="updates.mode is notify or auto")
                cleaned["mode"] = update_settings["mode"]
            if "hour" in update_settings:
                try:
                    hour = int(update_settings["hour"])
                except (TypeError, ValueError):
                    raise HTTPException(status_code=422, detail="updates.hour is an hour of the day")
                if not 0 <= hour <= 23:
                    raise HTTPException(status_code=422, detail="updates.hour is an hour of the day")
                cleaned["hour"] = hour
            update["updates"] = cleaned
        backup_settings = update.get("backups")
        if isinstance(backup_settings, dict) and "schedule" in backup_settings:
            try:
                backup_settings["schedule"] = validate_schedule(backup_settings["schedule"])
            except BackupError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            backups.set_keep(backup_settings["schedule"]["keep"])
        settings.patch(update)
        # Turning retention off is a deletion, not just a preference change.
        if update.get("chat_history") is False:
            conversations.purge()
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

    def _history_enabled() -> None:
        if not settings.get("chat_history"):
            raise HTTPException(
                status_code=409,
                detail="Chat history is turned off in Settings, so conversations are not saved.",
            )

    # ---- bots ----------------------------------------------------------
    #
    # Bot profiles are configuration, not transcripts: they stay available while
    # chat history is off, which is why none of these call `_history_enabled`.

    @app.get("/api/bots")
    def list_bots(archived: bool = False) -> dict:
        return {
            "builtin": builtin_profile(),
            "bots": bots.list(archived=archived),
            "tools": list(SELECTABLE_TOOLS),
        }

    @app.post("/api/bots", status_code=201)
    def create_bot(payload: BotPayload) -> dict:
        return bots.create(payload.model_dump())

    @app.get("/api/bots/{bot_id}")
    def read_bot(bot_id: str) -> dict:
        return bots.get(bot_id)

    @app.patch("/api/bots/{bot_id}")
    def patch_bot(bot_id: str, payload: BotPatch) -> dict:
        return bots.update(bot_id, payload.model_dump(exclude_unset=True))

    @app.post("/api/bots/{bot_id}/duplicate", status_code=201)
    def duplicate_bot(bot_id: str) -> dict:
        return bots.duplicate(bot_id)

    @app.delete("/api/bots/{bot_id}", status_code=204)
    def delete_bot(bot_id: str) -> Response:
        bots.delete(bot_id)
        return Response(status_code=204)

    @app.post("/api/bots/draft")
    async def draft_instructions(payload: DraftRequest) -> dict:
        """Optional model-assisted drafting.

        Failure here is never fatal: the editor keeps working and the person
        writes the instructions themselves, which is the path that always works.
        """
        try:
            return {"ok": True, "instructions": await assistant.draft_instructions(payload.purpose)}
        except AssistantError as exc:
            return {"ok": False, "instructions": "", "error": str(exc)}
        except Exception:
            return {
                "ok": False,
                "instructions": "",
                "error": "Could not draft instructions. Write them yourself and save.",
            }

    @app.post("/api/bots/preview")
    async def preview_bot(payload: PreviewRequest, request: Request) -> StreamingResponse:
        """Answer one message with an unsaved profile.

        Transient by construction: no conversation is created, nothing is
        stored, and the preview is given no tools whatever the editor shows.
        """
        client_id = request.client.host if request.client else "local"
        profile = {
            **builtin_profile(),
            "id": "preview",
            "name": payload.name or "Preview",
            "instructions": payload.instructions,
            "model": payload.model,
            "tools": [],
        }
        return _sse(
            lambda emit: assistant.preview(client_id, profile, payload.message, emit)
        )

    # ---- conversations -------------------------------------------------

    @app.get("/api/chat/conversations")
    def list_conversations(query: str = "", archived: bool = False, limit: int = 50) -> dict:
        if not settings.get("chat_history"):
            return {"conversations": [], "enabled": False}
        return {
            "conversations": conversations.browse(query=query, archived=archived, limit=limit),
            "enabled": True,
        }

    @app.post("/api/chat/conversations", status_code=201)
    def create_conversation(payload: NewConversation | None = Body(None)) -> dict:
        _history_enabled()
        payload = payload or NewConversation()
        if payload.kind == "room":
            # Membership is validated against the store before the room exists,
            # so a room can never be created around a bot that is not usable.
            for bot_id in payload.botIds:
                bots.usable(bot_id)
            return conversations.create(
                payload.title,
                kind="room",
                purpose=payload.purpose,
                mode=payload.mode,
                lead_bot_id=payload.leadBotId,
                bot_ids=payload.botIds,
            )
        bots.usable(payload.botId)
        return conversations.create(payload.title, kind="direct", bot_id=payload.botId)

    @app.put("/api/chat/conversations/{conversation_id}/members")
    def set_members(conversation_id: str, payload: MembersPayload) -> dict:
        _history_enabled()
        for bot_id in payload.botIds:
            bots.usable(bot_id)
        return conversations.set_members(conversation_id, payload.botIds, payload.leadBotId)

    @app.get("/api/chat/conversations/{conversation_id}/run")
    def read_run(conversation_id: str) -> dict:
        _history_enabled()
        return {"run": conversations.active_run(conversation_id)}

    @app.delete("/api/chat/conversations/{conversation_id}/run", status_code=200)
    def cancel_run(conversation_id: str) -> dict:
        """Stop whatever this conversation is doing.

        A reloaded page has no stream to abort, so the run is settled here and
        the in-flight task, if this process still owns one, is cancelled too.
        """
        _history_enabled()
        run = conversations.active_run(conversation_id)
        if run is None:
            return {"stopped": False}
        conversations.update_run(run["id"], status="stopped")
        task = _live_runs.pop(conversation_id, None)
        if task is not None and not task.done():
            task.cancel()
        return {"stopped": True, "runId": run["id"]}

    @app.post("/api/chat/conversations/import")
    def import_conversation(payload: LegacyImport) -> dict:
        _history_enabled()
        return conversations.import_legacy([m.model_dump() for m in payload.messages])

    @app.get("/api/chat/conversations/{conversation_id}")
    def read_conversation(conversation_id: str) -> dict:
        _history_enabled()
        return conversations.get(conversation_id)

    @app.patch("/api/chat/conversations/{conversation_id}")
    def patch_conversation(conversation_id: str, payload: ConversationPatch) -> dict:
        _history_enabled()
        return conversations.update(
            conversation_id,
            title=payload.title,
            archived=payload.archived,
            draft=payload.draft,
        )

    @app.delete("/api/chat/conversations/{conversation_id}", status_code=204)
    def delete_conversation(conversation_id: str) -> Response:
        _history_enabled()
        conversations.delete(conversation_id)
        return Response(status_code=204)

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
        keep = bool(settings.get("chat_history"))

        conversation = None
        if payload.conversationId and keep:
            try:
                conversation = conversations.get(payload.conversationId)
            except AppServiceError:
                raise HTTPException(status_code=404, detail="conversation not found")

        is_room = bool(conversation and conversation.get("kind") == "room")

        # One durable run per send. A repeated requestId is the same send
        # arriving twice, and must not produce a second set of answers.
        run_id = ""
        if keep and conversation is not None:
            try:
                opened = conversations.start_run(conversation["id"], payload.requestId)
            except AppServiceError as exc:
                raise HTTPException(status_code=exc.status, detail=exc.detail)
            if opened.get("duplicate"):
                raise HTTPException(
                    status_code=409,
                    detail="That message was already sent. Reload to see the answer.",
                )
            run_id = opened["id"]

        async def body(emit) -> None:
            if is_room:
                # Refuse an unusable room before anything is written down, so a
                # rejected send leaves no orphan message in the transcript.
                rooms.check_ready(conversation["id"])
                # Retrying one failed bot answers the question already stored;
                # it must not append the same question a second time.
                if not payload.only:
                    stored = conversations.append(conversation["id"], "user", text)
                    emit({"conversationId": conversation["id"], "title": stored["title"],
                          "runId": run_id})
                else:
                    emit({"conversationId": conversation["id"], "runId": run_id})
                await rooms.run(
                    client_id, conversation, text, emit,
                    recipients=payload.recipients, keep=True, run_id=run_id,
                    only=payload.only or None,
                )
            else:
                bot_id = (conversation or {}).get("botId") or BUILTIN_BOT_ID
                await assistant.run(
                    client_id, payload.conversationId, text, emit,
                    bot_id=bot_id, run_id=run_id,
                )

        def settle(status: str) -> None:
            if keep and run_id:
                conversations.update_run(run_id, status=status)

        return _sse(body, conversation_id=(conversation or {}).get("id"), settle=settle)

    def _sse(body, *, conversation_id: str | None = None, settle=None) -> StreamingResponse:
        """Run `body(emit)` in a task and stream what it emits as SSE.

        Losing the stream cancels the task: an answer nobody is reading should
        not keep a local model busy. What was generated up to that point is
        already stored, so a reload shows it as interrupted rather than lost.
        """
        queue: asyncio.Queue = asyncio.Queue()

        def emit(event: dict) -> None:
            queue.put_nowait(event)

        async def run() -> None:
            outcome = "complete"
            try:
                await body(emit)
            except AssistantError as exc:
                outcome = "failed"
                emit({"error": str(exc)})
            except AppServiceError as exc:
                outcome = "failed"
                emit({"error": exc.detail})
            except asyncio.CancelledError:
                outcome = "stopped"
                raise
            except Exception:
                outcome = "failed"
                emit({"error": "The assistant failed unexpectedly. Please retry."})
            finally:
                if settle is not None:
                    settle(outcome)
                if conversation_id:
                    _live_runs.pop(conversation_id, None)
                queue.put_nowait(None)

        task = asyncio.create_task(run())
        if conversation_id:
            _live_runs[conversation_id] = task

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

    # Logs. The hub session is the gate (the auth middleware rejects anything
    # else on /api/*); "Show developer tools" decides what this browser shows,
    # never what a request may do, so it is not re-checked here.
    # Health checks. The last sweep is served as-is so opening Settings does
    # not start thirteen checks; Run now is the deliberate action.
    @app.get("/api/doctor")
    def doctor_status() -> dict:
        last = doctor.last() or {"checks": [], "ranAt": None, "summary": summarise([])}
        # The desk reads one source for "is anything asking for me", so the
        # pending update rides along with the checks rather than costing the
        # desk a second request.
        status = updates.status()
        return {
            **last,
            "update": {
                "available": status["available"],
                "latest": status["latest"],
                "current": status["current"],
            },
        }

    @app.post("/api/doctor/run")
    async def doctor_run() -> dict:
        return await asyncio.to_thread(doctor.collect)

    @app.post("/api/doctor/{key}/repair")
    async def doctor_repair(key: str, request: Request) -> dict:
        result = await asyncio.to_thread(doctor.repair, key, actor=request_actor(auth, request))
        if result.get("check") is None:
            # No such check, or nothing registered to repair it.
            raise HTTPException(status_code=422, detail=result.get("detail", "That repair is not available."))
        return result

    # Errors. The dashboard reports its own failures here; app frames are not
    # hooked, because an app's errors belong to the app.
    @app.get("/api/errors")
    def list_errors(source: str = "", resolved: str = "", search: str = "", page: int = 1) -> dict:
        wanted = None if resolved not in ("true", "false") else resolved == "true"
        return errors.list(source=source or None, resolved=wanted, search=search or None, page=page)

    @app.get("/api/errors/stats")
    def error_stats() -> dict:
        return errors.stats()

    @app.post("/api/errors/client", status_code=202)
    def report_client_error(payload: ClientError) -> dict:
        # A render loop that throws every frame must not be able to fill the
        # database. Over the cap Vela accepts the request and drops the report.
        if not errors.accept_client_report():
            return {"recorded": False, "reason": f"more than {CLIENT_LIMIT_PER_MINUTE} a minute"}
        row = errors.record(
            "dashboard",
            payload.message,
            type_=payload.type,
            traceback=payload.stack,
            endpoint=payload.url,
        )
        return {"recorded": bool(row)}

    @app.post("/api/errors/{error_id}/resolve")
    def resolve_error(error_id: int, payload: dict[str, Any] | None = Body(None)) -> dict:
        resolved = True if payload is None else bool(payload.get("resolved", True))
        row = errors.resolve(error_id, resolved)
        if row is None:
            raise HTTPException(status_code=404, detail="No such error")
        return row

    @app.delete("/api/errors/{error_id}", status_code=204)
    def delete_error(error_id: int) -> Response:
        if not errors.delete(error_id):
            raise HTTPException(status_code=404, detail="No such error")
        return Response(status_code=204)

    # Support bundles. Built on this computer, for the user to share
    # themselves; nothing here sends anything anywhere.
    @app.get("/api/support-bundle")
    def list_bundles() -> dict:
        return {"bundles": support.list()}

    @app.post("/api/support-bundle", status_code=201)
    async def create_bundle() -> dict:
        try:
            return await asyncio.to_thread(support.build)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not build the bundle: {exc}")

    @app.get("/api/support-bundle/{name}")
    def download_bundle(name: str) -> FileResponse:
        try:
            path = support.path(name)
        except (FileNotFoundError, OSError):
            raise HTTPException(status_code=404, detail="No such bundle")
        return FileResponse(path, media_type="application/zip", filename=name)

    @app.get("/api/logs")
    def list_logs() -> dict:
        return {"logs": logs.files()}

    @app.get("/api/logs/{name}")
    def read_log(name: str, lines: int = DEFAULT_LINES, from_end: bool = True,
                 pattern: str = "") -> dict:
        try:
            if pattern:
                return logs.search(name, pattern, lines=lines)
            return logs.read(name, lines=lines, from_end=from_end)
        except LogError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.get("/api/logs/{name}/download")
    def download_log(name: str) -> FileResponse:
        try:
            path = logs.path(name)
        except LogError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return FileResponse(path, media_type="text/plain", filename=name)

    @app.delete("/api/logs/{name}")
    def clear_log(name: str, request: Request) -> dict:
        # Clearing a log destroys evidence, so it takes a deliberate header
        # rather than a bare DELETE a stray link could produce.
        if request.headers.get("x-vela-confirm") != "clear":
            raise HTTPException(status_code=428, detail="Confirm clearing this log")
        try:
            return logs.clear(name, actor=request_actor(auth, request))
        except LogError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    # Updates. One anonymous request to the GitHub releases API, at most every
    # six hours, and only while the check is on.
    @app.get("/api/updates")
    def update_status() -> dict:
        return updates.status()

    @app.post("/api/updates/check")
    async def check_updates() -> dict:
        return await asyncio.to_thread(updates.check, force=True)

    @app.get("/api/updates/job")
    def update_job_state() -> dict:
        return {
            **update_job.state(),
            "rollback": rollback_available(config, capability()),
        }

    @app.post("/api/updates/apply")
    async def apply_update(request: Request) -> dict:
        # Replacing Vela with another copy of Vela is not something a stray
        # request may start.
        if request.headers.get("x-vela-confirm") != "update":
            raise HTTPException(status_code=428, detail="Confirm installing this update")
        try:
            return await asyncio.to_thread(update_job.apply, stop=_request_shutdown)
        except UpdateError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/api/updates/rollback")
    async def rollback_update(request: Request) -> dict:
        if request.headers.get("x-vela-confirm") != "rollback":
            raise HTTPException(status_code=428, detail="Confirm going back")
        try:
            return await asyncio.to_thread(update_job.rollback, stop=_request_shutdown)
        except UpdateError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

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

    @app.get("/api/backups/stats")
    def backup_stats() -> dict:
        return {
            **backups.stats(),
            "schedule": describe_schedule((settings.get("backups") or {}).get("schedule")),
        }

    @app.post("/api/backups/{name}/restore")
    async def restore_backup(name: str, request: Request) -> dict:
        # Restoring replaces live files and stops running apps. It takes a
        # deliberate header so no stray link or retry can start one.
        if request.headers.get("x-vela-confirm") != "restore":
            raise HTTPException(status_code=428, detail="Confirm restoring this backup")
        try:
            return await asyncio.to_thread(
                backups.restore, name, lifecycle=lifecycle,
                actor=request_actor(auth, request),
            )
        except BackupError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

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
