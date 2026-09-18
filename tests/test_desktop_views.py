"""Open views and how they are arranged.

A view is the identity that survives being minimized, maximized, moved between
panes and restored. The app process it talks to has its own, shorter, life, and
the point of most of these is that the two do not get confused: minimizing a
window must not end a session, closing one must not uninstall anything, and a
window opened against one installation of an app must not be treated as still
bound to it after that installation is replaced.
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
from vela.desktops.models import MAX_VIEWS_PER_DESKTOP

ROOT = base.ROOT


class DesktopViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-desktop-views-")
        self.root = Path(self.temp.name)
        self.apps = self.root / "catalog"
        shutil.copytree(ROOT / "tests/fixtures/chat-fixture", self.apps / "chat-fixture")
        (self.apps / "chat-fixture" / "app.json").write_text(json.dumps(copy.deepcopy(base.FIXTURE)))
        self.config = Config(self.root / "data", self.apps, ROOT / "web/dist")
        self.config.ensure_dirs()
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}
        self.desktop = self.client.get("/api/desktops", headers=self.hub).json()["defaultId"]
        self.assertEqual(
            self.client.post("/api/apps/chat-fixture/install", headers=self.hub).status_code, 200
        )

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    # ---- helpers

    @property
    def storage(self):
        """The app storage this hub is using, for the state a test is about."""
        from vela.app_storage import AppStorage

        return AppStorage(self.config.data_dir / "app-data.sqlite")

    def restart(self):
        """Start the hub again over the same data, as a new version would."""
        self.client.close()
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def get(self, path):
        return self.client.get(path, headers=self.hub)

    def views(self, desktop_id=None):
        return self.get(f"/api/desktops/{desktop_id or self.desktop}/views").json()

    def open(self, desktop_id=None, **payload):
        return self.client.post(
            f"/api/desktops/{desktop_id or self.desktop}/views", headers=self.hub, json=payload
        )

    def open_app(self, app_id="chat-fixture", **extra):
        response = self.open(kind="app", appId=app_id, **extra)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def patch_view(self, view_id, **payload):
        return self.client.patch(
            f"/api/desktops/{self.desktop}/views/{view_id}", headers=self.hub, json=payload
        )

    def layout(self, desktop_id=None):
        return self.get(f"/api/desktops/{desktop_id or self.desktop}/layout").json()

    def save_layout(self, **payload):
        return self.client.put(
            f"/api/desktops/{self.desktop}/layout", headers=self.hub, json=payload
        )

    # ---- opening

    def test_a_fresh_desktop_has_nothing_open(self):
        payload = self.views()
        self.assertEqual(payload["views"], [])
        self.assertEqual(payload["layout"]["arrangement"], "floating")
        self.assertIsNone(payload["layout"]["selectedView"])

    def test_opening_an_app_binds_the_view_to_its_installation(self):
        view = self.open_app()
        self.assertEqual(view["kind"], "app")
        self.assertEqual(view["appId"], "chat-fixture")
        self.assertTrue(view["installationId"])
        self.assertTrue(view["available"])
        self.assertTrue(view["agentViewable"])
        self.assertEqual(self.layout()["selectedView"], view["id"], "opening it selects it")

    def test_opening_the_same_app_again_brings_the_window_forward(self):
        first = self.open_app()
        self.patch_view(first["id"], minimized=True)
        again = self.open_app()
        self.assertEqual(again["id"], first["id"], "one window, not two")
        self.assertFalse(again["window"]["minimized"], "and it comes back rather than staying hidden")
        self.assertEqual(len(self.views()["views"]), 1)

    def test_a_second_window_of_one_app_can_be_asked_for(self):
        first = self.open_app()
        second = self.open_app(newView=True)
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(len(self.views()["views"]), 2)
        # They share an app and an installation but are separate windows.
        self.assertEqual(second["installationId"], first["installationId"])

    def test_the_same_app_in_two_desktops_gets_a_view_each(self):
        other = self.client.post("/api/desktops", headers=self.hub, json={}).json()["id"]
        here = self.open_app()
        there = self.open(other, kind="app", appId="chat-fixture").json()
        self.assertNotEqual(here["id"], there["id"])
        self.assertEqual(here["installationId"], there["installationId"], "one installation")
        self.assertEqual([v["id"] for v in self.views()["views"]], [here["id"]])
        self.assertEqual([v["id"] for v in self.views(other)["views"]], [there["id"]])

    def test_an_app_that_is_not_installed_cannot_be_opened(self):
        self.assertEqual(self.open(kind="app", appId="not-a-real-app").status_code, 404)

    def test_owner_surfaces_are_a_closed_list_and_not_agent_targets(self):
        library = self.open(kind="host", surface="library")
        self.assertEqual(library.status_code, 201, library.text)
        self.assertFalse(library.json()["agentViewable"], "owner controls are not agent targets")
        # A host view that could name any path would put the owner's dashboard,
        # with its credentials, inside something that is not the dashboard.
        for surface in ("settings", "../settings", "https://example.com", ""):
            with self.subTest(surface=surface):
                self.assertEqual(self.open(kind="host", surface=surface).status_code, 422)

    def test_a_web_view_needs_a_real_address(self):
        self.assertEqual(self.open(kind="web", url="https://example.com/a").status_code, 201)
        for bad in ("file:///etc/passwd", "javascript:alert(1)", "example.com", ""):
            with self.subTest(url=bad):
                self.assertEqual(self.open(kind="web", url=bad).status_code, 422)

    def test_a_desktop_holds_a_bounded_number_of_views(self):
        for index in range(MAX_VIEWS_PER_DESKTOP):
            self.assertEqual(
                self.open(kind="web", url=f"https://example.com/{index}").status_code, 201
            )
        refused = self.open(kind="web", url="https://example.com/too-many")
        self.assertEqual(refused.status_code, 429)

    # ---- presentation

    def test_minimizing_is_presentation_and_keeps_the_view(self):
        view = self.open_app()
        saved = self.patch_view(
            view["id"],
            minimized=True,
            restoreBounds={"x": 40, "y": 60, "width": 900, "height": 700},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        body = saved.json()
        self.assertTrue(body["window"]["minimized"])
        self.assertEqual(body["window"]["restoreBounds"]["width"], 900)
        # The view is still open, still bound to its installation, and still the
        # selected one. Minimizing ends nothing.
        self.assertEqual(len(self.views()["views"]), 1)
        self.assertEqual(self.layout()["selectedView"], view["id"])
        self.assertEqual(body["installationId"], view["installationId"])

    def test_window_bounds_are_bounded(self):
        view = self.open_app()
        self.assertEqual(
            self.patch_view(view["id"], bounds={"x": 0, "y": 0, "width": 800, "height": 600}).status_code,
            200,
        )
        for bad in (
            {"x": 0, "y": 0, "width": 4, "height": 600},
            {"x": 0, "y": 0, "width": 800, "height": 999999},
            {"x": 0, "y": 0, "width": "wide", "height": 600},
            {"x": 0, "y": 0, "width": 800},
        ):
            with self.subTest(bounds=bad):
                self.assertEqual(self.patch_view(view["id"], bounds=bad).status_code, 422)
        # The refused ones changed nothing.
        self.assertEqual(self.views()["views"][0]["window"]["bounds"]["width"], 800)

    def test_a_view_remembers_a_route_and_not_a_page_dump(self):
        view = self.open_app()
        saved = self.patch_view(view["id"], state={"route": "/notes/3", "scroll": 220})
        self.assertEqual(saved.json()["state"], {"route": "/notes/3", "scroll": 220})
        # Anything else is dropped rather than stored: a saved layout must not
        # become somewhere a half-typed message or a token ends up.
        kept = self.patch_view(view["id"], state={"route": "/x", "draft": "secret", "token": "t"})
        self.assertEqual(kept.json()["state"], {"route": "/x"})

    def test_raising_a_window_changes_the_stacking_order(self):
        first = self.open_app()
        second = self.open(kind="web", url="https://example.com/a").json()
        self.assertGreater(second["window"]["stack"], first["window"]["stack"])
        raised = self.patch_view(first["id"], **{"raise": True})
        self.assertGreater(raised.json()["window"]["stack"], second["window"]["stack"])

    def test_a_view_from_another_desktop_cannot_be_reached_through_this_one(self):
        other = self.client.post("/api/desktops", headers=self.hub, json={}).json()["id"]
        theirs = self.open(other, kind="web", url="https://example.com/a").json()
        self.assertEqual(self.patch_view(theirs["id"], minimized=True).status_code, 404)
        self.assertEqual(
            self.client.delete(
                f"/api/desktops/{self.desktop}/views/{theirs['id']}", headers=self.hub
            ).status_code,
            404,
        )

    # ---- apps installed before installing gave them an identity

    def test_an_installed_app_with_no_identity_is_adopted_and_can_have_a_window(self):
        # Installing an app has created an installation identity for some time,
        # but it did not always: on a Vela that was upgraded, the apps that were
        # already there had none until something happened to start a session for
        # them. A window binds itself to an identity rather than creating one,
        # so those apps could not be given a window at all — which, now that a
        # window is how an app opens, means they could not be opened.
        with self.storage.connection() as db:
            db.execute("DELETE FROM installations WHERE app_id=?", ("chat-fixture",))
        self.assertIsNone(self.storage.installation("chat-fixture"), "the state being fixed")
        self.assertEqual(self.open(kind="app", appId="chat-fixture").status_code, 404)

        self.restart()
        identity = self.storage.installation("chat-fixture")
        self.assertIsNotNone(identity, "startup gives an installed app the identity it lacked")
        view = self.open_app()
        self.assertEqual(view["installationId"], identity)
        self.assertTrue(view["available"])

    def test_adoption_does_not_bring_a_removed_app_back(self):
        # `installation` refuses to create one precisely so that asking about a
        # removed app cannot resurrect it. Reconciling must keep that property:
        # only an app whose code is really installed is given an identity.
        self.assertEqual(
            self.client.delete("/api/apps/chat-fixture", headers=self.hub).status_code, 200
        )
        self.restart()
        self.assertIsNone(self.storage.installation("chat-fixture"))
        self.assertEqual(self.open(kind="app", appId="chat-fixture").status_code, 404)

    def test_adoption_leaves_an_existing_identity_alone(self):
        before = self.storage.installation("chat-fixture")
        self.restart()
        self.assertEqual(self.storage.installation("chat-fixture"), before, "not reissued")

    # ---- installation identity

    def test_reinstalling_an_app_does_not_hand_its_window_to_the_new_installation(self):
        view = self.open_app()
        original = view["installationId"]
        self.assertEqual(
            self.client.delete("/api/apps/chat-fixture", headers=self.hub).status_code, 200
        )
        gone = self.views()["views"][0]
        self.assertFalse(gone["available"])
        self.assertEqual(gone["unavailableReason"], "uninstalled")
        self.assertEqual(gone["installationId"], original, "the window remembers what it opened")

        self.assertEqual(
            self.client.post("/api/apps/chat-fixture/install", headers=self.hub).status_code, 200
        )
        after = self.views()["views"][0]
        self.assertFalse(
            after["available"],
            "a new installation with the same id does not inherit the old window",
        )
        self.assertEqual(after["unavailableReason"], "reinstalled")
        # Opening it again is what produces a window bound to the new one.
        fresh = self.open_app()
        self.assertNotEqual(fresh["installationId"], original)
        self.assertTrue(fresh["available"])

    # ---- layout

    def test_a_layout_is_saved_against_its_revision(self):
        first = self.open_app()
        second = self.open(kind="host", surface="library").json()
        saved = self.save_layout(
            revision=0,
            arrangement="split",
            primaryView=first["id"],
            secondaryView=second["id"],
            dividerRatio=0.35,
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["revision"], 1)
        self.assertEqual(saved.json()["dividerRatio"], 0.35)

        stale = self.save_layout(revision=0, arrangement="floating")
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.headers["X-Vela-Desk-Revision"], "1")
        self.assertEqual(self.layout()["arrangement"], "split", "the first save is still there")

    def test_a_layout_cannot_name_a_view_that_is_not_open(self):
        view = self.open_app()
        self.assertEqual(
            self.save_layout(revision=0, arrangement="maximized", maximizedView="0" * 32).status_code,
            422,
        )
        self.assertEqual(
            self.save_layout(
                revision=0, arrangement="split", primaryView=view["id"], secondaryView=view["id"]
            ).status_code,
            422,
            "a split cannot show the same view on both sides",
        )

    def test_the_divider_leaves_both_panes_usable(self):
        first = self.open_app()
        second = self.open(kind="host", surface="library").json()
        for bad in (0.01, 0.99, -1, 2):
            with self.subTest(ratio=bad):
                self.assertEqual(
                    self.save_layout(
                        revision=0,
                        arrangement="split",
                        primaryView=first["id"],
                        secondaryView=second["id"],
                        dividerRatio=bad,
                    ).status_code,
                    422,
                )

    def test_closing_one_half_of_a_split_leaves_an_empty_pane(self):
        first = self.open_app()
        second = self.open(kind="host", surface="library").json()
        self.save_layout(
            revision=0, arrangement="split", primaryView=first["id"], secondaryView=second["id"]
        )
        self.client.delete(
            f"/api/desktops/{self.desktop}/views/{second['id']}", headers=self.hub
        )
        after = self.layout()
        self.assertEqual(after["arrangement"], "split", "the user asked for two panes")
        self.assertEqual(after["primaryView"], first["id"])
        self.assertIsNone(after["secondaryView"], "and the emptied one is explicit")
        self.assertEqual(after["selectedView"], first["id"])

    def test_closing_the_maximized_view_returns_to_floating(self):
        view = self.open_app()
        self.save_layout(revision=0, arrangement="maximized", maximizedView=view["id"])
        self.client.delete(f"/api/desktops/{self.desktop}/views/{view['id']}", headers=self.hub)
        after = self.layout()
        self.assertEqual(after["arrangement"], "floating")
        self.assertIsNone(after["maximizedView"])
        self.assertIsNone(after["selectedView"])

    def test_selecting_a_view_is_not_saving_an_arrangement(self):
        first = self.open_app()
        second = self.open(kind="web", url="https://example.com/a").json()
        self.save_layout(revision=0, arrangement="maximized", maximizedView=first["id"])
        before = self.layout()["revision"]
        chosen = self.client.post(
            f"/api/desktops/{self.desktop}/selected-view",
            headers=self.hub,
            json={"viewId": second["id"]},
        )
        self.assertEqual(chosen.status_code, 200)
        self.assertEqual(chosen.json()["selectedView"], second["id"])
        self.assertEqual(
            self.layout()["revision"],
            before,
            "clicking a window must not conflict with a drag someone else is finishing",
        )

    # ---- lifetime

    def test_closing_a_view_leaves_the_app_installed_and_its_data_alone(self):
        created = self.client.post("/api/apps/chat-fixture/session", headers=self.hub)
        app = {"Authorization": "Bearer " + created.json()["token"]}
        self.client.put("/api/app/storage", headers=app, json={"value": {"n": 1}, "revision": 0})

        view = self.open_app()
        self.assertEqual(
            self.client.delete(
                f"/api/desktops/{self.desktop}/views/{view['id']}", headers=self.hub
            ).status_code,
            200,
        )
        self.assertEqual(self.views()["views"], [])
        self.assertTrue(
            next(
                item
                for item in self.get("/api/apps").json()["apps"]
                if item["id"] == "chat-fixture"
            )["installed"]
        )
        self.assertEqual(self.client.get("/api/app/storage", headers=app).json()["value"], {"n": 1})

    def test_deleting_a_desktop_closes_its_views_and_not_another_desktops(self):
        other = self.client.post("/api/desktops", headers=self.hub, json={}).json()["id"]
        mine = self.open_app()
        theirs = self.open(other, kind="web", url="https://example.com/a").json()
        self.client.delete(f"/api/desktops/{other}", headers=self.hub)
        self.assertEqual([v["id"] for v in self.views()["views"]], [mine["id"]])
        self.assertEqual(self.get(f"/api/desktops/{other}/views").status_code, 404)
        self.assertEqual(theirs["desktopId"], other)

    def test_views_survive_a_restart(self):
        view = self.open_app()
        self.patch_view(view["id"], bounds={"x": 12, "y": 34, "width": 640, "height": 480})
        self.save_layout(revision=0, arrangement="maximized", maximizedView=view["id"])
        self.client.close()

        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}
        payload = self.views()
        self.assertEqual(len(payload["views"]), 1)
        self.assertEqual(payload["views"][0]["window"]["bounds"], {"x": 12, "y": 34, "width": 640, "height": 480})
        self.assertEqual(payload["layout"]["arrangement"], "maximized")
        self.assertEqual(payload["layout"]["maximizedView"], view["id"])

    def test_views_need_a_hub_session(self):
        self.assertEqual(self.client.get(f"/api/desktops/{self.desktop}/views").status_code, 401)
        self.assertEqual(
            self.client.post(f"/api/desktops/{self.desktop}/views", json={"kind": "app"}).status_code,
            401,
        )


if __name__ == "__main__":
    unittest.main()
