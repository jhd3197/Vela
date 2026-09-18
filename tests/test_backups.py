"""Backups: the round trip, the safety copy taken before a restore, the daily
schedule and its arithmetic, and retention.

Every test runs against a disposable data directory. Nothing here reads or
writes a real Vela installation, and no test restores over one.
"""

import json
import sqlite3
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import test_app_contract as base
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.backups import (
    DEFAULT_SCHEDULE,
    KEEP_SAFETY,
    SAFETY_PREFIX,
    BackupError,
    BackupStore,
    describe_schedule,
    next_run,
    validate_schedule,
)
from vela.config import Config

ROOT = base.ROOT


class ScheduleTests(unittest.TestCase):
    def test_an_absent_schedule_is_the_default_not_an_error(self):
        self.assertEqual(validate_schedule(None), DEFAULT_SCHEDULE)
        self.assertNotIn("error", describe_schedule(None))

    def test_a_time_must_look_like_a_time(self):
        for bad in ("25:00", "3:00", "0300", "", "03:60", None, 3):
            with self.subTest(time=bad):
                with self.assertRaises(BackupError):
                    validate_schedule({"time": bad})

    def test_keep_is_bounded(self):
        self.assertEqual(validate_schedule({"keep": 5})["keep"], 5)
        for bad in (0, -1, 1000, "many"):
            with self.subTest(keep=bad):
                with self.assertRaises(BackupError):
                    validate_schedule({"keep": bad})

    def test_a_schedule_that_is_off_has_no_next_run(self):
        self.assertIsNone(next_run({"enabled": False, "time": "03:00", "keep": 7}))

    def test_the_next_run_is_the_next_time_that_clock_reading_comes_round(self):
        schedule = {"enabled": True, "time": "03:00", "keep": 7}
        from vela.automations.schedules import resolve_timezone

        tz, _ = resolve_timezone(None)
        after = datetime(2026, 6, 1, 10, 0, tzinfo=tz)
        due = next_run(schedule, after=after)
        self.assertEqual((due.hour, due.minute), (3, 0))
        self.assertGreater(due, after)
        self.assertLess(due - after, timedelta(days=1))

        # Before the hour on the same day, it is later today.
        early = datetime(2026, 6, 1, 1, 0, tzinfo=tz)
        self.assertEqual(next_run(schedule, after=early).date(), early.date())

    def test_describe_reports_the_zone_and_a_bad_schedule_without_raising(self):
        described = describe_schedule({"enabled": True, "time": "03:00", "keep": 7})
        self.assertTrue(described["timezone"])
        self.assertTrue(described["nextRunAt"])
        broken = describe_schedule({"time": "nope"})
        self.assertIn("error", broken)
        self.assertIsNone(broken["nextRunAt"])

    def test_an_aware_moment_is_required(self):
        with self.assertRaises(ValueError):
            next_run({"enabled": True, "time": "03:00", "keep": 7}, after=datetime(2026, 6, 1))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-backups-")
        self.root = Path(self.temp.name)
        self.config = Config(self.root / "data", self.root / "catalog", ROOT / "web/dist")
        self.config.ensure_dirs()
        self.store = BackupStore(self.config)
        self.write_state("original")

    def tearDown(self):
        self.temp.cleanup()

    def write_state(self, marker):
        (self.config.settings_file).write_text(
            json.dumps({"theme": marker}), encoding="utf-8"
        )
        (self.config.state_file).write_text(json.dumps({"marker": marker}), encoding="utf-8")
        database = self.config.data_dir / "app-data.sqlite"
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE IF NOT EXISTS documents (value TEXT)")
        connection.execute("DELETE FROM documents")
        connection.execute("INSERT INTO documents VALUES (?)", (json.dumps({"note": marker}),))
        connection.commit()
        connection.close()
        # A real manifest: `verify` loads it, so a stub would fail the drill
        # and a restore would refuse to run.
        app_dir = self.config.installed_dir / "notes"
        app_dir.mkdir(parents=True, exist_ok=True)
        manifest = json.loads(
            (ROOT / "tests/fixtures/chat-fixture/app.json").read_text(encoding="utf-8")
        )
        manifest["id"] = "notes"
        manifest["version"] = "1.0.0"
        manifest["description"] = marker
        (app_dir / "app.json").write_text(json.dumps(manifest), encoding="utf-8")

    def write_desktops(self, name):
        """A desktops file the way the service writes one."""
        from vela.desktops.store import DesktopStore

        path = self.config.data_dir / "desktops.sqlite"
        store = DesktopStore(path)
        existing = store.list()
        if existing:
            store.rename(existing[0]["id"], name, existing[0]["revision"])
        else:
            store.create(name, boards={"desktop": [], "phone": []})

    def read_desktop_name(self):
        from vela.desktops.store import DesktopStore

        return DesktopStore(self.config.data_dir / "desktops.sqlite").list()[0]["name"]

    def read_marker(self):
        return json.loads(self.config.settings_file.read_text(encoding="utf-8"))["theme"]

    def read_document(self):
        connection = sqlite3.connect(self.config.data_dir / "app-data.sqlite")
        try:
            return json.loads(connection.execute("SELECT value FROM documents").fetchone()[0])["note"]
        finally:
            connection.close()

    def test_a_restore_brings_back_how_the_desk_was_arranged(self):
        # Appearance and boards used to live in settings.json. They live in
        # desktops.sqlite now, and a restore that stopped bringing the wallpaper
        # back would be a regression nobody asked for.
        self.write_desktops("Before")
        made = self.store.create()
        self.write_desktops("After")
        self.assertEqual(self.read_desktop_name(), "After")

        result = self.store.restore(made["name"])

        self.assertIn("desktops.sqlite", result["restored"])
        self.assertEqual(self.read_desktop_name(), "Before")

    def test_a_damaged_desktops_file_fails_the_drill_rather_than_being_restored(self):
        self.write_desktops("Before")
        made = self.store.create()
        copy = self.store._dir / made["name"] / "desktops.sqlite"
        copy.write_bytes(b"this is not a database")

        drill = self.store.verify(made["name"])
        self.assertFalse(drill["ok"])
        with self.assertRaises(BackupError):
            self.store.restore(made["name"])

    def test_a_backup_restores_the_settings_state_and_app_data_it_captured(self):
        made = self.store.create()
        self.write_state("changed")
        self.assertEqual(self.read_marker(), "changed")

        result = self.store.restore(made["name"])

        self.assertEqual(self.read_marker(), "original")
        self.assertEqual(self.read_document(), "original")
        self.assertEqual(
            json.loads((self.config.installed_dir / "notes/app.json").read_text())["description"],
            "original",
        )
        self.assertIn("settings.json", result["restored"])
        self.assertIn("app-data.sqlite", result["restored"])

    def test_a_restore_saves_a_copy_of_what_it_replaced_first(self):
        made = self.store.create()
        self.write_state("changed")
        result = self.store.restore(made["name"])

        safety = result["safety"]
        self.assertTrue(safety.startswith(SAFETY_PREFIX))
        self.assertTrue((self.config.data_dir / "backups" / safety).is_dir())
        # And it is a real backup: restoring it undoes the restore.
        self.store.restore(safety)
        self.assertEqual(self.read_marker(), "changed")

    def test_a_safety_copy_is_marked_so_it_is_not_mistaken_for_one_you_made(self):
        made = self.store.create()
        self.store.restore(made["name"])
        entries = {entry["name"]: entry for entry in self.store.list()}
        self.assertTrue(any(entry["safety"] for entry in entries.values()))
        self.assertFalse(entries[made["name"]]["safety"])

    def test_restoring_a_backup_that_does_not_verify_changes_nothing(self):
        made = self.store.create()
        self.write_state("changed")
        # Corrupt the copy after it was taken.
        (self.config.data_dir / "backups" / made["name"] / "settings.json").write_text(
            "{not json", encoding="utf-8"
        )
        with self.assertRaises(BackupError):
            self.store.restore(made["name"])
        self.assertEqual(self.read_marker(), "changed", "a failed verify must change nothing")
        # And it did not take a safety copy either, because nothing happened.
        self.assertFalse(any(entry["safety"] for entry in self.store.list()))

    def test_an_unknown_or_malformed_name_is_refused(self):
        for bad in ("", "nope", "../../settings.json", "20260101"):
            with self.subTest(name=bad):
                with self.assertRaises(BackupError):
                    self.store.restore(bad)

    def test_running_process_apps_are_stopped_and_started_again(self):
        made = self.store.create()
        stopped, launched = [], []

        class FakeLifecycle:
            class registry:
                @staticmethod
                def list_apps():
                    return [
                        {"id": "notes", "running": True, "runtime": "process"},
                        {"id": "web-thing", "running": True, "runtime": "web"},
                        {"id": "idle", "running": False, "runtime": "process"},
                    ]

            def stop_app(self, app_id):
                stopped.append(app_id)

            def launch_app(self, app_id):
                launched.append(app_id)

        result = self.store.restore(made["name"], lifecycle=FakeLifecycle())
        # Only the running process app: a web app has nothing to stop, and an
        # app that was not running must not be started by a restore.
        self.assertEqual(stopped, ["notes"])
        self.assertEqual(launched, ["notes"])
        self.assertEqual(result["stopped"], ["notes"])
        self.assertEqual(result["restarted"], ["notes"])
        self.assertEqual(result["failedToRestart"], [])

    def test_nothing_is_restored_when_an_app_will_not_stop(self):
        made = self.store.create()
        self.write_state("changed")

        class StubbornLifecycle:
            class registry:
                @staticmethod
                def list_apps():
                    return [{"id": "notes", "running": True, "runtime": "process"}]

            def stop_app(self, app_id):
                raise RuntimeError("will not stop")

        with self.assertRaises(BackupError):
            self.store.restore(made["name"], lifecycle=StubbornLifecycle())
        self.assertEqual(self.read_marker(), "changed")

    def test_an_app_that_will_not_start_again_is_reported_not_hidden(self):
        made = self.store.create()

        class HalfLifecycle:
            class registry:
                @staticmethod
                def list_apps():
                    return [{"id": "notes", "running": True, "runtime": "process"}]

            def stop_app(self, app_id):
                pass

            def launch_app(self, app_id):
                raise RuntimeError("port taken")

        result = self.store.restore(made["name"], lifecycle=HalfLifecycle())
        self.assertEqual(result["failedToRestart"], ["notes"])
        # The restore itself still happened.
        self.assertEqual(self.read_marker(), "original")

    def test_stats_report_the_count_size_and_newest_backup(self):
        self.assertEqual(self.store.stats()["count"], 0)
        self.assertIsNone(self.store.stats()["lastSuccessAt"])
        made = self.store.create()
        stats = self.store.stats()
        self.assertEqual(stats["count"], 1)
        self.assertEqual(stats["lastName"], made["name"])
        self.assertGreater(stats["totalSize"], 0)

    def test_stats_ignore_a_safety_copy_when_naming_the_newest_backup(self):
        made = self.store.create()
        self.store.restore(made["name"])
        self.assertEqual(self.store.stats()["lastName"], made["name"])

    def test_retention_prunes_old_backups_but_keeps_the_safety_copies(self):
        store = BackupStore(self.config, keep=2)
        names = []
        with mock.patch("vela.backups.datetime") as clock:
            for n in range(5):
                clock.now.return_value = datetime(2026, 6, 1, 3, 0, n)
                clock.strptime = datetime.strptime
                names.append(store.create()["name"])
        kept = [entry["name"] for entry in store.list()]
        self.assertEqual(len(kept), 2)
        self.assertEqual(sorted(kept), sorted(names[-2:]))

    def test_safety_copies_are_bounded_on_their_own_count(self):
        store = BackupStore(self.config, keep=2)
        made = store.create()
        for _ in range(KEEP_SAFETY + 2):
            store.restore(made["name"])
        safety = [entry for entry in store.list() if entry["safety"]]
        self.assertLessEqual(len(safety), KEEP_SAFETY)

    def test_set_keep_follows_the_stored_schedule(self):
        self.store.set_keep(3)
        self.assertEqual(self.store.stats()["keep"], 3)


