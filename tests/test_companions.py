"""Companion apps: desktop apps on this computer that register themselves.

A registration file is written by a program the owner may never have looked at,
so these tests are mostly about what Vela refuses: talking to anything but
loopback, trusting a declaration the owner did not review, drawing a summary
that names an action it did not declare, and handing out the companion's token.
"""

import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from vela.api import create_app
from vela.companions import fingerprint, read_registration
from vela.config import Config

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "q2oV1YwJmSgHkq8H1mZtV6z4B0Jf3r9WcXnL7pEaUuA"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

REGISTRATION = {
    "companion": 1,
    "id": "desk",
    "name": "Desk",
    "description": "Usage and automations.",
    "version": "0.4.0",
    "color": "#8b7ff6",
    "icon": "desk.png",
    "endpoint": "http://127.0.0.1:53211",
    "token": TOKEN,
    "pid": os.getpid(),
    "executable": str(Path(__file__).resolve()),
    "widgets": [
        {"id": "today", "name": "Today", "layout": "stat", "size": "s"},
        {"id": "run", "name": "Automation", "layout": "actions", "size": "m"},
    ],
    "actions": [
        {"id": "pause", "title": "Pause automation"},
        {"id": "stop", "title": "Stop automation", "confirm": "Stop it?"},
    ],
}


