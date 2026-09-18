"""Themes: the validators, the bundled set, the settings link and the HTTP surface.

A theme is the one thing a user can hand Vela that decides what every screen
looks like, so the tests that matter here are the refusals. A theme that carried
CSS, or a font the browser would have to fetch, or a `url(`, would be a program
somebody else wrote running on a dashboard that also shows this person's files.

Everything uses disposable data directories.
"""

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import test_app_contract as base
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.config import Config
from vela.themes import (
    BUNDLED_WALLPAPERS,
    CANONICAL_TOKENS,
    MAX_IMPORTED,
    MAX_THEME_BYTES,
    STOCK_SLUG,
    ThemeError,
    Themes,
    expand_aliases,
    sanitize_tokens,
    swatches,
    validate_document,
    validate_token,
)

ROOT = base.ROOT


def a_theme(**overrides):
    """A minimal valid theme, for a test to break one thing about."""
    document = {
        "schema_version": 1,
        "slug": "friend",
        "name": "From a friend",
        "author": "Someone",
        "version": "1.0.0",
        "bases": ["light"],
        "tokens": {
            "light": {
                "--bg": "#eeeeee",
                "--bg-card": "#ffffff",
                "--text": "#111111",
                "--accent": "#5533cc",
            }
        },
    }
    document.update(overrides)
    return document


class TokenValueTests(unittest.TestCase):
    def test_a_colour_may_be_a_hex_an_rgb_or_a_color_mix(self):
        for good in ("#fff", "#ffffff", "#ffffffaa", "rgb(1, 2, 3)", "rgba(1, 2, 3, 0.5)",
                     "color-mix(in srgb, #ffffff 14%, transparent)"):
            self.assertEqual(validate_token("--bg", good), good, good)

    def test_a_theme_can_never_reach_the_network(self):
        """`url(` is the only way out, so it is refused everywhere.

        A theme that could fetch a font or an image is a theme that can tell
        somebody else when this dashboard was opened and from where.
        """
        for reaching in (
            "url(https://example.test/x.png)",
            "URL(//evil.test/a)",
            "#fff; background: url(//evil.test/a)",
            "@import url(x)",
            "expression(alert(1))",
            "javascript:alert(1)",
        ):
            self.assertIsNone(validate_token("--bg", reaching), reaching)
            self.assertIsNone(validate_token("--glow", reaching), reaching)
            self.assertIsNone(validate_token("--shadow-md", reaching), reaching)

    def test_a_value_cannot_close_its_declaration_and_open_another(self):
        for escaping in ("#fff}", "#fff;color:red", "#fff{", "<script>", "#fff/*x*/", "#fff\\"):
            self.assertIsNone(validate_token("--bg", escaping), escaping)

    def test_a_font_comes_from_the_allow_list_and_nowhere_else(self):
        allowed = "'Inter', system-ui, -apple-system, 'Segoe UI', sans-serif"
        self.assertEqual(validate_token("--font", allowed), allowed)
        # A face Vela does not already load would have to be fetched.
        self.assertIsNone(validate_token("--font", "'Comic Sans MS', cursive"))
        self.assertIsNone(validate_token("--font", "Inter"))

    def test_a_length_is_a_length_and_a_shadow_is_a_shadow(self):
        self.assertEqual(validate_token("--radius-md", "14px"), "14px")
        self.assertEqual(validate_token("--radius-md", "0.5rem"), "0.5rem")
        for bad in ("14", "14 px", "calc(14px + 2px)", "-4px"):
            self.assertIsNone(validate_token("--radius-md", bad), bad)
        self.assertEqual(validate_token("--shadow-md", "none"), "none")
        self.assertEqual(
            validate_token("--shadow-md", "0 0 0 1px rgba(0, 0, 0, 0.1)"),
            "0 0 0 1px rgba(0, 0, 0, 0.1)",
        )

    def test_a_gradient_is_a_gradient(self):
        good = "linear-gradient(160deg, #cfe0cd 0%, #5f7d70 100%)"
        self.assertEqual(validate_token("--glow", good), good)
        self.assertEqual(validate_token("--glow", "none"), "none")
        self.assertIsNone(validate_token("--glow", "#ffffff"))

    def test_a_token_nobody_declared_is_not_a_token(self):
        self.assertIsNone(validate_token("--not-a-token", "#fff"))
        self.assertIsNone(validate_token("--accent-500", "#fff"), "a ramp step is derived")
        self.assertIsNone(validate_token("--space-4", "8px"), "spacing is not theme data")

    def test_a_value_is_trimmed_and_bounded(self):
        self.assertEqual(validate_token("--bg", "  #ffffff  "), "#ffffff")
        self.assertIsNone(validate_token("--bg", ""))
        self.assertIsNone(validate_token("--bg", "#" + "f" * 300))
        self.assertIsNone(validate_token("--bg", 16777215), "a colour is text")


