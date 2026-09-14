"""Android runner stub — not supported yet."""

from pathlib import Path

from .base import Runner

_MESSAGE = (
    "Vela cannot launch apps on Android yet. "
    "The android platform runner is a stub; support is planned for a future release."
)


class AndroidRunner(Runner):
    def launch(self, app_id: str, command: str, cwd: Path, log_path: Path) -> tuple[int, int | None]:
        raise NotImplementedError(_MESSAGE)

    def stop(self, pid: int) -> None:
        raise NotImplementedError(_MESSAGE)
