"""Hub settings: rail-pin defaults, patch validation and sanitisation.

Everything uses disposable data. The dashboard decides which pins still name a
real app; the store's job, checked here, is to keep `rail.pinned` a bounded list
of unique id strings whatever a patch sends.
"""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import test_app_contract as base
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.config import Config
from vela.settings import MAX_PINS, SettingsStore, sanitize_pins

ROOT = base.ROOT
FIXTURE = json.loads((ROOT / "tests/fixtures/chat-fixture/app.json").read_text())


class SanitizeTests(unittest.TestCase):
    def test_pins_are_unique_non_empty_strings_in_order(self):
        self.assertEqual(sanitize_pins(["ask", "library", "ask"]), ["ask", "library"])
        self.assertEqual(sanitize_pins([" ask ", "", "  ", "notes"]), ["ask", "notes"])
        self.assertEqual(sanitize_pins(["ask", 3, None, {"x": 1}, "library"]), ["ask", "library"])

    def test_pins_are_bounded(self):
        many = [f"app-{n}" for n in range(MAX_PINS + 10)]
        self.assertEqual(len(sanitize_pins(many)), MAX_PINS)


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-settings-")
        self.store = SettingsStore(Path(self.temp.name) / "settings.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_rail_defaults_to_ask_and_library(self):
        self.assertEqual(self.store.get("rail"), {"pinned": ["ask", "library"]})

    def test_a_stored_pin_list_replaces_the_default(self):
        self.store.patch({"rail": {"pinned": ["notes", "ask"]}})
        self.assertEqual(self.store.get("rail")["pinned"], ["notes", "ask"])


class RailPinApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-settings-api-")
        self.root = Path(self.temp.name)
        self.apps = self.root / "catalog"
        shutil.copytree(ROOT / "tests/fixtures/chat-fixture", self.apps / "chat-fixture")
        self.config = Config(self.root / "data", self.apps, ROOT / "web/dist")
        self.config.ensure_dirs()
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_default_rail_pins_are_reported(self):
        rail = self.client.get("/api/settings", headers=self.hub).json()["rail"]
        self.assertEqual(rail["pinned"], ["ask", "library"])

    def test_patch_stores_and_sanitises_pins(self):
        response = self.client.patch(
            "/api/settings",
            headers=self.hub,
            json={"rail": {"pinned": ["library", "ask", "library", "", 5]}},
        )
        self.assertEqual(response.status_code, 200, response.text)
        rail = self.client.get("/api/settings", headers=self.hub).json()["rail"]
        self.assertEqual(rail["pinned"], ["library", "ask"])

    def test_patch_rejects_a_non_list_pinned(self):
        response = self.client.patch(
            "/api/settings", headers=self.hub, json={"rail": {"pinned": "ask"}}
        )
        self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
