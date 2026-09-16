"""App-provided desk widgets: the manifest declaration, the capability gate,
the summary schema and its limits, and cleanup on uninstall.

Everything uses disposable data and the pinned fixture app. A summary is the
one thing an app can put on the user's home screen, so the rules here are the
ones that keep that surface honest: declared up front, granted at install,
capped in size, and gone when the app is.
"""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import test_app_contract as base
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.config import Config
from vela.manifest import ManifestError, validate_manifest
from vela.widgets import (
    MAX_SUMMARY_BYTES,
    WidgetError,
    validate_declarations,
    validate_summary,
)

ROOT = base.ROOT

WIDGETS = [
    {"id": "sync", "name": "Sync", "layout": "stat", "size": "m"},
    {"id": "queued", "name": "Queued changes", "layout": "list", "size": "l"},
]


def manifest_with_widgets(widgets=None, capability=True):
    data = copy.deepcopy(base.FIXTURE)
    if capability:
        data.setdefault("capabilities", {}).setdefault("optional", []).append("widgets")
    if widgets is not None:
        data["widgets"] = widgets
    return data


class DeclarationTests(unittest.TestCase):
    def test_a_declared_widget_needs_the_capability(self):
        with self.assertRaises(ManifestError) as raised:
            validate_manifest(
                manifest_with_widgets(WIDGETS, capability=False), "chat-fixture", Path("chat-fixture")
            )
        self.assertIn("widgets require the widgets capability", str(raised.exception))

        manifest = validate_manifest(
            manifest_with_widgets(WIDGETS), "chat-fixture", Path("chat-fixture")
        )
        self.assertIn("widgets", manifest.capabilities)
        self.assertEqual([w["id"] for w in manifest.widgets], ["sync", "queued"])

    def test_the_capability_alone_declares_nothing(self):
        manifest = validate_manifest(
            manifest_with_widgets(), "chat-fixture", Path("chat-fixture")
        )
        self.assertIn("widgets", manifest.capabilities)
        self.assertEqual(manifest.widgets, [])

    def test_declarations_are_checked_field_by_field(self):
        cases = {
            "widget id must match": [{**WIDGETS[0], "id": "Sync"}],
            "duplicate widget id": [WIDGETS[0], WIDGETS[0]],
            "layout must be one of": [{**WIDGETS[0], "layout": "chart"}],
            "size must be s, m or l": [{**WIDGETS[0], "size": "xl"}],
            "needs a name": [{**WIDGETS[0], "name": ""}],
            "unknown fields": [{**WIDGETS[0], "colour": "red"}],
            "at most 4 widgets per app": [
                {**WIDGETS[0], "id": f"w{n}"} for n in range(5)
            ],
            "widgets must be a list": "sync",
        }
        for reason, widgets in cases.items():
            with self.subTest(reason=reason), self.assertRaises(ValueError) as raised:
                validate_declarations(widgets, "chat-fixture")
            self.assertIn(reason, str(raised.exception))

    def test_a_bad_declaration_fails_the_whole_manifest(self):
        with self.assertRaises(ManifestError):
            validate_manifest(
                manifest_with_widgets([{**WIDGETS[0], "layout": "chart"}]),
                "chat-fixture",
                Path("chat-fixture"),
            )


class SummaryTests(unittest.TestCase):
    def test_a_full_summary_round_trips(self):
        payload = {
            "value": "73",
            "unit": "changes",
            "delta": "+12",
            "caption": "queued since 02:14",
            "progress": 32.25,
            "rows": [{"label": "Shopping", "detail": "2 min ago"}, {"label": "Ideas"}],
            "actions": [{"action": "sync-now", "label": "Sync now"}],
            "attention": True,
            "badge": "73",
            "expiresAt": "2026-09-16T02:14:00Z",
        }
        checked = validate_summary(payload)
        self.assertEqual(checked["progress"], 32.2)
        self.assertEqual(checked["rows"][1], {"label": "Ideas"})
        self.assertEqual(checked["actions"], payload["actions"])
        self.assertTrue(checked["attention"])
        self.assertEqual(checked["badge"], "73")

    def test_a_badge_is_short_text_and_a_blank_one_is_no_badge(self):
        # A count arrives as a number often enough that refusing it would only
        # push the same `str()` into every app.
        self.assertEqual(validate_summary({"badge": 7})["badge"], "7")
        self.assertEqual(validate_summary({"badge": " 12 "})["badge"], "12")
        self.assertEqual(validate_summary({"badge": "99+"})["badge"], "99+")
        # Nothing to badge is the absence of one, not an empty circle.
        self.assertNotIn("badge", validate_summary({"badge": ""}))
        self.assertNotIn("badge", validate_summary({"badge": "   "}))

    def test_nothing_to_report_is_a_valid_summary(self):
        self.assertEqual(validate_summary({}), {})

    def test_the_schema_is_enforced(self):
        cases = {
            "unknown summary fields": {"headline": "hi"},
            "value must be text": {"value": 73},
            "longer than 200": {"caption": "x" * 201},
            "progress must be a number": {"progress": "32"},
            "progress must be between": {"progress": 140},
            "at most 8 rows": {"rows": [{"label": str(n)} for n in range(9)]},
            "unknown row fields": {"rows": [{"label": "a", "colour": "red"}]},
            "each row is an object": {"rows": ["plain"]},
            "at most 3 actions": {"actions": [{"action": f"a{n}", "label": "x"} for n in range(4)]},
            "an action names one": {"actions": [{"action": "Sync Now", "label": "x"}]},
            "attention is true or false": {"attention": "yes"},
            "a badge is at most 3 characters": {"badge": "1200"},
            "a badge is a short string": {"badge": ["7"]},
            "ISO 8601": {"expiresAt": "tomorrow"},
            "a summary is a JSON object": ["value"],
        }
        for reason, payload in cases.items():
            with self.subTest(reason=reason), self.assertRaises(WidgetError) as raised:
                validate_summary(payload)
            self.assertIn(reason, raised.exception.detail)
            self.assertEqual(raised.exception.status, 422)

    def test_an_oversized_summary_is_413_not_422(self):
        with self.assertRaises(WidgetError) as raised:
            validate_summary({"rows": [{"label": "x" * 200, "detail": "y" * 200} for _ in range(8)],
                              "caption": "z" * 200, "value": "w" * 200, "unit": "u" * 200,
                              "delta": "d" * 200, "expiresAt": "e" * 200})
        self.assertEqual(raised.exception.status, 413)
        self.assertIn(str(MAX_SUMMARY_BYTES), raised.exception.detail)


