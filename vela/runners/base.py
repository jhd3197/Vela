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

    # ----------------------------------------------------- managed services --
    #
    # A managed web app is an ordinary program with an argument vector, not a
    # command line to be handed to a shell. The two methods below exist so that
    # path is separate from the legacy `run` string: no shell means no quoting
    # rule to get wrong on one platform and no interpolation an argument value
    # could escape, and a filtered environment means the child starts with what
    # the package declared rather than with everything this process happens to
    # be holding.

    def launch_service(
        self,
        argv: list[str],
        cwd: Path,
        log_path: Path,
        env: dict[str, str],
    ) -> tuple[int, int | None]:
        """Start a program from an argument vector, with no shell involved.

        Returns (PID, creation-time token), the same pair `launch` returns.
        """
        raise NotImplementedError(
            "This platform cannot run managed web apps yet."
        )

    def stop_service(self, pid: int, *, timeout: float = 15.0) -> str:
        """Stop a service and everything it started, and say how it ended.

        Returns "exited" when the process was already gone, "stopped" when it
        finished within the graceful window, or "killed" when the owned tree had
        to be forced. The caller records which happened, because a service that
        is always killed is a package problem worth seeing.
        """
        raise NotImplementedError(
            "This platform cannot run managed web apps yet."
        )
