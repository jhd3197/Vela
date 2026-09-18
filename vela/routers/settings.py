"""Preferences, and the notification channel they configure.

How a desk is dressed belongs to a desktop; which folders it may show and
whether it asks about the weather stay global. The settings route keeps both
halves in one object so the dashboard's shape does not change, and reads the
appearance back from the desktop rather than a second copy.
"""

from typing import Any

from fastapi import APIRouter, Body
from pydantic import BaseModel, Field

from ..backups import BackupError, validate_schedule
from ..desktops import DesktopError
from ..errors_http import Unprocessable
from ..files import validate_shares
from ..settings import normalize_identity, sanitize_pins
from ..system_metrics import validate_volumes
from ..updates import MODES

_SETTINGS_KEYS = {"theme", "theme_id", "chat_model", "chat_history", "ntfy_config", "desk",
                  "rail", "backups", "updates", "identity", "files"}


class NotifyPublishRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    message: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=5)
    priority: int = Field(default=3, ge=1, le=5)


def router(settings, desktops, config, backups, conversations, agent_runs, notifier,
           themes) -> APIRouter:
    api = APIRouter(prefix="/api", tags=["settings"])

    _APPEARANCE_KEYS = ("wallpaper", "dim", "labels")

    def _settings_view() -> dict:
        view = settings.public_view()
        try:
            look = desktops.appearance(desktops.default_id())
        except DesktopError:
            return view
        view["desk"] = {**(view.get("desk") or {}),
                        **{key: look[key] for key in _APPEARANCE_KEYS}}
        return view

    @api.get("/settings")
    def get_settings() -> dict:
        return _settings_view()

    @api.patch("/settings")
    def patch_settings(payload: dict[str, Any] = Body(...)) -> dict:
        update = {key: value for key, value in payload.items() if key in _SETTINGS_KEYS}
        # A theme id names a theme this server actually has. Storing one that
        # does not would leave the dashboard asking for a file that is not
        # there on every load, and falling back silently each time.
        if "theme_id" in update:
            if not isinstance(update["theme_id"], str) or not themes.exists(update["theme_id"]):
                raise Unprocessable("theme_id must name a theme this server has",
                                    code="settings.theme_unknown")
        # A desk volume names a real folder on this computer, so it is checked
        # before it is stored rather than failing later inside a widget.
        desk = update.get("desk")
        appearance = {}
        if isinstance(desk, dict):
            appearance = {key: desk.pop(key) for key in _APPEARANCE_KEYS if key in desk}
            if not desk:
                update.pop("desk")
        if isinstance(desk, dict) and "volumes" in desk:
            try:
                desk["volumes"] = validate_volumes(desk["volumes"])
            except ValueError as exc:
                raise Unprocessable(str(exc), code="settings.volumes_invalid") from exc
        # The avatar letter follows the display name rather than being sent, so
        # an identity patch is normalised before it is stored.
        if "identity" in update:
            try:
                update["identity"] = normalize_identity(update["identity"])
            except ValueError as exc:
                raise Unprocessable(str(exc), code="settings.identity_invalid") from exc
        # A share names a folder on this computer, so it is checked before it is
        # stored rather than failing later inside the Files app.
        share_update = update.get("files")
        if isinstance(share_update, dict) and "shares" in share_update:
            try:
                share_update["shares"] = validate_shares(share_update["shares"], config.data_dir)
            except ValueError as exc:
                raise Unprocessable(str(exc), code="settings.shares_invalid") from exc
        # Rail pins are a list of app ids; store the shape, not the meaning —
        # the dashboard drops ids that no longer name a real app.
        rail = update.get("rail")
        if isinstance(rail, dict) and "pinned" in rail:
            if not isinstance(rail["pinned"], list):
                raise Unprocessable("rail.pinned must be a list of ids",
                                    code="settings.rail_pinned_invalid")
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
                    raise Unprocessable("updates.mode is notify or auto",
                                        code="settings.updates_mode_invalid")
                cleaned["mode"] = update_settings["mode"]
            if "hour" in update_settings:
                try:
                    hour = int(update_settings["hour"])
                except (TypeError, ValueError) as exc:
                    raise Unprocessable("updates.hour is an hour of the day",
                                        code="settings.updates_hour_invalid") from exc
                if not 0 <= hour <= 23:
                    raise Unprocessable("updates.hour is an hour of the day",
                                        code="settings.updates_hour_invalid")
                cleaned["hour"] = hour
            update["updates"] = cleaned
        backup_settings = update.get("backups")
        if isinstance(backup_settings, dict) and "schedule" in backup_settings:
            try:
                backup_settings["schedule"] = validate_schedule(backup_settings["schedule"])
            except BackupError as exc:
                raise Unprocessable(str(exc),
                                    code="settings.backup_schedule_invalid") from exc
            backups.set_keep(backup_settings["schedule"]["keep"])
        if appearance:
            desktops.save_appearance(desktops.default_id(), appearance)
        settings.patch(update)
        # Turning retention off is a deletion, not just a preference change.
        if update.get("chat_history") is False:
            conversations.purge()
            # The same meaning for agent tasks: transcripts, events and results
            # go, and anything still running continues in the volatile mode the
            # documentation describes rather than quietly writing on.
            agent_runs.purge()
        return {"ok": True}

    @api.post("/notify/test")
    async def notify_test(payload: dict[str, Any] | None = Body(None)) -> dict:
        if payload and isinstance(payload.get("ntfy_config"), dict):
            settings.patch({"ntfy_config": payload["ntfy_config"]})
        receipt = await notifier.publish(
            "Vela",
            "Notification test — if you can read this, delivery is working.",
            tags=["bell"],
            kind="test",
        )
        return {"ok": True, **receipt}

    @api.post("/notify/publish")
    async def notify_publish(payload: NotifyPublishRequest) -> dict:
        receipt = await notifier.publish(
            payload.title, payload.message, tags=payload.tags, priority=payload.priority
        )
        return {"ok": True, **receipt}

    @api.get("/notifications")
    def list_notifications() -> dict:
        return {"notifications": notifier.recent()}

    return api
