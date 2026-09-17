"""Supervision of the browser an agent desktop renders in.

Vela owns this process's whole lifetime. It starts hidden, speaks the versioned
line protocol frozen in the plan's Phase 0 over its own pipes, receives no Vela
credential and no network address, and is stopped when Vela stops. If it dies,
the desktops it was carrying are reported as interrupted rather than quietly
restarted: a browser session cannot be recreated, and pretending otherwise would
mean an agent believing it is still looking at a page that is gone.

Modelled on `vela/automations/worker.py`, which has been supervising a Node
process in this repository for a while. Two workers that fail the same way are
two workers a maintainer only has to learn once.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import REPO_ROOT

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1024 * 1024
START_TIMEOUT_SECONDS = 45
STOP_TIMEOUT_SECONDS = 10

#: The worker exits on its own if Vela goes quiet for longer than its own limit,
#: so a force-killed server cannot leave a browser running. Ping well inside it.
HEARTBEAT_SECONDS = 25

BUNDLED_NODE_DIR = "node-runtime"
WORKER_DIR = Path("scripts/browser-worker")


class RuntimeUnavailable(RuntimeError):
    """The browser runtime is missing, or refused to start."""


@dataclass(frozen=True)
class RuntimeInfo:
    node: str
    protocol: int
    browser_available: bool
    browser_reason: str | None

    def as_dict(self):
        return {
            "node": self.node,
            "protocol": self.protocol,
            "browser": {"available": self.browser_available, "reason": self.browser_reason},
        }


def node_executable() -> str | None:
    """The Node runtime to use: an explicit override, the bundled copy, then PATH."""
    override = os.environ.get("VELA_BROWSER_NODE") or os.environ.get("VELA_AUTOMATION_NODE")
    if override:
        return override if Path(override).is_file() else None
    name = "node.exe" if platform.system() == "Windows" else "node"
    bundled = REPO_ROOT / BUNDLED_NODE_DIR / name
    if bundled.is_file():
        return str(bundled)
    if platform.system() != "Windows":
        bundled_bin = REPO_ROOT / BUNDLED_NODE_DIR / "bin" / name
        if bundled_bin.is_file():
            return str(bundled_bin)
    return shutil.which("node")


def worker_script() -> Path:
    return REPO_ROOT / WORKER_DIR / "src/index.mjs"


def provenance() -> dict:
    path = REPO_ROOT / WORKER_DIR / "provenance.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def availability() -> dict:
    """Whether agent desktops can run on this computer, in plain words.

    Answered before any work is accepted rather than discovered halfway through
    a task. Each reason says what to do about it, because "unavailable" on its
    own is not something anybody can act on.
    """
    script = worker_script()
    node = node_executable()
    record = provenance()
    installed = (REPO_ROOT / WORKER_DIR / "node_modules/playwright-core").is_dir()
    browser = record.get("browser") or {}
    if not script.is_file():
        detail = "The agent desktop runtime was not installed with this copy of Vela."
    elif node is None:
        detail = (
            "Vela could not find its bundled runtime. Reinstall the Vela download, "
            "or install Node.js 20 or later."
        )
    elif not installed:
        detail = (
            "The agent desktop runtime has no browser engine installed. Run "
            "python scripts/setup-browser-worker.py from a Vela checkout."
        )
    elif not browser.get("present"):
        detail = (
            "The browser this Vela was built against is not on this computer. Run "
            "python scripts/setup-browser-worker.py to fetch it."
        )
    else:
        detail = None
    return {
        "available": detail is None,
        "detail": detail,
        "node": node,
        "provenance": record,
    }


class BrowserRuntime:
    """One supervised worker process, holding one browser per agent desktop."""

    def __init__(self, frames_dir: Path, *, log=None):
        self._frames_dir = Path(frames_dir)
        self._log = log or (lambda message: None)
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._heartbeat: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future] = {}
        self._ready: asyncio.Future | None = None
        self._counter = 0
        self.info: RuntimeInfo | None = None
        #: Desktops this worker currently holds a browser for. Volatile by
        #: design: a runtime is never restored, only started again.
        self.desktops: set[str] = set()

    @property
    def running(self) -> bool:
        return bool(self._process and self._process.returncode is None)

    # ---------------------------------------------------------- lifecycle --

    async def start(self) -> RuntimeInfo:
        async with self._lock:
            if self.running:
                return self.info
            await self._spawn()
            return self.info

    async def _spawn(self):
        state = availability()
        if not state["available"]:
            raise RuntimeUnavailable(state["detail"])
        self._frames_dir.mkdir(parents=True, exist_ok=True)
        creation = 0
        if platform.system() == "Windows":
            # Keep the console window hidden; Vela may be running from the tray.
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        script = worker_script()
        self._ready = asyncio.get_running_loop().create_future()
        try:
            self._process = await asyncio.create_subprocess_exec(
                state["node"],
                str(script),
                str(self._frames_dir),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(script.parent.parent),
                creationflags=creation,
                # A minimal environment: no Vela token, no data directory, no
                # proxy. What the browser may reach is decided by the policy it
                # is given, not by what it inherited.
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "NODE_ENV": "production",
                    "SystemRoot": os.environ.get("SystemRoot", ""),
                    "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
                    "HOME": os.environ.get("HOME", ""),
                    "USERPROFILE": os.environ.get("USERPROFILE", ""),
                    **(
                        {"VELA_BROWSER_SILENCE_MS": os.environ["VELA_BROWSER_SILENCE_MS"]}
                        if "VELA_BROWSER_SILENCE_MS" in os.environ
                        else {}
                    ),
                },
            )
        except OSError as exc:
            raise RuntimeUnavailable(f"Vela could not start the browser runtime: {exc}") from exc
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        try:
            self.info = await asyncio.wait_for(self._ready, START_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            await self._terminate()
            raise RuntimeUnavailable("The browser runtime did not start in time.") from exc
        if self.info.protocol != PROTOCOL_VERSION:
            await self._terminate()
            raise RuntimeUnavailable(
                f"The browser runtime speaks protocol {self.info.protocol}; this Vela "
                f"expects {PROTOCOL_VERSION}. Reinstall the Vela download."
            )
        if not self.info.browser_available:
            await self._terminate()
            raise RuntimeUnavailable(
                self.info.browser_reason or "The browser this Vela was built against is missing."
            )
        self._heartbeat = asyncio.create_task(self._heartbeat_loop())
        self._log(f"agent desktop runtime ready (node {self.info.node})")

    async def stop(self):
        async with self._lock:
            if self.running:
                try:
                    await asyncio.wait_for(
                        self._send({"type": "shutdown"}), STOP_TIMEOUT_SECONDS
                    )
                    await asyncio.wait_for(self._process.wait(), STOP_TIMEOUT_SECONDS)
                except (asyncio.TimeoutError, RuntimeUnavailable, OSError):
                    pass
            await self._terminate()

    async def _heartbeat_loop(self):
        """Tell the worker Vela is still here, so a killed server leaves nothing."""
        try:
            while self.running:
                await asyncio.sleep(HEARTBEAT_SECONDS)
                if not self.running:
                    return
                await self._send({"type": "ping"})
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a missed beat must not kill the loop
            return

    # ------------------------------------------------------------ commands --

    async def command(self, name: str, *, timeout: float = 30.0, **fields):
        """Send one command and wait for its result.

        Every command carries the identities the protocol froze, and the result
        echoes them back. A worker that answered about a different desktop is a
        worker whose answer is discarded.
        """
        if not self.running:
            raise RuntimeUnavailable("The browser runtime is not running.")
        self._counter += 1
        command_id = f"c{self._counter}"
        future = asyncio.get_running_loop().create_future()
        self._pending[command_id] = future
        await self._send({"type": "command", "name": name, "commandId": command_id, **fields})
        try:
            message = await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(command_id, None)
            raise RuntimeUnavailable(f"The browser runtime did not answer {name} in time.") from exc
        if message.get("type") == "error":
            raise RuntimeUnavailable(message.get("detail") or "The browser refused that.")
        return message.get("result") or {}

    async def open_desktop(self, desktop_id: str, runtime_session_id: str, policy: dict):
        result = await self.command(
            "session.open",
            desktopId=desktop_id,
            runtimeSessionId=runtime_session_id,
            policy=policy,
            timeout=START_TIMEOUT_SECONDS,
        )
        self.desktops.add(desktop_id)
        return result

    async def close_desktop(self, desktop_id: str):
        try:
            return await self.command("session.close", desktopId=desktop_id)
        finally:
            self.desktops.discard(desktop_id)

    # -------------------------------------------------------------- plumbing --

    async def _send(self, message):
        process = self._process
        if not process or process.returncode is not None or not process.stdin:
            raise RuntimeUnavailable("The browser runtime is not running.")
        line = json.dumps({"v": PROTOCOL_VERSION, **message}, separators=(",", ":")).encode()
        if len(line) + 1 > MAX_MESSAGE_BYTES:
            raise RuntimeUnavailable("That command is too large to send to the browser runtime.")
        process.stdin.write(line + b"\n")
        await process.stdin.drain()

    async def _drain_stderr(self):
        process = self._process
        if not process or not process.stderr:
            return
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    return
                self._log("browser worker: " + line.decode("utf-8", "replace").rstrip())
        except (asyncio.CancelledError, ValueError):
            return

    async def _read_loop(self):
        process = self._process
        stream = process.stdout
        stream._limit = MAX_MESSAGE_BYTES * 2  # Refuse to buffer an unbounded line.
        try:
            while True:
                try:
                    line = await stream.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    self._log("browser worker sent an oversized message; stopping it")
                    break
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except ValueError:
                    self._log("browser worker sent a line that was not JSON")
                    continue
                if not isinstance(message, dict) or message.get("v") != PROTOCOL_VERSION:
                    self._log("browser worker sent an unsupported protocol version")
                    continue
                self._dispatch(message)
        except asyncio.CancelledError:
            raise
        finally:
            await self._fail_pending("The browser runtime stopped.")

    def _dispatch(self, message):
        kind = message.get("type")
        if kind == "hello":
            browser = message.get("browser") or {}
            if self._ready and not self._ready.done():
                self._ready.set_result(
                    RuntimeInfo(
                        node=str(message.get("node", "unknown")),
                        protocol=int(message.get("protocol", 0)),
                        browser_available=bool(browser.get("available")),
                        browser_reason=browser.get("reason"),
                    )
                )
            return
        if kind in ("ready", "heartbeat", "closed", "accepted"):
            return
        if kind in ("result", "error"):
            future = self._pending.pop(message.get("commandId", ""), None)
            if future is not None and not future.done():
                future.set_result(message)
            elif kind == "error":
                # An error with no command behind it is the worker telling Vela
                # something was refused — a denied navigation, say. Worth the
                # log; not worth failing anything that is still running.
                self._log(f"browser worker: {message.get('code')}: {message.get('detail')}")
            return
        self._log(f"browser worker sent an unexpected message: {kind}")

    async def _fail_pending(self, detail):
        for future in list(self._pending.values()):
            if not future.done():
                future.set_result({"type": "error", "code": "runtime_unavailable", "detail": detail})
        self._pending.clear()
        self.desktops.clear()
        if self._ready and not self._ready.done():
            self._ready.set_exception(RuntimeUnavailable(detail))

    async def _terminate(self):
        process = self._process
        self._process = None
        for task in (self._heartbeat, self._reader_task, self._stderr_task):
            if task:
                task.cancel()
        self._heartbeat = self._reader_task = self._stderr_task = None
        self.desktops.clear()
        if not process or process.returncode is not None:
            return
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            await asyncio.wait_for(process.wait(), STOP_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass
