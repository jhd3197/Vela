"""Data-directory layout and environment overrides."""

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = Path.home() / ".vela"
ENV_DATA_DIR = "VELA_DATA_DIR"


#: The parent name managed web apps are published under on the computer running
#: Vela. `*.localhost` resolves to loopback in every browser Vela supports
#: without a hosts file or a DNS server, which is what makes an app-per-hostname
#: layout usable on a personal machine.
DEFAULT_APP_DOMAIN = "apps.localhost"

#: The parent name offered for other devices on the same network. `.invalid` is
#: reserved by RFC 2606, so it can never collide with a real site, and Vela's own
#: Wi-Fi certificate authority is already constrained to it. It resolves only
#: where an operator has pointed a DNS server at this computer; see docs/APPS.md.
DEFAULT_APP_LAN_DOMAIN = "apps.vela.invalid"


@dataclass(frozen=True)
class Config:
    data_dir: Path
    apps_dir: Path
    web_dist: Path
    remote_access: bool = False
    public_origin: str | None = None
    catalog_source: str | None = None
    catalog_sha256: str | None = None
    app_domain: str = DEFAULT_APP_DOMAIN
    app_lan_domain: str = DEFAULT_APP_LAN_DOMAIN
    app_gateway_port: int = 7700
    app_gateway_lan_port: int | None = None

    @property
    def app_domains(self) -> tuple[str, ...]:
        """Every parent name a managed app answers on, most local first."""
        found = [self.app_domain]
        if self.app_lan_domain and self.app_lan_domain != self.app_domain:
            found.append(self.app_lan_domain)
        return tuple(found)

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


def _port(value: Any, fallback: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return fallback
    return port if 0 < port < 65536 else fallback


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
        app_domain=os.environ.get("VELA_APP_DOMAIN", DEFAULT_APP_DOMAIN),
        app_lan_domain=os.environ.get("VELA_APP_LAN_DOMAIN", DEFAULT_APP_LAN_DOMAIN),
        # The port the dashboard was reached on is the port an app address has
        # to carry: a managed app is served by this same listener under a
        # different name, not by a second server on a port of its own.
        app_gateway_port=_port(os.environ.get("VELA_PORT"), 7700),
    )
    config.ensure_dirs()
    return config


def write_json_atomic(path: Path, data: Any) -> None:
    """Write JSON so a crash mid-write cannot lose the previous good file.

    The new content goes to a temporary file first and `replace` swaps it in,
    which is atomic on both platforms Vela ships for. Before the swap the file
    that is about to be replaced is kept as `<name>.bak`, but only when it
    still parses: a `.bak` is only worth having if it is something the doctor's
    `settings` repair can put back.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        else:
            try:
                shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
            except OSError:
                # A backup copy is a convenience. Failing to make one must not
                # stop the write the user actually asked for.
                pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)
