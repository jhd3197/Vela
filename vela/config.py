"""Data-directory layout and environment overrides."""

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = Path.home() / ".vela"
ENV_DATA_DIR = "VELA_DATA_DIR"


@dataclass(frozen=True)
class Config:
    data_dir: Path
    apps_dir: Path
    web_dist: Path
    remote_access: bool = False
    public_origin: str | None = None
    catalog_source: str | None = None
    catalog_sha256: str | None = None

    @property
    def installed_dir(self) -> Path:
        return self.data_dir / "installed"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def settings_file(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        self.installed_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)


def dir_size(path: Path) -> int:
    """Total size in bytes of all files under path."""
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                pass
    return total


def load_config() -> Config:
    data_dir = Path(os.environ.get(ENV_DATA_DIR, DEFAULT_DATA_DIR)).expanduser()
    config = Config(
        data_dir=data_dir,
        apps_dir=Path(os.environ.get("VELA_APPS_DIR", data_dir / "sources")).expanduser(),
        web_dist=REPO_ROOT / "web" / "dist",
        remote_access=os.environ.get("VELA_REMOTE_ACCESS") == "1",
        public_origin=os.environ.get("VELA_PUBLIC_ORIGIN"),
        catalog_source=os.environ.get("VELA_CATALOG"),
        catalog_sha256=os.environ.get("VELA_CATALOG_SHA256"),
    )
    config.ensure_dirs()
    return config
