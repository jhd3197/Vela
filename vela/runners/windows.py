"""Windows subprocess runner."""

import os
import signal
import subprocess
import time
from pathlib import Path

from ..state import pid_alive, pid_ctime
from .base import Runner


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
