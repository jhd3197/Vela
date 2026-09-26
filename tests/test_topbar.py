"""The top bar contract: window options an app declares, menus it declares,
and the caps the published status items are held to.

Two halves, deliberately. Menus and window options are *manifest* facts, so
they are validated here, in the engine, where an install is refused. Status
items are per-window runtime state that never reaches this server (decision
D05 in `plans/TOP-BAR-PROGRESS.md`), so the engine's part in those is owning
the numbers: this module is where the caps and the icon set are written down,
and the tests below check that the dashboard's bridge really uses them rather
than a second set that drifted.

Everything uses disposable data and the pinned fixture app.
"""
import copy
import json
import re
import unittest
from pathlib import Path

import test_app_contract as base
from vela.desktops.models import MAX_WINDOW, MIN_WINDOW_HEIGHT, MIN_WINDOW_WIDTH
from vela.manifest import (
    DEFAULT_WINDOW,
    SUPPORTED_CAPABILITIES,
    ManifestError,
    validate_manifest,
)
from vela.topbar import (
    MAX_MENU_ITEMS,
    MAX_MENUS_PER_APP,
    MAX_STATUS_BYTES,
    MAX_STATUS_ITEMS,
    MAX_STATUS_LABEL,
    MAX_STATUS_TITLE,
    MENU_ACTIONS,
    STATUS_ICONS,
    STATUS_TONES,
    validate_menu_declarations,
)

ROOT = base.ROOT

MENUS = [
    {
        "id": "file",
        "label": "File",
        "items": [
            {"id": "new", "label": "New sheet"},
            {"id": "close", "label": "Close", "action": "close"},
        ],
    },
    {"id": "view", "label": "View", "items": [{"id": "back", "label": "Back to desk",
                                               "action": "return"}]},
]


def manifest_with(topbar=None, capability=True, window=None):
    data = copy.deepcopy(base.FIXTURE)
    if capability:
        data.setdefault("capabilities", {}).setdefault("optional", []).append("topbar")
    if topbar is not None:
        data["topbar"] = topbar
    if window is not None:
        data["view"] = {**data["view"], "window": window}
    return data


def check(data):
    return validate_manifest(data, "chat-fixture", Path("chat-fixture"))


class WindowOptionTests(unittest.TestCase):
    def test_an_app_that_says_nothing_gets_the_window_it_always_had(self):
        view = check(copy.deepcopy(base.FIXTURE)).view
        self.assertEqual(view["window"], DEFAULT_WINDOW)
        self.assertTrue(view["window"]["resizable"])
        self.assertTrue(view["window"]["maximizable"])
        self.assertNotIn("defaultSize", view["window"])

    def test_declared_options_survive_and_the_rest_keep_their_defaults(self):
        view = check(manifest_with(window={"maximizable": False})).view
        self.assertFalse(view["window"]["maximizable"])
        self.assertTrue(view["window"]["resizable"], "one option is not the other")

        view = check(
            manifest_with(window={"resizable": False, "defaultSize": {"width": 320,
                                                                     "height": 460}})
        ).view
        self.assertFalse(view["window"]["resizable"])
        self.assertEqual(view["window"]["defaultSize"], {"width": 320, "height": 460})

    def test_unknown_window_fields_fail_closed(self):
        cases = [
            {"alwaysOnTop": True},
            {"resizable": "no"},
            {"defaultSize": {"width": 320}},
            {"defaultSize": {"width": 320, "height": 460, "depth": 2}},
            # Below what the desk can draw, and above what it will store.
            {"defaultSize": {"width": MIN_WINDOW_WIDTH - 1, "height": 460}},
            {"defaultSize": {"width": 320, "height": MIN_WINDOW_HEIGHT - 1}},
            {"defaultSize": {"width": MAX_WINDOW + 1, "height": 460}},
        ]
        for window in cases:
            with self.subTest(window=window), self.assertRaises(ManifestError):
                check(manifest_with(window=window))

    def test_only_a_window_surface_may_describe_a_window(self):
        data = copy.deepcopy(base.FIXTURE)
        data["view"] = {"surface": "external", "url": "https://example.test",
                        "window": {"resizable": False}}
        with self.assertRaises(ManifestError):
            check(data)

    def test_the_schema_and_the_desk_agree_about_how_big_a_window_may_be(self):
        # The one that would bite silently: an app declares a size the schema
        # allows and the desk refuses, and the window opens somewhere else
        # entirely with nobody told why.
        schema = json.loads(
            (ROOT / "vela/assets/manifest-v2.schema.json").read_text(encoding="utf-8")
        )
        size = schema["properties"]["view"]["properties"]["window"]["properties"]["defaultSize"]
        self.assertEqual(size["properties"]["width"]["minimum"], MIN_WINDOW_WIDTH)
        self.assertEqual(size["properties"]["height"]["minimum"], MIN_WINDOW_HEIGHT)
        self.assertEqual(size["properties"]["width"]["maximum"], MAX_WINDOW)
        self.assertEqual(size["properties"]["height"]["maximum"], MAX_WINDOW)


