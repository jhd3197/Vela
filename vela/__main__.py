"""`python -m vela` starts the backend with uvicorn on port 7700."""

import argparse
import getpass
import ipaddress
import os
import sys
import webbrowser
from urllib.parse import urlsplit
import uvicorn
from .config import load_config
from .access import set_password


class BrowserServer(uvicorn.Server):
    """Open the dashboard only after this server successfully binds its port."""

    def __init__(self, config, dashboard_url=None):
        super().__init__(config)
        self.dashboard_url = dashboard_url

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started and self.dashboard_url:
            print(f'\nVela Server is ready: {self.dashboard_url}\nKeep this window open. Press Ctrl+C to stop.\n')
            try:
                webbrowser.open(self.dashboard_url)
            except webbrowser.Error:
                print('Open the address above in your browser.')


def main() -> None:
    parser = argparse.ArgumentParser(description="Vela Server — your apps, hosted on your computer")
    parser.add_argument("--set-password", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7700)
    parser.add_argument("--cert")
    parser.add_argument("--key")
    parser.add_argument("--origin", help="Exact HTTPS origin clients use, e.g. https://vela.home:7700")
    parser.add_argument("--open-browser", action=argparse.BooleanOptionalAction, default=None,
                        help="Open the dashboard after startup (default for the packaged server)")
    args = parser.parse_args()
    config = load_config()
    if args.set_password:
        password = getpass.getpass("New Vela password (at least 12 characters): ")
        if password != getpass.getpass("Confirm password: "): parser.error("Passwords do not match")
        try: set_password(config.data_dir / "access.json", password)
        except ValueError as exc: parser.error(str(exc))
        print("Vela access password saved. Restart the engine to invalidate existing sessions.")
        return
    try: local = ipaddress.ip_address(args.host).is_loopback
    except ValueError: local = args.host == "localhost"
    remote = not local or bool(args.origin)
    if remote:
        parsed = urlsplit(args.origin or "")
        if not args.cert or not args.key or parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
            parser.error("LAN hosting requires --cert, --key and an exact HTTPS --origin")
        if not (config.data_dir / "access.json").is_file(): parser.error("Run --set-password first")
        os.environ["VELA_REMOTE_ACCESS"] = "1"
        os.environ["VELA_PUBLIC_ORIGIN"] = args.origin
    elif bool(args.cert) != bool(args.key): parser.error("Supply both --cert and --key")
    open_browser = args.open_browser if args.open_browser is not None else bool(getattr(sys, 'frozen', False)) and not remote
    hostname = f'[{args.host}]' if ':' in args.host else args.host
    dashboard_url = args.origin if remote else f"{'https' if args.cert else 'http'}://{hostname}:{args.port}"
    server_config = uvicorn.Config("vela.api:app", host=args.host, port=args.port,
                                   ssl_certfile=args.cert, ssl_keyfile=args.key, proxy_headers=False)
    BrowserServer(server_config, dashboard_url if open_browser else None).run()


if __name__ == "__main__":
    main()
