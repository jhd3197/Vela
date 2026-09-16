"""Open counts behind the Launchpad's Frequent tab.

The store answers one question — which apps does this person actually reach for
— and the checks here are mostly about what it refuses to do: keep more than
thirty days, grow without bound, or survive an uninstall. Everything uses
disposable data.
"""
import json
import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import test_app_contract as base
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.config import Config
from vela.usage import MAX_ID, MAX_TRACKED, WINDOW_DAYS, UsageStore

ROOT = base.ROOT


class UsageStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-usage-")
        self.path = Path(self.temp.name) / "usage.json"
        self.store = UsageStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def stored(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_opens_are_counted_per_app_and_totalled_over_the_window(self):
        for _ in range(3):
            self.store.record("notes")
        self.store.record("health")
        self.assertEqual(self.store.totals(), {"notes": 3, "health": 1})

    def test_totals_come_back_most_opened_first(self):
        self.store.record("health")
        for _ in range(5):
            self.store.record("notes")
        for _ in range(2):
            self.store.record("meals")
        self.assertEqual(list(self.store.totals()), ["notes", "meals", "health"])

    def test_a_day_older_than_the_window_is_dropped_on_the_next_write(self):
        stale = (date.today() - timedelta(days=WINDOW_DAYS + 5)).isoformat()
        edge = (date.today() - timedelta(days=WINDOW_DAYS - 1)).isoformat()
        self.path.write_text(json.dumps({"notes": {stale: 9, edge: 2}}), encoding="utf-8")
        # Reading already ignores the stale day.
        self.assertEqual(self.store.totals(), {"notes": 2})
        self.store.record("notes")
        self.assertNotIn(stale, self.stored()["notes"])
        self.assertIn(edge, self.stored()["notes"])

    def test_an_app_with_only_stale_days_disappears_entirely(self):
        stale = (date.today() - timedelta(days=WINDOW_DAYS + 1)).isoformat()
        self.path.write_text(json.dumps({"gone": {stale: 4}}), encoding="utf-8")
        self.assertEqual(self.store.totals(), {})

    def test_a_damaged_file_is_read_for_what_still_makes_sense(self):
        self.path.write_text(
            json.dumps(
                {
                    "notes": {date.today().isoformat(): 2},
                    "bad-count": {date.today().isoformat(): "lots"},
                    "negative": {date.today().isoformat(): -3},
                    "not-a-dict": 7,
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(self.store.totals(), {"notes": 2})

    def test_unreadable_json_counts_as_no_history_rather_than_an_error(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.totals(), {})
        self.store.record("notes")
        self.assertEqual(self.store.totals(), {"notes": 1})

    def test_an_empty_or_overlong_id_is_not_stored(self):
        self.store.record("")
        self.store.record("   ")
        self.store.record("x" * (MAX_ID + 1))
        self.assertEqual(self.store.totals(), {})

    def test_the_file_stops_growing_once_it_is_full(self):
        # Seeded rather than recorded one at a time: the cap is the subject
        # here, and five hundred atomic writes in a row only exercises the
        # filesystem.
        today = date.today().isoformat()
        self.path.write_text(
            json.dumps({f"app-{i:04d}": {today: 1} for i in range(MAX_TRACKED)}),
            encoding="utf-8",
        )
        self.assertEqual(len(self.store.totals()), MAX_TRACKED)
        self.store.record("one-too-many")
        self.assertNotIn("one-too-many", self.store.totals())
        # An app already being counted keeps counting.
        self.store.record("app-0000")
        self.assertEqual(self.store.totals()["app-0000"], 2)

    def test_forgetting_an_app_removes_its_counts(self):
        self.store.record("notes")
        self.store.record("health")
        self.store.forget("notes")
        self.assertEqual(self.store.totals(), {"health": 1})
        # Forgetting something that was never counted is not an error.
        self.store.forget("never-opened")


class UsageApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-usage-api-")
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

    def test_counts_need_a_hub_session(self):
        self.assertEqual(self.client.get("/api/usage").status_code, 401)
        self.assertEqual(self.client.post("/api/usage/notes").status_code, 401)

    def test_recording_an_open_shows_up_in_the_totals(self):
        self.assertEqual(self.client.get("/api/usage", headers=self.hub).json()["totals"], {})
        for _ in range(2):
            recorded = self.client.post("/api/usage/notes", headers=self.hub)
            self.assertEqual(recorded.status_code, 200)
        self.assertEqual(recorded.json(), {"id": "notes", "count": 2})
        body = self.client.get("/api/usage", headers=self.hub).json()
        self.assertEqual(body["totals"], {"notes": 2})
        self.assertEqual(body["windowDays"], WINDOW_DAYS)

    def test_a_core_tool_is_counted_like_an_app(self):
        # Frequent ranks Vela's own tools beside installed apps, so the store
        # takes core ids without knowing anything about the registry.
        self.client.post("/api/usage/ask", headers=self.hub)
        self.assertEqual(self.client.get("/api/usage", headers=self.hub).json()["totals"], {"ask": 1})


if __name__ == "__main__":
    unittest.main()
