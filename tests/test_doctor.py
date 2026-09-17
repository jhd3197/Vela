"""Health checks: the registry's isolation and time budget, what each check
says about a disposable data directory, the two repairs, and the one
notification a newly failing check earns.

Every test builds its own data directory. Nothing here reads or repairs a real
Vela installation.
"""

import asyncio
import json
import os
import socket
import time
import unittest
import tempfile
from pathlib import Path
from unittest import mock

import test_app_contract as base
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.config import Config, write_json_atomic
from vela.doctor import FAIL, OK, SKIPPED, WARN, Doctor, summarise
from vela.notify import NotifyScheduler
from vela.state import StateStore

ROOT = base.ROOT


def _config(root: Path) -> Config:
    config = Config(root / "data", root / "catalog", ROOT / "web/dist")
    config.ensure_dirs()
    return config


class RegistryTests(unittest.TestCase):
    """The lifted registry: one bad check must not spoil the sweep."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-doctor-")
        self.config = _config(Path(self.temp.name))
        self.doctor = Doctor(self.config)
        self.doctor._checks.clear()

    def tearDown(self):
        self.temp.cleanup()

    def test_a_check_that_raises_becomes_one_warning_and_the_sweep_survives(self):
        def explode():
            raise RuntimeError("no")

        self.doctor.register("boom", "Boom", explode)
        self.doctor.register("fine", "Fine", lambda: {"status": OK, "detail": "good"})
        checks = {check["key"]: check for check in self.doctor.collect()["checks"]}
        self.assertEqual(checks["boom"]["status"], WARN)
        self.assertIn("no", checks["boom"]["detail"])
        self.assertEqual(checks["fine"]["status"], OK)

    def test_a_check_that_overruns_is_a_warning_and_does_not_block_the_sweep(self):
        self.doctor.register("slow", "Slow", lambda: time.sleep(5), timeout=0.2)
        self.doctor.register("fast", "Fast", lambda: {"status": OK, "detail": "quick"})
        started = time.monotonic()
        checks = {check["key"]: check for check in self.doctor.collect()["checks"]}
        elapsed = time.monotonic() - started
        self.assertEqual(checks["slow"]["status"], WARN)
        self.assertIn("did not finish in time", checks["slow"]["detail"])
        self.assertEqual(checks["fast"]["status"], OK)
        # The point of the timeout: the sweep does not wait out the slow check.
        self.assertLess(elapsed, 4, f"the sweep waited {elapsed:.1f}s for a timed-out check")

    def test_an_unrenderable_status_is_reported_as_a_warning_not_passed_through(self):
        self.doctor.register("odd", "Odd", lambda: {"status": "purple", "detail": "?"})
        self.doctor.register("empty", "Empty", lambda: "not a dict")
        checks = {check["key"]: check for check in self.doctor.collect()["checks"]}
        self.assertEqual(checks["odd"]["status"], WARN)
        self.assertEqual(checks["empty"]["status"], WARN)

    def test_a_repair_is_only_offered_where_there_is_something_to_repair(self):
        self.doctor.register("a", "A", lambda: {"status": FAIL, "detail": "x"}, repair=lambda: {})
        self.doctor.register("b", "B", lambda: {"status": OK, "detail": "x"}, repair=lambda: {})
        self.doctor.register("c", "C", lambda: {"status": FAIL, "detail": "x"})
        checks = {check["key"]: check for check in self.doctor.collect()["checks"]}
        self.assertTrue(checks["a"]["repairable"])
        # Nothing to fix, so no button.
        self.assertFalse(checks["b"]["repairable"])
        # Broken, but Vela has no repair for it.
        self.assertFalse(checks["c"]["repairable"])

    def test_repairing_an_unknown_or_unrepairable_check_says_so(self):
        self.doctor.register("c", "C", lambda: {"status": FAIL, "detail": "x"})
        self.assertFalse(self.doctor.repair("nothing")["ok"])
        self.assertFalse(self.doctor.repair("c")["ok"])

    def test_a_repair_that_raises_is_reported_rather_than_escaping(self):
        def explode():
            raise RuntimeError("could not")

        self.doctor.register("a", "A", lambda: {"status": FAIL, "detail": "x"}, repair=explode)
        result = self.doctor.repair("a")
        self.assertFalse(result["ok"])
        self.assertIn("could not", result["detail"])

    def test_failing_checks_sort_first_so_the_list_leads_with_the_problem(self):
        self.doctor.register("z-ok", "Z", lambda: {"status": OK, "detail": ""})
        self.doctor.register("a-skip", "A", lambda: {"status": SKIPPED, "detail": ""})
        self.doctor.register("m-fail", "M", lambda: {"status": FAIL, "detail": ""})
        self.doctor.register("n-warn", "N", lambda: {"status": WARN, "detail": ""})
        order = [check["key"] for check in self.doctor.collect()["checks"]]
        self.assertEqual(order, ["m-fail", "n-warn", "z-ok", "a-skip"])

    def test_the_summary_counts_only_the_checks_that_applied(self):
        self.assertEqual(summarise([])["text"], "Vela has not checked itself yet.")
        checks = [
            {"key": "a", "status": OK},
            {"key": "b", "status": SKIPPED},
            {"key": "c", "status": FAIL},
        ]
        summary = summarise(checks)
        self.assertEqual(summary["considered"], 2)
        self.assertEqual(summary["attention"], 1)
        self.assertEqual(summary["text"], "1 of 2 checks need attention.")


class CheckTests(unittest.TestCase):
    """Each shipped check against a data directory built for it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-doctor-checks-")
        self.root = Path(self.temp.name)
        self.config = _config(self.root)
        self.state = StateStore(self.config.state_file)
        self.doctor = Doctor(self.config, state=self.state)

    def tearDown(self):
        self.temp.cleanup()

    def one(self, key):
        return self.doctor._run_one(key, self.doctor._checks[key])

    def test_data_dir_reports_the_free_space_it_found(self):
        check = self.one("data-dir")
        self.assertIn(check["status"], (OK, WARN, FAIL))
        self.assertIn("free", check["detail"])

    def test_data_dir_fails_when_the_directory_is_gone(self):
        doctor = Doctor(Config(self.root / "missing", self.root / "c", ROOT / "web/dist"))
        check = doctor._run_one("data-dir", doctor._checks["data-dir"])
        self.assertEqual(check["status"], FAIL)
        self.assertIn("missing", check["detail"])

    def test_port_fails_when_nothing_is_listening_there(self):
        # Bind a socket, learn a free port, then release it: nothing is on it.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            free = probe.getsockname()[1]
        with mock.patch.dict(os.environ, {"VELA_HOST": "127.0.0.1", "VELA_PORT": str(free)}):
            check = self.one("port")
        self.assertEqual(check["status"], FAIL)
        self.assertIn("not answering", check["detail"])

    def test_port_passes_when_something_answers_there(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            with mock.patch.dict(os.environ, {"VELA_HOST": "127.0.0.1", "VELA_PORT": str(port)}):
                check = self.one("port")
        self.assertEqual(check["status"], OK)

    def test_certificate_is_skipped_without_https_and_fails_when_the_file_is_gone(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VELA_CERT_FILE", None)
            self.assertEqual(self.one("certificate")["status"], SKIPPED)
        with mock.patch.dict(os.environ, {"VELA_CERT_FILE": str(self.root / "nope.pem")}):
            check = self.one("certificate")
        self.assertEqual(check["status"], FAIL)
        self.assertIn("missing", check["detail"])

    def test_stale_apps_finds_a_dead_pid_and_the_repair_clears_only_that_record(self):
        # A PID that cannot be running: 0 is never a live user process.
        self.state.set("ghost", pid=999_999_998, port=None)
        self.state.set("also-ghost", pid=999_999_999, port=None)
        check = self.one("stale-apps")
        self.assertEqual(check["status"], WARN)
        self.assertIn("ghost", check["detail"])
        self.assertTrue(check["repairable"])

        result = self.doctor.repair("stale-apps")
        self.assertTrue(result["ok"])
        self.assertEqual(self.state.all(), {})
        # The check is re-run, so the caller sees the state it is now in.
        self.assertEqual(result["check"]["status"], OK)

    def test_a_live_app_is_not_called_stale(self):
        self.state.set("me", pid=os.getpid(), port=None)
        self.assertEqual(self.one("stale-apps")["status"], OK)

    def test_orphans_finds_a_folder_with_no_manifest_and_repairs_it_after_a_backup(self):
        orphan = self.config.installed_dir / "leftover"
        orphan.mkdir(parents=True)
        (orphan / "something.txt").write_text("x", encoding="utf-8")
        real = self.config.installed_dir / "proper"
        real.mkdir(parents=True)
        (real / "app.json").write_text('{"id": "proper"}', encoding="utf-8")

        check = self.one("orphans")
        self.assertEqual(check["status"], WARN)
        self.assertIn("leftover", check["detail"])

        backed_up = []

        class FakeBackups:
            def create(self):
                backed_up.append(True)
                return {"name": "fixture"}

        doctor = Doctor(self.config, state=self.state, backups=FakeBackups())
        result = doctor.repair("orphans")
        self.assertTrue(result["ok"], result)
        # Nothing is deleted before there is a copy to go back to.
        self.assertEqual(backed_up, [True])
        self.assertFalse(orphan.exists())
        self.assertTrue(real.exists(), "a real app must not be removed")

    def test_orphans_removes_nothing_when_the_backup_fails(self):
        orphan = self.config.installed_dir / "leftover"
        orphan.mkdir(parents=True)

        class FailingBackups:
            def create(self):
                raise RuntimeError("disk full")

        doctor = Doctor(self.config, state=self.state, backups=FailingBackups())
        result = doctor.repair("orphans")
        self.assertFalse(result["ok"])
        self.assertTrue(orphan.exists(), "nothing may be removed without a backup")

    def test_settings_fails_on_an_unreadable_file_and_the_repair_puts_the_last_one_back(self):
        # A good write leaves a .bak behind; the next one is corrupted by hand.
        write_json_atomic(self.config.settings_file, {"theme": "dark"})
        write_json_atomic(self.config.settings_file, {"theme": "light"})
        self.assertTrue(self.config.settings_file.with_suffix(".json.bak").is_file())
        self.config.settings_file.write_text("{not json", encoding="utf-8")

        check = self.one("settings")
        self.assertEqual(check["status"], FAIL)
        self.assertIn("settings.json", check["detail"])

        result = self.doctor.repair("settings")
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            json.loads(self.config.settings_file.read_text(encoding="utf-8")), {"theme": "dark"}
        )
        # The unreadable file is kept rather than deleted: it is the only copy
        # of whatever was lost.
        self.assertTrue(self.config.settings_file.with_suffix(".json.broken").is_file())
        self.assertEqual(self.one("settings")["status"], OK)

    def test_settings_repair_says_so_when_there_is_nothing_to_put_back(self):
        self.config.settings_file.write_text("{not json", encoding="utf-8")
        result = self.doctor.repair("settings")
        self.assertFalse(result["ok"])
        self.assertIn("no earlier copy", result["detail"])

    def test_backups_warns_when_there_are_none(self):
        class EmptyBackups:
            def list(self):
                return []

        doctor = Doctor(self.config, backups=EmptyBackups())
        check = doctor._run_one("backups", doctor._checks["backups"])
        self.assertEqual(check["status"], WARN)
        self.assertIn("never backed itself up", check["detail"])

    def test_backups_warns_when_the_newest_is_old(self):
        class OldBackups:
            def list(self):
                return [{"name": "old", "created_at": "2020-01-01T00:00:00", "size": 1}]

        doctor = Doctor(self.config, backups=OldBackups())
        self.assertEqual(doctor._run_one("backups", doctor._checks["backups"])["status"], WARN)

    def test_catalog_and_ollama_are_skipped_when_not_configured(self):
        self.assertEqual(self.one("catalog")["status"], SKIPPED)
        self.assertEqual(self.one("ollama")["status"], SKIPPED)

    def test_update_is_skipped_until_vela_can_check_for_one(self):
        self.assertEqual(self.one("update")["status"], SKIPPED)

    def test_every_shipped_check_answers_something_renderable(self):
        result = self.doctor.collect()
        self.assertEqual(len(result["checks"]), len(self.doctor.keys()))
        for check in result["checks"]:
            with self.subTest(key=check["key"]):
                self.assertIn(check["status"], (OK, WARN, FAIL, SKIPPED))
                self.assertTrue(check["title"])
                self.assertTrue(check["detail"], "a check must explain itself")
                self.assertTrue(check["ranAt"])


class NotificationTests(unittest.TestCase):
    """A newly failing check is announced once, not once a day forever."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-doctor-notify-")
        self.config = _config(Path(self.temp.name))
        self.published = []

        class FakeNotifier:
            def config(_self):
                return {"server": "https://ntfy.example", "topic": "vela", "events": {}}

            async def publish(_self, title, message, **kwargs):
                self.published.append((title, message, kwargs.get("kind")))
                return {"ok": True}

        self.notifier = FakeNotifier()
        self.doctor = Doctor(self.config)
        self.doctor._checks.clear()
        self.failing = {"status": FAIL, "detail": "The data directory is full."}
        self.doctor.register("disk", "Room to work", lambda: dict(self.failing))
        self.scheduler = NotifyScheduler(self.notifier, None, self.config, doctor=self.doctor)

    def tearDown(self):
        self.temp.cleanup()

    def test_a_failure_is_announced_once_and_again_only_after_it_clears(self):
        asyncio.run(self.scheduler.run_doctor())
        self.assertEqual(len(self.published), 1)
        self.assertEqual(self.published[0][2], "health")
        self.assertIn("full", self.published[0][1])

        # Still failing tomorrow: no second notification.
        asyncio.run(self.scheduler.run_doctor())
        self.assertEqual(len(self.published), 1)

        # Fixed, then broken again: that is news, so it is announced again.
        self.failing = {"status": OK, "detail": "Plenty of room."}
        asyncio.run(self.scheduler.run_doctor())
        self.assertEqual(len(self.published), 1)
        self.failing = {"status": FAIL, "detail": "The data directory is full."}
        asyncio.run(self.scheduler.run_doctor())
        self.assertEqual(len(self.published), 2)

    def test_nothing_is_published_without_a_notification_destination(self):
        class Unconfigured:
            def config(_self):
                return {"server": "", "topic": "", "events": {}}

            async def publish(_self, *args, **kwargs):  # pragma: no cover - must not run
                raise AssertionError("published without a destination")

        scheduler = NotifyScheduler(Unconfigured(), None, self.config, doctor=self.doctor)
        result = asyncio.run(scheduler.run_doctor())
        # The sweep still happens: the desk and Settings read it either way.
        self.assertEqual(result["summary"]["attention"], 1)

    def test_a_warning_is_not_announced(self):
        self.failing = {"status": WARN, "detail": "Getting low."}
        asyncio.run(self.scheduler.run_doctor())
        self.assertEqual(self.published, [])


class DoctorApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-doctor-api-")
        self.root = Path(self.temp.name)
        self.config = _config(self.root)
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_reading_and_running_need_the_hub_session(self):
        self.assertEqual(self.client.get("/api/doctor").status_code, 401)
        self.assertEqual(self.client.post("/api/doctor/run").status_code, 401)
        self.assertEqual(self.client.post("/api/doctor/data-dir/repair").status_code, 401)

    def test_reading_before_a_run_reports_no_result_rather_than_starting_one(self):
        body = self.client.get("/api/doctor", headers=self.hub).json()
        self.assertEqual(body["checks"], [])
        self.assertIsNone(body["ranAt"])
        self.assertEqual(body["summary"]["text"], "Vela has not checked itself yet.")

    def test_running_returns_every_check_and_the_result_is_then_readable(self):
        body = self.client.post("/api/doctor/run", headers=self.hub).json()
        self.assertEqual(len(body["checks"]), 16)
        self.assertTrue(body["ranAt"])
        again = self.client.get("/api/doctor", headers=self.hub).json()
        self.assertEqual(again["ranAt"], body["ranAt"])

    def test_an_unknown_check_cannot_be_repaired(self):
        response = self.client.post("/api/doctor/not-a-check/repair", headers=self.hub)
        self.assertEqual(response.status_code, 422)

    def test_a_repair_is_recorded_in_the_activity_log(self):
        from vela.logging_setup import configure_logging, teardown_logging
        import logging

        configure_logging(self.config)
        try:
            self.client.post("/api/doctor/stale-apps/repair", headers=self.hub)
            for handler in logging.getLogger("vela.audit").handlers:
                handler.flush()
            text = (self.config.logs_dir / "audit.log").read_text(encoding="utf-8")
        finally:
            teardown_logging()
        self.assertIn("repair", text)
        self.assertIn("check=stale-apps", text)


if __name__ == "__main__":
    unittest.main()
