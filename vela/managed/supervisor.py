"""Owning a managed app's process: starting it, proving it, and stopping it all.

The distinction this module exists to keep is between *intent* and *observation*.
Whether the user wants an app running is a decision, stored by
`vela/managed/store.py` and unchanged by anything that happens to a process.
Whether the app is running is a fact, re-measured here, and never inferred from
having asked for it.

Four rules shape everything below:

1. A listening socket is not readiness. The declared HTTP probe has to answer
   the way the package said before the app is published as ready, because
   Memos binds its port well before it has finished migrating its database.
2. A PID is not an identity. Every record carries the creation-time token the
   platform gave at launch, so a reused number is a dead process rather than
   somebody else's to kill.
3. A stop takes the tree. A service that starts a helper leaves an orphan
   otherwise, and the next start collides with the port it is still holding.
4. A crash loop is bounded. Restarts back off and run out, and what is shown
   afterwards is the reason it stopped rather than a badge that says Failed
   while the machine keeps trying for ever.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..config import write_json_atomic
from ..errors_http import Conflict, Upstream
from ..state import pid_alive, pid_ctime
from .contract import ManagedManifest

LOG = logging.getLogger(__name__)

#: How often the reconciler re-measures processes and restarts what it owns.
RECONCILE_INTERVAL_SECONDS = 5.0

#: How long a readiness probe waits for one response. Separate from the total
#: start deadline: a server that accepts and then stalls must not swallow the
#: whole budget in a single request.
PROBE_TIMEOUT_SECONDS = 5.0

#: Ports this host will try before giving up on finding a free one.
PORT_ATTEMPTS = 20


@dataclass(frozen=True, slots=True)
class ServiceState:
    """What is true about one managed service right now."""

    app_id: str
    state: str
    pid: int | None = None
    port: int | None = None
    generation: int | None = None
    release_id: str | None = None
    started_at: str | None = None
    detail: str = ""
    restarts: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "pid": self.pid,
            "port": self.port,
            "generation": self.generation,
            "releaseId": self.release_id,
            "startedAt": self.started_at,
            "detail": self.detail,
            "restarts": self.restarts,
        }


class StartFailed(Upstream):
    """The service did not come up, with what the log said about why."""

    code = "managed.start_failed"


def free_port() -> int:
    """A loopback port nothing is listening on right now.

    Bind-and-release has a race with anything else on the machine that binds in
    the same instant. It is used anyway, because the alternative -- handing the
    child a socket -- is not something an arbitrary upstream server supports.
    The race is answered where it matters instead: a start that fails because
    the port went away is retried on a new one, and readiness is checked against
    the app's own response rather than against the socket being open.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


