"""The error record: merging by fingerprint, retention, the client cap, and the
server handler that records a 5xx without changing what the caller sees.

Every test uses its own diagnostics database.
"""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import test_app_contract as base
from unittest import mock

from fastapi.testclient import TestClient
from vela.backups import BackupStore
from vela.api import create_app
from vela.config import Config
from vela.errors import (
    CLIENT_LIMIT_PER_MINUTE,
    RETENTION_ROWS,
    ErrorStore,
    fingerprint,
)

ROOT = base.ROOT


class FingerprintTests(unittest.TestCase):
    def test_the_same_failure_in_the_same_place_is_one_fingerprint(self):
        a = fingerprint("server", "ValueError", "bad input", "/api/apps")
        b = fingerprint("server", "ValueError", "bad input", "/api/apps")
        self.assertEqual(a, b)

    def test_the_same_message_from_another_endpoint_is_a_different_one(self):
        a = fingerprint("server", "ValueError", "bad input", "/api/apps")
        b = fingerprint("server", "ValueError", "bad input", "/api/desk")
        self.assertNotEqual(a, b)

    def test_only_the_first_200_characters_of_a_message_count(self):
        # An error that embeds a changing id must still merge rather than
        # filling the list with near-duplicates.
        head = "failed while writing " + "x" * 190
        self.assertEqual(
            fingerprint("server", "OSError", head + " id=1", None),
            fingerprint("server", "OSError", head + " id=2", None),
        )


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-errors-")
        self.store = ErrorStore(Path(self.temp.name) / "diagnostics.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def test_repeating_a_failure_raises_its_count_instead_of_adding_a_row(self):
        for _ in range(3):
            self.store.record("server", "It broke", type_="ValueError", endpoint="/api/apps")
        listing = self.store.list()
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["errors"][0]["count"], 3)

    def test_a_resolved_error_happening_again_starts_a_new_row(self):
        first = self.store.record("server", "It broke", type_="ValueError")
        self.store.resolve(first["id"])
        self.store.record("server", "It broke", type_="ValueError")
        listing = self.store.list(resolved=None)
        self.assertEqual(listing["total"], 2, "a resolved error is history, not a row to reopen")
        self.assertEqual(self.store.list(resolved=False)["total"], 1)

    def test_different_failures_stay_apart(self):
        self.store.record("server", "It broke", type_="ValueError")
        self.store.record("dashboard", "It broke", type_="ValueError")
        self.store.record("server", "Something else", type_="ValueError")
        self.assertEqual(self.store.list()["total"], 3)

    def test_an_unknown_source_is_recorded_as_the_engine_rather_than_refused(self):
        row = self.store.record("nonsense", "It broke")
        self.assertEqual(row["source"], "server")

    def test_an_empty_message_records_nothing(self):
        self.assertIsNone(self.store.record("server", ""))
        self.assertEqual(self.store.list()["total"], 0)

    def test_long_text_is_cut_rather_than_stored_whole(self):
        row = self.store.record("server", "x" * 9000, traceback="y" * 90_000)
        self.assertLessEqual(len(row["message"]), 2000)
        self.assertLessEqual(len(row["traceback"]), 20_000)

    def test_filters_and_search_narrow_the_list(self):
        self.store.record("server", "disk failure", type_="OSError", endpoint="/api/backups")
        self.store.record("dashboard", "render failure", type_="TypeError", endpoint="/desk")
        self.assertEqual(self.store.list(source="dashboard")["total"], 1)
        self.assertEqual(self.store.list(search="disk")["total"], 1)
        self.assertEqual(self.store.list(search="failure")["total"], 2)
        self.assertEqual(self.store.list(search="nothing")["total"], 0)

    def test_resolving_and_reopening_move_a_row_between_the_two_lists(self):
        row = self.store.record("server", "It broke")
        self.assertEqual(self.store.list(resolved=False)["total"], 1)
        self.store.resolve(row["id"])
        self.assertEqual(self.store.list(resolved=False)["total"], 0)
        self.assertEqual(self.store.list(resolved=True)["total"], 1)
        self.store.resolve(row["id"], False)
        self.assertEqual(self.store.list(resolved=False)["total"], 1)

    def test_deleting_removes_it_and_says_whether_there_was_anything_to_delete(self):
        row = self.store.record("server", "It broke")
        self.assertTrue(self.store.delete(row["id"]))
        self.assertFalse(self.store.delete(row["id"]))
        self.assertEqual(self.store.list()["total"], 0)

    def test_stats_count_what_is_open_and_what_is_recent(self):
        first = self.store.record("server", "one")
        self.store.record("dashboard", "two")
        self.store.resolve(first["id"])
        stats = self.store.stats()
        self.assertEqual(stats["unresolved"], 1)
        self.assertEqual(stats["lastDay"], 1)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["bySource"], {"dashboard": 1})

    def test_rows_older_than_the_retention_window_are_dropped(self):
        old = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
        connection = self.store._connect()
        connection.execute(
            "INSERT INTO errors (fingerprint, source, message, count, first_seen, last_seen,"
            " resolved) VALUES ('old', 'server', 'ancient', 1, ?, ?, 0)",
            (old, old),
        )
        connection.commit()
        connection.close()
        # Any write prunes.
        self.store.record("server", "fresh")
        messages = [row["message"] for row in self.store.list()["errors"]]
        self.assertIn("fresh", messages)
        self.assertNotIn("ancient", messages)

    def test_the_row_count_is_bounded(self):
        for n in range(RETENTION_ROWS + 25):
            self.store.record("server", f"failure {n}")
        self.assertLessEqual(self.store.list()["total"], RETENTION_ROWS)

    def test_the_dashboard_cap_stops_a_flood_and_recovers(self):
        for _ in range(CLIENT_LIMIT_PER_MINUTE):
            self.assertTrue(self.store.accept_client_report())
        self.assertFalse(self.store.accept_client_report())
        # The window is time-based, so clearing it re-arms the cap.
        self.store._client_hits.clear()
        self.assertTrue(self.store.accept_client_report())

    def test_recording_never_raises_even_when_the_database_will_not_open(self):
        broken = ErrorStore(Path(self.temp.name) / "nowhere" / "x" / "diagnostics.sqlite")
        broken._path = Path(self.temp.name)  # a directory, not a database
        self.assertIsNone(broken.record("server", "It broke"))


class ErrorApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-errors-api-")
        self.root = Path(self.temp.name)
        self.config = Config(self.root / "data", self.root / "catalog", ROOT / "web/dist")
        self.config.ensure_dirs()
        self.app = create_app(self.config)
        # Let the app's own handler answer, the way uvicorn does, instead of
        # re-raising into the test client.
        self.client = TestClient(self.app, raise_server_exceptions=False)
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_every_route_needs_the_hub_session(self):
        self.assertEqual(self.client.get("/api/errors").status_code, 401)
        self.assertEqual(self.client.get("/api/errors/stats").status_code, 401)
        self.assertEqual(
            self.client.post("/api/errors/client", json={"message": "x"}).status_code, 401
        )

    def test_the_dashboard_can_report_one_of_its_own_failures(self):
        response = self.client.post(
            "/api/errors/client",
            headers=self.hub,
            json={"message": "Cannot read x of undefined", "type": "TypeError", "url": "/desk"},
        )
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["recorded"])
        rows = self.client.get("/api/errors", headers=self.hub).json()["errors"]
        self.assertEqual(rows[0]["source"], "dashboard")
        self.assertEqual(rows[0]["endpoint"], "/desk")

    def test_a_flood_of_client_reports_is_accepted_but_dropped(self):
        for _ in range(CLIENT_LIMIT_PER_MINUTE):
            self.client.post("/api/errors/client", headers=self.hub, json={"message": "loop"})
        response = self.client.post(
            "/api/errors/client", headers=self.hub, json={"message": "loop again"}
        )
        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.json()["recorded"])

    def test_a_client_report_without_a_message_is_refused_by_the_schema(self):
        self.assertEqual(
            self.client.post("/api/errors/client", headers=self.hub, json={}).status_code, 422
        )

    def test_resolving_and_deleting_an_unknown_error_is_a_404(self):
        self.assertEqual(
            self.client.post("/api/errors/999/resolve", headers=self.hub).status_code, 404
        )
        self.assertEqual(self.client.delete("/api/errors/999", headers=self.hub).status_code, 404)

    def test_an_unhandled_server_failure_is_recorded_and_still_answers_500(self):
        # Break a real endpoint rather than adding a test-only route: the
        # catch-all 404 would shadow anything registered after create_app, and
        # the point is to exercise the handler the way production hits it.
        def explode(_self):
            raise RuntimeError("the engine fell over")

        with mock.patch.object(BackupStore, "list", explode):
            response = self.client.get("/api/backups", headers=self.hub)
        self.assertEqual(response.status_code, 500)
        rows = self.client.get("/api/errors", headers=self.hub).json()["errors"]
        self.assertTrue(rows, "an unhandled failure must be recorded")
        self.assertEqual(rows[0]["source"], "server")
        self.assertEqual(rows[0]["type"], "RuntimeError")
        self.assertIn("fell over", rows[0]["message"])
        self.assertEqual(rows[0]["endpoint"], "/api/backups")
        self.assertIn("RuntimeError", rows[0]["traceback"])

    def test_the_same_server_failure_twice_is_one_row_with_a_count(self):
        def explode(_self):
            raise RuntimeError("the engine fell over")

        with mock.patch.object(BackupStore, "list", explode):
            self.client.get("/api/backups", headers=self.hub)
            self.client.get("/api/backups", headers=self.hub)
        rows = self.client.get("/api/errors", headers=self.hub).json()
        self.assertEqual(rows["total"], 1)
        self.assertEqual(rows["errors"][0]["count"], 2)

    def test_an_ordinary_http_error_is_not_recorded_as_a_failure(self):
        # A 404 is an answer, not a breakage.
        self.client.get("/api/apps/no-such-app", headers=self.hub)
        self.assertEqual(self.client.get("/api/errors", headers=self.hub).json()["total"], 0)


if __name__ == "__main__":
    unittest.main()
