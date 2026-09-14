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