class ManagedSupervisor:
    """Starts, watches and stops the processes behind managed web apps."""

    def __init__(self, store, runner, *, logs_dir: Path, public_url):
        self.store = store
        self.runner = runner
        self.logs_dir = Path(logs_dir)
        #: Called with an app id to get the address the app is published on, so
        #: an upstream server that generates absolute links makes the right ones.
        self._public_url = public_url
        self.path = store.data_dir / "managed-services.json"
        self.lock = threading.RLock()
        #: Crash bookkeeping, in memory: a restart budget is about this run of
        #: Vela, not about a count that survives across weeks.
        self._restarts: dict[str, list[float]] = {}
        self._failures: dict[str, str] = {}
        #: Set while a start or stop this process asked for is in flight, so the
        #: reconciler does not decide a starting service has crashed.
        self._busy: set[str] = set()
        #: One lock per app, held for a whole start or stop. Checking that
        #: nothing is running and then launching are two steps, and four Open
        #: clicks arriving together would otherwise all pass the check and all
        #: launch -- four servers fighting over one data directory.
        self._app_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------ recording --

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        write_json_atomic(self.path, data)

    def record(self, app_id: str) -> dict[str, Any] | None:
        entry = self._read().get(app_id)
        return entry if isinstance(entry, dict) else None

    def _save(self, app_id: str, entry: dict[str, Any] | None) -> None:
        with self.lock:
            data = self._read()
            if entry is None:
                data.pop(app_id, None)
            else:
                data[app_id] = entry
            self._write(data)

    def _app_lock(self, app_id: str) -> threading.Lock:
        with self.lock:
            return self._app_locks.setdefault(app_id, threading.Lock())

    def log_path(self, app_id: str) -> Path:
        """One log per app, in the directory the dashboard's log reader knows."""
        return self.logs_dir / f"managed-{app_id}.log"

    # -------------------------------------------------------------- reading --

    def alive(self, app_id: str) -> dict[str, Any] | None:
        """The recorded process if it is still the one that was launched."""
        entry = self.record(app_id)
        if not entry:
            return None
        if not pid_alive(entry.get("pid", -1), entry.get("pid_ctime")):
            return None
        return entry

    def state(self, app_id: str, record: dict[str, Any] | None = None) -> ServiceState:
        """What to show for this app, measured rather than remembered."""
        entry = self.alive(app_id)
        failure = self._failures.get(app_id)
        if entry is None:
            if app_id in self._busy:
                return ServiceState(app_id, "starting", detail="Starting")
            return ServiceState(
                app_id,
                "failed" if failure else "stopped",
                detail=failure or "",
                restarts=len(self._restarts.get(app_id, [])),
            )
        state = "ready" if entry.get("ready") else "starting"
        return ServiceState(
            app_id,
            state,
            pid=entry.get("pid"),
            port=entry.get("port"),
            generation=entry.get("generation"),
            release_id=entry.get("release_id"),
            started_at=entry.get("started_at"),
            detail=failure or "",
            restarts=len(self._restarts.get(app_id, [])),
        )

    def endpoint(self, app_id: str) -> tuple[str, int] | None:
        """`(host, port)` for the gateway, or None when nothing is serving."""
        entry = self.alive(app_id)
        if not entry or not entry.get("ready"):
            return None
        return entry.get("bind", "127.0.0.1"), int(entry["port"])

    # -------------------------------------------------------------- command --

    def _environment(self, manifest: ManagedManifest, values: dict[str, str]) -> dict[str, str]:
        """A minimal environment, plus what the package declared.

        Vela's own environment is not inherited wholesale. A child that gets
        `VELA_DATA_DIR` or an API token in its environment has been handed
        something nobody reviewed, and it would show up in every crash dump the
        upstream project collects.
        """
        keep = ("SystemRoot", "windir", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS",
                "PROCESSOR_ARCHITECTURE", "TZ", "LANG", "LC_ALL", "HOME", "USERPROFILE")
        env: dict[str, str] = {}
        for name in keep:
            if name in os.environ:
                env[name] = os.environ[name]
        # A PATH is kept because a program may call a system tool, but it is the
        # one this process was started with rather than an app-supplied one.
        env["PATH"] = os.environ.get("PATH", "")
        temporary = values["dataDir"]
        env["TMP"] = env["TEMP"] = temporary
        for name, value in manifest.environment.items():
            env[name] = _substitute(value, values)
        return env

    def _argv(self, executable: Path, manifest: ManagedManifest, values: dict[str, str]) -> list[str]:
        return [str(executable)] + [
            _substitute(argument, values) for argument in manifest.command_args
        ]

    # ---------------------------------------------------------------- start --

    def start(
        self,
        app_id: str,
        manifest: ManagedManifest,
        *,
        code: Path,
        data: Path,
        executable: Path,
        generation: int,
        release_id: str,
        reason: str = "requested",
    ) -> ServiceState:
        """Start the service and wait for its own readiness answer."""
        with self._app_lock(app_id):
            if self.alive(app_id):
                # Not an error: two Open clicks are one launch, and the second
                # answers with the running service rather than a second process.
                return self.state(app_id)
            with self.lock:
                self._busy.add(app_id)
            try:
                return self._start(
                    app_id, manifest, code=code, data=data, executable=executable,
                    generation=generation, release_id=release_id, reason=reason,
                )
            finally:
                with self.lock:
                    self._busy.discard(app_id)

    def _start(self, app_id, manifest, *, code, data, executable, generation, release_id, reason):
        endpoint = manifest.endpoint
        bind = endpoint["bind"]
        log_path = self.log_path(app_id)
        last_error = ""
        for attempt in range(3):
            port = free_port()
            values = {
                "port": str(port),
                "host": bind,
                "dataDir": str(data),
                "codeDir": str(code),
                "appId": app_id,
                "publicUrl": self._public_url(app_id),
            }
            argv = self._argv(executable, manifest, values)
            env = self._environment(manifest, values)
            working = data if manifest.working_directory == "data" else code
            _append(log_path, f"\n=== {app_id}: starting ({reason}) on port {port} ===\n")
            try:
                pid, ctime = self.runner.launch_service(argv, working, log_path, env)
            except NotImplementedError as exc:
                raise Conflict(str(exc), code="managed.platform_unsupported") from exc
            except OSError as exc:
                raise StartFailed(
                    f"{manifest.name} could not be started: {exc}"
                ) from exc
            entry = {
                "pid": pid,
                "pid_ctime": ctime if ctime is not None else pid_ctime(pid),
                "port": port,
                "bind": bind,
                "generation": generation,
                "release_id": release_id,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "ready": False,
            }
            self._save(app_id, entry)
            ready, detail = self._await_ready(app_id, manifest, entry)
            if ready:
                entry["ready"] = True
                self._save(app_id, entry)
                self._failures.pop(app_id, None)
                return self.state(app_id)
            last_error = detail
            self._stop_process(entry, manifest)
            self._save(app_id, None)
            if "port" not in detail.lower() or attempt == 2:
                break
        self._failures[app_id] = last_error
        raise StartFailed(last_error or f"{manifest.name} did not become ready")

    def _await_ready(self, app_id, manifest, entry) -> tuple[bool, str]:
        readiness = manifest.readiness
        deadline = time.monotonic() + float(readiness["startTimeoutSeconds"])
        interval = float(readiness["intervalSeconds"])
        expected = set(readiness["expectStatus"])
        url = f"http://{_host_for_url(entry['bind'])}:{entry['port']}{readiness['path']}"
        last = "no response yet"
        with httpx.Client(timeout=PROBE_TIMEOUT_SECONDS, trust_env=False,
                          follow_redirects=False) as client:
            while time.monotonic() < deadline:
                if not pid_alive(entry["pid"], entry["pid_ctime"]):
                    return False, (
                        f"{manifest.name} stopped while starting. "
                        f"{_tail(self.log_path(app_id))}"
                    )
                try:
                    response = client.get(url)
                except httpx.HTTPError as exc:
                    last = f"{type(exc).__name__}"
                else:
                    if response.status_code in expected:
                        return True, ""
                    last = f"answered HTTP {response.status_code}"
                time.sleep(interval)
        return False, (
            f"{manifest.name} did not answer {readiness['path']} within "
            f"{readiness['startTimeoutSeconds']:g}s ({last}). {_tail(self.log_path(app_id))}"
        )

    # ----------------------------------------------------------------- stop --

    def stop(self, app_id: str, manifest: ManagedManifest | None = None) -> ServiceState:
        """Stop the service and everything it started. Safe to call when stopped."""
        with self._app_lock(app_id):
            with self.lock:
                self._busy.add(app_id)
            try:
                entry = self.record(app_id)
                if entry:
                    outcome = self._stop_process(entry, manifest)
                    _append(self.log_path(app_id), f"=== {app_id}: {outcome} ===\n")
                self._save(app_id, None)
                # A deliberate stop clears the crash history. The budget exists
                # to end a loop, and starting again by hand is the person saying
                # the cause has been dealt with.
                self._restarts.pop(app_id, None)
                self._failures.pop(app_id, None)
            finally:
                with self.lock:
                    self._busy.discard(app_id)
        # Measured after the busy mark is gone, so a stop reports "stopped"
        # rather than the "starting" that mark otherwise means.
        return self.state(app_id)

    def _stop_process(self, entry: dict[str, Any], manifest: ManagedManifest | None) -> str:
        pid = entry.get("pid")
        if not pid or not pid_alive(pid, entry.get("pid_ctime")):
            # Either it exited, or the number now belongs to something else.
            # Both mean the same thing here: there is nothing of ours to signal.
            return "exited"
        if entry.get("pid_ctime") is None:
            # A record with no creation token cannot prove that this number is
            # still the process we launched, and a wrong guess terminates
            # somebody else's program. Leaving a possible orphan is the smaller
            # mistake, and it is named in the log rather than hidden.
            LOG.warning(
                "managed: process %s has no creation token, so it was not signalled", pid
            )
            return "unverified"
        timeout = float(manifest.lifetime["stopTimeoutSeconds"]) if manifest else 15.0
        try:
            return self.runner.stop_service(pid, timeout=timeout)
        except NotImplementedError:
            self.runner.stop(pid)
            return "stopped"

    def stop_all(self, manifests: dict[str, ManagedManifest]) -> list[str]:
        """Stop every running managed service. Used on an orderly shutdown."""
        stopped = []
        for app_id in list(self._read()):
            try:
                self.stop(app_id, manifests.get(app_id))
                stopped.append(app_id)
            except Exception:  # noqa: BLE001 - a shutdown stops what it can
                LOG.exception("managed: could not stop %s", app_id)
        return stopped

    def forget(self, app_id: str) -> None:
        """Drop the process record, keeping what is known about its crashes.

        Called by the reconciler between a crash and the restart it is about to
        attempt. Clearing the restart history here is what would make the budget
        meaningless: every crash would look like the first one, and the loop
        would never end.
        """
        self._save(app_id, None)
        self._failures.pop(app_id, None)

    def forget_entirely(self, app_id: str) -> None:
        """Drop everything about an app, for one that is being removed."""
        self.forget(app_id)
        self._restarts.pop(app_id, None)
        with self.lock:
            self._app_locks.pop(app_id, None)

    # ----------------------------------------------------------- reconciling --

    def adopt(self, app_id: str, manifest: ManagedManifest, generation: int) -> bool:
        """Re-attach to a process this Vela started before it restarted.

        Ownership is proved twice: the recorded creation-time token must still
        match the live process, and the app must answer its own readiness probe
        on the recorded port. Either check failing means the record is stale --
        a reused PID, or somebody else's server on that port -- and the record
        is dropped rather than adopted.
        """
        entry = self.record(app_id)
        if not entry:
            return False
        if not pid_alive(entry.get("pid", -1), entry.get("pid_ctime")):
            self._save(app_id, None)
            return False
        if entry.get("generation") != generation:
            # The code changed while this process was not running. Whatever is
            # alive was reviewed against something else.
            self._stop_process(entry, manifest)
            self._save(app_id, None)
            return False
        ready, _ = self._probe_once(manifest, entry)
        if not ready:
            self._stop_process(entry, manifest)
            self._save(app_id, None)
            return False
        entry["ready"] = True
        self._save(app_id, entry)
        return True

    def _probe_once(self, manifest: ManagedManifest, entry: dict[str, Any]) -> tuple[bool, str]:
        readiness = manifest.readiness
        url = f"http://{_host_for_url(entry.get('bind', '127.0.0.1'))}:{entry['port']}{readiness['path']}"
        try:
            with httpx.Client(timeout=PROBE_TIMEOUT_SECONDS, trust_env=False,
                              follow_redirects=False) as client:
                response = client.get(url)
        except httpx.HTTPError as exc:
            return False, type(exc).__name__
        return response.status_code in set(readiness["expectStatus"]), f"HTTP {response.status_code}"

    def crashed(self, app_id: str) -> bool:
        """A service this process started that is no longer there."""
        return bool(self.record(app_id)) and not self.alive(app_id)

    def may_restart(self, app_id: str, manifest: ManagedManifest) -> tuple[bool, float, str]:
        """Whether to restart after a crash, how long to wait, and why not.

        The budget is per window rather than per lifetime, so an app that
        crashed twice this morning and was fixed is not refused this afternoon.
        Running out is reported once and then left alone: a Failed badge over a
        machine that is still restarting every two seconds is the worst of both.
        """
        policy = manifest.lifetime["restart"]
        window = float(policy["windowSeconds"])
        limit = int(policy["maxRetries"])
        moment = time.monotonic()
        history = [stamp for stamp in self._restarts.get(app_id, []) if stamp > moment - window]
        self._restarts[app_id] = history
        if len(history) >= limit:
            return False, 0.0, (
                f"{manifest.name} stopped {len(history)} times in the last "
                f"{window:g} seconds and was not started again. Open its log to see why, "
                "then start it when the cause is fixed."
            )
        delay = float(policy["backoffSeconds"]) * (2 ** len(history))
        return True, min(delay, 60.0), ""

    def note_restart(self, app_id: str) -> None:
        self._restarts.setdefault(app_id, []).append(time.monotonic())

    def note_failure(self, app_id: str, message: str) -> None:
        self._failures[app_id] = message

    def failure(self, app_id: str) -> str:
        return self._failures.get(app_id, "")

    def busy(self, app_id: str) -> bool:
        return app_id in self._busy


def _substitute(value: str, values: dict[str, str]) -> str:
    """Fill the bounded placeholder set. Anything else was refused at validation."""
    for name, replacement in values.items():
        value = value.replace("{" + name + "}", replacement)
    return value


def _host_for_url(bind: str) -> str:
    return f"[{bind}]" if ":" in bind else bind


def _append(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:  # pragma: no cover - a log that cannot be written is not fatal
        pass


def _tail(path: Path, lines: int = 12) -> str:
    """The end of the app's log, for a failure message worth reading."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    recent = [line for line in content[-lines:] if line.strip()]
    return ("Last log lines: " + " / ".join(recent)) if recent else ""
