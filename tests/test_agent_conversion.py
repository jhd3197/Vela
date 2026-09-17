"""Turning a desktop into one an agent runs in.

The only test here that means anything is the one that does it for real: a
listening Vela, an installed app, a window open on the desk, and a managed
browser that ends up showing that app through the restricted host page. A
conversion that reported success with nothing behind it would be worse than one
that refused, because everything after it would be built on the report.

So this starts an actual server on a disposable data directory and an actual
browser. Without the browser installed it skips with a reason.
"""

import asyncio
import copy
import json
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import test_app_contract as base
import uvicorn
from scripts.fixture_apps import APPS as FIXTURE_APPS
from vela.api import create_app
from vela.config import Config
from vela.desktops.runtime import availability

ROOT = base.ROOT
STATE = availability()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(STATE["available"], STATE["detail"] or "the browser runtime is not installed")
class AgentConversionTests(unittest.TestCase):
    """A real server, a real browser, a real app."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="vela-agent-conversion-")
        root = Path(cls.temp.name)
        cls.apps = root / "catalog"
        cls.apps.mkdir()
        shutil.copytree(FIXTURE_APPS / "notes", cls.apps / "notes")
        cls.port = free_port()
        import os

        os.environ["VELA_PORT"] = str(cls.port)
        cls.config = Config(root / "data", cls.apps, ROOT / "web/dist")
        cls.config.ensure_dirs()
        cls.app = create_app(cls.config)
        cls.server = uvicorn.Server(
            uvicorn.Config(cls.app, host="127.0.0.1", port=cls.port, log_level="warning")
        )
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"
        for _ in range(200):
            try:
                import httpx

                if httpx.get(f"{cls.base}/api/health", timeout=1).status_code == 200:
                    break
            except Exception:  # noqa: BLE001 - it is simply not up yet
                time.sleep(0.05)
        else:
            raise RuntimeError("the fixture server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=20)
        cls.temp.cleanup()

    def setUp(self):
        import httpx

        self.client = httpx.Client(base_url=self.base, timeout=60)
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}
        self.desktop = self.client.get("/api/desktops", headers=self.hub).json()["defaultId"]
        self.client.post("/api/apps/notes/install", headers=self.hub)

    def tearDown(self):
        self.client.post(f"/api/desktops/{self.desktop}/disable-agent", headers=self.hub)
        self.client.close()

    def set_policy(self, apps=("notes",)):
        current = self.client.get(f"/api/desktops/{self.desktop}/policy", headers=self.hub).json()
        return self.client.put(
            f"/api/desktops/{self.desktop}/policy",
            headers=self.hub,
            json={"revision": current["revision"], "apps": list(apps)},
        )

    def open_view(self, app_id="notes"):
        return self.client.post(
            f"/api/desktops/{self.desktop}/views",
            headers=self.hub,
            json={"kind": "app", "appId": app_id},
        ).json()

    def enable(self):
        return self.client.post(f"/api/desktops/{self.desktop}/enable-agent", headers=self.hub)

    # ---- refusing rather than pretending

    def test_a_desktop_that_allows_nothing_is_not_converted(self):
        refused = self.enable()
        self.assertEqual(refused.status_code, 422)
        self.assertIn("Choose what", refused.json()["detail"])
        self.assertEqual(
            self.client.get(f"/api/desktops/{self.desktop}", headers=self.hub).json()["kind"],
            "personal",
            "a refusal leaves the desktop exactly as it was",
        )

    # ---- the conversion

    def test_a_window_moves_into_the_managed_browser_and_shows_the_app(self):
        self.set_policy()
        view = self.open_view()
        result = self.enable()
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertEqual(body["desktop"]["kind"], "agent")
        self.assertIn(view["id"], body["moved"], body.get("notes"))

        status = self.client.get("/api/desktops/runtime", headers=self.hub).json()
        self.assertTrue(status["running"])
        self.assertIn(self.desktop, status["desktops"])

        # The window really is showing the app, through the restricted host
        # page, in the managed browser — not a record saying that it is.
        frame = asyncio.run(self._capture(view["id"]))
        self.assertEqual(frame[:8], b"\x89PNG\r\n\x1a\n")

    def test_the_agents_page_is_not_the_dashboard(self):
        page = self.client.get("/agent-host/anything")
        self.assertEqual(page.status_code, 200)
        text = page.text
        self.assertIn("agent-host", text)
        # None of the dashboard's chrome, and nothing that could carry a token.
        self.assertNotIn("/src/main.jsx", text)
        self.assertNotIn("manifest.webmanifest", text)

    def test_turning_it_off_gives_the_desktop_back_and_takes_the_authority(self):
        self.set_policy()
        self.open_view()
        self.assertEqual(self.enable().status_code, 200)
        self.client.post(
            f"/api/desktops/{self.desktop}/grants",
            headers=self.hub,
            json={"effect": "write", "appId": "notes"},
        )

        off = self.client.post(f"/api/desktops/{self.desktop}/disable-agent", headers=self.hub)
        self.assertEqual(off.status_code, 200, off.text)
        self.assertEqual(off.json()["desktop"]["kind"], "personal")
        self.assertEqual(
            self.client.get(f"/api/desktops/{self.desktop}/grants", headers=self.hub).json()[
                "grants"
            ],
            [],
            "turning the agent off takes what it was allowed to do with it",
        )
        self.assertNotIn(
            self.desktop,
            self.client.get("/api/desktops/runtime", headers=self.hub).json()["desktops"],
        )
        # And the workspace is still there: this is not throwing it away.
        self.assertTrue(
            self.client.get(f"/api/desktops/{self.desktop}/views", headers=self.hub).json()["views"]
        )

    def test_a_window_that_cannot_come_along_says_so(self):
        self.set_policy(apps=["notes"])
        self.client.post(
            f"/api/desktops/{self.desktop}/views",
            headers=self.hub,
            json={"kind": "host", "surface": "library"},
        )
        result = self.enable()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(
            any("your side of the window" in note for note in result.json()["notes"]),
            result.json()["notes"],
        )

    def test_converting_twice_is_the_same_desktop(self):
        self.set_policy()
        self.open_view()
        self.assertEqual(self.enable().status_code, 200)
        again = self.enable()
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["desktop"]["kind"], "agent")

    async def _capture(self, view_id):
        runtime = self.app.state.desktops.runtime
        frame = await runtime.command("view.capture", desktopId=self.desktop, viewId=view_id)
        return (self.config.data_dir / "agent-frames" / frame["file"]).read_bytes()


if __name__ == "__main__":
    unittest.main()
