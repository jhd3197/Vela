"""Companion apps: desktop apps on this computer that register themselves.

A companion keeps its own window, process and installer. When it starts it
writes `<data dir>/companions/<id>.json` (see `vela-contracts`'
`docs/COMPANIONS.md`) and serves three routes on a loopback port. Vela finds
the file, the owner connects it once, and from then on Vela polls it for desk
widget summaries and calls it to run one of its declared actions.

Everything here runs in one direction. Vela calls the companion; the companion
never calls Vela, never learns its address and never holds a Vela credential.
It gets no SDK session, no app storage and no agent access. What it sends is
data, checked by the same rules an SDK app's widget summary is.

The companions folder belongs to the user Vela runs as, so any program that user
runs can write there. Nothing in a registration is trusted until the owner
connects it, and the review is pinned: a later change to the executable, the
widgets or the actions stops Vela talking to it until the owner looks again.
Name, description, version and colour follow the file without asking.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import psutil
from jsonschema import Draft202012Validator

from .app_storage import AppServiceError
from .loop import BackgroundThread
from .logging_setup import audit
from .widgets import WidgetError, validate_summary

LOG = logging.getLogger(__name__)

#: Every companion's app id starts with this. A package id cannot contain a
#: double hyphen, so the two can never collide.
PREFIX = "pc--"

SCAN_INTERVAL_SECONDS = 5
#: How often a connected, running companion is asked for its widgets.
WIDGET_REFRESH_SECONDS = 15
MAX_FILES = 64
MAX_REGISTRATION_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_ICON_BYTES = 256 * 1024
MAX_MESSAGE = 200
STATUS_TIMEOUT = 2.0
WIDGETS_TIMEOUT = 5.0
ACTION_TIMEOUT = 10.0

_PNG = b"\x89PNG\r\n\x1a\n"
_SCHEMA = json.loads(
    (Path(__file__).resolve().parent / "assets/companion-v1.schema.json").read_text(encoding="utf-8")
)
_VALIDATOR = Draft202012Validator(_SCHEMA)


class CompanionError(AppServiceError):
    code = "companions.error"


def app_id_for(companion_id: str) -> str:
    return PREFIX + companion_id


def companion_id_of(app_id: str) -> str:
    return app_id[len(PREFIX):]


def read_registration(path: Path) -> dict[str, Any] | None:
    """One registration file, checked, or None when it is not one.

    A file that does not parse, is too big, fails the schema or is not named
    after its own id is ignored rather than reported: the folder is shared with
    every program the user runs, and a stray file is not the owner's problem.
    """
    try:
        if path.stat().st_size > MAX_REGISTRATION_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or next(_VALIDATOR.iter_errors(data), None) is not None:
        return None
    if data["id"] != path.stem:
        return None
    for key in ("widgets", "actions"):
        ids = [item["id"] for item in data.get(key, [])]
        if len(ids) != len(set(ids)):
            return None
    return data


def fingerprint(registration: dict[str, Any]) -> str:
    """What the owner reviews: where the program is and what it offers."""
    reviewed = {
        "executable": registration.get("executable"),
        "widgets": registration.get("widgets", []),
        "actions": registration.get("actions", []),
    }
    encoded = json.dumps(reviewed, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _pid_alive(pid: int) -> bool:
    try:
        return psutil.pid_exists(pid)
    except Exception:  # noqa: BLE001 - an unreadable process table is "not running"
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _public(registration: dict[str, Any]) -> dict[str, Any]:
    """A registration without the parts only Vela may see."""
    return {
        "id": registration["id"],
        "name": registration["name"],
        "description": registration.get("description", ""),
        "version": registration["version"],
        "color": registration.get("color"),
        "executable": registration.get("executable"),
        "widgets": registration.get("widgets", []),
        "actions": registration.get("actions", []),
        "fingerprint": fingerprint(registration),
    }


class Companions:
    """Found and connected companions, and the loop that keeps them current."""

    def __init__(self, config, storage, widgets, *, notifier=None, transport=None,
                 folder: Path | None = None, launcher=None):
        self._storage = storage
        self._widgets = widgets
        self._notifier = notifier
        self.folder = folder or (config.data_dir / "companions")
        self._icons = config.data_dir / "companion-icons"
        self._transport = transport
        self._launch = launcher or _launch_detached
        self._lock = threading.RLock()
        #: companion id -> what the last scan saw for it.
        self._present: dict[str, dict[str, Any]] = {}
        #: companion id -> {online, polled, error, attention}
        self._live: dict[str, dict[str, Any]] = {}
        with storage.connection() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS companions (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    version TEXT NOT NULL, color TEXT, executable TEXT,
                    widgets TEXT NOT NULL, actions TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, connected_at TEXT NOT NULL
                )"""
            )
        self.loop = BackgroundThread("companions.scan", SCAN_INTERVAL_SECONDS, self.scan)

    # ------------------------------------------------------------ identity --

    @staticmethod
    def owns(app_id: str) -> bool:
        return isinstance(app_id, str) and app_id.startswith(PREFIX)

    def _rows(self) -> dict[str, dict[str, Any]]:
        with self._storage.connection() as db:
            rows = db.execute("SELECT * FROM companions ORDER BY name, id").fetchall()
        out = {}
        for row in rows:
            record = dict(row)
            record["widgets"] = json.loads(record["widgets"])
            record["actions"] = json.loads(record["actions"])
            out[record["id"]] = record
        return out

    def _row(self, app_id: str) -> dict[str, Any]:
        if not self.owns(app_id):
            raise CompanionError(404, "Companion not found.")
        record = self._rows().get(companion_id_of(app_id))
        if record is None:
            raise CompanionError(404, "Companion not found.")
        return record

    # ---------------------------------------------------------------- HTTP --

    def _client(self, timeout: float) -> httpx.Client:
        # No proxies and no redirects: this only ever talks to loopback, and a
        # redirect would be a way to make Vela call somewhere else.
        return httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False,
                            transport=self._transport)

    def _call(self, registration: dict[str, Any], method: str, path: str, *,
              timeout: float, body: Any = None) -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {registration['token']}"}
        url = registration["endpoint"] + path
        with self._client(timeout) as client:
            with client.stream(method, url, headers=headers, json=body) as response:
                data = b""
                for chunk in response.iter_bytes():
                    data += chunk
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise CompanionError(502, "The app sent more than Vela reads.")
                status = response.status_code
        try:
            return status, json.loads(data) if data else None
        except ValueError:
            return status, None

    def _online(self, registration: dict[str, Any]) -> bool:
        try:
            status, body = self._call(registration, "GET", "/vela/v1/status", timeout=STATUS_TIMEOUT)
        except (httpx.HTTPError, CompanionError):
            return False
        return status == 200 and isinstance(body, dict) and body.get("id") == registration["id"]

    # ---------------------------------------------------------------- scan --

    def scan(self) -> None:
        """Read the folder, check each connected companion, refresh widgets."""
        present: dict[str, dict[str, Any]] = {}
        if self.folder.is_dir():
            for path in sorted(self.folder.glob("*.json"))[:MAX_FILES]:
                registration = read_registration(path)
                if registration is not None and _pid_alive(registration["pid"]):
                    present[registration["id"]] = registration
        rows = self._rows()
        with self._lock:
            self._present = present
        for companion_id, row in rows.items():
            registration = present.get(companion_id)
            live = self._live.setdefault(companion_id, {"online": False, "polled": 0.0,
                                                        "error": None, "attention": set()})
            if registration is None or fingerprint(registration) != row["fingerprint"]:
                live["online"] = False
                continue
            self._follow(row, registration)
            live["online"] = self._online(registration)
            if live["online"] and time.monotonic() - live["polled"] >= WIDGET_REFRESH_SECONDS:
                self._pull_widgets(row, registration)

    def refresh(self, app_id: str) -> None:
        """Ask one companion for its widgets now, e.g. after an action."""
        row = self._row(app_id)
        registration = self._current(row)
        if registration is not None:
            self._pull_widgets(row, registration)

    def _current(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """The registration Vela may talk to for this connection, if any."""
        with self._lock:
            registration = self._present.get(row["id"])
        if registration is None or fingerprint(registration) != row["fingerprint"]:
            return None
        return registration

    def _follow(self, row: dict[str, Any], registration: dict[str, Any]) -> None:
        """Keep the unreviewed details in step with the file."""
        fresh = {
            "name": registration["name"],
            "description": registration.get("description", ""),
            "version": registration["version"],
            "color": registration.get("color"),
        }
        if all(row[key] == value for key, value in fresh.items()):
            return
        with self._storage.connection() as db:
            db.execute("UPDATE companions SET name=?, description=?, version=?, color=? WHERE id=?",
                       (*fresh.values(), row["id"]))
        row.update(fresh)

    def _pull_widgets(self, row: dict[str, Any], registration: dict[str, Any]) -> None:
        live = self._live.setdefault(row["id"], {"online": False, "polled": 0.0,
                                                 "error": None, "attention": set()})
        live["polled"] = time.monotonic()
        declared = {item["id"] for item in row["widgets"]}
        if not declared:
            return
        try:
            status, body = self._call(registration, "GET", "/vela/v1/widgets", timeout=WIDGETS_TIMEOUT)
        except (httpx.HTTPError, CompanionError) as exc:
            live["error"] = f"Could not read its widgets: {exc}"
            return
        if status != 200 or not isinstance(body, dict) or not isinstance(body.get("widgets"), dict):
            live["error"] = f"Its widgets answer was not usable (HTTP {status})."
            return
        actions = {item["id"] for item in row["actions"]}
        live["error"] = None
        app_id = app_id_for(row["id"])
        raised = []
        for widget_id, payload in body["widgets"].items():
            if widget_id not in declared:
                continue
            try:
                summary = validate_summary(payload)
            except WidgetError as exc:
                live["error"] = f"Widget {widget_id}: {exc.detail}"
                continue
            if any(item["action"] not in actions for item in summary.get("actions", [])):
                live["error"] = f"Widget {widget_id} names an action it did not declare."
                continue
            self._widgets.put(app_id, widget_id, summary)
            if summary.get("attention") and widget_id not in live["attention"]:
                raised.append((widget_id, summary))
            if summary.get("attention"):
                live["attention"].add(widget_id)
            else:
                live["attention"].discard(widget_id)
        for widget_id, summary in raised:
            self._announce(row, widget_id, summary)

    def _announce(self, row: dict[str, Any], widget_id: str, summary: dict[str, Any]) -> None:
        """Tell the owner's phone, once, that a companion is waiting for them."""
        if self._notifier is None:
            return
        config = self._notifier.config()
        if not config["server"] or not config["topic"] or config["events"].get("companions") is False:
            return
        name = next((item["name"] for item in row["widgets"] if item["id"] == widget_id), widget_id)
        detail = summary.get("caption") or summary.get("value") or name
        try:
            asyncio.run(self._notifier.publish(f"{row['name']} needs you", detail,
                                               tags=["bell"], priority=4, kind="companion"))
        except Exception as exc:  # noqa: BLE001 - a notification is a courtesy
            LOG.info("companions: could not notify for %s: %s", row["id"], exc)

    # ------------------------------------------------------------- listing --

    def found(self) -> list[dict[str, Any]]:
        """Running companions the owner has not connected."""
        rows = self._rows()
        with self._lock:
            present = dict(self._present)
        return [_public(registration) for companion_id, registration in sorted(present.items())
                if companion_id not in rows]

    def _state(self, row: dict[str, Any]) -> tuple[str, str]:
        with self._lock:
            registration = self._present.get(row["id"])
        live = self._live.get(row["id"], {})
        if registration is None:
            return "offline", f"{row['name']} is not running on this computer."
        if fingerprint(registration) != row["fingerprint"]:
            return "changed", f"{row['name']} changed what it offers. Review it to keep using it."
        if not live.get("online"):
            return "offline", f"{row['name']} is running but not answering."
        return "online", live.get("error") or ""

    def summary(self, row: dict[str, Any]) -> dict[str, Any]:
        state, detail = self._state(row)
        executable = row.get("executable")
        with self._lock:
            registration = self._present.get(row["id"])
        pending = None
        if state == "changed" and registration is not None:
            pending = _public(registration)
        app_id = app_id_for(row["id"])
        return {
            "id": app_id,
            "kind": "companion",
            "profile": "companion",
            "schemaVersion": None,
            "name": row["name"],
            "version": row["version"],
            "description": row["description"],
            "category": "companion",
            "author": "",
            "color": row.get("color") or "#7b4dff",
            "installed": True,
            "running": state == "online",
            "supported": True,
            "runtime": "companion",
            "runtimes": ["companion"],
            "isolation": "trusted-native",
            "capabilities": ["widgets"] if row["widgets"] else [],
            "widgets": row["widgets"],
            "unavailableCapabilities": [],
            "view": {"surface": "companion", "chrome": "hub"},
            "url": f"/app/{app_id}",
            "iconUrl": f"/api/apps/{app_id}/icon" if self.icon_path(app_id) else None,
            "companion": {
                "id": row["id"],
                "state": state,
                "detail": detail,
                "executable": executable,
                "canStart": state == "offline" and registration is None
                            and bool(executable) and Path(executable).is_file(),
                "actions": row["actions"],
                "connectedAt": row["connected_at"],
                "pending": pending,
            },
        }

    def list_apps(self) -> list[dict[str, Any]]:
        return [self.summary(row) for row in self._rows().values()]

    def get(self, app_id: str) -> dict[str, Any]:
        return self.summary(self._row(app_id))

    def declared_widgets(self, app_id: str) -> list[dict[str, Any]]:
        try:
            return list(self._row(app_id)["widgets"])
        except CompanionError:
            return []

    def action_ids(self, app_id: str) -> list[str]:
        try:
            return sorted(item["id"] for item in self._row(app_id)["actions"])
        except CompanionError:
            return []

    def icon_path(self, app_id: str) -> Path | None:
        if not self.owns(app_id):
            return None
        path = self._icons / f"{companion_id_of(app_id)}.png"
        return path if path.is_file() else None

    # ----------------------------------------------------------- the owner --

    def connect(self, companion_id: str, reviewed: str) -> dict[str, Any]:
        """Connect a found companion, exactly as the owner saw it."""
        with self._lock:
            registration = self._present.get(companion_id)
        if registration is None:
            raise CompanionError(404, "That app is no longer running on this computer.")
        if fingerprint(registration) != reviewed:
            raise CompanionError(409, "That app changed while you were looking. Review it again.")
        if companion_id in self._rows():
            raise CompanionError(409, "That app is already connected.")
        self._copy_icon(registration)
        with self._storage.connection() as db:
            db.execute(
                "INSERT INTO companions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (companion_id, registration["name"], registration.get("description", ""),
                 registration["version"], registration.get("color"), registration.get("executable"),
                 json.dumps(registration.get("widgets", [])), json.dumps(registration.get("actions", [])),
                 reviewed, _now()),
            )
        audit("companion", f"connected id={companion_id}")
        self._settle(companion_id)
        return self.get(app_id_for(companion_id))

    def review(self, app_id: str, reviewed: str) -> dict[str, Any]:
        """Accept what a connected companion offers now."""
        row = self._row(app_id)
        with self._lock:
            registration = self._present.get(row["id"])
        if registration is None:
            raise CompanionError(409, f"{row['name']} is not running, so there is nothing new to review.")
        if fingerprint(registration) != reviewed:
            raise CompanionError(409, "That app changed while you were looking. Review it again.")
        widgets = registration.get("widgets", [])
        self._copy_icon(registration)
        with self._storage.connection() as db:
            db.execute(
                "UPDATE companions SET executable=?, widgets=?, actions=?, fingerprint=? WHERE id=?",
                (registration.get("executable"), json.dumps(widgets),
                 json.dumps(registration.get("actions", [])), reviewed, row["id"]),
            )
        # A widget it no longer offers must not stay on the desk.
        self._widgets.forget(app_id, keep={item["id"] for item in widgets})
        audit("companion", f"reviewed id={row['id']}")
        self._settle(row["id"])
        return self.get(app_id)

    def _settle(self, companion_id: str) -> None:
        """Check a just-connected companion straight away rather than in 5s."""
        row = self._rows()[companion_id]
        registration = self._current(row)
        if registration is None:
            return
        live = self._live.setdefault(companion_id, {"online": False, "polled": 0.0,
                                                    "error": None, "attention": set()})
        live["online"] = self._online(registration)
        if live["online"]:
            self._pull_widgets(row, registration)

    def _copy_icon(self, registration: dict[str, Any]) -> None:
        name = registration.get("icon")
        target = self._icons / f"{registration['id']}.png"
        if not name:
            return
        source = (self.folder / name).resolve()
        try:
            if not source.is_relative_to(self.folder.resolve()) or not source.is_file():
                return
            if source.stat().st_size > MAX_ICON_BYTES:
                return
            data = source.read_bytes()
        except OSError:
            return
        if not data.startswith(_PNG):
            return
        self._icons.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, target)

    def remove(self, app_id: str) -> dict[str, Any]:
        row = self._row(app_id)
        with self._storage.connection() as db:
            db.execute("DELETE FROM companions WHERE id=?", (row["id"],))
        self._widgets.forget(app_id)
        self._live.pop(row["id"], None)
        icon = self._icons / f"{row['id']}.png"
        icon.unlink(missing_ok=True)
        audit("companion", f"removed id={row['id']}")
        return {"id": app_id, "installed": False}

    def run_action(self, app_id: str, action_id: str) -> dict[str, Any]:
        row = self._row(app_id)
        action = next((item for item in row["actions"] if item["id"] == action_id), None)
        if action is None:
            raise CompanionError(404, f"{row['name']} has no action called {action_id!r}.")
        registration = self._current(row)
        if registration is None or not self._live.get(row["id"], {}).get("online"):
            state, detail = self._state(row)
            raise CompanionError(409, detail or f"{row['name']} is not running on this computer.")
        try:
            status, body = self._call(registration, "POST", f"/vela/v1/actions/{action_id}",
                                      timeout=ACTION_TIMEOUT, body={})
        except httpx.TimeoutException as exc:
            raise CompanionError(504, f"{row['name']} did not answer in time.") from exc
        except httpx.HTTPError as exc:
            raise CompanionError(502, f"Could not reach {row['name']}.") from exc
        message = ""
        if isinstance(body, dict):
            text = body.get("message") if status < 400 else body.get("error")
            if isinstance(text, str):
                message = text.strip()[:MAX_MESSAGE]
        audit("companion", f"action id={row['id']} action={action_id} status={status}")
        if status >= 400:
            raise CompanionError(422 if status < 500 else 502,
                                 message or f"{row['name']} could not {action['title'].lower()}.")
        self._pull_widgets(row, registration)
        return {"ok": True, "message": message}

    def start(self, app_id: str) -> dict[str, Any]:
        """Start a companion that is not running, from its reviewed executable."""
        row = self._row(app_id)
        summary = self.summary(row)
        if summary["companion"]["state"] != "offline" or self._current(row) is not None:
            return summary
        executable = row.get("executable")
        if not executable or not Path(executable).is_file():
            raise CompanionError(409, f"Vela does not know where {row['name']} is installed. Open it on this computer.")
        try:
            self._launch(executable)
        except OSError as exc:
            raise CompanionError(502, f"Could not start {row['name']}: {exc}") from exc
        audit("companion", f"started id={row['id']}")
        return {**summary, "starting": True}


def _launch_detached(executable: str) -> None:
    """Start a program so that it outlives Vela and owns no Vela handles."""
    kwargs: dict[str, Any] = {
        "cwd": str(Path(executable).parent),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    if sys.platform == "darwin" and ".app/Contents/MacOS/" in executable:
        # Through LaunchServices, so the app gets its own Dock entry.
        bundle = executable.split(".app/Contents/MacOS/")[0] + ".app"
        subprocess.Popen(["open", "-a", bundle], **kwargs)
        return
    subprocess.Popen([executable], **kwargs)


__all__ = ["Companions", "CompanionError", "PREFIX", "app_id_for", "fingerprint",
           "read_registration"]
