"""Terminals: a real shell inside the bot's own computer, and nowhere else.

A controller-mode Vela is one bot's computer: the container is the sandbox,
so a shell inside it is a feature rather than a boundary violation. Everywhere
else the rule stands exactly as it always has — an agent desktop on a person's
Vela has no shell, and nothing here loosens that (see docs/AGENT-DESKTOPS.md).

Three decisions shape this file.

**The shell is rooted at the workspace, not at Vela.** A terminal starts in
`/workspace` (the volume a bot's own tools survive sleep in) with a fresh,
minimal environment Vela built itself: nothing is inherited, so no Vela secret
— the controller token's path, session keys, anything else in this process's
environment — can reach the shell. HOME, the npm prefix, PYTHONUSERBASE and
PIP user installs all point under the workspace, and PATH starts at its
`.local/bin`, so anything the bot installs lands where it survives the
container being replaced.

**The terminal is a view, watched like any other.** A terminal opens a window
of the terminal kind on the desktop, so the existing viewer machinery — the
frame route, the lease and take-over rules — applies unchanged. A frame is
text, not a picture: the recent scrollback with terminal control sequences
stripped, plus a revision a watcher can compare. `terminal_read` answers the
same question incrementally for the controller's agent.

**Nothing outlives its desktop.** Terminals die with the desktop that owns
them: when the desktop is deleted, when its agent is turned off, when Vela
quiesces for a restore or stops. A run cancelled by a person takes the
terminals it started with it; a run that merely ended leaves them — a dev
server a task started is a thing the next task may need. The scrollback is
bounded, so a chatty process can fill memory no further than the cap says.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import secrets
import signal
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .desktops.models import DesktopError, validate_view_id
from .desktops.store import new_id

if os.name == "posix":
    try:
        import fcntl
        import termios

        _HAS_PTY = hasattr(os, "openpty")
    except ImportError:  # pragma: no cover - a POSIX without termios is exotic
        _HAS_PTY = False
else:
    _HAS_PTY = False

#: Open terminals per desktop. Few, because each is a live shell process.
MAX_TERMINALS_PER_DESKTOP = 4

#: Scrollback kept per terminal. Bounded so one chatty process cannot fill the
#: server's memory; older output is dropped and reads say so.
MAX_SCROLLBACK_BYTES = 256 * 1024

#: Longest `terminal_run` waits, whatever the caller asked. The driven-run
#: step has its own backstop above this; answering inside it is the point.
MAX_RUN_SECONDS = 30

#: Default window size handed to the pty, so ordinary line programs behave.
TERMINAL_ROWS = 30
TERMINAL_COLUMNS = 120

#: Most output one tool answer carries. Deliberately under the transcript's
#: per-step clip (`budget.MAX_STEP_OUTPUT_BYTES`), so a capped answer still
#: fits the step envelope with its exit code and cursor beside the text.
MAX_OUTPUT_BYTES = 16_000

#: Lines a watcher sees in one frame.
FRAME_LINES = 60

#: Longest command line, title and key name accepted.
MAX_COMMAND_BYTES = 4000
MAX_TITLE = 120
MAX_KEY = 20

#: How long closing waits for the shell to take the hint before it is killed.
CLOSE_GRACE_SECONDS = 1.0

#: Named keys a person or a run may send, as the bytes a terminal expects.
KEY_BYTES = {
    "ctrl-c": b"\x03",
    "ctrl-d": b"\x04",
    "ctrl-z": b"\x1a",
    "ctrl-l": b"\x0c",
    "enter": b"\r",
    "tab": b"\t",
    "escape": b"\x1b",
    "backspace": b"\x7f",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
}

_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*\x07?)")


def pty_supported() -> bool:
    """Whether this platform can host a terminal at all."""
    return _HAS_PTY and os.name == "posix"


def find_shell() -> str | None:
    """bash where it exists, sh as the floor — the image ships both."""
    for candidate in ("/bin/bash", "/usr/bin/bash", "/bin/sh", "/usr/bin/sh"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def shell_env(workspace: Path, shell: str) -> dict[str, str]:
    """The environment a terminal starts with: built, never inherited.

    Inheriting this process's environment would hand the shell whatever Vela
    was given — the controller token's path, session keys, proxies — so the
    environment is composed here from nothing, and everything in it points at
    the workspace a bot's own tools live in.
    """
    local = workspace / ".local"
    home = workspace / ".home"
    return {
        "HOME": str(home),
        "PATH": ":".join(
            [
                str(local / "bin"),
                "/usr/local/sbin",
                "/usr/local/bin",
                "/usr/sbin",
                "/usr/bin",
                "/sbin",
                "/bin",
            ]
        ),
        "TERM": "xterm-256color",
        "SHELL": shell,
        "PREFIX": str(local),
        "NPM_CONFIG_PREFIX": str(local),
        "PYTHONUSERBASE": str(local),
        "PIP_USER": "1",
        "TMPDIR": str(workspace / ".tmp"),
    }


def strip_ansi(text: str) -> str:
    """Control sequences removed, for frames and tool answers.

    Not an emulator: a full-screen program (an editor, a pager) will not draw
    faithfully in a stripped frame. The raw bytes are still what `read`
    returns pieces of, minus the sequences — enough for line-oriented work.
    """
    return _ANSI.sub("", text)


def clip_output(text: str, limit: int = MAX_OUTPUT_BYTES) -> tuple[str, bool]:
    """Head and tail around a marker, so a long answer never looks complete."""
    if len(text.encode("utf-8")) <= limit:
        return text, False
    half = limit // 2
    return text[:half] + "\n…[output cut]…\n" + text[-half:], True


class Terminal:
    """One live shell behind a pseudo-terminal.

    The child is a session leader with the pty as its controlling terminal,
    so Ctrl-C written to the master reaches its foreground process group and
    a close can take the whole tree down at once. Reads ride the event loop's
    reader notification; the fd is non-blocking so a stopped child can never
    stall the loop.
    """

    def __init__(
        self,
        terminal_id: str,
        *,
        desktop_id: str,
        view_id: str,
        run_id: str | None,
        title: str,
        shell: str,
        cwd: Path,
        env: dict[str, str],
        log,
    ):
        self.id = terminal_id
        self.desktop_id = desktop_id
        self.view_id = view_id
        self.run_id = run_id
        self.title = title
        self.shell = shell
        self.cwd = str(cwd)
        self.created_at = time.time()
        self._log = log
        self._loop = asyncio.get_running_loop()
        self._closed = False
        self._proc: subprocess.Popen | None = None
        self._master: int | None = None
        #: (cursor, text) chunks, oldest first; the cursor only advances, so a
        #: reader holding one asks for what came after it.
        self._chunks: list[list[Any]] = []
        self._next_cursor = 0
        self._bytes = 0
        #: True once scrollback was cut and a `since` cursor may be stale.
        self.dropped = False
        self.revision = 0
        self._changed = asyncio.Event()
        self._pending = bytearray()
        self._spawn(env)

    # ------------------------------------------------------------ process --

    def _spawn(self, env: dict[str, str]) -> None:
        master, slave = os.openpty()
        with contextlib.suppress(OSError):
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", TERMINAL_ROWS, TERMINAL_COLUMNS, 0, 0))

        def _own_the_tty() -> None:
            # Runs in the child after its stdio is the pty: become a session
            # leader and take the tty, so job control and Ctrl-C work.
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        try:
            self._proc = subprocess.Popen(
                [self.shell],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=env,
                cwd=self.cwd,
                close_fds=True,
                preexec_fn=_own_the_tty,
            )
        except Exception:
            os.close(master)
            os.close(slave)
            raise
        os.close(slave)
        os.set_blocking(master, False)
        self._master = master
        self._loop.add_reader(master, self._on_readable)

    @property
    def pid(self) -> int:
        return self._proc.pid if self._proc is not None else -1

    @property
    def exited(self) -> bool:
        return self._closed or self._proc is None or self._proc.poll() is not None

    @property
    def exit_code(self) -> int | None:
        return None if self._proc is None else self._proc.poll()

    @property
    def cursor(self) -> int:
        return self._next_cursor

    # -------------------------------------------------------------- reads --

    def _on_readable(self) -> None:
        try:
            data = os.read(self._master, 65536) if self._master is not None else b""
        except BlockingIOError:
            return
        except OSError:
            data = b""  # EIO: the child is gone and the pty is done.
        if not data:
            self._ended()
            return
        self._feed(data)

    def _feed(self, data: bytes) -> None:
        text = data.decode("utf-8", errors="replace")
        self._chunks.append([self._next_cursor, text])
        self._next_cursor += 1
        self._bytes += len(data)
        while self._bytes > MAX_SCROLLBACK_BYTES and len(self._chunks) > 1:
            dropped = self._chunks.pop(0)
            self._bytes -= len(dropped[1].encode("utf-8"))
            self.dropped = True
        self.revision += 1
        self._changed.set()
        self._changed = asyncio.Event()

    def _text_since(self, cursor: int) -> str:
        return "".join(
            text for chunk_cursor, text in self._chunks if chunk_cursor >= cursor
        )

    def _lost_since(self, cursor: int) -> bool:
        """Whether output between the cursor and what is kept was dropped."""
        return self.dropped and bool(self._chunks) and self._chunks[0][0] > cursor

    def read(self, since: int = 0) -> dict[str, Any]:
        """Everything since a cursor, with where to ask next.

        `dropped` is only ever about the scrollback cap: true when output
        between the cursor given and the oldest kept chunk no longer exists.
        An old cursor that still has its chunks reads them all, missed nothing.
        """
        since = max(0, int(since))
        return {
            "output": strip_ansi(self._text_since(since)),
            "cursor": self._next_cursor,
            "dropped": self._lost_since(since),
        }

    def frame(self) -> dict[str, Any]:
        """What a watcher sees: the recent screen as text, control codes gone."""
        lines = strip_ansi(self._text_since(0)).splitlines()[-FRAME_LINES:]
        return {
            "text": "\n".join(lines),
            "cursor": {"row": max(0, len(lines) - 1), "column": len(lines[-1]) if lines else 0},
            "revision": self.revision,
            "capturedAt": time.time(),
        }

    # ------------------------------------------------------------- writes --

    def _flush(self) -> None:
        while self._pending and self._master is not None and not self._closed:
            try:
                written = os.write(self._master, self._pending)
            except BlockingIOError:
                self._loop.add_writer(self._master, self._on_writable)
                return
            except OSError:
                self._pending.clear()
                return
            del self._pending[:written]
        if self._master is not None:
            self._loop.remove_writer(self._master)

    def _on_writable(self) -> None:
        self._flush()

    def write(self, data: bytes) -> None:
        if self._closed:
            raise RuntimeError("That terminal is closed.")
        self._pending.extend(data)
        self._flush()

    async def _wait_change(self, seconds: float) -> None:
        waiter = self._changed
        self._changed = asyncio.Event()
        try:
            await asyncio.wait_for(waiter.wait(), timeout=seconds)
        except (asyncio.TimeoutError, TimeoutError):
            pass

    # --------------------------------------------------------------- run --

    async def run(self, command: str, timeout_seconds: float, wait_for: str) -> dict[str, Any]:
        """One command line, then wait as asked.

        `exit` appends a marker the shell prints with the command's status, so
        the answer can carry a real exit code. `output` returns the moment
        anything new appears. `none` sends the line and returns at once: the
        command keeps running, which is what a dev server wants.
        """
        start = self._next_cursor
        deadline = time.monotonic() + timeout_seconds
        marker = f"vela-exit-{secrets.token_hex(4)}"
        if wait_for == "none":
            self.write(command.encode("utf-8") + b"\r")
            output = clip_output(strip_ansi(self._text_since(start)))[0]
            return {"exitCode": None, "running": True, "output": output,
                    "truncated": False, "cursor": self._next_cursor}
        # `exit` and `output` both wait on the same line; only the end marker
        # turns a wait into a finished command with its status. The `\r?` is
        # not optional in practice: a pty line may end in carriage return,
        # line feed, or both, depending on the program.
        self.write(command.encode("utf-8") + f"\nprintf '\\n%s:%s\\n' {marker} \"$?\"\r".encode())
        pattern = re.compile(rf"^{marker}:(\d+)\r?$", re.M)
        while True:
            plain = strip_ansi(self._text_since(start))
            found = pattern.search(plain)
            if found is not None:
                exit_code, finished = int(found.group(1)), True
            elif self.exited:
                # The shell ended before the marker could print — `exit 3`
                # never reaches the printf. Its own status is the honest code.
                exit_code, finished = self.exit_code, True
            else:
                exit_code, finished = None, False
            new_output = self._next_cursor > start
            if finished or (wait_for == "output" and new_output):
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await self._wait_change(min(remaining, 0.2))
        output, truncated = clip_output(plain)
        return {
            "exitCode": exit_code,
            "running": not finished,
            "output": output,
            "truncated": truncated,
            "cursor": self._next_cursor,
        }

    # -------------------------------------------------------------- close --

    def _ended(self) -> None:
        """The child's end of the pty closed: stop reading, reap later."""
        if self._master is not None:
            with contextlib.suppress(Exception):
                self._loop.remove_reader(self._master)
            with contextlib.suppress(Exception):
                self._loop.remove_writer(self._master)
            self._master = None
        self.revision += 1
        self._changed.set()
        self._changed = asyncio.Event()
        if self._proc is not None and self._proc.poll() is None:
            # Reap without stalling the loop: the child is already exiting if
            # its tty is gone, and the reaper thread only waits for that.
            proc = self._proc

            def _reap() -> None:
                with contextlib.suppress(Exception):
                    proc.wait(timeout=5)

            threading.Thread(target=_reap, daemon=True).start()

    def close(self) -> None:
        """Kill the shell and everything it started. Idempotent."""
        if self._closed:
            return
        self._closed = True
        if self._master is not None:
            with contextlib.suppress(Exception):
                self._loop.remove_reader(self._master)
            with contextlib.suppress(Exception):
                self._loop.remove_writer(self._master)
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            # The child leads its own process group (it called setsid), so the
            # signal reaches what the shell started as well as the shell.
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGHUP)
            grace = time.monotonic() + CLOSE_GRACE_SECONDS
            while proc.poll() is None and time.monotonic() < grace:
                time.sleep(0.02)
            if proc.poll() is None:
                with contextlib.suppress(OSError):
                    os.killpg(proc.pid, signal.SIGKILL)
        if proc is not None:
            with contextlib.suppress(Exception):
                proc.wait(timeout=5)
        if self._master is not None:
            with contextlib.suppress(OSError):
                os.close(self._master)
            self._master = None
        self.revision += 1
        self._changed.set()