class FakeCompanion:
    """The companion side of the protocol, answering Vela's three routes."""

    def __init__(self):
        self.calls = []
        self.online = True
        self.widgets = {
            "today": {"value": "$4.12", "caption": "18 calls"},
            "run": {"caption": "Step 2 of 5", "attention": True,
                    "actions": [{"action": "pause", "label": "Pause"}]},
        }
        self.action_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, str(request.url)))
        if request.url.host not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError(f"Vela called {request.url}")
        if not self.online:
            raise httpx.ConnectError("refused", request=request)
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"error": "no"})
        if request.url.path == "/vela/v1/status":
            return httpx.Response(200, json={"id": "desk"})
        if request.url.path == "/vela/v1/widgets":
            return httpx.Response(200, json={"widgets": self.widgets})
        if request.url.path.startswith("/vela/v1/actions/"):
            if self.action_status != 200:
                return httpx.Response(self.action_status, json={"error": "Nothing is running."})
            self.widgets["run"] = {"caption": "Paused"}
            return httpx.Response(200, json={"message": "Paused after this step."})
        return httpx.Response(404)


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-companions-")
        self.root = Path(self.temp.name)
        self.config = Config(self.root / "data", self.root / "catalog", ROOT / "web/dist")
        self.config.ensure_dirs()
        self.folder = self.config.data_dir / "companions"
        self.folder.mkdir()
        self.fake = FakeCompanion()
        self.launched = []
        self.app = create_app(self.config, companion_transport=httpx.MockTransport(self.fake.handler),
                              companion_launcher=self.launched.append)
        self.companions = self.app.state.companions
        self.client = TestClient(self.app)
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def register(self, **changes):
        data = {**copy.deepcopy(REGISTRATION), **changes}
        (self.folder / f"{data['id']}.json").write_text(json.dumps(data), encoding="utf-8")
        (self.folder / "desk.png").write_bytes(PNG)
        self.companions.scan()
        return data

    def connect(self):
        found = self.client.get("/api/companions", headers=self.hub).json()["found"]
        self.assertEqual([item["id"] for item in found], ["desk"])
        response = self.client.post("/api/companions/found/desk/connect", headers=self.hub,
                                    json={"fingerprint": found[0]["fingerprint"]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_found_companions_wait_for_the_owner(self):
        self.register()
        found = self.client.get("/api/companions", headers=self.hub).json()
        self.assertEqual(found["connected"], [])
        entry = found["found"][0]
        self.assertEqual(entry["name"], "Desk")
        self.assertEqual(entry["executable"], REGISTRATION["executable"])
        # Nothing an unconnected companion says is shown as an app, and Vela
        # has not called it.
        apps = self.client.get("/api/apps", headers=self.hub).json()["apps"]
        self.assertFalse(any(app["id"].startswith("pc--") for app in apps))
        self.assertEqual(self.fake.calls, [])
        # Its token and endpoint never leave Vela.
        text = json.dumps(found)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("53211", text)

    def test_connect_shows_it_as_an_app_with_widgets(self):
        self.register()
        app = self.connect()
        self.assertEqual(app["id"], "pc--desk")
        self.assertEqual(app["kind"], "companion")
        self.assertTrue(app["running"])
        self.assertEqual(app["companion"]["state"], "online")
        self.assertEqual([w["id"] for w in app["widgets"]], ["today", "run"])
        self.assertIn(app["id"], [a["id"] for a in self.client.get("/api/apps", headers=self.hub).json()["apps"]])
        self.assertNotIn(TOKEN, json.dumps(app))

        widgets = self.client.get("/api/widgets", headers=self.hub).json()["widgets"]
        mine = {w["id"]: w for w in widgets if w["appId"] == "pc--desk"}
        self.assertEqual(mine["today"]["summary"]["value"], "$4.12")
        self.assertTrue(mine["run"]["summary"]["attention"])
        self.assertEqual(mine["run"]["grantedActions"], ["pause", "stop"])

        icon = self.client.get("/api/apps/pc--desk/icon")
        self.assertEqual(icon.status_code, 200)
        self.assertEqual(icon.headers["content-type"], "image/png")
        self.assertEqual(icon.content, PNG)

    def test_the_connection_survives_a_restart(self):
        self.register()
        self.connect()
        restarted = create_app(self.config, companion_transport=httpx.MockTransport(self.fake.handler))
        with TestClient(restarted) as client:
            token = client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
            app = client.get("/api/apps/pc--desk", headers={"Authorization": "Bearer " + token}).json()
            self.assertEqual(app["name"], "Desk")

    def test_actions_run_on_the_companion_and_refresh_its_widgets(self):
        self.register()
        self.connect()
        response = self.client.post("/api/companions/pc--desk/actions/pause", headers=self.hub)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True, "message": "Paused after this step."})
        self.assertIn(("POST", "http://127.0.0.1:53211/vela/v1/actions/pause"), self.fake.calls)
        run = self.client.get("/api/apps/pc--desk/widgets", headers=self.hub).json()["widgets"][1]
        self.assertEqual(run["summary"], {"caption": "Paused"})

        self.assertEqual(self.client.post("/api/companions/pc--desk/actions/format-disk",
                                          headers=self.hub).status_code, 404)
        self.fake.action_status = 409
        refused = self.client.post("/api/companions/pc--desk/actions/stop", headers=self.hub)
        self.assertEqual(refused.status_code, 422)
        self.assertEqual(refused.json()["detail"], "Nothing is running.")

    def test_owner_routes_need_the_owner(self):
        self.register()
        self.assertEqual(self.client.get("/api/companions").status_code, 401)
        found = self.client.get("/api/companions", headers=self.hub).json()["found"][0]
        self.assertEqual(self.client.post("/api/companions/found/desk/connect",
                                          json={"fingerprint": found["fingerprint"]}).status_code, 401)
        self.connect()
        self.assertEqual(self.client.post("/api/companions/pc--desk/actions/pause").status_code, 401)

    def test_connecting_needs_the_reviewed_fingerprint(self):
        self.register()
        response = self.client.post("/api/companions/found/desk/connect", headers=self.hub,
                                    json={"fingerprint": "0" * 64})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get("/api/companions", headers=self.hub).json()["connected"], [])

    def test_a_changed_declaration_stops_vela_until_reviewed(self):
        data = self.register()
        self.connect()
        changed = copy.deepcopy(data)
        changed["actions"].append({"id": "wipe", "title": "Delete everything"})
        self.register(**changed)
        app = self.client.get("/api/apps/pc--desk", headers=self.hub).json()
        self.assertEqual(app["companion"]["state"], "changed")
        self.assertFalse(app["running"])
        self.assertEqual([a["id"] for a in app["companion"]["actions"]], ["pause", "stop"])
        self.assertEqual([a["id"] for a in app["companion"]["pending"]["actions"]], ["pause", "stop", "wipe"])
        self.fake.calls.clear()
        self.companions.scan()
        self.assertEqual(self.fake.calls, [], "Vela kept talking to an unreviewed companion")
        self.assertEqual(self.client.post("/api/companions/pc--desk/actions/pause",
                                          headers=self.hub).status_code, 409)

        reviewed = self.client.post("/api/companions/pc--desk/review", headers=self.hub,
                                    json={"fingerprint": fingerprint(changed)})
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertEqual(reviewed.json()["companion"]["state"], "online")
        self.assertIn("wipe", [a["id"] for a in reviewed.json()["companion"]["actions"]])

    def test_name_and_version_follow_the_file_without_review(self):
        self.register()
        self.connect()
        self.register(name="Prompture Desk", version="0.5.0")
        app = self.client.get("/api/apps/pc--desk", headers=self.hub).json()
        self.assertEqual((app["name"], app["version"]), ("Prompture Desk", "0.5.0"))
        self.assertEqual(app["companion"]["state"], "online")

    def test_bad_summaries_are_not_drawn(self):
        self.fake.widgets = {
            "today": {"value": "<b>x</b>", "script": "alert(1)"},
            "run": {"actions": [{"action": "wipe", "label": "Delete everything"}]},
            "undeclared": {"value": "1"},
        }
        self.register()
        app = self.connect()
        self.assertEqual(app["companion"]["state"], "online")
        widgets = self.client.get("/api/apps/pc--desk/widgets", headers=self.hub).json()["widgets"]
        self.assertEqual([w["summary"] for w in widgets], [None, None])

    def test_offline_and_start(self):
        self.register()
        self.connect()
        (self.folder / "desk.json").unlink()
        self.companions.scan()
        app = self.client.get("/api/apps/pc--desk", headers=self.hub).json()
        self.assertEqual(app["companion"]["state"], "offline")
        self.assertTrue(app["companion"]["canStart"])
        # Its last summaries stay, so the desk can say how old they are.
        widgets = self.client.get("/api/apps/pc--desk/widgets", headers=self.hub).json()["widgets"]
        self.assertEqual(widgets[0]["summary"]["value"], "$4.12")
        self.assertEqual(self.client.post("/api/companions/pc--desk/actions/pause",
                                          headers=self.hub).status_code, 409)
        started = self.client.post("/api/apps/pc--desk/launch", headers=self.hub)
        self.assertEqual(started.status_code, 200, started.text)
        self.assertTrue(started.json()["starting"])
        self.assertEqual(self.launched, [REGISTRATION["executable"]])

    def test_a_running_process_that_does_not_answer_is_offline(self):
        self.register()
        self.connect()
        self.fake.online = False
        self.companions.scan()
        app = self.client.get("/api/apps/pc--desk", headers=self.hub).json()
        self.assertEqual(app["companion"]["state"], "offline")
        self.assertFalse(app["companion"]["canStart"], "it is running; starting it again would be a second copy")

    def test_remove_forgets_it_and_it_can_be_found_again(self):
        self.register()
        self.connect()
        self.assertEqual(self.client.delete("/api/apps/pc--desk", headers=self.hub).status_code, 200)
        self.assertEqual(self.client.get("/api/apps/pc--desk", headers=self.hub).status_code, 404)
        self.assertFalse(any(w["appId"] == "pc--desk" for w in
                             self.client.get("/api/widgets", headers=self.hub).json()["widgets"]))
        self.assertEqual(self.client.get("/api/apps/pc--desk/icon").status_code, 404)
        found = self.client.get("/api/companions", headers=self.hub).json()["found"]
        self.assertEqual([item["id"] for item in found], ["desk"])

    def test_a_dead_process_is_not_found(self):
        self.register(pid=4_000_000)
        self.assertEqual(self.client.get("/api/companions", headers=self.hub).json()["found"], [])

    def test_registration_files_are_checked(self):
        cases = {
            "lan": {"endpoint": "http://192.168.1.4:4100"},
            "icon-path": {"icon": "../secret.png"},
            "short-token": {"token": "abc"},
        }
        for name, change in cases.items():
            with self.subTest(name):
                path = self.folder / "desk.json"
                path.write_text(json.dumps({**REGISTRATION, **change}), encoding="utf-8")
                self.assertIsNone(read_registration(path))
        path = self.folder / "other.json"
        path.write_text(json.dumps(REGISTRATION), encoding="utf-8")
        self.assertIsNone(read_registration(path), "a file must be named after its own id")
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(read_registration(path))
        duplicate = copy.deepcopy(REGISTRATION)
        duplicate["actions"].append({"id": "pause", "title": "Again"})
        (self.folder / "desk.json").write_text(json.dumps(duplicate), encoding="utf-8")
        self.assertIsNone(read_registration(self.folder / "desk.json"))

    def test_an_icon_that_is_not_a_png_is_not_copied(self):
        self.register()
        (self.folder / "desk.png").write_bytes(b"<svg onload='alert(1)'/>")
        found = self.client.get("/api/companions", headers=self.hub).json()["found"][0]
        self.client.post("/api/companions/found/desk/connect", headers=self.hub,
                         json={"fingerprint": found["fingerprint"]})
        self.assertEqual(self.client.get("/api/apps/pc--desk/icon").status_code, 404)

    def test_its_widgets_can_go_on_a_desk(self):
        self.register()
        self.connect()
        self.assertIn("pc--desk:today", self.app.state.desktops._known_types())


if __name__ == "__main__":
    unittest.main()
