"""ntfy push notifications: publish client, event ring buffer, periodic scheduler."""

import asyncio
from collections import deque
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import Config, dir_size
from .registry import Registry
from .settings import SettingsStore
from .errors_http import Upstream

SCHEDULE_INTERVAL_SECONDS = 15 * 60
DIGEST_HOUR = 9
# The health checks run once shortly after startup, then daily.
DOCTOR_STARTUP_DELAY_SECONDS = 60
DOCTOR_INTERVAL_SECONDS = 24 * 60 * 60
# How often the scheduler looks at the backup schedule. A minute is fine
# grained enough for a schedule whose smallest unit is a minute.
BACKUP_TICK_SECONDS = 60
# The update check waits a couple of minutes after startup, then runs daily.
UPDATE_STARTUP_DELAY_SECONDS = 120
UPDATE_INTERVAL_SECONDS = 24 * 60 * 60
EVENT_BUFFER_SIZE = 50


class NotifyError(Upstream):
    """Base class for ntfy publish failures."""

    code = "notify.publish_failed"


class NotifyConfigError(NotifyError):
    """Server/topic missing or the server address is invalid."""


class NotifyAuthError(NotifyError):
    """The ntfy server denied the configured credentials."""


class NotifyUnreachableError(NotifyError):
    """The ntfy server (or its tunnel) could not be reached."""


class NotifyRateLimitError(NotifyError):
    """The ntfy server is rate limiting publishes."""


class NotifyRejectedError(NotifyError):
    """The server rejected the notification or returned an invalid receipt."""


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{num_bytes} B"


class Notifier:
    """ntfy JSON publish client plus an in-memory log of recent notifications."""

    def __init__(self, settings: SettingsStore):
        self._settings = settings
        self._events: deque[dict[str, Any]] = deque(maxlen=EVENT_BUFFER_SIZE)

    def config(self) -> dict[str, Any]:
        raw = self._settings.get("ntfy_config") or {}
        events = raw.get("events")
        return {
            "server": str(raw.get("server") or "").rstrip("/"),
            "topic": str(raw.get("topic") or ""),
            "user": str(raw.get("user") or ""),
            "pass": str(raw.get("pass") or ""),
            "events": events if isinstance(events, dict) else {},
        }

    def recent(self) -> list[dict[str, Any]]:
        return list(self._events)[::-1]

    def _record(self, title: str, kind: str) -> None:
        self._events.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "title": title,
                "kind": kind,
            }
        )

    async def publish(
        self,
        title: str,
        message: str,
        tags: list[str] | None = None,
        priority: int = 3,
        kind: str = "publish",
    ) -> dict[str, Any]:
        cfg = self.config()
        if not cfg["server"] or not cfg["topic"]:
            raise NotifyConfigError("Set an ntfy server and topic in Settings first")
        parsed = urlparse(cfg["server"])
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise NotifyConfigError("Enter a valid http:// or https:// ntfy server address")

        auth = (cfg["user"], cfg["pass"]) if cfg["user"] else None
        body = {
            "topic": cfg["topic"],
            "title": str(title or "Vela"),
            "message": str(message or ""),
            "tags": [str(tag) for tag in (tags or [])][:5],
            "priority": min(5, max(1, int(priority))),
        }
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
                response = await client.post(f"{cfg['server']}/{cfg['topic']}", json=body, auth=auth)
        except httpx.HTTPError as exc:
            raise NotifyUnreachableError(
                "Could not reach the ntfy server. Check the server address and connection."
            ) from exc

        if response.status_code in (401, 403):
            raise NotifyAuthError(
                "ntfy denied access. Check the saved username, password, and topic permissions."
            )
        if response.status_code == 429:
            raise NotifyRateLimitError("ntfy is rate limiting notifications. Wait before trying again.")
        if response.status_code in (502, 503, 504, 530):
            raise NotifyUnreachableError(
                f"The notification server or its tunnel is unavailable (HTTP {response.status_code})."
            )
        if response.status_code >= 400:
            raise NotifyRejectedError(f"ntfy rejected the notification (HTTP {response.status_code}).")

        try:
            receipt = response.json()
        except ValueError:
            receipt = None
        if (
            not isinstance(receipt, dict)
            or receipt.get("event") != "message"
            or not isinstance(receipt.get("id"), str)
            or not receipt["id"]
            or receipt.get("topic") != cfg["topic"]
            or not isinstance(receipt.get("time"), (int, float))
        ):
            raise NotifyRejectedError(
                "The server did not return a valid ntfy receipt. Delivery is unconfirmed."
            )

        self._record(body["title"], kind)
        return {
            "id": receipt["id"],
            "accepted_at": datetime.fromtimestamp(receipt["time"]).isoformat(timespec="seconds"),
        }


