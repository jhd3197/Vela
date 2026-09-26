"""A disposable Vela with a managed web app installed, for browser acceptance.

Plain HTTP on loopback, deliberately: a managed app is published under
`<app>.apps.localhost`, and what the browser suite has to exercise is that
Chrome resolves that name to this computer on its own and treats the origin as
trustworthy enough to accept the gateway's `__Host-` session cookie. Wrapping it
in TLS would test a different deployment and hide the one being claimed.

The package is built for this machine by `tests/fixtures/managed-web`, installed
through the same review and approval path a person uses, and left stopped. Data
lives in a temporary directory that goes away with the process.

    python scripts/serve-managed-fixtures.py --port 17717
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/fixtures/managed-web"))

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=17717)
parser.add_argument("--domain", default="apps.localhost")
args = parser.parse_args()

with tempfile.TemporaryDirectory(prefix="vela-managed-fixtures-",
                                 ignore_cleanup_errors=True) as temporary:
    work = Path(temporary)
    os.environ["VELA_DATA_DIR"] = str(work / "data")
    os.environ["VELA_PORT"] = str(args.port)

    import httpx
    import uvicorn
    from fastapi.testclient import TestClient

    import fixture_build
    from vela.api import create_app
    from vela.config import Config

    config = Config(work / "data", work / "sources", ROOT / "web/dist",
                    app_domain=args.domain, app_gateway_port=args.port)
    (work / "sources").mkdir(parents=True, exist_ok=True)
    config.ensure_dirs()
    app = create_app(config)

    # Installed before the server starts listening, through the real review and
    # approval path, so the browser suite starts from an installed app rather
    # than from a database somebody wrote by hand.
    with TestClient(app) as client:
        token = client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        hub = {"Authorization": "Bearer " + token}
        package = fixture_build.build_package(work / "package")
        review = client.post("/api/managed/review", headers=hub,
                             json={"folder": str(package)}).json()
        installed = client.post(
            f"/api/managed/review/{review['review']}/install", headers=hub,
            json={"artifactDigest": review["artifactDigest"],
                  "packageDigest": review["packageDigest"],
                  "trust": "trusted-native"},
        )
        installed.raise_for_status()
        print(f"installed {review['id']} v{review['version']} "
              f"({review['artifact']['os']} {review['artifact']['arch']})", flush=True)

    print(f"ready on http://127.0.0.1:{args.port}", flush=True)
    try:
        uvicorn.run(app, host="127.0.0.1", port=args.port, proxy_headers=False,
                    log_level="warning")
    finally:
        # Never leave the application's process behind when the fixture stops.
        app.state.managed_apps.stop_all()
