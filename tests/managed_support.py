"""Shared setup for the managed-web-app suites.

One disposable server, one real fixture package, and the helpers every managed
test needs to review, install and reach an app. Nothing here touches the
installed Vela: the data directory is a temporary one, the ports are allocated
per test, and the fixture service is built for this machine and thrown away.

Run the suites with: python -m unittest discover -s tests.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "tests/fixtures/managed-web"
if str(FIXTURE_DIR) not in sys.path:
    sys.path.insert(0, str(FIXTURE_DIR))

# api.py constructs its default ASGI app on import. Keep that off the user's data.
_bootstrap = tempfile.TemporaryDirectory(prefix="vela-test-managed-")
atexit.register(_bootstrap.cleanup)
os.environ.setdefault("VELA_DATA_DIR", _bootstrap.name)

import unittest  # noqa: E402

import fixture_build  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from vela.api import create_app  # noqa: E402
from vela.config import Config  # noqa: E402
from vela.managed.gateway import SESSION_COOKIE as GATEWAY_COOKIE  # noqa: E402

APP_ID = fixture_build.APP_ID
#: The app's own address in these tests. Port 80 so the gateway's idea of the
#: origin and the test client's `Host` header are the same string, which is what
#: a launch ticket is bound to.
APP_HOST = f"{APP_ID}.apps.localhost"
APP_ORIGIN = f"http://{APP_HOST}"


class ManagedTestCase(unittest.TestCase):
    """A running Vela with no managed apps yet, and the tools to install one."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-managed-")
        self.root = Path(self.temp.name)
        # Registered first so it runs last: cleanups are undone in reverse, and
        # deleting the data directory before the server's shutdown hooks have
        # run is how a passing test turns into a confusing SQLite error.
        self.addCleanup(self.remove_temporary_data)
        self.sources = self.root / "catalog"
        self.sources.mkdir()
        self.config = Config(
            self.root / "data",
            self.sources,
            ROOT / "web/dist",
            app_domain="apps.localhost",
            app_lan_domain="apps.vela.invalid",
            app_gateway_port=80,
        )
        self.config.ensure_dirs()
        self.app = create_app(self.config)
        # Entered as a context manager, which runs the startup and shutdown
        # hooks. That is deliberate rather than incidental: managed-app recovery
        # and start-with-Vela happen on startup, and the gateway's connection
        # pool belongs to the loop the server runs on, so every request in a
        # test has to share one loop the way a real server does.
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))
        token = self.client.get(
            "/api/session", headers={"X-Vela-Bootstrap": "1"}
        ).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}
        self.addCleanup(self.stop_everything)

    def remove_temporary_data(self):
        # Windows will not delete a directory a stopped process's log handle is
        # still in, and a test that leaves one behind fails the *next* test.
        for _ in range(10):
            try:
                self.temp.cleanup()
                return
            except (OSError, PermissionError):
                time.sleep(0.2)
        shutil.rmtree(self.root, ignore_errors=True)

    def stop_everything(self):
        """Never leave a child process behind, whatever the test did."""
        try:
            self.service.stop_all()
        except Exception:  # noqa: BLE001 - cleanup is best effort
            pass

    # ------------------------------------------------------------- services --

    @property
    def service(self):
        """The `ManagedApps` this server built."""
        return self.app.state.managed_apps

    # ------------------------------------------------------------- packages --

    def build_package(self, **options) -> Path:
        return fixture_build.build_package(self.root / "packages", **options)

    def build_archive(self, **options) -> Path:
        return fixture_build.build_archive(self.root / "packages", **options)

    def review(self, folder=None, **options):
        folder = folder or self.build_package(**options)
        response = self.client.post(
            "/api/managed/review", headers=self.hub, json={"folder": str(folder)}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def install(self, review=None, **options):
        review = review or self.review(**options)
        response = self.client.post(
            f"/api/managed/review/{review['review']}/install",
            headers=self.hub,
            json={
                "artifactDigest": review["artifactDigest"],
                "packageDigest": review["packageDigest"],
                "trust": "trusted-native",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def start(self, app_id=APP_ID):
        response = self.client.post(f"/api/managed/{app_id}/start", headers=self.hub)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def stop(self, app_id=APP_ID):
        response = self.client.post(f"/api/managed/{app_id}/stop", headers=self.hub)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def status(self, app_id=APP_ID):
        response = self.client.get(f"/api/managed/{app_id}/status", headers=self.hub)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    # -------------------------------------------------------------- gateway --

    def launch(self, app_id=APP_ID, **payload):
        response = self.client.post(
            f"/api/managed/{app_id}/launch", headers=self.hub, json=payload
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def enter(self, app_id=APP_ID, client=None, **payload):
        """Redeem a launch ticket the way a browser does, and keep the cookie.

        The cookie is moved into the jar by hand. `__Host-` cookies must carry
        `Secure`, and browsers accept a `Secure` cookie from `http://*.localhost`
        because that origin is trustworthy -- `httpx` applies the plain RFC rule
        instead and drops it. Keeping the cookie here tests everything after the
        exchange; that browsers really accept it is a real-browser check, and
        `docs/TESTING.md` says where it lives.
        """
        client = client or self.client
        ticket = self.launch(app_id, **payload)
        response = client.get(
            ticket["url"],
            headers={"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303, response.text)
        value = self.gateway_cookie(response)
        self.assertIsNotNone(value, "the launch exchange set no session cookie")
        client.cookies.set(GATEWAY_COOKIE, value, domain=APP_HOST, path="/")
        return response

    @staticmethod
    def gateway_cookie(response):
        """The gateway session value a response set, or None."""
        for raw in response.headers.get_list("set-cookie"):
            if raw.startswith(GATEWAY_COOKIE + "="):
                value = raw.split("=", 1)[1].split(";", 1)[0]
                return value or None
        return None

    def browser(self):
        """A second, independent browser: its own cookie jar, same server.

        Built on the same ASGI app and the same portal, so it shares the
        server's event loop while keeping a cookie jar entirely of its own.
        """
        client = TestClient(self.app)
        client.portal = getattr(self.client, "portal", None)
        return client

    def app_get(self, path="/", client=None, **kwargs):
        return (client or self.client).get(APP_ORIGIN + path, **kwargs)

    def app_post(self, path, client=None, **kwargs):
        return (client or self.client).post(APP_ORIGIN + path, **kwargs)

    # --------------------------------------------------------------- helpers --

    def read_manifest(self, folder: Path) -> dict:
        return json.loads((Path(folder) / "app.json").read_text(encoding="utf-8"))

    def write_manifest(self, folder: Path, data: dict) -> Path:
        (Path(folder) / "app.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        return Path(folder)
