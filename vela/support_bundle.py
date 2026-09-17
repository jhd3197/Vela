"""A redacted picture of this server, in one file, for the user to share.

Structure, `_scrub` and `_safe` follow ServerKit's `support_bundle_service.py`
(MIT, same owner); the collectors are Vela's.

Two rules decide what goes in. Nothing that belongs to the user's apps — app
storage, chat history, wallpapers — is ever collected, and every piece of free
text that is collected goes through `_scrub` first. The bundle is written to
this computer and nothing sends it anywhere: the user attaches it themselves,
if they choose to.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .config import Config, dir_size
from .logging_setup import audit

LOG = logging.getLogger(__name__)

# Bundles are kept for a week. They are a snapshot for one conversation, not
# an archive, and they are the largest thing Vela writes on request.
KEEP_DAYS = 7
LOG_TAIL_LINES = 500
RECENT_ERRORS = 100
NAME_RE = re.compile(r"^vela-support-\d{8}-\d{6}\.zip$")

# Any settings key that looks like a credential is replaced wholesale, whatever
# its value looks like.
SECRET_KEY_RE = re.compile(r"(?i)(token|secret|pass(?:word)?|key|credential|authorization)")

_SECRETY = (
    r"[\w.-]*(?:token|secret|passw(?:or)?d|api[_-]?key|private[_-]?key|access[_-]?key"
    r"|credential|authorization)[\w.-]*"
)
# The value must be neither an existing redaction nor an auth scheme:
# `Authorization: Bearer x` is rewritten by the bearer rule above first, and
# without these two guards this rule would then replace the word "Bearer"
# itself, leaving a line nobody can read for no extra safety.
_ASSIGNMENT_RE = re.compile(
    r"(?i)([\"']?" + _SECRETY + r"[\"']?\s*[:=]\s*)([\"']?)(?!\[REDACTED)(?!(?:bearer|basic)\s)([^\s\"',;}]+)"
)
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
# A bare JWT: three dot-separated base64url segments.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")

REDACTED = "[REDACTED]"


def scrub(text: Any) -> Any:
    """Redact anything that looks like a credential. Safe to run twice."""
    if not text:
        return text
    if not isinstance(text, str):
        text = str(text)
    # Order matters: a bare JWT and a "Bearer <token>" header are caught first,
    # so an `Authorization: Bearer x` line never leaves the token behind when
    # the assignment rule rewrites the same line.
    text = _JWT_RE.sub("[REDACTED-JWT]", text)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    text = _ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    return text


def scrub_value(key: str, value: Any) -> Any:
    """One settings value, redacted when its key names a credential."""
    if isinstance(value, dict):
        return {inner: scrub_value(inner, item) for inner, item in value.items()}
    if isinstance(value, list):
        return [scrub_value(key, item) for item in value]
    if SECRET_KEY_RE.search(key or ""):
        # Keep whether it was set, which is the diagnostic fact, not the value.
        return REDACTED if value not in (None, "", False) else value
    return scrub(value) if isinstance(value, str) else value


def _safe(section: str, collect: Callable[[], Any], default: Any = None) -> Any:
    """Run one collector. A broken collector must not sink the whole bundle."""
    try:
        return collect()
    except Exception as exc:  # noqa: BLE001 - diagnostics are best effort
        LOG.warning("support bundle: %s could not be collected: %s", section, exc)
        return default if default is not None else {"error": f"{section} could not be collected"}


class SupportBundle:
    """Builds and lists the bundles under `<data_dir>/support/`."""

    def __init__(
        self,
        config: Config,
        *,
        version: str = "",
        registry=None,
        settings=None,
        errors=None,
        doctor=None,
        automations=None,
        desktops=None,
    ):
        self._config = config
        self._version = version
        self._registry = registry
        self._desktops = desktops
        self._settings = settings
        self._errors = errors
        self._doctor = doctor
        self._automations = automations
        self._dir = config.data_dir / "support"

    # ----------------------------------------------------------- collectors

    def _meta(self) -> dict[str, Any]:
        return {
            "vela": self._version,
            "generated": datetime.now().isoformat(timespec="seconds"),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version,
            "frozen": bool(getattr(sys, "frozen", False)),
            "dataDir": str(self._config.data_dir),
            "sizes": {
                "dataDir": dir_size(self._config.data_dir),
                "installed": dir_size(self._config.installed_dir)
                if self._config.installed_dir.is_dir()
                else 0,
                "logs": dir_size(self._config.logs_dir) if self._config.logs_dir.is_dir() else 0,
            },
        }

    def _settings_view(self) -> dict[str, Any]:
        if self._settings is None:
            return {}
        stored = self._settings.public_view()
        return {key: scrub_value(key, value) for key, value in stored.items()}

    def _apps(self) -> list[dict[str, Any]]:
        if self._registry is None:
            return []
        # Ids, versions and whether each one runs. Not their data.
        return [
            {
                "id": app.get("id"),
                "name": app.get("name"),
                "version": app.get("version"),
                "runtime": app.get("runtime"),
                "installed": app.get("installed"),
                "running": app.get("running"),
            }
            for app in self._registry.list_apps()
        ]

    def _doctor_view(self) -> dict[str, Any]:
        if self._doctor is None:
            return {}
        result = self._doctor.last() or self._doctor.collect()
        return {
            "ranAt": result.get("ranAt"),
            "summary": result.get("summary"),
            "checks": [
                {**check, "detail": scrub(check.get("detail"))}
                for check in result.get("checks", [])
            ],
        }

    def _errors_view(self) -> list[dict[str, Any]]:
        if self._errors is None:
            return []
        return [
            {
                **row,
                "message": scrub(row.get("message")),
                "traceback": scrub(row.get("traceback")),
                "endpoint": scrub(row.get("endpoint")),
            }
            for row in self._errors.recent(RECENT_ERRORS)
        ]

    def _automations_view(self) -> dict[str, Any]:
        if self._automations is None:
            return {}
        # Workflow ids and their last outcome only: a workflow document can
        # name folders, addresses and message text.
        status = self._automations.status()
        return {
            "available": status.get("available"),
            "running": status.get("running"),
            "runsToday": status.get("runsToday"),
        }

    def _desk(self) -> dict[str, Any]:
        """How each desktop is arranged, from the desktops the server reads.

        Not the preserved `desk.json`: that file is the pre-migration copy and
        says nothing about how the desk looks now.
        """
        if self._desktops is None:
            return {}
        out: list[dict[str, Any]] = []
        for desktop in self._desktops.store.list():
            look = self._desktops.store.appearance(desktop["id"])
            out.append({
                "name": desktop["name"],
                "kind": desktop["kind"],
                "boards": self._desktops.boards(desktop["id"])["boards"],
                # The picture's digest names a file on this computer and means
                # nothing to a reader, so only the choice is reported.
                "appearance": {key: look[key] for key in ("wallpaper", "dim", "labels")},
            })
        return {"desktops": out}

    def _logs(self) -> dict[str, str]:
        out: dict[str, str] = {}
        logs_dir = self._config.logs_dir
        if not logs_dir.is_dir():
            return out
        for child in sorted(logs_dir.iterdir()):
            if not child.is_file() or child.suffix not in (".log", ".1", ".2", ".3", ".4", ".5"):
                continue
            try:
                lines = child.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            out[child.name] = scrub("\n".join(lines[-LOG_TAIL_LINES:]))
        return out

    # --------------------------------------------------------------- build

    def build(self) -> dict[str, Any]:
        self._dir.mkdir(parents=True, exist_ok=True)
        name = f"vela-support-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        target = self._dir / name
        sections = {
            "meta.json": _safe("meta", self._meta),
            "settings.json": _safe("settings", self._settings_view),
            "doctor.json": _safe("doctor", self._doctor_view),
            "apps.json": _safe("apps", self._apps, default=[]),
            "desk.json": _safe("desk", self._desk),
            "errors.json": _safe("errors", self._errors_view, default=[]),
            "automations.json": _safe("automations", self._automations_view),
        }
        logs = _safe("logs", self._logs, default={})
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("README.txt", README)
            for filename, payload in sections.items():
                bundle.writestr(filename, json.dumps(payload, indent=2, default=str))
            for filename, text in (logs or {}).items():
                bundle.writestr(f"logs/{filename}", text)
        self.prune()
        audit("support-bundle", f"file={name}")
        return {
            "name": name,
            "size": target.stat().st_size,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }

    def list(self) -> list[dict[str, Any]]:
        if not self._dir.is_dir():
            return []
        entries = []
        for child in sorted(self._dir.iterdir(), reverse=True):
            if not child.is_file() or not NAME_RE.match(child.name):
                continue
            stat = child.stat()
            entries.append(
                {
                    "name": child.name,
                    "size": stat.st_size,
                    "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(
                        timespec="seconds"
                    ),
                }
            )
        return entries

    def path(self, name: str) -> Path:
        """The file behind a bundle name, for a download response."""
        if not NAME_RE.match(name or ""):
            raise FileNotFoundError(name)
        candidate = (self._dir / name).resolve()
        if candidate.parent != self._dir.resolve() or not candidate.is_file():
            raise FileNotFoundError(name)
        return candidate

    def prune(self) -> None:
        cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
        for entry in self.list():
            try:
                if datetime.fromisoformat(entry["created_at"]) < cutoff:
                    (self._dir / entry["name"]).unlink()
            except (OSError, ValueError):
                continue


README = """\
Vela support bundle

What this is
------------
A snapshot of how this Vela server is set up and what has gone wrong on it
recently. It was created on this computer, and Vela did not send it anywhere.
You decide who sees it.

What is in it
-------------
meta.json         Vela's version, this computer's platform, and directory sizes.
settings.json     Your hub settings, with anything that looks like a password,
                  token or key replaced by [REDACTED].
doctor.json       The last health check run and what it found.
apps.json         Which apps are installed, their versions and whether they run.
desk.json         How each of your desktops is arranged.
errors.json       The most recent recorded errors.
automations.json  Whether automations can run, and how many ran today.
logs/             The last 500 lines of each log, with credentials redacted.

What is NOT in it
-----------------
Anything your apps saved, your chat history, your wallpapers, your Vela
password, app credentials, or the contents of any automation.

Before you share it
-------------------
Vela redacts what it can recognise. Have a look through anyway — a log line can
carry a file path or an address that you would rather not pass on.
"""