class SanitizeTests(unittest.TestCase):
    def test_what_is_kept_and_what_is_named_as_dropped(self):
        kept, dropped = sanitize_tokens({
            "--bg": "#ffffff",
            "--accent": "url(evil)",
            "--space-4": "8px",
            "nonsense": True,
        })
        self.assertEqual(kept, {"--bg": "#ffffff"})
        # An invalid canonical token and an unknown key are both reported, so
        # the review sheet can say what it left out rather than quietly
        # applying three quarters of somebody's theme.
        self.assertEqual(sorted(dropped), ["--accent", "--space-4", "nonsense"])

    def test_the_legacy_names_the_stylesheet_still_reads(self):
        expanded = expand_aliases({"--radius-md": "14px", "--shadow-md": "none"})
        self.assertEqual(expanded["--radius"], "14px")
        self.assertEqual(expanded["--shadow-card"], "none")
        # An alias is never set on its own.
        self.assertNotIn("--radius", expand_aliases({"--bg": "#fff"}))


class DocumentTests(unittest.TestCase):
    def test_a_theme_round_trips(self):
        checked = validate_document(a_theme())
        self.assertEqual(checked["theme"]["slug"], "friend")
        self.assertEqual(checked["theme"]["bases"], ["light"])
        self.assertEqual(checked["dropped"], {})

    def test_the_structural_rules(self):
        cases = {
            "schema_version must be 1": a_theme(schema_version=2),
            "slug must match": a_theme(slug="Not A Slug"),
            "bases lists at least one": a_theme(bases=[]),
            "bases holds light, dark": a_theme(bases=["light", "light"]),
            "version looks like": a_theme(version="one"),
            "tokens holds one object": a_theme(tokens="#fff"),
        }
        for reason, document in cases.items():
            with self.subTest(reason=reason), self.assertRaises(ThemeError) as raised:
                validate_document(document)
            self.assertIn(reason, raised.exception.detail)

    def test_a_base_that_sets_nothing_recognisable_is_not_a_theme(self):
        with self.assertRaises(ThemeError) as raised:
            validate_document(a_theme(tokens={"light": {"--nope": "#fff"}}))
        self.assertIn("sets no tokens Vela recognises", raised.exception.detail)

    def test_a_theme_may_suggest_a_wallpaper_it_cannot_set_one(self):
        checked = validate_document(a_theme(suggests={"wallpaper": "paramo"}))
        self.assertEqual(checked["theme"]["suggests"], {"wallpaper": "paramo"})
        with self.assertRaises(ThemeError):
            validate_document(a_theme(suggests={"wallpaper": "not-a-wallpaper"}))

    def test_unknown_top_level_keys_are_dropped_and_named(self):
        checked = validate_document(a_theme(studio={"x": 1}, future="yes"))
        self.assertEqual(checked["unknown"], ["future", "studio"])
        self.assertNotIn("studio", checked["theme"])

    def test_the_swatch_strip_comes_from_the_theme_itself(self):
        strip = swatches(validate_document(a_theme())["theme"])
        self.assertTrue(strip)
        for value in strip:
            self.assertRegex(value, r"^#")


