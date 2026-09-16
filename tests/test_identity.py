"""The server's two names, and the letter the rail draws from them.

Both names are labels the user chose: the server name is not the address Vela
answers on, and nothing here changes how the server listens. The one rule worth
testing is that the avatar letter is derived, never accepted from a caller —
two surfaces showing a different letter for the same person would be worse than
not offering the choice at all.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import test_app_contract as base
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.config import Config
from vela.settings import MAX_NAME, SettingsStore, normalize_identity

ROOT = base.ROOT


class NormalizeTests(unittest.TestCase):
    def test_names_are_trimmed_and_kept_as_given(self):
        self.assertEqual(
            normalize_identity({"displayName": "  Marco  ", "serverName": " vela.home "}),
            {"displayName": "Marco", "serverName": "vela.home"},
        )

    def test_a_field_that_is_absent_is_left_alone_rather_than_blanked(self):
        # A patch that only sets one name must not clear the other.
        self.assertEqual(normalize_identity({"displayName": "Marco"}), {"displayName": "Marco"})

    def test_a_name_may_be_cleared(self):
        self.assertEqual(normalize_identity({"displayName": ""}), {"displayName": ""})

    def test_the_initial_is_never_taken_from_the_caller(self):
        # `initial` is derived on read, so a caller cannot set a letter that
        # disagrees with the name beside it.
        self.assertNotIn("initial", normalize_identity({"displayName": "Marco", "initial": "Z"}))

    def test_an_unknown_field_or_a_wrong_type_is_refused(self):
        for payload in ({"nickname": "M"}, {"displayName": 7}, "Marco", ["Marco"]):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                normalize_identity(payload)

    def test_a_name_longer_than_the_limit_is_refused(self):
        with self.assertRaises(ValueError):
            normalize_identity({"serverName": "x" * (MAX_NAME + 1)})
        # Exactly the limit is fine.
        self.assertEqual(len(normalize_identity({"serverName": "x" * MAX_NAME})["serverName"]), MAX_NAME)


class InitialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-identity-")
        self.settings = SettingsStore(Path(self.temp.name) / "settings.json")

    def tearDown(self):
        self.temp.cleanup()

    def initial(self, **identity):
        self.settings.set("identity", identity)
        return self.settings.get("identity")["initial"]

    def test_the_letter_comes_from_the_display_name(self):
        self.assertEqual(self.initial(displayName="Marco"), "M")
        self.assertEqual(self.initial(displayName="marco"), "M")

    def test_punctuation_and_spaces_are_skipped(self):
        self.assertEqual(self.initial(displayName="  ·  marco"), "M")

    def test_an_accent_is_kept_rather_than_folded_away(self):
        self.assertEqual(self.initial(displayName="Ávila"), "Á")

    def test_a_digit_counts_as_a_letter(self):
        self.assertEqual(self.initial(displayName="1Password"), "1")

    def test_the_server_name_is_used_when_there_is_no_display_name(self):
        self.assertEqual(self.initial(displayName="", serverName="vela.marco.house"), "V")

    def test_no_names_means_no_letter_rather_than_a_placeholder(self):
        # The rail draws no avatar at all in this case; it does not invent "V".
        self.assertEqual(self.initial(displayName="", serverName=""), "")


class IdentityApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-identity-api-")
        self.root = Path(self.temp.name)
        apps = self.root / "catalog"
        shutil.copytree(ROOT / "tests/fixtures/chat-fixture", apps / "chat-fixture")
        self.config = Config(self.root / "data", apps, ROOT / "web/dist")
        self.config.ensure_dirs()
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def identity(self):
        return self.client.get("/api/settings", headers=self.hub).json()["identity"]

    def test_a_fresh_server_has_no_names_and_no_letter(self):
        self.assertEqual(
            self.identity(), {"serverName": "", "displayName": "", "initial": ""}
        )

    def test_naming_the_server_stores_both_and_derives_the_letter(self):
        saved = self.client.patch(
            "/api/settings",
            headers=self.hub,
            json={"identity": {"displayName": "Marco", "serverName": "vela.marco.house"}},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(
            self.identity(),
            {"displayName": "Marco", "serverName": "vela.marco.house", "initial": "M"},
        )
        # The letter follows a rename rather than sticking.
        self.client.patch(
            "/api/settings", headers=self.hub, json={"identity": {"displayName": "Ana"}}
        )
        after = self.identity()
        self.assertEqual(after["initial"], "A")
        # Setting one name leaves the other where it was.
        self.assertEqual(after["serverName"], "vela.marco.house")

    def test_a_bad_identity_is_refused_with_a_reason(self):
        for payload in ({"nickname": "M"}, {"displayName": 7}, {"serverName": "x" * 61}):
            with self.subTest(payload=payload):
                refused = self.client.patch(
                    "/api/settings", headers=self.hub, json={"identity": payload}
                )
                self.assertEqual(refused.status_code, 422, refused.text)
        # Nothing was stored by any of those.
        self.assertEqual(self.identity()["displayName"], "")

    def test_the_names_need_a_hub_session(self):
        self.assertEqual(
            self.client.patch("/api/settings", json={"identity": {"displayName": "M"}}).status_code,
            401,
        )

    def test_naming_the_server_does_not_change_how_it_listens(self):
        # The server name is a label. Nothing about the bind address, the
        # certificate or remote access moves because of it.
        before = self.client.get("/api/system/metrics", headers=self.hub).json()["network"]["mode"]
        self.client.patch(
            "/api/settings",
            headers=self.hub,
            json={"identity": {"serverName": "vela.somewhere.else"}},
        )
        after = self.client.get("/api/system/metrics", headers=self.hub).json()["network"]["mode"]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
