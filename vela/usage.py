"""How often each app is opened (`<data_dir>/usage.json`).

This exists for one feature: the Launchpad's **Frequent** tab, which needs to
know which apps this person actually reaches for. It is deliberately the
smallest thing that can answer that question.

What is stored is a count per app per day, nothing else — no timestamps within
the day, no order, no session, no device. Thirty days are kept and older days
are dropped on every write, so the file cannot grow without bound and a habit
from two months ago stops shaping today's grid. It never leaves this computer:
no code outside the Launchpad reads it, and nothing sends it anywhere.

Ids are whatever the dashboard opens — an installed app's id, or one of Vela's
own core ids (`ask`, `library`, …). The store does not check them against the
registry: an app that is later removed simply stops being counted, and its rows
age out on their own.
"""

from __future__ import annotations

import json
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import write_json_atomic

#: How far back Frequent looks. Also the point at which a day is forgotten.
WINDOW_DAYS = 30

#: A bound on the file. Far more apps than anyone installs, but a malformed or
#: hostile caller cannot grow usage.json past it.
MAX_TRACKED = 500

#: Ids longer than this are not real; storing them would only waste the file.
MAX_ID = 120


def _today() -> str:
    return date.today().isoformat()


def _cutoff() -> str:
    return (date.today() - timedelta(days=WINDOW_DAYS - 1)).isoformat()


class UsageStore:
    """Open counts per app per day, pruned to the last 30 days."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, int]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        # A file edited by hand is read for whatever still makes sense rather
        # than thrown away: a wrong count costs a tab ordering, not data.
        clean: dict[str, dict[str, int]] = {}
        for app_id, days in data.items():
            if not isinstance(app_id, str) or not isinstance(days, dict):
                continue
            counts: dict[str, int] = {}
            for day, count in days.items():
                if not isinstance(day, str) or isinstance(count, bool):
                    continue
                if not isinstance(count, int) or count <= 0:
                    continue
                counts[day] = count
            if counts:
                clean[app_id] = counts
        return clean

    def _prune(self, data: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
        cutoff = _cutoff()
        pruned: dict[str, dict[str, int]] = {}
        for app_id, days in data.items():
            kept = {day: count for day, count in days.items() if day >= cutoff}
            if kept:
                pruned[app_id] = kept
        return pruned

    def record(self, app_id: str) -> dict[str, Any]:
        """Count one open of `app_id` today. Returns that app's window total."""
        app_id = (app_id or "").strip()
        if not app_id or len(app_id) > MAX_ID:
            return {"id": app_id, "count": 0}
        with self._lock:
            data = self._prune(self._load())
            if app_id not in data and len(data) >= MAX_TRACKED:
                # Full: keep counting what is already tracked rather than
                # evicting someone's real habit for a stray id.
                return {"id": app_id, "count": 0}
            day = _today()
            days = data.setdefault(app_id, {})
            days[day] = days.get(day, 0) + 1
            write_json_atomic(self._path, data)
            return {"id": app_id, "count": sum(days.values())}

    def totals(self) -> dict[str, int]:
        """Opens per app over the window, highest first."""
        data = self._prune(self._load())
        totals = {app_id: sum(days.values()) for app_id, days in data.items()}
        return dict(sorted(totals.items(), key=lambda pair: (-pair[1], pair[0])))

    def forget(self, app_id: str) -> None:
        """Drop an app's counts, for when it is uninstalled."""
        with self._lock:
            data = self._prune(self._load())
            if data.pop(app_id, None) is not None:
                write_json_atomic(self._path, data)