class Terminals:
    """Every live terminal, keyed by desktop — the lifecycle owner."""

    def __init__(self, desktops, config, *, log=None):
        self.desktops = desktops
        self._config = config
        self._log = log or (lambda message: None)
        #: terminal id -> terminal.
        self._all: dict[str, Terminal] = {}

    # ------------------------------------------------------------ enabled --

    @property
    def supported(self) -> bool:
        """Terminals exist only in controller mode and only where a pty does."""
        return bool(self._config.controller_mode) and pty_supported() and find_shell() is not None

    def _require_supported(self) -> None:
        if not self._config.controller_mode:
            raise DesktopError(404, "Terminals exist only in controller mode.")
        if not pty_supported():
            raise DesktopError(
                503,
                "A terminal needs a POSIX pseudo-terminal, which this platform does not have.",
            )
        if find_shell() is None:
            raise DesktopError(503, "A terminal needs a shell, and none was found on this computer.")

    def _emit(self, desktop_id: str, kind: str, payload: dict[str, Any]) -> None:
        runs = getattr(self.desktops, "runs", None)
        if runs is None:
            return
        with contextlib.suppress(Exception):
            runs.supervisor.emit(desktop_id, kind, payload)

    # ------------------------------------------------------------ opening --

    def open(
        self,
        desktop_id: str,
        *,
        title: Any = None,
        cwd: Any = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """A new terminal on this desktop: a view, a shell, a fresh workspace-rooted env."""
        self._require_supported()
        desktop = self.desktops.store.get(desktop_id)
        if desktop["kind"] != "agent":
            raise DesktopError(409, "This desktop is not running an agent.")
        live = [t for t in self._all.values() if t.desktop_id == desktop_id]
        if len(live) >= MAX_TERMINALS_PER_DESKTOP:
            raise DesktopError(
                429,
                f"A desktop holds {MAX_TERMINALS_PER_DESKTOP} terminals. Close one first.",
            )
        shell = find_shell()
        workspace = Path(self._config.workspace_dir)
        directory = self._resolve_cwd(workspace, cwd)
        for needed in (workspace / ".home", workspace / ".local" / "bin", workspace / ".tmp", directory):
            with contextlib.suppress(OSError):
                needed.mkdir(parents=True, exist_ok=True)
        label = str(title).strip()[:MAX_TITLE] if isinstance(title, str) else ""
        opened_by = "agent" if run_id else "human"
        view = self.desktops.open_terminal_view(desktop_id, title=label, opened_by=opened_by)
        terminal = Terminal(
            new_id(),
            desktop_id=desktop_id,
            view_id=view["id"],
            run_id=run_id,
            title=label or "Terminal",
            shell=shell,
            cwd=directory,
            env=shell_env(workspace, shell),
            log=self._log,
        )
        self._all[terminal.id] = terminal
        self._emit(desktop_id, "terminal.opened", {"terminalId": terminal.id, "viewId": view["id"]})
        return self.describe(terminal)

    def _resolve_cwd(self, workspace: Path, cwd: Any) -> Path:
        base = workspace.resolve()
        if cwd is None or (isinstance(cwd, str) and not cwd.strip()):
            return base
        if not isinstance(cwd, str):
            raise DesktopError(422, "A terminal's directory is a path under the workspace.")
        requested = Path(cwd)
        if not requested.is_absolute():
            requested = base / requested
        resolved = requested.resolve()
        if resolved != base and base not in resolved.parents:
            raise DesktopError(422, "A terminal's directory stays inside the workspace.")
        return resolved

    def describe(self, terminal: Terminal) -> dict[str, Any]:
        return {
            "terminalId": terminal.id,
            "viewId": terminal.view_id,
            "desktopId": terminal.desktop_id,
            "title": terminal.title,
            "shell": terminal.shell,
            "cwd": terminal.cwd,
            "runId": terminal.run_id,
            "running": not terminal.exited,
            "createdAt": terminal.created_at,
        }

    def for_desktop(self, desktop_id: str) -> list[dict[str, Any]]:
        return [
            self.describe(terminal)
            for terminal in self._all.values()
            if terminal.desktop_id == desktop_id
        ]

    # ------------------------------------------------------------ using them --

    def get(self, desktop_id: str, terminal_id: Any) -> Terminal:
        self.desktops.store.get(desktop_id)
        if not isinstance(terminal_id, str):
            raise DesktopError(404, "There is no terminal by that id on this desktop.")
        terminal = self._all.get(terminal_id)
        if terminal is None or terminal.desktop_id != desktop_id:
            raise DesktopError(404, "There is no terminal by that id on this desktop.")
        return terminal

    def require_by_view(self, desktop_id: str, view_id: Any) -> Terminal:
        """The terminal behind a window, for the input route and the frame route."""
        try:
            view_id = validate_view_id(view_id)
        except DesktopError:
            raise DesktopError(404, "That terminal is no longer open.")
        terminal = next((t for t in self._all.values() if t.view_id == view_id), None)
        if terminal is None or terminal.desktop_id != desktop_id:
            raise DesktopError(404, "That terminal is no longer open.")
        return terminal

    def read(self, desktop_id: str, terminal_id: Any, since: Any = None) -> dict[str, Any]:
        terminal = self.get(desktop_id, terminal_id)
        try:
            since_cursor = max(0, int(since or 0))
        except (TypeError, ValueError):
            raise DesktopError(422, "A read names where to read from: a cursor, or nothing.")
        return terminal.read(since_cursor)

    async def run(
        self,
        desktop_id: str,
        terminal_id: Any,
        command: Any,
        *,
        timeout_seconds: Any = None,
        wait_for: Any = None,
    ) -> dict[str, Any]:
        terminal = self.get(desktop_id, terminal_id)
        if terminal.exited:
            raise DesktopError(409, "That terminal has exited. Open a new one.")
        if not isinstance(command, str) or not command.strip():
            raise DesktopError(422, "Running needs a command line.")
        if "\n" in command or "\r" in command:
            raise DesktopError(422, "A command is one line.")
        if len(command.encode("utf-8")) > MAX_COMMAND_BYTES:
            raise DesktopError(413, f"A command is at most {MAX_COMMAND_BYTES} bytes.")
        mode = wait_for if wait_for is not None else "exit"
        if mode not in ("exit", "output", "none"):
            raise DesktopError(422, "Waiting is one of: 'exit', 'output' or 'none'.")
        try:
            timeout = 10.0 if timeout_seconds is None else float(timeout_seconds)
        except (TypeError, ValueError):
            raise DesktopError(422, "A timeout is a number of seconds.")
        timeout = min(max(timeout, 0.1), MAX_RUN_SECONDS)
        try:
            return await terminal.run(command.strip(), timeout, mode)
        except RuntimeError as exc:
            raise DesktopError(409, str(exc)) from exc

    def write(
        self, desktop_id: str, terminal_id: Any, *, text: Any = None, keys: Any = None
    ) -> dict[str, Any]:
        terminal = self.get(desktop_id, terminal_id)
        if terminal.exited:
            raise DesktopError(409, "That terminal has exited. Open a new one.")
        if text is None and keys is None:
            raise DesktopError(422, "Writing sends text or one named key.")
        if text is not None and keys is not None:
            raise DesktopError(422, "Writing sends text or a key, not both at once.")
        if keys is not None:
            if not isinstance(keys, str) or keys not in KEY_BYTES:
                raise DesktopError(
                    422,
                    "That key is not one a terminal accepts: " + ", ".join(sorted(KEY_BYTES)) + ".",
                )
            data = KEY_BYTES[keys]
        else:
            if not isinstance(text, str):
                raise DesktopError(422, "Text to send is a string.")
            if not text:
                raise DesktopError(422, "There is nothing to send.")
            if len(text.encode("utf-8")) > MAX_COMMAND_BYTES * 4:
                raise DesktopError(413, "That is more than a terminal write accepts at once.")
            data = text.encode("utf-8")
        try:
            terminal.write(data)
        except RuntimeError as exc:
            raise DesktopError(409, str(exc)) from exc
        return {"sent": True, "cursor": terminal.cursor}

    # ------------------------------------------------------------ closing --

    def close(self, desktop_id: str, terminal_id: Any) -> dict[str, Any]:
        terminal = self.get(desktop_id, terminal_id)
        self._close(terminal)
        return {"closed": True, "terminalId": terminal.id}

    def _close(self, terminal: Terminal) -> None:
        terminal.close()
        self._all.pop(terminal.id, None)
        with contextlib.suppress(Exception):
            self.desktops.store.close_view(terminal.view_id)
        self._emit(
            terminal.desktop_id,
            "terminal.closed",
            {"terminalId": terminal.id, "viewId": terminal.view_id},
        )

    def close_by_view(self, desktop_id: str, view_id: Any) -> None:
        """The person closed the window: the shell goes with it."""
        try:
            view_id = validate_view_id(view_id)
        except DesktopError:
            return
        for terminal in [t for t in self._all.values() if t.view_id == view_id]:
            if terminal.desktop_id == desktop_id:
                self._close(terminal)

    def close_run(self, desktop_id: str, run_id: str) -> int:
        """A cancelled run's terminals go with it; what other runs opened stays."""
        closed = 0
        for terminal in [t for t in self._all.values() if t.desktop_id == desktop_id]:
            if terminal.run_id == run_id:
                self._close(terminal)
                closed += 1
        return closed

    def close_desktop(self, desktop_id: str) -> int:
        closed = 0
        for terminal in [t for t in self._all.values() if t.desktop_id == desktop_id]:
            self._close(terminal)
            closed += 1
        return closed

    def stop_all(self) -> int:
        closed = 0
        for terminal in list(self._all.values()):
            self._close(terminal)
            closed += 1
        return closed

    # ------------------------------------------------------------ watching --

    def frame(self, desktop_id: str, view_id: Any) -> dict[str, Any]:
        terminal = self.require_by_view(desktop_id, view_id)
        return {
            "viewId": terminal.view_id,
            "desktopId": desktop_id,
            "kind": "terminal",
            "title": terminal.title,
            "terminalId": terminal.id,
            **terminal.frame(),
        }
