"""Desktops through the API: creating, renaming, deleting, boards and appearance.

The point of these is the boundaries rather than the CRUD. A desktop is a
workspace, so deleting one must not touch an installed app's data; `/api/desk`
is the first desktop's boards under its old name, so the two routes have to
agree about the same revision; and two dashboards saving at once must not lose
the first edit.
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
from vela.desktops.models import MAX_DESKTOPS

ROOT = base.ROOT

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
OTHER_JPEG = b"\xff\xd8\xff\xe0" + b"\x11" * 64


def board(widgets, cols=6):
    return {"cols": cols, "widgets": widgets}


def clock(i="w1", x=0, y=0):
    return {"i": i, "type": "clock", "x": x, "y": y, "w": 2, "h": 1, "cfg": {}}


class DesktopApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-desktops-")
        self.root = Path(self.temp.name)
        self.apps = self.root / "catalog"
        shutil.copytree(ROOT / "tests/fixtures/chat-fixture", self.apps / "chat-fixture")
        (self.apps / "chat-fixture" / "app.json").write_text(json.dumps(copy.deepcopy(base.FIXTURE)))
        self.config = Config(self.root / "data", self.apps, ROOT / "web/dist")
        self.config.ensure_dirs()
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def get(self, path):
        return self.client.get(path, headers=self.hub)

    def desktops(self):
        return self.get("/api/desktops").json()

    def default_id(self):
        return self.desktops()["defaultId"]

    # ------------------------------------------------------------ basics --

    def test_a_fresh_server_starts_with_one_desktop(self):
        listed = self.desktops()
        self.assertEqual(len(listed["desktops"]), 1)
        first = listed["desktops"][0]
        self.assertEqual(first["name"], "Desktop 1")
        self.assertEqual(first["kind"], "personal")
        self.assertEqual(listed["defaultId"], first["id"])
        # And it has the widgets a fresh desk has always shown.
        self.assertTrue(self.get(f"/api/desktops/{first['id']}/boards").json()["boards"]["desktop"]["widgets"])

    def test_desktops_need_a_hub_session(self):
        self.assertEqual(self.client.get("/api/desktops").status_code, 401)
        self.assertEqual(self.client.post("/api/desktops", json={}).status_code, 401)

    def test_a_new_desktop_is_named_for_you_and_starts_empty(self):
        created = self.client.post("/api/desktops", headers=self.hub, json={})
        self.assertEqual(created.status_code, 201, created.text)
        made = created.json()
        self.assertEqual(made["name"], "Desktop 2")
        # A new workspace does not inherit the first one's widgets, which would
        # copy whatever those widgets were configured with.
        self.assertEqual(made["boards"]["desktop"]["widgets"], [])
        self.assertEqual(made["boards"]["phone"]["widgets"], [])
        self.assertEqual(made["appearance"]["wallpaper"], "choroni")

        again = self.client.post("/api/desktops", headers=self.hub, json={})
        self.assertEqual(again.json()["name"], "Desktop 3")

    def test_a_desktop_can_be_named_and_renamed_against_its_revision(self):
        made = self.client.post("/api/desktops", headers=self.hub, json={"name": "  Work   trip "})
        self.assertEqual(made.json()["name"], "Work trip")
        desktop_id, revision = made.json()["id"], made.json()["revision"]

        renamed = self.client.patch(
            f"/api/desktops/{desktop_id}", headers=self.hub, json={"name": "Taxes", "revision": revision}
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(renamed.json()["name"], "Taxes")

        stale = self.client.patch(
            f"/api/desktops/{desktop_id}", headers=self.hub, json={"name": "Other", "revision": revision}
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.get(f"/api/desktops/{desktop_id}").json()["name"], "Taxes")

    def test_a_desktop_name_cannot_be_blank_or_endless(self):
        self.assertEqual(
            self.client.post("/api/desktops", headers=self.hub, json={"name": "   "}).status_code, 422
        )
        self.assertEqual(
            self.client.post("/api/desktops", headers=self.hub, json={"name": "x" * 400}).status_code,
            422,
        )

    def test_an_unknown_desktop_is_a_404_and_an_id_cannot_name_a_path(self):
        self.assertEqual(self.get("/api/desktops/" + "0" * 32).status_code, 404)
        # Encoded, because the HTTP client resolves a literal `..` before it
        # is ever sent. What matters is what the route does with the segment.
        for bad in ("%2E%2E", "..%2F..%2Fsettings.json", "%2E%2E%2Fdesktops.sqlite", "not-an-id"):
            with self.subTest(id=bad):
                self.assertEqual(self.get(f"/api/desktops/{bad}").status_code, 404)

    def test_the_server_keeps_a_bounded_number_of_desktops(self):
        for _ in range(MAX_DESKTOPS - 1):
            self.assertEqual(self.client.post("/api/desktops", headers=self.hub, json={}).status_code, 201)
        refused = self.client.post("/api/desktops", headers=self.hub, json={})
        self.assertEqual(refused.status_code, 429)
        self.assertEqual(len(self.desktops()["desktops"]), MAX_DESKTOPS)

    # ------------------------------------------------------------ boards --

    def test_boards_are_validated_and_saved_against_their_revision(self):
        desktop_id = self.client.post("/api/desktops", headers=self.hub, json={}).json()["id"]
        current = self.get(f"/api/desktops/{desktop_id}/boards").json()

        saved = self.client.put(
            f"/api/desktops/{desktop_id}/boards",
            headers=self.hub,
            json={
                "revision": current["revision"],
                "boards": {"desktop": board([clock()]), "phone": board([], cols=2)},
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["revision"], current["revision"] + 1)

        stale = self.client.put(
            f"/api/desktops/{desktop_id}/boards",
            headers=self.hub,
            json={
                "revision": current["revision"],
                "boards": {"desktop": board([]), "phone": board([], cols=2)},
            },
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.headers["X-Vela-Desk-Revision"], str(current["revision"] + 1))
        # The first edit is still there.
        self.assertEqual(
            len(self.get(f"/api/desktops/{desktop_id}/boards").json()["boards"]["desktop"]["widgets"]),
            1,
        )

    def test_a_board_that_would_not_draw_again_is_refused(self):
        desktop_id = self.default_id()
        revision = self.get(f"/api/desktops/{desktop_id}/boards").json()["revision"]
        overlapping = self.client.put(
            f"/api/desktops/{desktop_id}/boards",
            headers=self.hub,
            json={
                "revision": revision,
                "boards": {
                    "desktop": board([clock("a"), clock("b")]),
                    "phone": board([], cols=2),
                },
            },
        )
        self.assertEqual(overlapping.status_code, 422)
        self.assertIn("overlap", overlapping.json()["detail"])

    def test_each_desktop_keeps_its_own_arrangement(self):
        first = self.default_id()
        second = self.client.post("/api/desktops", headers=self.hub, json={}).json()["id"]
        self.client.put(
            f"/api/desktops/{second}/boards",
            headers=self.hub,
            json={
                "revision": 0,
                "boards": {"desktop": board([clock("only-here")]), "phone": board([], cols=2)},
            },
        )
        ids = [w["i"] for w in self.get(f"/api/desktops/{first}/boards").json()["boards"]["desktop"]["widgets"]]
        self.assertNotIn("only-here", ids)

    # ------------------------------------------ the compatibility routes --

    def test_the_desk_route_is_the_first_desktop_under_its_old_name(self):
        desktop_id = self.default_id()
        legacy = self.get("/api/desk").json()
        scoped = self.get(f"/api/desktops/{desktop_id}/boards").json()
        self.assertEqual(legacy, scoped, "one board, one revision, two names for it")

        self.client.put(
            "/api/desk",
            headers=self.hub,
            json={
                "revision": legacy["revision"],
                "boards": {"desktop": board([clock("through-the-alias")]), "phone": board([], cols=2)},
            },
        )
        after = self.get(f"/api/desktops/{desktop_id}/boards").json()
        self.assertEqual(
            [w["i"] for w in after["boards"]["desktop"]["widgets"]], ["through-the-alias"]
        )
        self.assertEqual(after["revision"], legacy["revision"] + 1)
        self.assertEqual(self.get("/api/desk").json(), after)

    def test_a_stale_desk_save_still_answers_409_with_the_revision(self):
        revision = self.get("/api/desk").json()["revision"]
        payload = {
            "revision": revision,
            "boards": {"desktop": board([clock()]), "phone": board([], cols=2)},
        }
        self.assertEqual(self.client.put("/api/desk", headers=self.hub, json=payload).status_code, 200)
        stale = self.client.put("/api/desk", headers=self.hub, json=payload)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.headers["X-Vela-Desk-Revision"], str(revision + 1))

    def test_the_settings_route_reads_and_writes_the_first_desktops_appearance(self):
        desktop_id = self.default_id()
        self.client.patch(
            "/api/settings", headers=self.hub, json={"desk": {"wallpaper": "canaima", "dim": False}}
        )
        look = self.get(f"/api/desktops/{desktop_id}/appearance").json()
        self.assertEqual(look["wallpaper"], "canaima")
        self.assertFalse(look["dim"])

        # And the other way round: the settings route shows what the desktop says.
        self.client.put(
            f"/api/desktops/{desktop_id}/appearance", headers=self.hub, json={"labels": False}
        )
        desk = self.get("/api/settings").json()["desk"]
        self.assertFalse(desk["labels"])
        self.assertEqual(desk["wallpaper"], "canaima")

    def test_the_global_half_of_the_desk_settings_is_still_global(self):
        self.client.patch("/api/settings", headers=self.hub, json={"desk": {"volumes": []}})
        desk = self.get("/api/settings").json()["desk"]
        self.assertIn("volumes", desk)
        self.assertIn("weather", desk)
        # Appearance is not duplicated into settings.json.
        stored = json.loads((self.config.data_dir / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("wallpaper", stored.get("desk", {}))

    # -------------------------------------------------------- appearance --

    def test_appearance_is_saved_against_its_own_revision(self):
        desktop_id = self.default_id()
        look = self.get(f"/api/desktops/{desktop_id}/appearance").json()
        saved = self.client.put(
            f"/api/desktops/{desktop_id}/appearance",
            headers=self.hub,
            json={"revision": look["revision"], "wallpaper": "avila"},
        )
        self.assertEqual(saved.status_code, 200)
        stale = self.client.put(
            f"/api/desktops/{desktop_id}/appearance",
            headers=self.hub,
            json={"revision": look["revision"], "wallpaper": "pueblo"},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(
            self.get(f"/api/desktops/{desktop_id}/appearance").json()["wallpaper"], "avila"
        )

    def test_renaming_a_desktop_does_not_conflict_with_arranging_it(self):
        # The point of separate revisions: two people doing unrelated things in
        # the same workspace should both succeed.
        desktop_id = self.default_id()
        desktop = self.get(f"/api/desktops/{desktop_id}").json()
        boards = self.get(f"/api/desktops/{desktop_id}/boards").json()

        renamed = self.client.patch(
            f"/api/desktops/{desktop_id}",
            headers=self.hub,
            json={"name": "Home", "revision": desktop["revision"]},
        )
        arranged = self.client.put(
            f"/api/desktops/{desktop_id}/boards",
            headers=self.hub,
            json={
                "revision": boards["revision"],
                "boards": {"desktop": board([clock()]), "phone": board([], cols=2)},
            },
        )
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(arranged.status_code, 200, arranged.text)

    # ----------------------------------------------------- wallpaper files --

    def test_each_desktop_keeps_its_own_picture(self):
        first = self.default_id()
        second = self.client.post("/api/desktops", headers=self.hub, json={}).json()["id"]
        for desktop_id, content in ((first, JPEG), (second, OTHER_JPEG)):
            saved = self.client.put(
                f"/api/desktops/{desktop_id}/wallpaper",
                headers={**self.hub, "Content-Type": "image/jpeg"},
                content=content,
            )
            self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.get(f"/api/desktops/{first}/wallpaper").content, JPEG)
        self.assertEqual(self.get(f"/api/desktops/{second}/wallpaper").content, OTHER_JPEG)
        # `/api/wallpaper` is the first desktop's picture.
        self.assertEqual(self.get("/api/wallpaper").content, JPEG)

    def test_two_desktops_choosing_the_same_photo_store_it_once_and_share_it(self):
        first = self.default_id()
        second = self.client.post("/api/desktops", headers=self.hub, json={}).json()["id"]
        for desktop_id in (first, second):
            self.client.put(
                f"/api/desktops/{desktop_id}/wallpaper",
                headers={**self.hub, "Content-Type": "image/jpeg"},
                content=JPEG,
            )
        assets = list((self.config.data_dir / "desktop-assets").iterdir())
        self.assertEqual(len(assets), 1, "the same image is stored once, by its digest")

        # Removing one desktop's wallpaper must not take the other's picture away.
        self.assertEqual(
            self.client.delete(f"/api/desktops/{first}/wallpaper", headers=self.hub).status_code, 200
        )
        self.assertEqual(self.get(f"/api/desktops/{first}/wallpaper").status_code, 404)
        self.assertEqual(self.get(f"/api/desktops/{second}/wallpaper").content, JPEG)

    # ---------------------------------------------------------- deletion --

    def test_deleting_a_desktop_leaves_installed_app_data_alone(self):
        self.assertEqual(
            self.client.post("/api/apps/chat-fixture/install", headers=self.hub).status_code, 200
        )
        created = self.client.post("/api/apps/chat-fixture/session", headers=self.hub)
        self.assertEqual(created.status_code, 200, created.text)
        app = {"Authorization": "Bearer " + created.json()["token"]}
        stored = self.client.put(
            "/api/app/storage", headers=app, json={"value": {"text": "still mine"}, "revision": 0}
        )
        self.assertEqual(stored.status_code, 200, stored.text)

        second = self.client.post("/api/desktops", headers=self.hub, json={}).json()["id"]
        self.assertEqual(self.client.delete(f"/api/desktops/{second}", headers=self.hub).status_code, 200)

        read = self.client.get("/api/app/storage", headers=app)
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.json()["value"], {"text": "still mine"})

    def test_the_last_desktop_cannot_be_deleted(self):
        refused = self.client.delete(f"/api/desktops/{self.default_id()}", headers=self.hub)
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(len(self.desktops()["desktops"]), 1)

    def test_deleting_a_desktop_sweeps_only_its_own_unreferenced_image(self):
        first = self.default_id()
        second = self.client.post("/api/desktops", headers=self.hub, json={}).json()["id"]
        self.client.put(
            f"/api/desktops/{first}/wallpaper",
            headers={**self.hub, "Content-Type": "image/jpeg"},
            content=JPEG,
        )
        self.client.put(
            f"/api/desktops/{second}/wallpaper",
            headers={**self.hub, "Content-Type": "image/jpeg"},
            content=OTHER_JPEG,
        )
        self.assertEqual(len(list((self.config.data_dir / "desktop-assets").iterdir())), 2)

        self.client.delete(f"/api/desktops/{second}", headers=self.hub)
        remaining = list((self.config.data_dir / "desktop-assets").iterdir())
        self.assertEqual(len(remaining), 1)
        self.assertEqual(self.get(f"/api/desktops/{first}/wallpaper").content, JPEG)

    def test_desktops_survive_a_restart(self):
        made = self.client.post("/api/desktops", headers=self.hub, json={"name": "Taxes"}).json()
        self.client.put(
            f"/api/desktops/{made['id']}/boards",
            headers=self.hub,
            json={
                "revision": 0,
                "boards": {"desktop": board([clock("kept")]), "phone": board([], cols=2)},
            },
        )
        self.client.close()

        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}
        names = [desktop["name"] for desktop in self.desktops()["desktops"]]
        self.assertEqual(names, ["Desktop 1", "Taxes"])
        self.assertEqual(
            [w["i"] for w in self.get(f"/api/desktops/{made['id']}/boards").json()["boards"]["desktop"]["widgets"]],
            ["kept"],
        )


if __name__ == "__main__":
    unittest.main()
