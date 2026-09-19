"""macOS/Linux subprocess runner."""

import os
import signal
import subprocess
import time
from pathlib import Path

from ..state import pid_alive, pid_ctime
from .base import Runner


class PosixRunner(Runner):
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
                start_new_session=True,
            )
        finally:
            log.close()
        # /proc starttime on Linux; None on macOS, degrading liveness to a bare PID check.
        return proc.pid, pid_ctime(proc.pid)

    def stop(self, pid: int) -> None:
        if not pid_alive(pid):
            return
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pgid, sig)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if not pid_alive(pid):
                    return
                time.sleep(0.1)

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
                # Its own session, so the group id is the child's pid and
                # signalling the group reaches what it started without reaching
                # anything Vela did.
                start_new_session=True,
            )
        finally:
            log.close()
        return proc.pid, pid_ctime(proc.pid)

    def stop_service(self, pid: int, *, timeout: float = 15.0) -> str:
        if not pid_alive(pid):
            return "exited"
        try:
            pgid = os.getpgid(pid)
        except ProcessLookupError:
            return "exited"
        except PermissionError:
            pgid = pid
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return "exited"
        deadline = time.monotonic() + max(0.5, timeout)
        while time.monotonic() < deadline:
            if not pid_alive(pid):
                return "stopped"
            time.sleep(0.1)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return "stopped"
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not pid_alive(pid):
                return "killed"
            time.sleep(0.1)
        return "killed"
