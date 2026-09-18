"""`python -m vela` starts the backend with uvicorn on port 7700."""

import argparse
import getpass
import ipaddress
import os
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit
import uvicorn
from .config import load_config
from .access import set_password
from .bundled_wallpapers import ensure as ensure_bundled_wallpapers
from .logging_setup import configure_logging


class BrowserServer(uvicorn.Server):
    """Open the dashboard only after this server successfully binds its port."""

    def __init__(self, config, dashboard_url=None, on_started=None):
        super().__init__(config)
        self.dashboard_url = dashboard_url
        self.on_started = on_started

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started and self.on_started:
            self.on_started()
        if self.started and self.dashboard_url:
            print(f'\nVela Server is ready: {self.dashboard_url}\n')
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
    parser.add_argument("--tray", action=argparse.BooleanOptionalAction, default=None,
                        help="Use Windows tray controls (default for the Windows download)")
    args = parser.parse_args()
    use_tray = args.tray if args.tray is not None else sys.platform == 'win32' and bool(getattr(sys, 'frozen', False))
    if use_tray and sys.platform != 'win32':
        parser.error('Tray controls are available on Windows; use --no-tray for a console server')
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
    # The health checks run inside the server, so they need to be told where it
    # was asked to listen and which certificate it was given.
    os.environ["VELA_HOST"] = args.host
    os.environ["VELA_PORT"] = str(args.port)
    if args.cert:
        os.environ["VELA_CERT_FILE"] = str(Path(args.cert).resolve())
    open_browser = args.open_browser if args.open_browser is not None else bool(getattr(sys, 'frozen', False)) and not remote
    # The bundled wallpapers publish as one 4x master each; the first start
    # downloads them and derives the sizes the dashboard draws. Later starts
    # find everything in place and skip this without touching the network.
    ensure_bundled_wallpapers(config.data_dir)
    hostname = f'[{args.host}]' if ':' in args.host else args.host
    dashboard_url = args.origin if remote else f"{'https' if args.cert else 'http'}://{hostname}:{args.port}"
    server_config = uvicorn.Config("vela.api:app", host=args.host, port=args.port,
                                   ssl_certfile=args.cert, ssl_keyfile=args.key, proxy_headers=False,
                                   timeout_graceful_shutdown=10, **({'log_config': None} if use_tray else {}))
    if use_tray:
        from .desktop import run_tray
        run_tray(lambda ready: BrowserServer(server_config, dashboard_url if open_browser else None, ready),
                 dashboard_url, config, args.host, args.port)
    else:
        # The console server writes the same server.log the tray does, so the
        # dashboard can show it however Vela was started.
        configure_logging(config)
        print('Keep this window open. Press Ctrl+C to stop Vela.')
        BrowserServer(server_config, dashboard_url if open_browser else None).run()


if __name__ == "__main__":
    main()
