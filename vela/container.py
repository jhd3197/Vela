"""Password-protected server behind an explicitly trusted HTTPS reverse proxy."""

import ipaddress
import os
from urllib.parse import urlsplit

import uvicorn

from .access import set_password
from .config import load_config


def configure():
    origin = os.environ.get("VELA_PUBLIC_ORIGIN", "")
    try:
        parsed = urlsplit(origin)
        valid_origin = (parsed.scheme == "https" and parsed.hostname and
                        not parsed.path and not parsed.query and not parsed.fragment and
                        not parsed.username and not parsed.password and parsed.port != 0 and
                        not any(char.isspace() for char in origin))
    except ValueError:
        valid_origin = False
    if not valid_origin:
        raise ValueError("Set VELA_PUBLIC_ORIGIN to the exact HTTPS browser origin, without a trailing slash")

    proxies = [value.strip() for value in os.environ.get("VELA_TRUSTED_PROXIES", "").split(",")]
    if not all(proxies):
        raise ValueError("Set VELA_TRUSTED_PROXIES to the proxy IP address as seen by the container")
    for proxy in proxies:
        try:
            network = ipaddress.ip_network(proxy, strict=False)
        except ValueError as exc:
            raise ValueError("VELA_TRUSTED_PROXIES accepts only IP addresses or CIDRs, never '*'") from exc
        if network.prefixlen == 0:
            raise ValueError("VELA_TRUSTED_PROXIES must not trust every address")

    config = load_config()
    password = os.environ.pop("VELA_INITIAL_PASSWORD", "")
    password_file = config.data_dir / "access.json"
    if not password_file.is_file():
        if not password:
            raise ValueError("Set VELA_INITIAL_PASSWORD for the first start (12–256 characters)")
        set_password(password_file, password)
    os.environ["VELA_REMOTE_ACCESS"] = "1"
    return ",".join(proxies)


def main():
    try:
        proxies = configure()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    uvicorn.run("vela.api:app", host="0.0.0.0", port=7700,
                proxy_headers=True, forwarded_allow_ips=proxies,
                timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()