class MenuDeclarationTests(unittest.TestCase):
    def test_topbar_is_a_capability_the_engine_knows(self):
        self.assertIn("topbar", SUPPORTED_CAPABILITIES)

    def test_declared_menus_need_the_capability(self):
        with self.assertRaises(ManifestError) as raised:
            check(manifest_with({"menus": MENUS}, capability=False))
        self.assertIn("topbar menus require the topbar capability", str(raised.exception))

        manifest = check(manifest_with({"menus": MENUS}))
        self.assertIn("topbar", manifest.capabilities)
        self.assertEqual([menu["id"] for menu in manifest.topbar_menus], ["file", "view"])

    def test_the_capability_alone_declares_nothing(self):
        manifest = check(manifest_with())
        self.assertIn("topbar", manifest.capabilities)
        self.assertEqual(manifest.topbar_menus, [])

    def test_an_unknown_capability_is_reported_rather_than_granted(self):
        data = copy.deepcopy(base.FIXTURE)
        data["capabilities"]["optional"] = ["topbar.future"]
        manifest = check(data)
        self.assertNotIn("topbar.future", manifest.capabilities)
        self.assertEqual(manifest.unavailable_capabilities, ["topbar.future"])

        # Required, and unknown, is an install that does not happen.
        data["capabilities"] = {"required": ["storage", "topbar.future"]}
        with self.assertRaises(ManifestError) as raised:
            check(data)
        self.assertIn("unsupported required capabilities", str(raised.exception))

    def test_declarations_are_checked_field_by_field(self):
        cases = {
            "topbar must be an object": [],
            "topbar has unknown fields": {"menus": MENUS, "items": []},
            "topbar menus must be a list": {"menus": "file"},
            "top bar menu id must match": {"menus": [{**MENUS[0], "id": "File"}]},
            "duplicate top bar menu id": {"menus": [MENUS[0], MENUS[0]]},
            "needs a label of at most": {"menus": [{**MENUS[0], "label": "x" * 25}]},
            "needs at least one item": {"menus": [{**MENUS[0], "items": []}]},
            "top bar menu has unknown fields": {"menus": [{**MENUS[0], "icon": "file"}]},
            "top bar menu item has unknown fields": {
                "menus": [{**MENUS[0], "items": [{"id": "new", "label": "New", "url": "/x"}]}]
            },
            "top bar menu action must be one of": {
                "menus": [{**MENUS[0], "items": [{"id": "new", "label": "New",
                                                  "action": "exec"}]}]
            },
            "duplicate item id": {
                "menus": [{**MENUS[0], "items": [{"id": "new", "label": "One"},
                                                 {"id": "new", "label": "Two"}]}]
            },
            f"at most {MAX_MENUS_PER_APP} top bar menus per app": {
                "menus": [{**MENUS[0], "id": f"m{n}"} for n in range(MAX_MENUS_PER_APP + 1)]
            },
            f"holds at most {MAX_MENU_ITEMS} items": {
                "menus": [
                    {
                        **MENUS[0],
                        "items": [
                            {"id": f"i{n}", "label": "Item"} for n in range(MAX_MENU_ITEMS + 1)
                        ],
                    }
                ]
            },
        }
        for reason, topbar in cases.items():
            with self.subTest(reason=reason), self.assertRaises(ValueError) as raised:
                validate_menu_declarations(topbar, "chat-fixture")
            self.assertIn(reason, str(raised.exception))

    def test_a_bad_declaration_fails_the_whole_manifest(self):
        # The schema catches the shape and the engine catches what a schema
        # cannot express. Either way the app is not installed.
        for topbar in ({"menus": [{**MENUS[0], "id": "File"}]},
                       {"menus": [MENUS[0], MENUS[0]]},
                       {"menus": MENUS, "items": []}):
            with self.subTest(topbar=topbar), self.assertRaises(ManifestError):
                check(manifest_with(topbar))

    def test_a_declared_action_is_one_the_host_performs(self):
        checked = validate_menu_declarations({"menus": MENUS}, "chat-fixture")
        actions = [item.get("action") for menu in checked for item in menu["items"]]
        self.assertEqual(actions, [None, "close", "return"])
        for action in actions:
            self.assertIn(action, (None, *MENU_ACTIONS))


class PublishedItemContractTests(unittest.TestCase):
    """The caps live here; the bridge is what enforces them. Check they match.

    A second copy of a number is a number that drifts, and the way it would be
    noticed is an app being refused by one half of the contract and accepted by
    the other. Reading the dashboard's module as text is blunt, and it is also
    the only check that actually fails when the two disagree.
    """

    def setUp(self):
        self.source = (ROOT / "web/src/shell/topbar-contract.js").read_text(encoding="utf-8")

    def _constant(self, name):
        match = re.search(rf"export const {name} = (\d+);", self.source)
        self.assertIsNotNone(match, f"{name} is not declared in topbar-contract.js")
        return int(match.group(1))

    def test_the_bridge_uses_the_caps_written_here(self):
        self.assertEqual(self._constant("MAX_ITEMS"), MAX_STATUS_ITEMS)
        self.assertEqual(self._constant("MAX_LABEL"), MAX_STATUS_LABEL)
        self.assertEqual(self._constant("MAX_TITLE"), MAX_STATUS_TITLE)
        self.assertEqual(self._constant("MAX_BYTES"), MAX_STATUS_BYTES)

    def test_the_bridge_offers_the_icons_and_tones_written_here(self):
        for name, expected in (("STATUS_ICONS", STATUS_ICONS), ("STATUS_TONES", STATUS_TONES)):
            block = re.search(rf"export const {name} = \[(.*?)\];", self.source, re.S)
            self.assertIsNotNone(block, f"{name} is not declared in topbar-contract.js")
            self.assertEqual(tuple(re.findall(r"'([a-z]+)'", block.group(1))), tuple(expected))

    def test_the_icon_set_is_closed_and_documented(self):
        contract = (ROOT / "docs/CONTRACT.md").read_text(encoding="utf-8")
        for icon in STATUS_ICONS:
            self.assertIn(f"`{icon}`", contract, f"{icon} is not named in docs/CONTRACT.md")


if __name__ == "__main__":
    unittest.main()