class BundledTests(unittest.TestCase):
    """The set that ships. A bad one here is a build mistake, not a surprise."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-themes-")
        self.themes = Themes(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_every_bundled_theme_loads_and_validates(self):
        listed = self.themes.list()
        self.assertGreaterEqual(len(listed), 6, "the plan asks for six to eight")
        for entry in listed:
            self.assertFalse(entry["imported"])
            self.assertTrue(entry["swatches"])
            self.assertTrue(entry["bases"])

    def test_the_stock_theme_is_there_and_is_the_default(self):
        self.assertTrue(self.themes.is_bundled(STOCK_SLUG))
        stock = self.themes.get(STOCK_SLUG)
        self.assertEqual(stock["bases"], ["light", "dark"])
        # It sets every canonical token, which is what makes it the one a
        # generated stylesheet can be produced from.
        for base_name in stock["bases"]:
            self.assertEqual(sorted(stock["tokens"][base_name]), sorted(CANONICAL_TOKENS))

    def test_one_of_them_is_built_for_contrast(self):
        self.assertTrue(self.themes.exists("contraste"))

    def test_a_suggested_wallpaper_is_one_that_ships(self):
        for entry in self.themes.list():
            suggested = entry.get("suggests", {}).get("wallpaper")
            if suggested:
                self.assertIn(suggested, BUNDLED_WALLPAPERS, entry["slug"])

    def test_the_wallpaper_list_matches_the_dashboard(self):
        """One list, checked, rather than two that drift.

        The dashboard owns the wallpapers and their tones; the server only needs
        the ids, to know what a theme may suggest.
        """
        source = (ROOT / "web/src/desk/wallpaper.js").read_text(encoding="utf-8")
        listed = re.findall(r"\{ id: '([a-z]+)'", source)
        self.assertEqual(sorted(listed), sorted(BUNDLED_WALLPAPERS))

    def test_a_bundled_theme_cannot_be_removed_or_overwritten(self):
        with self.assertRaises(ThemeError) as removing:
            self.themes.remove(STOCK_SLUG)
        self.assertEqual(removing.exception.status, 409)
        with self.assertRaises(ThemeError) as importing:
            self.themes.import_document(a_theme(slug=STOCK_SLUG))
        self.assertEqual(importing.exception.status, 409)

    def test_an_unknown_slug_is_a_404(self):
        with self.assertRaises(ThemeError) as raised:
            self.themes.get("nothing-here")
        self.assertEqual(raised.exception.status, 404)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-themes-")
        self.root = Path(self.temp.name)
        self.themes = Themes(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_an_imported_theme_is_listed_read_back_and_exported(self):
        self.themes.import_document(a_theme())
        self.assertTrue(self.themes.exists("friend"))
        entry = next(item for item in self.themes.list() if item["slug"] == "friend")
        self.assertTrue(entry["imported"])
        # What comes out imports back in, which is the whole promise of export.
        exported = json.loads(self.themes.export("friend"))
        fresh = Themes(Path(tempfile.mkdtemp(prefix="vela-themes-2-")))
        fresh.import_document(exported)
        self.assertEqual(fresh.get("friend"), self.themes.get("friend"))

    def test_an_export_is_the_sanitized_document_not_what_arrived(self):
        self.themes.import_document(a_theme(
            tokens={"light": {"--bg": "#eeeeee", "--accent": "url(evil)"}},
            studio={"junk": True},
        ))
        stored = json.loads(self.themes.export("friend"))
        self.assertNotIn("studio", stored)
        self.assertNotIn("--accent", stored["tokens"]["light"])

    def test_the_same_slug_twice_needs_saying_so(self):
        self.themes.import_document(a_theme())
        with self.assertRaises(ThemeError) as raised:
            self.themes.import_document(a_theme(name="Different"))
        self.assertEqual(raised.exception.status, 409)
        self.themes.import_document(a_theme(name="Different"), replace=True)
        self.assertEqual(self.themes.get("friend")["name"], "Different")

    def test_this_server_keeps_a_bounded_number_of_them(self):
        for index in range(MAX_IMPORTED):
            self.themes.import_document(a_theme(slug=f"theme-{index}"))
        with self.assertRaises(ThemeError) as raised:
            self.themes.import_document(a_theme(slug="one-too-many"))
        self.assertEqual(raised.exception.status, 409)
        self.assertIn(str(MAX_IMPORTED), raised.exception.detail)
        # Replacing one that is already here still works when it is full.
        self.themes.import_document(a_theme(slug="theme-0", name="Again"), replace=True)

    def test_a_file_that_stopped_reading_as_a_theme_is_skipped_not_deleted(self):
        """Somebody's theme is their work; a stricter reader does not delete it."""
        self.themes.import_document(a_theme())
        broken = self.root / "themes" / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        self.assertEqual(sorted(self.themes.imported()), ["friend"])
        self.assertEqual(self.themes.rejected(), ["broken"])
        self.assertTrue(broken.is_file())

    def test_a_theme_that_renames_itself_on_disk_is_not_trusted(self):
        self.themes.import_document(a_theme())
        (self.root / "themes" / "friend.json").rename(self.root / "themes" / "other.json")
        self.assertEqual(self.themes.imported(), {})

    def test_removing_one_that_is_not_here(self):
        with self.assertRaises(ThemeError) as raised:
            self.themes.remove("never-existed")
        self.assertEqual(raised.exception.status, 404)