class ThemeBackupTests(unittest.TestCase):
    """A theme somebody imported is their work, and lives nowhere else."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-backups-themes-")
        self.root = Path(self.temp.name)
        self.config = Config(self.root / "data", self.root / "catalog", ROOT / "web/dist")
        self.config.ensure_dirs()
        self.store = BackupStore(self.config)
        self.themes = self.config.data_dir / "themes"
        self.themes.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp.cleanup()

    def write_theme(self, slug, **overrides):
        document = {
            "schema_version": 1,
            "slug": slug,
            "name": slug.title(),
            "author": "Someone",
            "version": "1.0.0",
            "bases": ["light"],
            "tokens": {"light": {"--bg": "#eeeeee", "--text": "#111111"}},
        }
        document.update(overrides)
        (self.themes / f"{slug}.json").write_text(json.dumps(document), encoding="utf-8")
        return document

    def test_an_imported_theme_survives_a_backup_and_a_restore(self):
        kept = self.write_theme("friend")
        backup = self.store.create()
        (self.themes / "friend.json").unlink()

        report = self.store.restore(backup["name"])
        self.assertIn("themes/friend.json", report["restored"])
        self.assertEqual(report["skipped"], [])
        self.assertEqual(
            json.loads((self.themes / "friend.json").read_text(encoding="utf-8")), kept)

    def test_a_theme_this_version_cannot_read_is_named_rather_than_restored(self):
        """Not restored, not silently dropped: said out loud in the report."""
        self.write_theme("friend")
        (self.themes / "stale.json").write_text(
            json.dumps({"schema_version": 99, "slug": "stale"}), encoding="utf-8")
        backup = self.store.create()
        for name in ("friend.json", "stale.json"):
            (self.themes / name).unlink()

        report = self.store.restore(backup["name"])
        self.assertIn("themes/friend.json", report["restored"])
        self.assertEqual(report["skipped"], ["themes/stale.json"])
        self.assertTrue((self.themes / "friend.json").is_file())
        self.assertFalse((self.themes / "stale.json").is_file())

    def test_the_selected_theme_comes_back_with_the_settings(self):
        self.write_theme("friend")
        settings = self.config.data_dir / "settings.json"
        settings.write_text(json.dumps({"theme": "light", "theme_id": "friend"}),
                            encoding="utf-8")
        backup = self.store.create()
        settings.write_text(json.dumps({"theme": "dark", "theme_id": "vela"}), encoding="utf-8")

        self.store.restore(backup["name"])
        stored = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(stored["theme_id"], "friend")

    def test_a_backup_from_before_themes_existed_restores_without_them(self):
        backup = self.store.create()
        shutil.rmtree(self.store._dir / backup["name"] / "themes", ignore_errors=True)
        self.write_theme("later")
        report = self.store.restore(backup["name"])
        self.assertEqual(report["skipped"], [])
        # Nothing in the backup means nothing to put back, not "remove what is
        # there": a restore replaces what it carries and no more.
        self.assertTrue((self.themes / "later.json").is_file())

    def test_the_bundled_themes_are_not_copied_into_every_backup(self):
        self.write_theme("friend")
        backup = self.store.create()
        copied = sorted(
            path.name for path in (self.store._dir / backup["name"] / "themes").glob("*.json"))
        self.assertEqual(copied, ["friend.json"])


class BackupApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-backups-api-")
        self.root = Path(self.temp.name)
        self.config = Config(self.root / "data", self.root / "catalog", ROOT / "web/dist")
        self.config.ensure_dirs()
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_stats_report_the_schedule_alongside_the_backups(self):
        body = self.client.get("/api/backups/stats", headers=self.hub).json()
        self.assertEqual(body["count"], 0)
        self.assertFalse(body["schedule"]["enabled"])
        self.assertIsNone(body["schedule"]["nextRunAt"])

    def test_a_restore_needs_the_confirmation_header(self):
        made = self.client.post("/api/backups", headers=self.hub).json()
        refused = self.client.post(f"/api/backups/{made['name']}/restore", headers=self.hub)
        self.assertEqual(refused.status_code, 428)
        ok = self.client.post(
            f"/api/backups/{made['name']}/restore",
            headers={**self.hub, "X-Vela-Confirm": "restore"},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()["safety"].startswith(SAFETY_PREFIX))

    def test_restoring_an_unknown_backup_is_a_409_not_a_crash(self):
        response = self.client.post(
            "/api/backups/20200101-000000/restore",
            headers={**self.hub, "X-Vela-Confirm": "restore"},
        )
        self.assertEqual(response.status_code, 409)

    def test_the_schedule_is_stored_through_settings_and_checked_first(self):
        bad = self.client.patch(
            "/api/settings", headers=self.hub, json={"backups": {"schedule": {"time": "99:99"}}}
        )
        self.assertEqual(bad.status_code, 422)
        good = self.client.patch(
            "/api/settings",
            headers=self.hub,
            json={"backups": {"schedule": {"enabled": True, "time": "04:15", "keep": 4}}},
        )
        self.assertEqual(good.status_code, 200)
        schedule = self.client.get("/api/backups/stats", headers=self.hub).json()["schedule"]
        self.assertTrue(schedule["enabled"])
        self.assertEqual(schedule["time"], "04:15")
        self.assertEqual(schedule["keep"], 4)
        self.assertTrue(schedule["nextRunAt"])

    def test_a_restore_is_recorded_in_the_activity_log(self):
        import logging

        from vela.logging_setup import configure_logging, teardown_logging

        made = self.client.post("/api/backups", headers=self.hub).json()
        configure_logging(self.config)
        try:
            self.client.post(
                f"/api/backups/{made['name']}/restore",
                headers={**self.hub, "X-Vela-Confirm": "restore"},
            )
            for handler in logging.getLogger("vela.audit").handlers:
                handler.flush()
            text = (self.config.logs_dir / "audit.log").read_text(encoding="utf-8")
        finally:
            teardown_logging()
        self.assertIn("restore", text)
        self.assertIn(f"backup={made['name']}", text)


if __name__ == "__main__":
    unittest.main()
