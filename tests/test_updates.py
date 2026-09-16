"""The update check: version comparison, which asset this computer wants, the
six-hour cache, what happens when GitHub cannot be reached, and — the one that
matters most — that turning the check off means no request is made at all.

Nothing here contacts GitHub. Every test drives a stub transport, and the one
that proves the switch works fails loudly if any request is attempted.
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import httpx
import test_app_contract as base
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.config import Config
from vela.settings import SettingsStore
from vela.updates import (
    CACHE_SECONDS,
    DEFAULT_UPDATES,
    UpdateChecker,
    UpdateError,
    UpdateJob,
    auto_mode_allowed,
    capability,
    checksum_asset,
    parse_sidecar,
    platform_asset,
    posix_swap_script,
    read_journal,
    rollback_available,
    sha256_file,
    startup_report,
    windows_installer_script,
    windows_swap_script,
    write_journal,
)
from vela.version import is_newer, parse_tag, version_tuple

ROOT = base.ROOT

ASSETS = [
    {"name": "vela-server-0.2.0-windows-x64-setup.exe", "browser_download_url": "https://x/setup", "size": 40},
    {"name": "vela-server-0.2.0-windows-x64-setup.exe.sha256", "browser_download_url": "https://x/setup.sha256"},
    {"name": "vela-server-0.2.0-windows-x64.zip", "browser_download_url": "https://x/zip", "size": 30},
    {"name": "vela-server-0.2.0-windows-x64.zip.sha256", "browser_download_url": "https://x/zip.sha256"},
    {"name": "vela-server-0.2.0-linux-x64.tar.gz", "browser_download_url": "https://x/linux", "size": 20},
    {"name": "vela-server-0.2.0-macos-arm64.tar.gz", "browser_download_url": "https://x/macos", "size": 20},
]

RELEASE = {"tag_name": "v0.2.0", "body": "## What changed\n\n- Everything", "assets": ASSETS}


def stub(payload, status=200, on_request=None):
    """A transport that answers from memory and never reaches the network."""

    def handler(request):
        if on_request is not None:
            on_request(request)
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def refusing_transport():
    """A transport that fails the test if it is used at all."""

    def handler(request):  # pragma: no cover - reaching this is the failure
        raise AssertionError(f"a request was made with checking off: {request.url}")

    return httpx.MockTransport(handler)


class VersionTests(unittest.TestCase):
    def test_a_version_is_three_numbers(self):
        self.assertEqual(version_tuple("0.2.10"), (0, 2, 10))
        for bad in ("v1.0.0", "1.0", "1.0.0-rc1", "", "01.0.0"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    version_tuple(bad)

    def test_a_tag_is_read_with_or_without_its_v(self):
        self.assertEqual(parse_tag("v0.2.0"), "0.2.0")
        self.assertEqual(parse_tag("0.2.0"), "0.2.0")
        self.assertIsNone(parse_tag("nightly"))
        self.assertIsNone(parse_tag(""))

    def test_newer_compares_by_number_not_by_text(self):
        self.assertTrue(is_newer("0.2.0", "0.1.10"))
        # The text comparison "0.1.10" > "0.1.9" is false; the number one is true.
        self.assertTrue(is_newer("0.1.10", "0.1.9"))
        self.assertFalse(is_newer("0.1.10", "0.1.10"))
        self.assertFalse(is_newer("0.1.9", "0.1.10"))

    def test_an_unreadable_version_is_never_newer(self):
        self.assertFalse(is_newer("nightly", "0.1.0"))
        self.assertFalse(is_newer("0.2.0", "not-a-version"))


class AssetTests(unittest.TestCase):
    def test_each_capability_asks_for_the_file_it_can_actually_use(self):
        with mock.patch("vela.updates.platform.machine", return_value="AMD64"):
            with mock.patch("vela.updates.sys.platform", "win32"):
                self.assertEqual(
                    platform_asset(ASSETS, kind="installer")["name"],
                    "vela-server-0.2.0-windows-x64-setup.exe",
                )
                self.assertEqual(
                    platform_asset(ASSETS, kind="portable")["name"],
                    "vela-server-0.2.0-windows-x64.zip",
                )
            with mock.patch("vela.updates.sys.platform", "linux"):
                self.assertEqual(
                    platform_asset(ASSETS, kind="tarball")["name"],
                    "vela-server-0.2.0-linux-x64.tar.gz",
                )

    def test_an_apple_silicon_mac_gets_the_arm_tarball(self):
        with mock.patch("vela.updates.platform.machine", return_value="arm64"):
            with mock.patch("vela.updates.sys.platform", "darwin"):
                self.assertEqual(
                    platform_asset(ASSETS, kind="tarball")["name"],
                    "vela-server-0.2.0-macos-arm64.tar.gz",
                )

    def test_the_portable_zip_is_not_mistaken_for_the_installer(self):
        # Both end in "windows-x64"; only the suffix tells them apart.
        with mock.patch("vela.updates.platform.machine", return_value="AMD64"):
            with mock.patch("vela.updates.sys.platform", "win32"):
                self.assertNotEqual(
                    platform_asset(ASSETS, kind="installer")["name"],
                    platform_asset(ASSETS, kind="portable")["name"],
                )

    def test_a_checkout_or_a_container_has_no_asset_to_download(self):
        self.assertIsNone(platform_asset(ASSETS, kind="source"))
        self.assertIsNone(platform_asset(ASSETS, kind="container"))

    def test_a_release_without_this_platform_offers_nothing_rather_than_the_wrong_file(self):
        with mock.patch("vela.updates.platform.machine", return_value="riscv64"):
            with mock.patch("vela.updates.sys.platform", "linux"):
                self.assertIsNone(platform_asset(ASSETS, kind="tarball"))

    def test_the_checksum_sidecar_is_found_by_name(self):
        found = checksum_asset(ASSETS, "vela-server-0.2.0-windows-x64.zip")
        self.assertEqual(found["name"], "vela-server-0.2.0-windows-x64.zip.sha256")
        self.assertIsNone(checksum_asset(ASSETS, "nothing.zip"))

    def test_a_source_checkout_reports_itself_as_source(self):
        # This test suite runs from a checkout, so this is the honest answer.
        with mock.patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("VELA_UPDATE_CAPABILITY", None)
            self.assertEqual(capability(), "source")

    def test_the_capability_can_be_pinned_for_a_distribution_check(self):
        with mock.patch.dict("os.environ", {"VELA_UPDATE_CAPABILITY": "portable"}):
            self.assertEqual(capability(), "portable")


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-updates-")
        self.root = Path(self.temp.name)
        self.config = Config(self.root / "data", self.root / "catalog", ROOT / "web/dist")
        self.config.ensure_dirs()
        self.settings = SettingsStore(self.config.settings_file)
        self.checker = UpdateChecker(self.config, "0.1.10", settings=self.settings)

    def tearDown(self):
        self.temp.cleanup()

    def test_nothing_is_known_before_the_first_check(self):
        status = self.checker.status()
        self.assertEqual(status["current"], "0.1.10")
        self.assertIsNone(status["latest"])
        self.assertFalse(status["available"])
        self.assertIsNone(status["checkedAt"])

    def test_a_newer_release_is_reported_with_its_notes_and_asset(self):
        with mock.patch("vela.updates.capability", return_value="portable"):
            with mock.patch("vela.updates.platform.machine", return_value="AMD64"):
                with mock.patch("vela.updates.sys.platform", "win32"):
                    status = self.checker.check(transport=stub(RELEASE))
        self.assertEqual(status["latest"], "0.2.0")
        self.assertTrue(status["available"])
        self.assertIn("Everything", status["notes"])
        self.assertEqual(status["asset"]["name"], "vela-server-0.2.0-windows-x64.zip")
        self.assertEqual(status["checksum"]["name"], "vela-server-0.2.0-windows-x64.zip.sha256")

    def test_the_same_version_is_not_an_update(self):
        checker = UpdateChecker(self.config, "0.2.0", settings=self.settings)
        status = checker.check(transport=stub(RELEASE))
        self.assertEqual(status["latest"], "0.2.0")
        self.assertFalse(status["available"])

    def test_turning_the_check_off_makes_no_request_at_all(self):
        self.settings.patch({"updates": {"check": False}})
        # The transport raises if it is touched.
        status = self.checker.check(transport=refusing_transport())
        self.assertEqual(status["skipped"], "off")
        self.assertIsNone(status["latest"])
        self.assertFalse(self.checker._cache.exists(), "nothing should have been written")

    def test_a_second_check_inside_six_hours_uses_the_cached_answer(self):
        calls = []
        self.checker.check(transport=stub(RELEASE, on_request=lambda r: calls.append(r)))
        self.assertEqual(len(calls), 1)
        again = self.checker.check(transport=stub(RELEASE, on_request=lambda r: calls.append(r)))
        self.assertEqual(len(calls), 1, "the cached answer should have been used")
        self.assertEqual(again["skipped"], "cached")

    def test_check_now_asks_again_even_inside_the_cache_window(self):
        calls = []
        self.checker.check(transport=stub(RELEASE, on_request=lambda r: calls.append(r)))
        self.checker.check(force=True, transport=stub(RELEASE, on_request=lambda r: calls.append(r)))
        self.assertEqual(len(calls), 2)

    def test_a_stale_cache_is_checked_again(self):
        self.checker.check(transport=stub(RELEASE))
        stale = json.loads(self.checker._cache.read_text(encoding="utf-8"))
        stale["checkedAt"] = (
            datetime.now(timezone.utc) - timedelta(seconds=CACHE_SECONDS + 60)
        ).isoformat(timespec="seconds")
        self.checker._cache.write_text(json.dumps(stale), encoding="utf-8")
        calls = []
        self.checker.check(transport=stub(RELEASE, on_request=lambda r: calls.append(r)))
        self.assertEqual(len(calls), 1)

    def test_a_failed_check_keeps_the_last_good_answer(self):
        self.checker.check(transport=stub(RELEASE))
        broken = httpx.MockTransport(lambda request: httpx.Response(500, json={}))
        status = self.checker.check(force=True, transport=broken)
        # Being offline is not evidence that there is no update.
        self.assertEqual(status["latest"], "0.2.0")
        self.assertTrue(status["error"])

    def test_a_first_check_that_fails_says_so_without_inventing_a_version(self):
        broken = httpx.MockTransport(lambda request: httpx.Response(503, json={}))
        status = self.checker.check(transport=broken)
        self.assertIsNone(status["latest"])
        self.assertTrue(status["error"])
        self.assertFalse(status["available"])

    def test_a_release_with_an_unreadable_tag_is_ignored_not_offered(self):
        status = self.checker.check(transport=stub({"tag_name": "nightly", "assets": []}))
        self.assertIsNone(status["latest"])
        self.assertFalse(status["available"])

    def test_the_request_goes_to_the_address_the_environment_names(self):
        seen = []
        with mock.patch.dict("os.environ", {"VELA_UPDATE_API": "https://fixture.test/releases"}):
            self.checker.check(transport=stub(RELEASE, on_request=lambda r: seen.append(str(r.url))))
        self.assertEqual(seen, ["https://fixture.test/releases"])

    def test_preferences_fall_back_to_the_defaults_when_stored_badly(self):
        self.settings.patch({"updates": {"mode": "nonsense", "hour": 99, "check": "yes"}})
        preferences = self.checker.preferences()
        self.assertEqual(preferences["mode"], DEFAULT_UPDATES["mode"])
        self.assertEqual(preferences["hour"], 23)
        self.assertTrue(preferences["check"])


class UpdateApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-updates-api-")
        self.root = Path(self.temp.name)
        self.config = Config(self.root / "data", self.root / "catalog", ROOT / "web/dist")
        self.config.ensure_dirs()
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_reading_the_status_needs_the_hub_session_and_makes_no_request(self):
        self.assertEqual(self.client.get("/api/updates").status_code, 401)
        body = self.client.get("/api/updates", headers=self.hub).json()
        self.assertIsNone(body["checkedAt"])
        self.assertTrue(body["check"], "checking is on by default")
        self.assertEqual(body["mode"], "notify", "installing automatically is opt-in")

    def test_the_preference_is_stored_and_validated(self):
        self.assertEqual(
            self.client.patch(
                "/api/settings", headers=self.hub, json={"updates": {"mode": "weekly"}}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.patch(
                "/api/settings", headers=self.hub, json={"updates": {"hour": 47}}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.client.patch(
                "/api/settings",
                headers=self.hub,
                json={"updates": {"check": False, "mode": "auto", "hour": 4}},
            ).status_code,
            200,
        )
        body = self.client.get("/api/updates", headers=self.hub).json()
        self.assertFalse(body["check"])
        self.assertEqual(body["mode"], "auto")
        self.assertEqual(body["hour"], 4)

    def test_checking_with_the_preference_off_reports_that_it_did_nothing(self):
        self.client.patch("/api/settings", headers=self.hub, json={"updates": {"check": False}})
        body = self.client.post("/api/updates/check", headers=self.hub).json()
        self.assertEqual(body["skipped"], "off")

    def test_the_doctor_reports_the_update_state_rather_than_checking_itself(self):
        checks = {
            check["key"]: check
            for check in self.client.post("/api/doctor/run", headers=self.hub).json()["checks"]
        }
        # Nothing has been checked yet, so there is nothing to report.
        self.assertEqual(checks["update"]["status"], "skipped")
        self.client.patch("/api/settings", headers=self.hub, json={"updates": {"check": False}})
        checks = {
            check["key"]: check
            for check in self.client.post("/api/doctor/run", headers=self.hub).json()["checks"]
        }
        self.assertEqual(checks["update"]["status"], "skipped")
        self.assertIn("turned off", checks["update"]["detail"])

    def test_the_desk_reads_the_pending_update_from_the_doctor_endpoint(self):
        body = self.client.get("/api/doctor", headers=self.hub).json()
        self.assertIn("update", body)
        self.assertFalse(body["update"]["available"])


class JournalTests(unittest.TestCase):
    """What the next process learns about the update that ran before it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-journal-")
        self.config = Config(
            Path(self.temp.name) / "data", Path(self.temp.name) / "catalog", ROOT / "web/dist"
        )
        self.config.ensure_dirs()

    def tearDown(self):
        self.temp.cleanup()

    def test_nothing_in_flight_means_nothing_to_report(self):
        self.assertIsNone(startup_report(self.config, "0.1.10"))

    def test_the_version_changing_means_it_worked(self):
        write_journal(self.config, {"state": "applying", "version": "0.2.0", "from": "0.1.10"})
        report = startup_report(self.config, "0.2.0")
        self.assertEqual(report["outcome"], "updated")
        self.assertEqual(report["to"], "0.2.0")

    def test_the_version_not_changing_means_it_did_not(self):
        write_journal(self.config, {"state": "applying", "version": "0.2.0", "from": "0.1.10"})
        report = startup_report(self.config, "0.1.10")
        self.assertEqual(report["outcome"], "failed")

    def test_a_recorded_error_is_reported_as_one(self):
        write_journal(self.config, {"state": "error", "version": "0.2.0", "error": "no checksum"})
        report = startup_report(self.config, "0.1.10")
        self.assertEqual(report["outcome"], "error")
        self.assertIn("checksum", report["message"])

    def test_a_report_is_made_once_not_on_every_restart(self):
        write_journal(self.config, {"state": "applying", "version": "0.2.0", "from": "0.1.10"})
        self.assertIsNotNone(startup_report(self.config, "0.2.0"))
        self.assertIsNone(startup_report(self.config, "0.2.0"))

    def test_an_unreadable_journal_reports_nothing_rather_than_raising(self):
        (self.config.data_dir / "updates").mkdir(parents=True, exist_ok=True)
        (self.config.data_dir / "updates" / "update.log").write_text("{not json", encoding="utf-8")
        self.assertEqual(read_journal(self.config), {})
        self.assertIsNone(startup_report(self.config, "0.1.10"))