class WidgetApiTests(unittest.TestCase):
    """The HTTP surface, against a fixture app that declares two widgets."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-widgets-")
        self.root = Path(self.temp.name)
        self.apps = self.root / "catalog"
        for app_id, widgets in (("chat-fixture", WIDGETS), ("other-app", None)):
            shutil.copytree(ROOT / "tests/fixtures/chat-fixture", self.apps / app_id)
            data = manifest_with_widgets(widgets, capability=widgets is not None)
            data["id"] = app_id
            (self.apps / app_id / "app.json").write_text(json.dumps(data))
        self.config = Config(self.root / "data", self.apps, ROOT / "web/dist")
        self.config.ensure_dirs()
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def session(self, app_id="chat-fixture"):
        self.assertEqual(
            self.client.post(f"/api/apps/{app_id}/install", headers=self.hub).status_code, 200
        )
        response = self.client.post(f"/api/apps/{app_id}/session", headers=self.hub)
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": "Bearer " + response.json()["token"]}

    def test_declared_widgets_appear_before_anything_is_published(self):
        app = self.session()
        listed = self.client.get("/api/apps/chat-fixture/widgets", headers=self.hub).json()
        self.assertEqual([w["id"] for w in listed["widgets"]], ["sync", "queued"])
        self.assertEqual(listed["widgets"][0]["summary"], None)
        self.assertEqual(listed["widgets"][0]["updatedAt"], None)
        self.assertEqual(listed["widgets"][0]["layout"], "stat")
        del app

    def test_publishing_stores_the_summary_and_the_desk_can_read_it(self):
        app = self.session()
        published = self.client.put(
            "/api/app/widgets/sync", headers=app, json={"summary": {"value": "73", "unit": "changes"}}
        )
        self.assertEqual(published.status_code, 200, published.text)
        self.assertEqual(published.json()["widgetId"], "sync")

        everything = self.client.get("/api/widgets", headers=self.hub).json()["widgets"]
        sync = next(w for w in everything if w["appId"] == "chat-fixture" and w["id"] == "sync")
        self.assertEqual(sync["summary"], {"value": "73", "unit": "changes"})
        self.assertEqual(sync["appName"], "Chat Studio")
        self.assertIsNotNone(sync["updatedAt"])
        # The other declared widget is listed with no summary, not omitted.
        queued = next(w for w in everything if w["id"] == "queued")
        self.assertIsNone(queued["summary"])

        # Publishing again replaces rather than accumulating.
        self.client.put("/api/app/widgets/sync", headers=app, json={"summary": {"value": "0"}})
        again = self.client.get("/api/apps/chat-fixture/widgets", headers=self.hub).json()
        self.assertEqual(again["widgets"][0]["summary"], {"value": "0"})

    def test_the_capability_and_the_declaration_are_both_required(self):
        other = self.session("other-app")
        refused = self.client.put(
            "/api/app/widgets/sync", headers=other, json={"summary": {"value": "1"}}
        )
        self.assertEqual(refused.status_code, 403, refused.text)
        self.assertIn("Widgets capability", refused.json()["detail"])

        app = self.session()
        unknown = self.client.put(
            "/api/app/widgets/invented", headers=app, json={"summary": {"value": "1"}}
        )
        self.assertEqual(unknown.status_code, 422)
        self.assertIn("does not declare", unknown.json()["detail"])

        oversized = self.client.put(
            "/api/app/widgets/sync", headers=app, json={"summary": {"caption": "x" * 5000}}
        )
        self.assertEqual(oversized.status_code, 413)

    def test_a_hub_session_cannot_publish_and_an_app_session_cannot_read_every_app(self):
        app = self.session()
        self.assertEqual(
            self.client.put(
                "/api/app/widgets/sync", headers=self.hub, json={"summary": {}}
            ).status_code,
            401,
        )
        self.assertEqual(self.client.get("/api/widgets", headers=app).status_code, 401)
        self.assertEqual(self.client.get("/api/widgets").status_code, 401)

    def test_uninstalling_forgets_what_the_app_published(self):
        app = self.session()
        self.client.put("/api/app/widgets/sync", headers=app, json={"summary": {"value": "73"}})
        self.assertEqual(len(self.client.get("/api/widgets", headers=self.hub).json()["widgets"]), 2)
        removed = self.client.delete("/api/apps/chat-fixture", headers=self.hub)
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(self.client.get("/api/widgets", headers=self.hub).json()["widgets"], [])
        # Reinstalling starts from nothing rather than resurrecting a stale line.
        self.session()
        listed = self.client.get("/api/apps/chat-fixture/widgets", headers=self.hub).json()
        self.assertIsNone(listed["widgets"][0]["summary"])


if __name__ == "__main__":
    unittest.main()