class ThemeApiTests(unittest.TestCase):
    """The HTTP surface, on a disposable data directory."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-theme-api-")
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

    def test_the_list_names_the_selected_theme(self):
        body = self.client.get("/api/themes", headers=self.hub).json()
        self.assertEqual(body["selected"], STOCK_SLUG)
        self.assertGreaterEqual(len(body["themes"]), 6)

    def test_a_theme_can_be_read_and_exported_as_a_file(self):
        document = self.client.get(f"/api/themes/{STOCK_SLUG}", headers=self.hub).json()
        self.assertEqual(document["slug"], STOCK_SLUG)
        exported = self.client.get(f"/api/themes/{STOCK_SLUG}/export", headers=self.hub)
        self.assertEqual(exported.status_code, 200)
        self.assertIn("attachment", exported.headers["content-disposition"])
        self.assertEqual(json.loads(exported.text), document)

    def test_importing_reports_what_it_left_out(self):
        response = self.client.post("/api/themes/import", headers=self.hub, json=a_theme(
            tokens={"light": {"--bg": "#eeeeee", "--accent": "url(evil)"}},
            studio={"x": 1},
        ))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["dropped"], {"light": ["--accent"]})
        self.assertEqual(body["unknown"], ["studio"])

    def test_each_refusal_says_which_rule_it_broke(self):
        cases = {
            "not JSON": ("not a theme at all", 422),
            "at most": (json.dumps({"pad": "x" * (MAX_THEME_BYTES + 10)}), 413),
        }
        for reason, (body, status) in cases.items():
            response = self.client.post(
                "/api/themes/import", headers={**self.hub, "Content-Type": "application/json"},
                content=body)
            self.assertEqual(response.status_code, status, reason)
            self.assertIn(reason, response.json()["detail"], reason)

        response = self.client.post("/api/themes/import", headers=self.hub,
                                    json=a_theme(slug=STOCK_SLUG))
        self.assertEqual(response.status_code, 409)
        self.assertIn("Vela ships", response.json()["detail"])

    def test_picking_a_theme_is_a_setting_and_is_checked(self):
        self.client.post("/api/themes/import", headers=self.hub, json=a_theme())
        ok = self.client.patch("/api/settings", headers=self.hub, json={"theme_id": "friend"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(
            self.client.get("/api/settings", headers=self.hub).json()["theme_id"], "friend")
        refused = self.client.patch("/api/settings", headers=self.hub,
                                    json={"theme_id": "no-such-theme"})
        self.assertEqual(refused.status_code, 422)
        self.assertEqual(refused.json()["code"], "settings.theme_unknown")

    def test_removing_the_theme_in_use_falls_back_in_the_same_request(self):
        self.client.post("/api/themes/import", headers=self.hub, json=a_theme())
        self.client.patch("/api/settings", headers=self.hub, json={"theme_id": "friend"})
        removed = self.client.delete("/api/themes/friend", headers=self.hub)
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()["selected"], STOCK_SLUG)
        # And the dashboard is not left pointing at a file that is gone.
        self.assertEqual(
            self.client.get("/api/settings", headers=self.hub).json()["theme_id"], STOCK_SLUG)

    def test_removing_a_theme_that_is_not_selected_leaves_the_selection_alone(self):
        self.client.post("/api/themes/import", headers=self.hub, json=a_theme())
        self.client.post("/api/themes/import", headers=self.hub, json=a_theme(slug="other"))
        self.client.patch("/api/settings", headers=self.hub, json={"theme_id": "other"})
        self.assertIsNone(self.client.delete("/api/themes/friend", headers=self.hub)
                          .json()["selected"])
        self.assertEqual(
            self.client.get("/api/settings", headers=self.hub).json()["theme_id"], "other")

    def test_an_app_session_cannot_change_what_this_computer_looks_like(self):
        response = self.client.post("/api/themes/import", json=a_theme())
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.delete("/api/themes/friend").status_code, 401)

    def test_a_settings_file_written_before_themes_existed_reads_as_stock(self):
        # Write something so the file exists, then take the key back out: this
        # is what a settings.json from before themes existed looks like.
        self.client.patch("/api/settings", headers=self.hub, json={"theme": "light"})
        path = self.config.data_dir / "settings.json"
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored.pop("theme_id", None)
        path.write_text(json.dumps(stored), encoding="utf-8")
        fresh = TestClient(create_app(self.config))
        token = fresh.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        view = fresh.get("/api/settings", headers={"Authorization": "Bearer " + token}).json()
        self.assertEqual(view["theme_id"], STOCK_SLUG)
        fresh.close()


if __name__ == "__main__":
    unittest.main()