class ChecksumTests(unittest.TestCase):
    def test_a_sidecar_is_read_and_checked_against_the_file_it_names(self):
        digest = "a" * 64
        self.assertEqual(parse_sidecar(digest + "  vela.zip\n", "vela.zip"), digest)

    def test_a_sidecar_for_another_file_is_refused(self):
        with self.assertRaises(UpdateError) as caught:
            parse_sidecar("a" * 64 + "  other.zip", "vela.zip")
        self.assertIn("other.zip", str(caught.exception))

    def test_a_malformed_sidecar_is_refused(self):
        for bad in ("", "not-a-digest vela.zip", "abc  vela.zip", "a" * 64):
            with self.subTest(text=bad):
                with self.assertRaises(UpdateError):
                    parse_sidecar(bad, "vela.zip")

    def test_the_digest_of_a_file_is_what_sha256_says(self):
        import hashlib

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "x.bin"
            path.write_bytes(b"vela" * 100_000)
            self.assertEqual(sha256_file(path), hashlib.sha256(b"vela" * 100_000).hexdigest())


class AutoModeTests(unittest.TestCase):
    """Unattended means nothing of the user's may be interrupted."""

    def test_a_quiet_server_may_update_itself(self):
        allowed, reason = auto_mode_allowed(
            apps_running=0, automation_running=False, doctor_failing=False
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_a_running_app_an_automation_or_a_failing_check_each_stop_it(self):
        cases = [
            ({"apps_running": 1, "automation_running": False, "doctor_failing": False}, "running"),
            ({"apps_running": 0, "automation_running": True, "doctor_failing": False}, "automation"),
            ({"apps_running": 0, "automation_running": False, "doctor_failing": True}, "health"),
        ]
        for kwargs, expected in cases:
            with self.subTest(**kwargs):
                allowed, reason = auto_mode_allowed(**kwargs)
                self.assertFalse(allowed)
                self.assertIn(expected, reason)


class ScriptTests(unittest.TestCase):
    """The scripts are generated here and checked here. Running one replaces
    the files this process runs from, so that step is not exercised."""

    def test_the_installer_script_waits_for_the_pid_then_installs_silently(self):
        text = windows_installer_script(
            pid=4321,
            setup=Path("C:/data/updates/setup.exe"),
            updates_dir=Path("C:/data/updates"),
            relaunch='"C:/Vela/Vela.exe" --no-open-browser',
        )
        self.assertIn("PID eq 4321", text)
        self.assertIn("goto wait", text)
        for flag in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"):
            self.assertIn(flag, text)
        self.assertIn("/LOG=", text)
        # The installer's own Run entry is skipifsilent, so the script relaunches.
        self.assertIn("Vela.exe", text.split("start ")[-1])
        self.assertTrue(text.endswith("\r\n"))

    def test_the_windows_swap_keeps_the_old_folder_and_puts_it_back_on_failure(self):
        text = windows_swap_script(
            pid=7,
            install_dir=Path("C:/Vela"),
            staged=Path("C:/Vela.new"),
            previous=Path("C:/Vela.previous"),
            relaunch='"C:/Vela/Vela.exe"',
        )
        self.assertIn("PID eq 7", text)
        # The old folder is moved aside, not deleted, and put back if the
        # second move fails.
        self.assertIn('move "C:\\Vela" "C:\\Vela.previous"', text)
        self.assertIn('move "C:\\Vela.new" "C:\\Vela"', text)
        self.assertIn('move "C:\\Vela.previous" "C:\\Vela"', text)

    def test_the_posix_swap_does_the_same_with_shell_commands(self):
        text = posix_swap_script(
            pid=9,
            install_dir=Path("/opt/vela"),
            staged=Path("/opt/vela.new"),
            previous=Path("/opt/vela.previous"),
            relaunch="/opt/vela/Vela",
        )
        self.assertIn("kill -0 9", text)
        self.assertIn('mv "/opt/vela" "/opt/vela.previous"', text)
        self.assertIn('mv "/opt/vela.previous" "/opt/vela"', text)
        self.assertTrue(text.startswith("#!/bin/sh"))


class ApplyTests(unittest.TestCase):
    """The whole apply path with its two dangerous steps injected: nothing is
    downloaded from the network and nothing on this computer is replaced."""

    class _Backups:
        def __init__(self):
            self.made = 0
            self.fail = False

        def create(self):
            if self.fail:
                raise RuntimeError("no room")
            self.made += 1
            return {"name": "fixture"}

    def setUp(self):
        import hashlib

        self.temp = tempfile.TemporaryDirectory(prefix="vela-apply-")
        self.root = Path(self.temp.name)
        self.config = Config(self.root / "data", self.root / "catalog", ROOT / "web/dist")
        self.config.ensure_dirs()
        self.settings = SettingsStore(self.config.settings_file)
        self.checker = UpdateChecker(self.config, "0.1.10", settings=self.settings)
        self.payload = b"a pretend vela release" * 1000
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.asset_name = "vela-server-0.2.0-windows-x64.zip"
        self.checker._write_cache(
            {
                "checkedAt": "2026-09-16T09:00:00+00:00",
                "latest": "0.2.0",
                "notes": "",
                "asset": {
                    "name": self.asset_name,
                    "url": "https://x/asset",
                    "size": len(self.payload),
                },
                "checksum": {"name": self.asset_name + ".sha256", "url": "https://x/sha"},
            }
        )
        self.backups = self._Backups()
        self.job = UpdateJob(self.config, self.checker, backups=self.backups)

    def tearDown(self):
        self.temp.cleanup()

    def transport(self, *, digest=None, name=None):
        body = self.payload
        sidecar = ((digest or self.digest) + "  " + (name or self.asset_name) + "\n").encode()

        def handler(request):
            if str(request.url).endswith("sha"):
                return httpx.Response(200, content=sidecar)
            return httpx.Response(200, content=body, headers={"content-length": str(len(body))})

        return httpx.MockTransport(handler)

    def as_capability(self, kind="portable"):
        return mock.patch("vela.updates.capability", return_value=kind)

    def test_preparing_downloads_verifies_and_backs_up(self):
        with self.as_capability():
            prepared = self.job.prepare(transport=self.transport())
        self.assertTrue(prepared["asset"].is_file())
        self.assertEqual(prepared["version"], "0.2.0")
        self.assertEqual(self.backups.made, 1)
        self.assertEqual(self.job.state()["state"], "backing-up")

    def test_a_checksum_mismatch_stops_before_any_backup_is_taken(self):
        with self.as_capability():
            with self.assertRaises(UpdateError) as caught:
                self.job.prepare(transport=self.transport(digest="b" * 64))
        self.assertIn("checksum", str(caught.exception))
        # The order is the point: nothing was backed up, and the bad file is gone.
        self.assertEqual(self.backups.made, 0)
        self.assertFalse((self.config.data_dir / "updates" / self.asset_name).exists())

    def test_a_sidecar_naming_another_file_is_refused(self):
        with self.as_capability():
            with self.assertRaises(UpdateError):
                self.job.prepare(transport=self.transport(name="something-else.zip"))
        self.assertEqual(self.backups.made, 0)

    def test_a_failed_backup_stops_the_update(self):
        self.backups.fail = True
        with self.as_capability():
            with self.assertRaises(UpdateError) as caught:
                self.job.prepare(transport=self.transport())
        self.assertIn("did not back up", str(caught.exception))

    def test_a_checkout_or_container_is_told_to_update_another_way(self):
        for kind in ("source", "container"):
            with self.subTest(kind=kind):
                with self.as_capability(kind):
                    with self.assertRaises(UpdateError) as caught:
                        self.job.prepare(transport=self.transport())
                self.assertIn("different way", str(caught.exception))

    def test_a_release_with_no_checksum_is_refused(self):
        cached = json.loads(self.checker._cache.read_text(encoding="utf-8"))
        cached["checksum"] = None
        self.checker._cache.write_text(json.dumps(cached), encoding="utf-8")
        with self.as_capability():
            with self.assertRaises(UpdateError) as caught:
                self.job.prepare(transport=self.transport())
        self.assertIn("checksum", str(caught.exception))

    def test_applying_writes_the_script_starts_it_detached_and_stops_the_server(self):
        started, stopped = [], []
        with self.as_capability("installer"):
            self.job.apply(
                transport=self.transport(),
                spawn=lambda path: started.append(path),
                stop=lambda: stopped.append(True),
            )
        self.assertEqual(len(started), 1)
        self.assertTrue(started[0].is_file())
        self.assertIn("/VERYSILENT", started[0].read_text(encoding="utf-8"))
        self.assertEqual(stopped, [True])
        self.assertEqual(self.job.state()["state"], "restarting")

    def test_the_journal_records_the_attempt_for_the_next_process(self):
        with self.as_capability("installer"):
            self.job.apply(transport=self.transport(), spawn=lambda path: None, stop=None)
        journal = read_journal(self.config)
        self.assertEqual(journal["version"], "0.2.0")
        self.assertIn(journal["state"], ("applying", "restarting"))
        self.assertEqual(journal["asset"], self.asset_name)

    def test_a_failure_leaves_the_job_in_error_with_the_reason(self):
        with self.as_capability("installer"):
            with self.assertRaises(UpdateError):
                self.job.apply(transport=self.transport(digest="c" * 64), spawn=lambda p: None)
        state = self.job.state()
        self.assertEqual(state["state"], "error")
        self.assertIn("checksum", state["message"])

    def test_a_second_update_cannot_start_while_one_is_running(self):
        with self.as_capability("installer"):
            self.job.apply(transport=self.transport(), spawn=lambda path: None)
            with self.assertRaises(UpdateError) as caught:
                self.job.apply(transport=self.transport(), spawn=lambda path: None)
        self.assertIn("already running", str(caught.exception))

    def test_staging_refuses_an_archive_that_is_not_a_vela_server(self):
        import zipfile

        archive = self.root / "not-vela.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("readme.txt", "nope")
        (self.root / "install").mkdir()
        with mock.patch("vela.updates.install_root", return_value=self.root / "install"):
            with self.assertRaises(UpdateError) as caught:
                self.job._stage(archive)
        self.assertIn("does not look like a Vela server", str(caught.exception))
        self.assertFalse((self.root / "install.new").exists(), "the staging folder is cleaned up")

    def test_staging_unwraps_the_single_top_level_folder_the_archives_contain(self):
        import zipfile

        archive = self.root / "vela.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("Vela/Vela.exe", "binary")
            bundle.writestr("Vela/_internal/web/index.html", "<html>")
        (self.root / "install").mkdir()
        with mock.patch("vela.updates.install_root", return_value=self.root / "install"):
            with mock.patch("vela.updates.sys.platform", "win32"):
                staged = self.job._stage(archive)
        self.assertTrue((staged / "Vela.exe").is_file())
        self.assertTrue((staged / "_internal/web/index.html").is_file())


class RollbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-rollback-")
        self.root = Path(self.temp.name)
        self.config = Config(self.root / "data", self.root / "catalog", ROOT / "web/dist")
        self.config.ensure_dirs()
        self.checker = UpdateChecker(self.config, "0.2.0")
        self.job = UpdateJob(self.config, self.checker)
        self.install = self.root / "install"
        self.install.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_there_is_nothing_to_roll_back_to_before_an_update(self):
        with mock.patch("vela.updates.install_root", return_value=self.install):
            self.assertFalse(rollback_available(self.config, "portable"))
            with self.assertRaises(UpdateError):
                self.job.rollback(spawn=lambda p: None)

    def test_a_kept_previous_folder_is_what_a_rollback_goes_back_to(self):
        (self.root / "install.previous").mkdir()
        started = []
        with mock.patch("vela.updates.install_root", return_value=self.install):
            with mock.patch("vela.updates.capability", return_value="portable"):
                self.assertTrue(rollback_available(self.config, "portable"))
                self.job.rollback(spawn=lambda path: started.append(path))
        self.assertEqual(len(started), 1)
        # The kept folder is moved into place by the same swap script.
        self.assertIn("install.rollback", started[0].read_text(encoding="utf-8"))

    def test_an_installer_rolls_back_with_the_previous_release_it_kept(self):
        asset = self.config.data_dir / "updates" / "vela-server-0.1.10-windows-x64-setup.exe"
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"setup")
        write_journal(self.config, {"previousAsset": asset.name})
        self.assertTrue(rollback_available(self.config, "installer"))
        started = []
        with mock.patch("vela.updates.install_root", return_value=self.install):
            with mock.patch("vela.updates.capability", return_value="installer"):
                self.job.rollback(spawn=lambda path: started.append(path))
        self.assertIn(asset.name, started[0].read_text(encoding="utf-8"))

    def test_a_source_checkout_has_no_rollback(self):
        self.assertFalse(rollback_available(self.config, "source"))


class ApplyApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-apply-api-")
        self.root = Path(self.temp.name)
        self.config = Config(self.root / "data", self.root / "catalog", ROOT / "web/dist")
        self.config.ensure_dirs()
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_applying_needs_the_confirmation_header(self):
        response = self.client.post("/api/updates/apply", headers=self.hub)
        self.assertEqual(response.status_code, 428)

    def test_applying_with_nothing_to_install_is_refused_not_attempted(self):
        response = self.client.post(
            "/api/updates/apply", headers={**self.hub, "X-Vela-Confirm": "update"}
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("no newer version", response.json()["detail"])

    def test_rolling_back_needs_its_own_header_and_something_to_go_back_to(self):
        self.assertEqual(
            self.client.post("/api/updates/rollback", headers=self.hub).status_code, 428
        )
        response = self.client.post(
            "/api/updates/rollback", headers={**self.hub, "X-Vela-Confirm": "rollback"}
        )
        self.assertEqual(response.status_code, 409)

    def test_the_job_reports_an_idle_state_and_whether_a_rollback_exists(self):
        body = self.client.get("/api/updates/job", headers=self.hub).json()
        self.assertEqual(body["state"], "idle")
        self.assertFalse(body["rollback"])

    def test_the_report_is_empty_when_no_update_ran(self):
        self.assertEqual(self.client.get("/api/updates/report", headers=self.hub).json(), {})

    def test_every_update_route_needs_the_hub_session(self):
        for path in ("/api/updates/job", "/api/updates/apply", "/api/updates/rollback"):
            with self.subTest(path=path):
                method = self.client.get if path.endswith("job") else self.client.post
                self.assertEqual(method(path).status_code, 401)


if __name__ == "__main__":
    unittest.main()
