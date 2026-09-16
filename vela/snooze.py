"""Putting an app's "needs you" aside for a while (`<data_dir>/snooze.json`).

An app can say it needs attention. The user cannot always do something about it
right then, and a flag that cannot be dismissed stops meaning anything — the
rail dot ends up permanently on and nobody looks at it again.

**Later** hides one app's widget from the desk's *Needs you* list and from the
rail's dot for eight hours. It does not clear the flag and it does not tell the
app anything: the app's own widget still shows exactly what it published. What
is stored is one expiry time per (app, widget), and an expiry in the past is
deleted the next time the file is read.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import write_json_atomic

#: How long Later puts something aside for. Long enough to get through a day's
#: work, short enough that something genuinely wrong comes back the same day.
SNOOZE_HOURS = 8

#: A bound on the file, far above any real desk.
MAX_SNOOZED = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _key(app_id: str, widget_id: str) -> str:
    return f"{app_id}/{widget_id}"


class SnoozeStore:
    """Which (app, widget) summaries are put aside, and until when."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    def _load(self) -> dict[str, str]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            key: value
            for key, value in data.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def _unexpired(self, data: dict[str, str]) -> dict[str, str]:
        now = _now()
        kept = {}
        for key, stamp in data.items():
            try:
                until = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if until > now:
                kept[key] = stamp
        return kept

    def active(self) -> dict[str, str]:
        """The snoozes that have not run out, `"app/widget"` to an ISO expiry."""
        with self._lock:
            data = self._load()
            kept = self._unexpired(data)
            if len(kept) != len(data):
                # Expired entries are cleared on the way past rather than in a
                # job of their own; nothing else needs to run for this to tidy.
                write_json_atomic(self._path, kept)
            return kept

    def snooze(self, app_id: str, widget_id: str, *, hours: int = SNOOZE_HOURS) -> dict[str, Any]:
        """Put one widget's attention flag aside. Returns when it comes back."""
        app_id, widget_id = (app_id or "").strip(), (widget_id or "").strip()
        if not app_id or not widget_id:
            raise ValueError("a snooze names an app and one of its widgets")
        until = (_now() + timedelta(hours=hours)).isoformat(timespec="seconds")
        with self._lock:
            data = self._unexpired(self._load())
            key = _key(app_id, widget_id)
            if key not in data and len(data) >= MAX_SNOOZED:
                raise ValueError("too many snoozed items")
            data[key] = until
            write_json_atomic(self._path, data)
        return {"appId": app_id, "widgetId": widget_id, "until": until}

    def wake(self, app_id: str, widget_id: str) -> dict[str, Any]:
        """Bring one back before its time, for an undo."""
        with self._lock:
            data = self._unexpired(self._load())
            removed = data.pop(_key(app_id, widget_id), None) is not None
            write_json_atomic(self._path, data)
        return {"appId": app_id, "widgetId": widget_id, "woken": removed}

    def forget(self, app_id: str) -> None:
        """Drop an app's snoozes, for when it is uninstalled."""
        with self._lock:
            data = self._unexpired(self._load())
            prefix = f"{app_id}/"
            kept = {key: value for key, value in data.items() if not key.startswith(prefix)}
            if len(kept) != len(data):
                write_json_atomic(self._path, kept)
