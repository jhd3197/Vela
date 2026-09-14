"""Runner abstraction: launch/stop/status for app processes."""

from abc import ABC, abstractmethod
from pathlib import Path

from ..state import pid_alive


class Runner(ABC):
    @abstractmethod
    def launch(self, app_id: str, command: str, cwd: Path, log_path: Path) -> tuple[int, int | None]:
        """Start the app process with stdout/stderr appended to log_path.

        Returns (PID, creation-time token); the token is None on platforms
        that cannot provide one, in which case liveness is a bare PID check.
        """

    @abstractmethod
    def stop(self, pid: int) -> None:
        """Terminate the process, escalating to a hard kill if needed."""

    def status(self, pid: int, ctime: int | None = None) -> bool:
        return pid_alive(pid, ctime)
