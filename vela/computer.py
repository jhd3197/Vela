"""Token-only Vela owned by one controller process — the bot's computer.

People never open this Vela directly: a controller application runs one
container per bot, publishes its port on the host's loopback only, and talks
to it with a per-container bearer token. There is no dashboard to sign in to,
no password, no phone access — the controller token is the whole sign-in
story, and later controller features (a driver API, a terminal, full-desktop
capture) exist only in this mode.
"""

import os

import uvicorn

from .bundled_wallpapers import ensure as ensure_bundled_wallpapers
from .config import load_config, load_controller_token


def configure():
    """Validate the controller environment and enable controller mode.

    The token is checked here so a misconfigured container refuses to start
    with a plain sentence; `load_config` then reads the same file once more
    into the config every service shares. Never logged, never an environment
    variable.
    """
    token = load_controller_token()
    os.environ["VELA_CONTROLLER_MODE"] = "1"
    return token


def main():
    try:
        configure()
        config = load_config()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    # Same first-start fetch as the desktop server: the bundled wallpapers
    # download once and every later start finds them in place.
    ensure_bundled_wallpapers(config.data_dir)
    uvicorn.run("vela.api:app", host="0.0.0.0", port=config.app_gateway_port,
                proxy_headers=False, timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()