class NotifyScheduler:
    """Periodic hub notifications: daily digest after 09:00 and dead-process alerts."""

    def __init__(
        self,
        notifier: Notifier,
        registry: Registry,
        config: Config,
        interval: int = SCHEDULE_INTERVAL_SECONDS,
        doctor=None,
        doctor_delay: int = DOCTOR_STARTUP_DELAY_SECONDS,
        doctor_interval: int = DOCTOR_INTERVAL_SECONDS,
    ):
        self._notifier = notifier
        self._registry = registry
        self._config = config
        self._interval = interval
        self._task: asyncio.Task | None = None
        self._doctor = doctor
        self._doctor_delay = doctor_delay
        self._doctor_interval = doctor_interval
        self._doctor_task: asyncio.Task | None = None
        self._backups = None
        self._backup_settings = None
        self._backup_interval = BACKUP_TICK_SECONDS
        # The moment the schedule last named. A run is due once it has passed.
        self._backup_due: datetime | None = None
        self._backup_task: asyncio.Task | None = None
        self._updates = None
        self._update_task: asyncio.Task | None = None
        # The version already announced. One notification per release, not one
        # a day until someone installs it.
        self._announced_version: str | None = None
        self._update_job = None
        self._update_registry = None
        self._update_automations = None
        self._update_doctor = None
        # Check keys already announced. A failure is worth one notification,
        # not one every day until someone fixes it; clearing the check arms it
        # again.
        self._announced: set[str] = set()
        self._last_digest_day = ""
        self._known_running: set[str] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())
        if self._doctor is not None:
            self._doctor_task = asyncio.create_task(self._doctor_loop())
        if self._backups is not None:
            self._backup_task = asyncio.create_task(self._backup_loop())
        if self._updates is not None:
            self._update_task = asyncio.create_task(self._update_loop())

    async def stop(self) -> None:
        for name in ("_task", "_doctor_task", "_backup_task", "_update_task"):
            task = getattr(self, name)
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                setattr(self, name, None)

    def attach_doctor(self, doctor) -> None:
        """Give the scheduler the doctor to sweep with, before `start()`."""
        self._doctor = doctor

    def attach_updates(self, updates) -> None:
        """Give the scheduler the update checker to run, before `start()`."""
        self._updates = updates

    async def _update_loop(self) -> None:
        await asyncio.sleep(UPDATE_STARTUP_DELAY_SECONDS)
        while True:
            try:
                await self.run_update_check()
            except Exception:
                pass
            await asyncio.sleep(UPDATE_INTERVAL_SECONDS)

    def attach_update_job(self, job, registry=None, automations=None, doctor=None) -> None:
        """What automatic mode needs to decide whether now is a safe moment."""
        self._update_job = job
        self._update_registry = registry
        self._update_automations = automations
        self._update_doctor = doctor

    async def _maybe_auto_update(self, status) -> None:
        """Install automatically, but only at the chosen hour and only if
        nothing of the user's would be interrupted."""
        from .updates import UpdateError, auto_mode_allowed

        if self._update_job is None or not status.get("available"):
            return
        if status.get("mode") != "auto" or datetime.now().hour != status.get("hour"):
            return
        if self._update_job.busy():
            return
        apps = 0
        if self._update_registry is not None:
            apps = sum(1 for app in self._update_registry.list_apps() if app.get("running"))
        automation_running = False
        if self._update_automations is not None:
            try:
                automation_running = bool(self._update_automations.status().get("running"))
            except Exception:
                automation_running = True
        failing = False
        if self._update_doctor is not None:
            last = self._update_doctor.last() or {}
            failing = any(check["status"] == "fail" for check in last.get("checks", []))
        allowed, reason = auto_mode_allowed(
            apps_running=apps, automation_running=automation_running, doctor_failing=failing
        )
        if not allowed:
            # Try again tomorrow; the notice stays up in the meantime.
            return
        try:
            await asyncio.to_thread(self._update_job.apply)
        except UpdateError:
            pass

    async def run_update_check(self) -> dict[str, Any]:
        """One check, announcing a version that has not been announced yet.

        With checking turned off this makes no request: the checker itself
        refuses, so the switch is honoured on every path, not just the button.
        """
        status = await asyncio.to_thread(self._updates.check)
        latest = status.get("latest")
        if not status.get("available") or not latest:
            # Nothing to say. If the user installs it, the next new release is
            # news again.
            self._announced_version = None
            return status
        if latest == self._announced_version:
            return status
        self._announced_version = latest
        cfg = self._notifier.config()
        if cfg["server"] and cfg["topic"]:
            await self._notifier.publish(
                f"Vela {latest} is available",
                f"You are running {status.get('current')}. Open Settings › Updates to see what changed.",
                tags=["arrow_up"],
                priority=3,
                kind="update",
            )
        await self._maybe_auto_update(status)
        return status

    def attach_backups(self, backups, settings) -> None:
        """Give the scheduler the backup store to run on its schedule."""
        self._backups = backups
        self._backup_settings = settings

    async def _backup_loop(self) -> None:
        from .backups import next_run

        while True:
            await asyncio.sleep(self._backup_interval)
            try:
                schedule = (self._backup_settings.get("backups") or {}).get("schedule")
                due = next_run(schedule)
                if due is None:
                    self._backup_due = None
                    continue
                now = datetime.now(due.tzinfo)
                # `next_run` answers with the next moment from now, so the run
                # is due when the moment it named last time has passed.
                if self._backup_due is not None and now >= self._backup_due:
                    await self.run_backup()
                self._backup_due = due
            except Exception:
                # A failed tick must not end the schedule.
                pass

    async def run_backup(self) -> dict[str, Any] | None:
        """One scheduled backup, announcing a failure rather than hiding it."""
        from .backups import BackupError

        try:
            made = await asyncio.to_thread(self._backups.create)
        except BackupError as exc:
            cfg = self._notifier.config()
            if cfg["server"] and cfg["topic"]:
                await self._notifier.publish(
                    "Vela could not back itself up",
                    str(exc),
                    tags=["warning"],
                    priority=4,
                    kind="backup",
                )
            return None
        return made

    async def _doctor_loop(self) -> None:
        # Wait before the first sweep: startup is the busiest moment on this
        # computer, and a check run then would measure the startup, not the
        # steady state.
        await asyncio.sleep(self._doctor_delay)
        while True:
            try:
                await self.run_doctor()
            except Exception:
                # A failed sweep is not worth ending the daily schedule over.
                pass
            await asyncio.sleep(self._doctor_interval)

    async def run_doctor(self) -> dict[str, Any]:
        """One sweep, announcing failures that were not already announced."""
        result = await asyncio.to_thread(self._doctor.collect)
        failing = {check["key"] for check in result["checks"] if check["status"] == "fail"}
        # A check that passes again may announce itself if it fails later.
        self._announced &= failing
        fresh = [
            check
            for check in result["checks"]
            if check["status"] == "fail" and check["key"] not in self._announced
        ]
        if fresh:
            self._announced |= {check["key"] for check in fresh}
            cfg = self._notifier.config()
            if cfg["server"] and cfg["topic"]:
                first = fresh[0]
                more = len(fresh) - 1
                await self._notifier.publish(
                    "Vela needs attention",
                    first["detail"] + (f"\n…and {more} more." if more else ""),
                    tags=["warning"],
                    priority=4,
                    kind="health",
                )
        return result

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self._tick()
            except Exception:
                # ntfy offline or a transient failure — try again next tick.
                pass

    async def _tick(self) -> None:
        cfg = self._notifier.config()
        manifests = self._registry.manifests()
        apps = self._registry.list_apps()
        events = cfg.get("events", {})

        process_apps = [a for a in apps if a["id"] in manifests and manifests[a["id"]].web is None]
        running_now = {a["id"] for a in process_apps if a["running"]}
        configured = bool(cfg["server"] and cfg["topic"])

        if events.get("status_alerts") and configured:
            if self._known_running is not None:
                for app_id in sorted(self._known_running - running_now):
                    if app_id not in manifests:
                        continue
                    name = manifests[app_id].name
                    await self._notifier.publish(
                        f"{name} stopped",
                        f"The {name} app process is no longer running.",
                        tags=["warning"],
                        priority=4,
                        kind="status_alert",
                    )
        self._known_running = running_now

        now = datetime.now()
        today = now.date().isoformat()
        if (
            events.get("digest")
            and configured
            and now.hour >= DIGEST_HOUR
            and self._last_digest_day != today
        ):
            self._last_digest_day = today
            running_names = [a["name"] for a in apps if a["running"]]
            installed = sum(1 for a in apps if a["installed"])
            message = "\n".join(
                [
                    f"Running apps: {', '.join(running_names) or 'none'}",
                    f"Installed apps: {installed}",
                    f"Data dir usage: {_human_size(dir_size(self._config.data_dir))}",
                ]
            )
            await self._notifier.publish(
                "Vela hub digest", message, tags=["bar_chart"], priority=2, kind="digest"
            )
