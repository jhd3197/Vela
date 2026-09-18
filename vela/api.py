"""The app factory: build the services, mount the routers, own the boundary.

No route is defined here. `vela/router_registry.py` holds the ordered mount
table and each router module owns its own paths, so what this file does is
build the services once, hand each router the ones it asked for, register the
three exception handlers and drive the process lifetime.

The error handlers are the boundary. A service raises a `vela.errors_http`
error and this renders it; nothing below here imports FastAPI to refuse a
request.
"""

import asyncio
import os
import signal
import threading
import logging
import traceback

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import __version__
from .assistant import Assistant
from .bots import BotStore
from .conversations import ConversationStore
from .rooms import Rooms
from .backups import KEEP_BACKUPS, BackupStore
from .config import Config, load_config
from .desk import CORE_WIDGET_TYPES
from .desktops import Desktops, Gateway
from .agent_runs.service import AgentRuns
from .usage import UsageStore
from .files import Files
from .snooze import SnoozeStore
from .weather import Weather
from .doctor import Doctor
from .errors import ErrorStore
from .support_bundle import SupportBundle
from .updates import UpdateChecker, UpdateJob, startup_report
from .notify import Notifier, NotifyScheduler
from .registry import Registry
from .router_registry import ROUTERS
from .runners import current_platform, get_runner
from .settings import SettingsStore
from .system_metrics import SystemMetrics
from .state import StateStore
from .wallpaper import Wallpaper
from .widgets import Widgets
from .auth import Auth
from .app_storage import AppStorage
from .errors_http import VelaError
from .agent_runs.approvals import ApprovalPending
from .app_services import AppServices
from .lifecycle import Lifecycle
from .logging_setup import audit
from .logs import LogStore
from .connections import Connections
from .catalog import Catalog
from .releases import Releases
from .actions import Actions
from .connected_apps import ConnectedApps
from .phone_access import PhoneAccess
from .automations import Automations


