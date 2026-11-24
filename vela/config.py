"""Data-directory layout and environment overrides."""

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = Path.home() / ".vela"
ENV_DATA_DIR = "VELA_DATA_DIR"
#: Controller mode is opt-in and only ever set by the `vela.computer` entry
#: point (or a test doing the same thing on disposable data). The flag and the
#: token travel on `Config`, so every service reads one source of truth.
ENV_CONTROLLER_MODE = "VELA_CONTROLLER_MODE"
#: The controller token lives in a file the controller mounts into the
#: container, never in the environment: `docker inspect` shows environment
#: variables, and a secret that shows there is not a secret.
ENV_CONTROLLER_TOKEN_FILE = "VELA_CONTROLLER_TOKEN_FILE"
ENV_CONTROLLER_TOKEN = "VELA_CONTROLLER_TOKEN"
#: A shorter token is a guessable token, and whitespace is how secrets end up
#: truncated or extended when a file is composed by hand.
CONTROLLER_TOKEN_MIN_LENGTH = 32

#: The controller protocol version, reported by `GET /api/controller` and
#: carried as the `computer` image's `vela.controller.protocol` label. Bumped
#: when the mode gains a capability a controller can rely on: 3 is the terminal
#: (`terminal` in the discovery features), alongside what 2 already carried
#: (the driven-run API) and 1 before it. Lives here, and not in
#: `vela.computer`, so that the dashboard's discovery route does not import the
#: container entry point.
CONTROLLER_PROTOCOL = 3

#: The workspace a controller-mode terminal is rooted at. In the computer image
#: it is the `/workspace` volume a bot's own tools survive in; a test points it
#: at a disposable directory so nothing it runs can touch the real one.
ENV_WORKSPACE_DIR = "VELA_WORKSPACE_DIR"
DEFAULT_WORKSPACE_DIR = "/workspace"


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
    controller_mode: bool = False
    controller_token: str | None = None
    workspace_dir: Path = Path(DEFAULT_WORKSPACE_DIR)

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


def load_controller_token() -> str:
    """The controller token from its file, validated once, for every entry point.

    The token is read from the file named by `VELA_CONTROLLER_TOKEN_FILE` at
    startup and kept in memory for the life of the process; it is never
    logged and never re-read at request time. A token passed directly in
    `VELA_CONTROLLER_TOKEN` is refused so it can never appear in
    `docker inspect`. The trailing line breaks a shell redirect adds are
    tolerated; anything else with whitespace or control characters in it is
    not a token.
    """
    if os.environ.get(ENV_CONTROLLER_TOKEN):
        raise ValueError(
            f"Pass the controller token as a file named by {ENV_CONTROLLER_TOKEN_FILE}, "
            f"not in the {ENV_CONTROLLER_TOKEN} environment variable"
        )
    named = os.environ.get(ENV_CONTROLLER_TOKEN_FILE, "")
    if not named:
        raise ValueError(f"Set {ENV_CONTROLLER_TOKEN_FILE} to the file containing the controller token")
    try:
        raw = Path(named).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise ValueError(f"The controller token file {named} is missing or cannot be read")
    token = raw.rstrip("\r\n")
    if len(token) < CONTROLLER_TOKEN_MIN_LENGTH:
        raise ValueError(
            f"The controller token must be at least {CONTROLLER_TOKEN_MIN_LENGTH} characters long"
        )
    if any(char.isspace() or not char.isprintable() for char in token):
        raise ValueError("The controller token must not contain whitespace or control characters")
    return token


def load_config() -> Config:
    data_dir = Path(os.environ.get(ENV_DATA_DIR, DEFAULT_DATA_DIR)).expanduser()
    controller_mode = os.environ.get(ENV_CONTROLLER_MODE) == "1"
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
        controller_mode=controller_mode,
        controller_token=load_controller_token() if controller_mode else None,
        workspace_dir=Path(os.environ.get(ENV_WORKSPACE_DIR, DEFAULT_WORKSPACE_DIR)).expanduser(),
    )
    config.ensure_dirs()
    return config


#: How long a swap retries a Windows PermissionError before giving up.
_REPLACE_RETRY_SECONDS = 2.0


def write_json_atomic(path: Path, data: Any) -> None:
    """Write JSON so a crash mid-write cannot lose the previous good file.

    The new content goes to a temporary file first and `replace` swaps it in,
    which is atomic on both platforms Vela ships for. Before the swap the file
    that is about to be replaced is kept as `<name>.bak`, but only when it
    still parses: a `.bak` is only worth having if it is something the doctor's
    `settings` repair can put back.

    The swap retries briefly on Windows, the same way `package_files.replace_dir`
    does and for the same reason: a virus scanner or the search indexer can still
    hold a handle on a file written moments ago, and `os.replace` then fails with
    `PermissionError` although nothing is wrong. Every setting, every app's run
    state and every managed service record goes through here, so failing the
    write the user asked for over a handle that is about to be released is the
    wrong answer -- and it is a failure that has shown up, intermittently, as
    settings and usage writes.
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
    deadline = time.monotonic() + _REPLACE_RETRY_SECONDS
    while True:
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
