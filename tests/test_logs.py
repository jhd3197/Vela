"""Reading Vela's own logs: listing, tailing, searching, clearing and the
directory boundary that keeps every other file on the computer out of reach.

Everything here runs against a disposable data directory.
"""

import logging
import unittest
import shutil
import tempfile
from pathlib import Path

import test_app_contract as base
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.config import Config
from vela.logging_setup import audit, configure_logging, teardown_logging
from vela.logs import LogError, LogStore

ROOT = base.ROOT


class LogStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-logs-")
        self.dir = Path(self.temp.name)
        (self.dir / "server.log").write_text(
            "".join(f"line {n}\n" for n in range(1, 11)), encoding="utf-8"
        )
        (self.dir / "server.log.1").write_text("older\n", encoding="utf-8")
        (self.dir / "audit.log").write_text("install actor=local app=notes\n", encoding="utf-8")
        (self.dir / "notes.log").write_text("app said hello\nWARNING something\n", encoding="utf-8")
        self.store = LogStore(self.dir)

    def tearDown(self):
        self.temp.cleanup()

    def test_files_report_kind_and_group_rotated_under_their_base(self):
        files = {entry["name"]: entry for entry in self.store.files()}
        self.assertEqual(files["server.log"]["kind"], "server")
        self.assertEqual(files["audit.log"]["kind"], "audit")
        self.assertEqual(files["notes.log"]["kind"], "app")
        self.assertEqual(files["server.log.1"]["base"], "server.log")
        self.assertTrue(files["server.log.1"]["rotated"])
        self.assertFalse(files["server.log"]["rotated"])
        self.assertEqual(files["server.log"]["size"], (self.dir / "server.log").stat().st_size)

    def test_tail_returns_the_end_of_the_file_and_reports_truncation(self):
        result = self.store.read("server.log", lines=3)
        self.assertEqual(result["lines"], ["line 8", "line 9", "line 10"])
        self.assertEqual(result["total"], 10)
        self.assertTrue(result["truncated"])

    def test_head_returns_the_beginning(self):
        result = self.store.read("server.log", lines=2, from_end=False)
        self.assertEqual(result["lines"], ["line 1", "line 2"])
        self.assertTrue(result["truncated"])

    def test_a_whole_short_file_is_not_truncated(self):
        result = self.store.read("notes.log", lines=200)
        self.assertEqual(result["lines"], ["app said hello", "WARNING something"])
        self.assertFalse(result["truncated"])

    def test_tail_spans_more_than_one_read_block(self):
        # The tail walks the file backwards in blocks; a file larger than one
        # block must still return the true last lines.
        big = self.dir / "big.log"
        big.write_text("".join(f"entry {n}\n" for n in range(50_000)), encoding="utf-8")
        result = self.store.read("big.log", lines=2)
        self.assertEqual(result["lines"], ["entry 49998", "entry 49999"])
        self.assertEqual(result["total"], 50_000)

    def test_search_is_a_case_insensitive_substring_by_default(self):
        result = self.store.search("notes.log", "warning")
        self.assertEqual(result["lines"], ["WARNING something"])
        self.assertEqual(result["total"], 1)

    def test_search_treats_a_slash_wrapped_pattern_as_a_regex(self):
        result = self.store.search("server.log", "/line 1[0-9]/")
        self.assertEqual(result["lines"], ["line 10"])
        # The same text without the slashes is a plain substring, so it also
        # matches "line 1" itself.
        plain = self.store.search("server.log", "line 1")
        self.assertEqual(plain["lines"], ["line 1", "line 10"])

    def test_search_reports_a_bad_regex_instead_of_raising_re_error(self):
        with self.assertRaises(LogError):
            self.store.search("server.log", "/[/")

    def test_search_counts_every_match_but_returns_at_most_the_asked_lines(self):
        result = self.store.search("server.log", "line", lines=4)
        self.assertEqual(len(result["lines"]), 4)
        self.assertEqual(result["total"], 10)
        self.assertTrue(result["truncated"])

    def test_names_outside_the_directory_are_refused(self):
        outside = self.dir.parent / "secret.txt"
        outside.write_text("private\n", encoding="utf-8")
        try:
            for name in (
                "../secret.txt",
                "..\\secret.txt",
                "sub/server.log",
                "/etc/passwd",
                "C:\\Windows\\win.ini",
                "",
                "..",
            ):
                with self.subTest(name=name):
                    with self.assertRaises(LogError):
                        self.store.read(name)
                    with self.assertRaises(LogError):
                        self.store.clear(name)
        finally:
            outside.unlink()

    def test_a_symlink_out_of_the_directory_is_refused(self):
        outside = self.dir.parent / "outside.log"
        outside.write_text("private\n", encoding="utf-8")
        link = self.dir / "linked.log"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("this platform does not allow creating symlinks here")
        try:
            with self.assertRaises(LogError):
                self.store.read("linked.log")
        finally:
            link.unlink()
            outside.unlink()

    def test_clear_truncates_in_place_so_an_open_handler_keeps_writing(self):
        path = self.dir / "server.log"
        with path.open("a", encoding="utf-8") as handle:
            self.store.clear("server.log")
            self.assertEqual(path.read_text(encoding="utf-8"), "")
            handle.write("after clear\n")
        self.assertEqual(path.read_text(encoding="utf-8"), "after clear\n")

    def test_an_unknown_log_is_not_created_by_reading_it(self):
        with self.assertRaises(LogError):
            self.store.read("nothing.log")
        self.assertFalse((self.dir / "nothing.log").exists())


class LoggingSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-logsetup-")
        self.config = Config(Path(self.temp.name), Path(self.temp.name) / "apps", ROOT / "web/dist")
        self.config.ensure_dirs()

    def tearDown(self):
        teardown_logging()
        self.temp.cleanup()

    def test_configure_writes_a_server_log_and_quiets_the_request_noise(self):
        configure_logging(self.config)
        logging.getLogger("vela.test").info("hello from the engine")
        logging.getLogger("httpx").info("HTTP Request: GET /api/apps 200 OK")
        for handler in logging.getLogger().handlers:
            handler.flush()
        text = (self.config.logs_dir / "server.log").read_text(encoding="utf-8")
        self.assertIn("hello from the engine", text)
        self.assertNotIn("HTTP Request", text)

    def test_audit_entries_go_to_their_own_file_with_the_actor(self):
        configure_logging(self.config)
        audit("install", "app=notes", actor="remote")
        for handler in logging.getLogger("vela.audit").handlers:
            handler.flush()
        text = (self.config.logs_dir / "audit.log").read_text(encoding="utf-8")
        self.assertIn("install actor=remote app=notes", text)
        # The audit trail is separate so it is not lost in the request log.
        self.assertNotIn("install actor=remote", (self.config.logs_dir / "server.log").read_text(encoding="utf-8"))

    def test_configuring_twice_does_not_write_every_line_twice(self):
        configure_logging(self.config)
        configure_logging(self.config)
        logging.getLogger("vela.test").info("only once")
        for handler in logging.getLogger().handlers:
            handler.flush()
        text = (self.config.logs_dir / "server.log").read_text(encoding="utf-8")
        self.assertEqual(text.count("only once"), 1)


class LogApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-logs-api-")
        self.root = Path(self.temp.name)
        self.config = Config(self.root / "data", self.root / "catalog", ROOT / "web/dist")
        self.config.ensure_dirs()
        (self.config.logs_dir / "server.log").write_text(
            "started\nWARNING disk is nearly full\n", encoding="utf-8"
        )
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_listing_and_reading_need_the_hub_session(self):
        self.assertEqual(self.client.get("/api/logs").status_code, 401)
        self.assertEqual(self.client.get("/api/logs/server.log").status_code, 401)
        names = [entry["name"] for entry in self.client.get("/api/logs", headers=self.hub).json()["logs"]]
        self.assertIn("server.log", names)

    def test_reading_tails_and_searching_uses_the_pattern_parameter(self):
        body = self.client.get("/api/logs/server.log?lines=1", headers=self.hub).json()
        self.assertEqual(body["lines"], ["WARNING disk is nearly full"])
        found = self.client.get("/api/logs/server.log?pattern=warning", headers=self.hub).json()
        self.assertEqual(found["lines"], ["WARNING disk is nearly full"])
        self.assertEqual(found["total"], 1)

    def test_an_unknown_or_escaping_name_is_a_404(self):
        self.assertEqual(self.client.get("/api/logs/nothing.log", headers=self.hub).status_code, 404)
        self.assertEqual(
            self.client.get("/api/logs/..%2F..%2Fsettings.json", headers=self.hub).status_code, 404
        )

    def test_download_returns_the_file_as_plain_text(self):
        response = self.client.get("/api/logs/server.log/download", headers=self.hub)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))
        self.assertIn("attachment", response.headers.get("content-disposition", ""))
        self.assertIn("started", response.text)

    def test_clearing_requires_the_confirmation_header(self):
        refused = self.client.delete("/api/logs/server.log", headers=self.hub)
        self.assertEqual(refused.status_code, 428)
        self.assertIn("started", (self.config.logs_dir / "server.log").read_text(encoding="utf-8"))
        ok = self.client.delete(
            "/api/logs/server.log", headers={**self.hub, "X-Vela-Confirm": "clear"}
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual((self.config.logs_dir / "server.log").read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