LOG = logging.getLogger(__name__)


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
    usage = UsageStore(config.data_dir / "usage.json")
    weather = Weather(settings)
    files = Files(config, settings)
    wallpaper = Wallpaper(config.data_dir)
    conversations = ConversationStore(config.data_dir / "chat.sqlite")
    bots = BotStore(config.data_dir / "chat.sqlite")
    assistant = Assistant(settings, registry, state, config, conversations, bots=bots)
    rooms = Rooms(conversations, bots, assistant)
    # A process that stopped mid-answer cannot finish it. Say so on the way back
    # up rather than showing a run that will never move again.
    conversations.interrupt_stale_runs()
    backups = BackupStore(config, keep=(settings.get("backups") or {}).get(
        "schedule", {}).get("keep", KEEP_BACKUPS))
    logs = LogStore(config.logs_dir)
    auth = Auth(config)
    storage = AppStorage(config.data_dir / "app-data.sqlite")
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

    # Desktops own the desk. `desk.json` is read once, on the way up, and then
    # left alone: there is one writable copy of a board, not two. An open window
    # binds to the installation it opened against, which app storage knows.
    desktops = Desktops(
        config.data_dir,
        known_types=lambda: _known_widget_types(),
        settings=settings,
        wallpaper=wallpaper,
        storage=storage,
        auth=auth,
        registry=registry,
        log=lambda message: print(f'[vela] {message}', flush=True),
        # The one origin the managed browser is allowed to talk to. `__main__`
        # records the port it bound; a server started another way falls back to
        # the documented one.
        origin=f"http://127.0.0.1:{os.environ.get('VELA_PORT', '7700')}",
    )
    # One guard, given to every service that can change something. For a person
    # it answers None and nothing changes; for an agent it returns the check
    # that service runs inside its own write transaction.
    guard = desktops.effect_guard
    # Every app-scoped request passes the gateway before a handler sees it, so
    # an operation nobody classified is refused rather than reaching one.
    auth.gateway = Gateway(desktops).check
    connected_apps = ConnectedApps(storage)
    app_services = AppServices(registry, auth, storage, guard=guard)
    connections = Connections(registry, storage, transport=connection_transport, guard=guard)
    lifecycle = Lifecycle(config, registry, state, runner, platform, auth, storage, desktops)
    catalog = Catalog(config)
    registry.catalog = catalog
    releases = Releases(config, lifecycle, app_services, catalog)
    actions = Actions(lifecycle, app_services, guard=guard)
    # An agent run invoking a named action goes through this same service, with
    # its own caller identity and its own grant check. Attached here because the
    # action service is built from services that are built from desktops.
    desktops.actions = actions
    # Tasks an agent desktop carries out. Created after actions because a run
    # invoking a named action goes through that service.
    agent_runs = AgentRuns(
        desktops, config.data_dir, settings=settings, notifier=notifier,
        log=lambda message: print(f'[vela] {message}', flush=True),
    )
    snooze = SnoozeStore(config.data_dir / "snooze.json")
    widgets = Widgets(storage, registry, actions, snooze, guard=guard)
    automations = Automations(config, registry, actions, notifier, settings,
                              log=lambda message: print(f'[vela] {message}', flush=True))
    updates = UpdateChecker(config, __version__, settings=settings)
    doctor = Doctor(config, state=state, registry=registry, settings=settings,
                    backups=backups, assistant=assistant, catalog=catalog, updates=updates)
    # Attached rather than passed: Doctor is built before the run service, and
    # reordering the assembly for two optional checks would be the tail wagging
    # the dog.
    doctor._desktops = desktops
    doctor._runs = agent_runs
    errors = ErrorStore(config.data_dir / "diagnostics.sqlite")
    update_job = UpdateJob(config, updates, backups=backups)
    support = SupportBundle(config, version=__version__, registry=registry, settings=settings,
                            errors=errors, doctor=doctor, automations=automations,
                            desktops=desktops, runs=agent_runs)
    # The scheduler runs the sweep daily and once after startup, and announces
    # a check that newly fails.
    scheduler.attach_doctor(doctor)
    scheduler.attach_backups(backups, settings)
    scheduler.attach_updates(updates)
    scheduler.attach_update_job(update_job, registry=registry,
                                automations=automations, doctor=doctor)

    app = FastAPI(title="vela", version=__version__)
    app.middleware("http")(auth.middleware)

    # Kept on `app.state` because something outside this factory reads them:
    # the tray, the test suite and `vela/desktop.py` all look here.
    app.state.automations = automations
    app.state.desktops = desktops
    app.state.agent_runs = agent_runs
    phone_access = PhoneAccess(app, config, auth)
    app.state.phone_access = phone_access

    def request_shutdown() -> None:
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

    # What a router may ask for, by the name its factory uses. A router that
    # names something not here fails at startup rather than at the first
    # request, which is the point of building the table in one place.
    services = {
        "actions": actions,
        "agent_runs": agent_runs,
        "app_services": app_services,
        "assistant": assistant,
        "auth": auth,
        "automations": automations,
        "backups": backups,
        "bots": bots,
        "catalog": catalog,
        "config": config,
        "connected_apps": connected_apps,
        "connections": connections,
        "conversations": conversations,
        "desktops": desktops,
        "doctor": doctor,
        "errors": errors,
        "files": files,
        "lifecycle": lifecycle,
        "logs": logs,
        "notifier": notifier,
        "phone_access": phone_access,
        "platform": platform,
        "registry": registry,
        "releases": releases,
        "request_shutdown": request_shutdown,
        "rooms": rooms,
        "runs": agent_runs,
        "settings": settings,
        "snooze": snooze,
        "state": state,
        "support": support,
        "system_metrics": system_metrics,
        "update_job": update_job,
        "update_report_state": lambda: getattr(app.state, "update_report", None),
        "updates": updates,
        "usage": usage,
        "weather": weather,
        "widgets": widgets,
    }
    for spec in ROUTERS:
        app.include_router(spec.build(services))

    # Say plainly what a restart did to work that was in flight, rather than
    # leaving a row that claims to still be running.
    for note in [agent_runs.prepare()]:
        if note["interrupted"]:
            LOG.info("desktops: %s task(s) were interrupted by a restart", note["interrupted"])
        if any(note["cleaned"].values()):
            LOG.info("desktops: cleaned up %s expired item(s)", sum(note["cleaned"].values()))
    # Import the existing desk before anything can read a desktop, and sweep
    # wallpaper files a crash may have left unreferenced.
    _desktop_migration = desktops.prepare()
    for note in _desktop_migration["notes"]:
        LOG.info("desktops: %s", note)

    @app.exception_handler(VelaError)
    async def vela_error(request, exc: VelaError):
        """Every expected failure, rendered once.

        `detail` is what it always was; `code` and `status` ride along. A few
        errors carry a header that means something to the caller — a desk
        conflict names the revision to reload — so the error brings it rather
        than each route remembering to attach one.
        """
        return JSONResponse(exc.to_body(), status_code=exc.status,
                            headers=exc.headers)

    @app.exception_handler(ApprovalPending)
    async def approval_pending(request, exc):
        """202: accepted for a decision, and nothing written.

        Deliberately not an error. A change waiting for its owner is not a
        change that failed, and an app told "failed" learns to give up on the
        thing it should be waiting for.
        """
        return JSONResponse(
            {
                "pending": exc.record,
                "detail": exc.record["summary"]["headline"],
            },
            status_code=202,
        )

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

    @app.on_event("startup")
    async def start_automations() -> None:
        await automations.start()

    @app.on_event("shutdown")
    async def stop_automations() -> None:
        await automations.stop()

    @app.on_event("startup")
    async def restore_phone_access() -> None:
        await phone_access.restore()

    @app.on_event("shutdown")
    async def stop_phone_access() -> None:
        await phone_access.stop()

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

    @app.on_event("startup")
    async def start_scheduler() -> None:
        # The loop everything asynchronous in this server belongs to. Recorded
        # because the browser runtime's pipes and futures live on it, and work
        # scheduled from another thread has to come back here rather than
        # awaiting on a loop that is not the one reading the worker.
        app.state.loop = asyncio.get_running_loop()
        scheduler.start()
        system_metrics.start()

    @app.on_event("shutdown")
    async def stop_scheduler() -> None:
        await scheduler.stop()
        await system_metrics.stop()
        # Tasks first, then the browser they were working in: a run that is
        # still dispatching into a closing browser is the one thing worse than a
        # run that stops.
        await agent_runs.stop()
        # A browser left running with nobody to stop it is the thing the
        # worker's own watchdog is a backstop for; this is the ordinary path.
        await desktops.stop_runtime()

    return app


app = create_app()
