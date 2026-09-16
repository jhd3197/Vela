"""The saved desk: seeding, validation, repair-on-read and revision conflicts.

The geometry rules here are the ones `web/src/desk/grid/layout.js` implements in
the browser and `tests/desk-layout.test.mjs` pins there. The server is
authoritative, so these are the ones that decide what can be stored.
Everything uses disposable data.
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
from vela.desk import (
    CORE_WIDGET_TYPES,
    MAX_WIDGETS_PER_BOARD,
    DeskError,
    DeskStore,
    default_boards,
    overlaps,
    validate_widgets,
)

ROOT = base.ROOT
KNOWN = set(CORE_WIDGET_TYPES)


def widget(i, kind="clock", x=0, y=0, w=2, h=1, cfg=None):
    return {"i": i, "type": kind, "x": x, "y": y, "w": w, "h": h, "cfg": cfg or {}}


class ValidationTests(unittest.TestCase):
    def test_a_valid_board_comes_back_normalised(self):
        board = [widget("w1"), widget("w2", y=1), widget("w3", "apps", x=2, y=0, w=4, h=2)]
        self.assertEqual(validate_widgets(board, 6, KNOWN), board)
        # Unknown extra keys are dropped rather than stored.
        extra = [{**widget("w1"), "colour": "red"}]
        self.assertEqual(validate_widgets(extra, 6, KNOWN), [widget("w1")])

    def test_geometry_and_type_rules_are_refused_with_a_reason(self):
        cases = {
            "must be at least one cell": [widget("w1", w=0)],
            "does not fit in 6 columns": [widget("w1", x=5, w=2)],
            "whole, non-negative": [widget("w1", x=-1)],
            "unknown widget type": [widget("w1", "not-a-widget")],
            "needs an id": [widget("")],
            "share the id": [widget("w1"), widget("w1", y=3)],
            "overlap": [widget("w1", w=4), widget("w2", x=2, w=4)],
            "at most 40 widgets": [widget(f"w{n}", y=n) for n in range(MAX_WIDGETS_PER_BOARD + 1)],
            "invalid options": [{**widget("w1"), "cfg": "nope"}],
        }
        for reason, board in cases.items():
            with self.subTest(reason=reason):
                with self.assertRaises(DeskError) as raised:
                    validate_widgets(board, 6, KNOWN)
                self.assertIn(reason, str(raised.exception))
        # True is not 1: a boolean coordinate is a bug, not a position.
        with self.assertRaises(DeskError):
            validate_widgets([{**widget("w1"), "x": True}], 6, KNOWN)

    def test_touching_widgets_do_not_overlap(self):
        a = widget("w1", w=2, h=2)
        self.assertFalse(overlaps(a, widget("w2", x=2, w=2, h=2)))
        self.assertFalse(overlaps(a, widget("w2", y=2, w=2, h=2)))
        self.assertTrue(overlaps(a, widget("w2", x=1, y=1, w=2, h=2)))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-desk-")
        self.path = Path(self.temp.name) / "desk.json"
        self.store = DeskStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_a_missing_file_seeds_the_default_desk(self):
        loaded = self.store.load(KNOWN)
        self.assertEqual(loaded["revision"], 0)
        self.assertEqual(loaded["boards"], default_boards())
        self.assertEqual([w["type"] for w in loaded["boards"]["phone"]["widgets"]],
                         ["clock", "needs-you", "apps", "ask"])
        # Reading does not write: the defaults are not frozen into a file the
        # user never asked for.
        self.assertFalse(self.path.exists())

    def test_saving_bumps_the_revision_and_writes_atomically(self):
        boards = default_boards()
        saved = self.store.save(boards, 0, KNOWN)
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(self.store.load(KNOWN)["revision"], 1)
        self.assertEqual(json.loads(self.path.read_text())["revision"], 1)
        # No temporary file is left behind.
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["desk.json"])
        again = self.store.save(boards, 1, KNOWN)
        self.assertEqual(again["revision"], 2)

    def test_a_stale_revision_is_refused_without_touching_the_file(self):
        boards = default_boards()
        self.store.save(boards, 0, KNOWN)
        stale = copy.deepcopy(boards)
        stale["desktop"]["widgets"] = [widget("w1")]
        with self.assertRaises(ValueError) as raised:
            self.store.save(stale, 0, KNOWN)
        # The conflict carries the revision the caller should reload from.
        self.assertEqual(raised.exception.args[0], 1)
        self.assertEqual(len(self.store.load(KNOWN)["boards"]["desktop"]["widgets"]), 4)

    def test_a_damaged_board_is_repaired_rather_than_thrown_away(self):
        self.path.write_text(
            json.dumps(
                {
                    "revision": 7,
                    "boards": {
                        "desktop": {
                            "cols": 6,
                            "widgets": [
                                # Too wide for the board.
                                widget("w1", "apps", x=4, w=6, h=2),
                                # Sits on top of w1 once w1 is pulled back.
                                widget("w2", "clock", x=0, y=0),
                                # Its app is gone.
                                widget("w3", "notes:sync"),
                                # Not a widget at all.
                                "rubbish",
                                {"i": "w5"},
                            ],
                        },
                        "phone": {"cols": 2, "widgets": []},
                    },
                }
            ),
            encoding="utf-8",
        )
        loaded = self.store.load(KNOWN)
        self.assertEqual(loaded["revision"], 7)
        desktop = loaded["boards"]["desktop"]["widgets"]
        # w3 named an uninstalled app's widget and the last two are not widgets
        # at all, so only w1 and w2 survive.
        self.assertEqual([w["i"] for w in desktop], ["w1", "w2"])
        self.assertEqual(desktop[0]["w"], 6, "a too-wide widget is narrowed to the board")
        self.assertEqual(desktop[0]["x"], 0)
        self.assertEqual(desktop[1]["y"], 2, "the widget it landed on is pushed clear")
        for a in desktop:
            for b in desktop:
                self.assertFalse(overlaps(a, b))
        # An empty board the user emptied stays empty.
        self.assertEqual(loaded["boards"]["phone"]["widgets"], [])

    def test_an_unreadable_file_falls_back_to_the_seeded_desk(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.store.load(KNOWN)["boards"], default_boards())


class DeskApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-desk-api-")
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

    def test_the_desk_needs_a_hub_session(self):
        self.assertEqual(self.client.get("/api/desk").status_code, 401)
        self.assertEqual(self.client.put("/api/desk", json={}).status_code, 401)

    def test_get_seeds_put_saves_and_a_stale_put_is_409(self):
        first = self.client.get("/api/desk", headers=self.hub).json()
        self.assertEqual(first["revision"], 0)
        boards = first["boards"]
        boards["desktop"]["widgets"] = [widget("w1", "system"), widget("w2", "backups", y=1)]
        saved = self.client.put(
            "/api/desk", headers=self.hub, json={"revision": 0, "boards": boards}
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["revision"], 1)
        reloaded = self.client.get("/api/desk", headers=self.hub).json()
        self.assertEqual([w["type"] for w in reloaded["boards"]["desktop"]["widgets"]],
                         ["system", "backups"])

        stale = self.client.put(
            "/api/desk", headers=self.hub, json={"revision": 0, "boards": boards}
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.headers["X-Vela-Desk-Revision"], "1")

    def test_an_invalid_board_is_422_and_changes_nothing(self):
        boards = self.client.get("/api/desk", headers=self.hub).json()["boards"]
        broken = copy.deepcopy(boards)
        broken["desktop"]["widgets"] = [widget("w1", "clock", x=5, w=4)]
        response = self.client.put(
            "/api/desk", headers=self.hub, json={"revision": 0, "boards": broken}
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("does not fit", response.json()["detail"])
        self.assertEqual(self.client.get("/api/desk", headers=self.hub).json()["revision"], 0)

        # A type no installed app provides is not storable either.
        broken["desktop"]["widgets"] = [widget("w1", "chat-fixture:sync")]
        self.assertEqual(
            self.client.put(
                "/api/desk", headers=self.hub, json={"revision": 0, "boards": broken}
            ).status_code,
            422,
        )


if __name__ == "__main__":
    unittest.main()
