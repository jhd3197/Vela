"""Persistent hub settings (settings.json) with atomic writes and secret redaction."""

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULT_SETTINGS: dict[str, Any] = {
    "theme": "dark",
    "chat_model": None,
    "chat_history": True,
    "ntfy_config": {
        "server": "",
        "topic": "",
        "user": "",
        "pass": "",
        "events": {"digest": True, "status_alerts": True},
    },
    # The desk: which volumes it may show, and how it is dressed. `volumes` is
    # a list of {path, label}; it stays empty until the user names one, because
    # Vela does not go looking through the computer's drives on its own.
    "desk": {"volumes": [], "wallpaper": "lake", "dim": True, "labels": True},
    # The rail: which apps the user pinned to it, in the order they chose.
    # Entries are core-app ids (ask, library, …) or installed-app ids; the
    # dashboard drops any that no longer resolve. Defaults to Ask and the
    # Marketplace.
    "rail": {"pinned": ["ask", "library"]},
}

# The most pins the rail will store. Far more than fit on screen, but a bound
# keeps a malformed patch from growing settings.json without limit.
MAX_PINS = 40


def sanitize_pins(value: Any) -> list[str]:
    """A rail pin list reduced to unique, non-empty id strings in order.

    The dashboard decides which ids still resolve to a real app; the store only
    guarantees the shape, so a patch cannot write anything but a bounded list of
    id strings. A non-list is rejected by the caller before this runs.
    """
    seen: set[str] = set()
    pins: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            continue
        pin = entry.strip()
        if not pin or pin in seen:
            continue
        seen.add(pin)
        pins.append(pin)
        if len(pins) >= MAX_PINS:
            break
    return pins


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


class SettingsStore:
    """settings.json: key/value hub settings, merged over DEFAULT_SETTINGS.

    Patch semantics: absent key keeps the stored value, "" clears a string,
    any other value replaces it. Nested dicts (ntfy_config, events) merge.
    """

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, key: str, default: Any = None) -> Any:
        data = self._load()
        if key in data:
            value = data[key]
            base = DEFAULT_SETTINGS.get(key)
            if isinstance(value, dict) and isinstance(base, dict):
                return _deep_merge(base, value)
            return value
        if key in DEFAULT_SETTINGS:
            return deepcopy(DEFAULT_SETTINGS[key])
        return default

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            data = self._load()
            data[key] = value
            self._save(data)

    def patch(self, changes: dict[str, Any]) -> None:
        with self._lock:
            data = self._load()
            for key, value in changes.items():
                if isinstance(value, dict) and isinstance(data.get(key), dict):
                    data[key] = _deep_merge(data[key], value)
                else:
                    data[key] = value
            self._save(data)

    def public_view(self) -> dict[str, Any]:
        """All settings with the ntfy password redacted to a configured flag."""
        view = deepcopy(DEFAULT_SETTINGS)
        stored = self._load()
        for key, value in stored.items():
            if isinstance(value, dict) and isinstance(view.get(key), dict):
                view[key] = _deep_merge(view[key], value)
            else:
                view[key] = deepcopy(value)
        ntfy = view.get("ntfy_config")
        if isinstance(ntfy, dict):
            ntfy["passConfigured"] = bool(ntfy.pop("pass", ""))
        return view
