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
    capability,
    checksum_asset,
    platform_asset,
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


if __name__ == "__main__":
    unittest.main()
