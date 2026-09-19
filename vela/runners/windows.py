"""Windows subprocess runner."""

import os
import signal
import subprocess
import time
from pathlib import Path

from ..state import pid_alive, pid_ctime
from .base import Runner

#: Keep a launched service off the desktop. A console program started without
#: this flag opens a window on the user's screen every time Vela starts it.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


class WindowsRunner(Runner):
    def launch(self, app_id: str, command: str, cwd: Path, log_path: Path) -> tuple[int, int | None]:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "ab", buffering=0)
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=cwd,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        finally:
            log.close()
        return proc.pid, pid_ctime(proc.pid)

    def stop(self, pid: int) -> None:
        if not pid_alive(pid):
            return
        # taskkill /T takes the whole tree (shell wrapper + child), /F forces it.
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not pid_alive(pid):
                return
            time.sleep(0.1)
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    # ----------------------------------------------------- managed services --

    def launch_service(self, argv, cwd: Path, log_path: Path, env) -> tuple[int, int | None]:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "ab", buffering=0)
        try:
            proc = subprocess.Popen(
                list(argv),
                shell=False,
                cwd=str(cwd),
                env=dict(env),
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                # A group of its own, so the tree can be taken together, and no
                # console window, so a service never appears on the desktop.
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | _NO_WINDOW,
            )
        finally:
            log.close()
        return proc.pid, pid_ctime(proc.pid)

    def stop_service(self, pid: int, *, timeout: float = 15.0) -> str:
        """Ask the tree to close, then take it.

        Windows has no SIGTERM for a console program without a window, so the
        polite step is `taskkill` without `/F`: it posts WM_CLOSE and lets a
        program that has a message loop exit on its own. A server that does not
        will not answer it, which is why the forced step is not optional and why
        the return value says which one ended the process.
        """
        if not pid_alive(pid):
            return "exited"
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"],
            capture_output=True,
            check=False,
            creationflags=_NO_WINDOW,
        )
        deadline = time.monotonic() + max(0.5, timeout)
        while time.monotonic() < deadline:
            if not pid_alive(pid):
                return "stopped"
            time.sleep(0.1)
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
            creationflags=_NO_WINDOW,
        )
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if not pid_alive(pid):
                return "killed"
            time.sleep(0.1)
        return "killed"
