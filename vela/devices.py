"""Paired devices: the Vela app on a phone, tablet or TV (`<data_dir>/devices.json`).

A browser signs in with the Vela password and holds a session for twelve hours.
An installed app should not need either: it pairs once and keeps a *device
credential* — long-lived, named, and revocable from Settings on its own without
touching the password or any other device.

Pairing is a one-time code shown by a signed-in session. The app trades it for
the credential, then trades the credential for an ordinary hub session whenever
it needs one. The credential never reaches the web page; only the session does,
so the dashboard in the app is the same dashboard with the same limits.

Only a SHA-256 of each credential is stored. It is 256 random bits, so a slow
hash would add nothing, and the file never holds anything that signs in.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .app_storage import AppServiceError
from .config import write_json_atomic

#: How long a pairing code can be used, and how many may be waiting at once.
CODE_SECONDS = 600
MAX_CODES = 8

#: A bound on the file, far above any real household.
MAX_DEVICES = 50

#: Easy to read off a screen and type on a remote: no 0/O, 1/I/L, 2/Z, 5/S.
CODE_ALPHABET = "34679ACDEFGHJKMNPQRTUVWXY"
CODE_LENGTH = 8

FORMS = ("phone", "tablet", "tv")
PLATFORMS = ("android",)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _digest(credential: str) -> str:
    return hashlib.sha256(credential.encode()).hexdigest()


def normalize_code(code: str) -> str:
    """The code as it is compared: case, dashes and spaces are for people."""
    return re.sub(r"[\s-]", "", code or "").upper()


def display_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def clean_name(name: Any, fallback: str = "Android device") -> str:
    text = _CONTROL.sub("", name if isinstance(name, str) else "").strip()
    return text[:60] or fallback


class Devices:
    """Paired devices on disk, and the pairing codes waiting in memory."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        # Codes live only in memory: a restart drops them, which is the right
        # answer for something that is meant to be used within minutes.
        self._codes: dict[str, float] = {}

    # ------------------------------------------------------------------ file

    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        rows = data.get("devices") if isinstance(data, dict) else None
        return [row for row in rows or [] if isinstance(row, dict) and isinstance(row.get("id"), str)
                and isinstance(row.get("secret"), str)]

    def _save(self, rows: list[dict[str, Any]]) -> None:
        write_json_atomic(self._path, {"version": 1, "devices": rows})

    @staticmethod
    def public(row: dict[str, Any]) -> dict[str, Any]:
        """A device as the dashboard sees it: everything except the secret."""
        return {key: row.get(key) for key in
                ("id", "name", "form", "platform", "appVersion", "pairedAt", "lastSeenAt")}

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self.public(row) for row in self._load()]

    # --------------------------------------------------------------- pairing

    def new_code(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            self._codes = {code: until for code, until in self._codes.items() if until > now}
            if len(self._codes) >= MAX_CODES:
                # The oldest code gives way; a person asking for another one
                # has given up on it.
                self._codes.pop(min(self._codes, key=self._codes.get))
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            self._codes[code] = now + CODE_SECONDS
        return {"code": display_code(code), "expiresIn": CODE_SECONDS}

    def pair(self, code: str, *, name: Any, form: Any, platform: Any,
             app_version: Any) -> tuple[dict[str, Any], str]:
        """Spend a code on a new device. Returns the device and its credential."""
        wanted = normalize_code(code)
        now = time.monotonic()
        with self._lock:
            until = self._codes.pop(wanted, None) if wanted else None
            if until is None or until <= now:
                raise AppServiceError(401, "That code has expired or was already used. "
                                           "Show a new code in Vela and scan it again.")
            rows = self._load()
            if len(rows) >= MAX_DEVICES:
                raise AppServiceError(409, "Too many paired devices. Remove one in Settings first.")
            credential = secrets.token_urlsafe(32)
            row = {
                "id": secrets.token_hex(8),
                "name": clean_name(name),
                "form": form if form in FORMS else "phone",
                "platform": platform if platform in PLATFORMS else "android",
                "appVersion": app_version[:32] if isinstance(app_version, str) else None,
                "pairedAt": _now(),
                "lastSeenAt": _now(),
                "secret": _digest(credential),
            }
            rows.append(row)
            self._save(rows)
        return self.public(row), credential

    def authenticate(self, credential: str) -> dict[str, Any]:
        """The device this credential belongs to, marked as seen now."""
        if not credential:
            raise AppServiceError(401, "This device is not paired with Vela")
        digest = _digest(credential)
        with self._lock:
            rows = self._load()
            for row in rows:
                if secrets.compare_digest(row["secret"], digest):
                    row["lastSeenAt"] = _now()
                    self._save(rows)
                    return self.public(row)
        raise AppServiceError(401, "This device was removed from Vela")

    # ------------------------------------------------------------- managing

    def rename(self, device_id: str, name: Any) -> dict[str, Any]:
        with self._lock:
            rows = self._load()
            for row in rows:
                if row["id"] == device_id:
                    row["name"] = clean_name(name, row.get("name") or "Android device")
                    self._save(rows)
                    return self.public(row)
        raise AppServiceError(404, "That device is not paired")

    def remove(self, device_id: str) -> None:
        with self._lock:
            rows = self._load()
            kept = [row for row in rows if row["id"] != device_id]
            if len(kept) == len(rows):
                raise AppServiceError(404, "That device is not paired")
            self._save(kept)
