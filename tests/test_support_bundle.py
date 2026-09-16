"""The support bundle: what it contains, what it must never contain, and that
planted secrets do not survive into it.

The bundle is the one thing Vela builds specifically to be shared, so these
tests plant credentials in every place a collector reads from and assert the
bundle carries none of them.
"""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import test_app_contract as base
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.config import Config
from vela.errors import ErrorStore
from vela.settings import SettingsStore
from vela.support_bundle import REDACTED, SupportBundle, scrub, scrub_value

ROOT = base.ROOT

# Values planted through the fixture. None of these may appear in a bundle.
SECRETS = (
    "hunter2-the-ntfy-password",
    "sk-live-9f8e7d6c5b4a3210",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.s3cr3t-signature-here",
)


class ScrubTests(unittest.TestCase):
    def test_a_bearer_token_keeps_the_scheme_and_loses_the_token(self):
        out = scrub("Authorization: Bearer abcdef1234567890")
        self.assertIn("Bearer", out)
        self.assertNotIn("abcdef1234567890", out)

    def test_a_bare_jwt_is_replaced(self):
        out = scrub(f"token was {SECRETS[2]}")
        self.assertNotIn("s3cr3t-signature-here", out)
        self.assertIn("[REDACTED-JWT]", out)

    def test_an_assignment_that_names_a_credential_loses_its_value(self):
        for line in (
            "password=hunter2-the-ntfy-password",
            'api_key: "sk-live-9f8e7d6c5b4a3210"',
            "ACCESS_TOKEN = abcdef1234567890",
            "client_secret:topsecretvalue",
        ):
            with self.subTest(line=line):
                out = scrub(line)
                self.assertIn(REDACTED, out, out)
                for secret in ("hunter2", "sk-live-9f8e7d6c5b4a3210", "abcdef1234567890", "topsecretvalue"):
                    self.assertNotIn(secret, out)

    def test_ordinary_text_is_left_alone(self):
        line = "2026-09-16 09:00:00 INFO vela.api: installed notes 1.2.0"
        self.assertEqual(scrub(line), line)

    def test_scrubbing_twice_changes_nothing_further(self):
        once = scrub("password=hunter2")
        self.assertEqual(scrub(once), once)

    def test_a_settings_value_is_redacted_by_the_name_of_its_key(self):
        self.assertEqual(scrub_value("pass", "anything at all"), REDACTED)
        self.assertEqual(scrub_value("apiKey", "anything at all"), REDACTED)
        # An empty credential stays empty: "not set" is the useful fact.
        self.assertEqual(scrub_value("pass", ""), "")
        self.assertEqual(scrub_value("theme", "dark"), "dark")

    def test_nested_settings_are_walked(self):
        out = scrub_value("ntfy_config", {"server": "https://ntfy.example", "pass": "hunter2"})
        self.assertEqual(out["server"], "https://ntfy.example")
        self.assertEqual(out["pass"], REDACTED)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-bundle-")
        self.root = Path(self.temp.name)
        self.config = Config(self.root / "data", self.root / "catalog", ROOT / "web/dist")
        self.config.ensure_dirs()

        # Plant a credential in every place a collector reads from.
        self.settings = SettingsStore(self.config.settings_file)
        self.settings.patch(
            {
                "theme": "dark",
                "ntfy_config": {
                    "server": "https://ntfy.example",
                    "topic": "vela",
                    "user": "juan",
                    "pass": SECRETS[0],
                },
            }
        )
        (self.config.logs_dir / "server.log").write_text(
            "\n".join(
                [
                    "2026-09-16 09:00:00 INFO vela.api: started",
                    f"2026-09-16 09:00:01 INFO vela.api: Authorization: Bearer {SECRETS[1]}",
                    f"2026-09-16 09:00:02 INFO vela.api: token={SECRETS[2]}",
                ]
            ),
            encoding="utf-8",
        )
        self.errors = ErrorStore(self.config.data_dir / "diagnostics.sqlite")
        self.errors.record("server", f"login failed with password={SECRETS[0]}", type_="ValueError")

        # Things a bundle must never touch.
        (self.config.data_dir / "app-data.sqlite").write_text(
            "app-storage-that-must-never-be-bundled", encoding="utf-8"
        )
        (self.config.data_dir / "chat.sqlite").write_text(
            "conversation-body-that-must-never-be-bundled", encoding="utf-8"
        )
        (self.config.data_dir / "access.json").write_text(
            json.dumps({"hash": "the-vela-password-hash"}), encoding="utf-8"
        )
        wallpapers = self.config.data_dir / "wallpaper"
        wallpapers.mkdir(exist_ok=True)
        (wallpapers / "mine.png").write_bytes(b"\x89PNG not really")

        self.bundle = SupportBundle(
            self.config, version="0.1.10", settings=self.settings, errors=self.errors
        )

    def tearDown(self):
        self.temp.cleanup()

    def read(self):
        made = self.bundle.build()
        path = self.config.data_dir / "support" / made["name"]
        with zipfile.ZipFile(path) as archive:
            return made, {name: archive.read(name).decode("utf-8", "replace") for name in archive.namelist()}

    def test_the_bundle_has_the_sections_the_plan_lists(self):
        _, contents = self.read()
        for expected in (
            "README.txt",
            "meta.json",
            "settings.json",
            "doctor.json",
            "apps.json",
            "desk.json",
            "errors.json",
            "automations.json",
            "logs/server.log",
        ):
            self.assertIn(expected, contents)

    def test_no_planted_secret_survives_anywhere_in_the_bundle(self):
        _, contents = self.read()
        whole = "\n".join(contents.values())
        for secret in SECRETS:
            self.assertNotIn(secret, whole, f"the bundle leaked {secret[:12]}…")
        # And specifically in the two files that read from the planted sources.
        self.assertNotIn(SECRETS[0], contents["settings.json"])
        self.assertNotIn(SECRETS[1], contents["logs/server.log"])

    def test_the_settings_section_keeps_what_is_diagnostic(self):
        _, contents = self.read()
        settings = json.loads(contents["settings.json"])
        # The address is the useful fact; the password is not.
        self.assertEqual(settings["ntfy_config"]["server"], "https://ntfy.example")
        self.assertEqual(settings["theme"], "dark")

    def test_app_data_chat_history_the_password_and_wallpapers_are_never_included(self):
        _, contents = self.read()
        names = set(contents)
        for forbidden in ("app-data.sqlite", "chat.sqlite", "access.json"):
            self.assertNotIn(forbidden, names)
            self.assertFalse(
                any(forbidden in name for name in names), f"{forbidden} must not be bundled"
            )
        self.assertFalse(any("wallpaper" in name for name in names))
        whole = "\n".join(contents.values())
        self.assertNotIn("the-vela-password-hash", whole)
        self.assertNotIn("conversation-body-that-must-never-be-bundled", whole)

    def test_the_readme_says_where_the_bundle_goes_and_what_is_left_out(self):
        _, contents = self.read()
        readme = contents["README.txt"]
        self.assertIn("did not send it anywhere", readme)
        self.assertIn("What is NOT in it", readme)

    def test_a_broken_collector_does_not_sink_the_bundle(self):
        class ExplodingSettings:
            def public_view(self):
                raise RuntimeError("settings unreadable")

        bundle = SupportBundle(self.config, version="0.1.10", settings=ExplodingSettings())
        made = bundle.build()
        with zipfile.ZipFile(self.config.data_dir / "support" / made["name"]) as archive:
            names = archive.namelist()
            settings = json.loads(archive.read("settings.json"))
        self.assertIn("meta.json", names)
        self.assertIn("error", settings)

    def test_only_the_tail_of_a_long_log_is_taken(self):
        (self.config.logs_dir / "big.log").write_text(
            "\n".join(f"line {n}" for n in range(3000)), encoding="utf-8"
        )
        _, contents = self.read()
        lines = contents["logs/big.log"].splitlines()
        self.assertLessEqual(len(lines), 500)
        self.assertIn("line 2999", lines[-1])

    def test_bundles_are_listed_newest_first_and_a_bad_name_cannot_be_downloaded(self):
        self.bundle.build()
        listing = self.bundle.list()
        self.assertTrue(listing)
        self.assertTrue(self.bundle.path(listing[0]["name"]).is_file())
        for bad in ("../settings.json", "not-a-bundle.zip", "", "vela-support-x.zip"):
            with self.subTest(name=bad):
                with self.assertRaises(FileNotFoundError):
                    self.bundle.path(bad)


class BundleApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-bundle-api-")
        self.root = Path(self.temp.name)
        self.config = Config(self.root / "data", self.root / "catalog", ROOT / "web/dist")
        self.config.ensure_dirs()
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_building_needs_the_hub_session(self):
        self.assertEqual(self.client.post("/api/support-bundle").status_code, 401)
        self.assertEqual(self.client.get("/api/support-bundle").status_code, 401)

    def test_a_bundle_can_be_built_listed_and_downloaded(self):
        made = self.client.post("/api/support-bundle", headers=self.hub)
        self.assertEqual(made.status_code, 201)
        name = made.json()["name"]
        listed = self.client.get("/api/support-bundle", headers=self.hub).json()["bundles"]
        self.assertEqual([entry["name"] for entry in listed], [name])
        download = self.client.get(f"/api/support-bundle/{name}", headers=self.hub)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.headers["content-type"], "application/zip")

    def test_a_name_that_is_not_a_bundle_is_a_404(self):
        # A literal `../..` is normalised away by the client before routing, so
        # the encoded form is what actually reaches the endpoint. The unencoded
        # case is covered at the store, where `path()` refuses it.
        for bad in ("..%2F..%2Fsettings.json", "anything.zip", "vela-support-nope.zip"):
            with self.subTest(name=bad):
                response = self.client.get(f"/api/support-bundle/{bad}", headers=self.hub)
                self.assertEqual(response.status_code, 404)

    def test_building_one_is_recorded_in_the_activity_log(self):
        import logging

        from vela.logging_setup import configure_logging, teardown_logging

        configure_logging(self.config)
        try:
            self.client.post("/api/support-bundle", headers=self.hub)
            for handler in logging.getLogger("vela.audit").handlers:
                handler.flush()
            text = (self.config.logs_dir / "audit.log").read_text(encoding="utf-8")
        finally:
            teardown_logging()
        self.assertIn("support-bundle", text)


if __name__ == "__main__":
    unittest.main()
