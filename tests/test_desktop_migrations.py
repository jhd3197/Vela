"""Turning an existing desk into Desktop 1, without losing any of it.

The desk being migrated is somebody's arrangement, so these run against the
store directly with disposable data: a clean desk, a missing one, a damaged one,
an interrupted migration and a second run. The one thing none of them may
produce is two Desktop 1s.
"""

import json
import tempfile
import unittest
from pathlib import Path

from vela.desktops.migration import MARKER, migrate
from vela.desktops.store import DesktopStore

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


class FakeSettings:
    """Just enough of `SettingsStore` for the migration to read the desk block."""

    def __init__(self, desk=None):
        self._desk = desk

    def get(self, key):
        return self._desk if key == "desk" else None


class DesktopMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-desktop-migration-")
        self.root = Path(self.temp.name)
        self.assets = self.root / "desktop-assets"
        self.desk = self.root / "desk.json"
        self.store = DesktopStore(self.root / "desktops.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def run_migration(self, settings=None, wallpaper=None, store=None):
        return migrate(
            store or self.store,
            desk_path=self.desk,
            settings=settings if settings is not None else FakeSettings(),
            wallpaper_path=wallpaper,
            assets_dir=self.assets,
        )

    def write_desk(self, boards, revision=7):
        self.desk.write_text(json.dumps({"revision": revision, "boards": boards}), encoding="utf-8")

    def test_an_arranged_desk_arrives_whole(self):
        self.write_desk(
            {
                "version": 1,
                "desktop": {
                    "cols": 6,
                    "widgets": [
                        {"i": "w1", "type": "clock", "x": 0, "y": 0, "w": 2, "h": 1, "cfg": {}},
                        {
                            "i": "mine",
                            "type": "chat-fixture:notes",
                            "x": 2,
                            "y": 0,
                            "w": 4,
                            "h": 2,
                            "cfg": {"pinned": True},
                        },
                    ],
                },
                "phone": {
                    "cols": 2,
                    "widgets": [{"i": "p1", "type": "ask", "x": 0, "y": 3, "w": 2, "h": 1, "cfg": {}}],
                },
            }
        )
        result = self.run_migration()
        self.assertTrue(result["migrated"])

        boards = self.store.boards(result["desktopId"])["widgets"]
        self.assertEqual([w["i"] for w in boards["desktop"]], ["w1", "mine"])
        # An app's own widget type survives even though no app is installed here:
        # dropping unknown types is a read-time decision, not a migration one.
        self.assertEqual(boards["desktop"][1]["type"], "chat-fixture:notes")
        self.assertEqual(boards["desktop"][1]["cfg"], {"pinned": True})
        # The phone board is the same desk on a smaller screen, not a second
        # desktop, so its own arrangement comes across untouched.
        self.assertEqual(boards["phone"], [
            {"i": "p1", "type": "ask", "x": 0, "y": 3, "w": 2, "h": 1, "cfg": {}}
        ])
        # The original file is left where it is.
        self.assertTrue(self.desk.is_file())

    def test_appearance_comes_from_the_desk_settings(self):
        self.write_desk({"desktop": {"widgets": []}, "phone": {"widgets": []}})
        result = self.run_migration(
            FakeSettings({"wallpaper": "canaima", "dim": False, "labels": False, "volumes": []})
        )
        look = self.store.appearance(result["desktopId"])
        self.assertEqual(look["wallpaper"], "canaima")
        self.assertFalse(look["dim"])
        self.assertFalse(look["labels"])

    def test_a_retired_wallpaper_becomes_one_that_can_be_drawn(self):
        result = self.run_migration(FakeSettings({"wallpaper": "lake"}))
        self.assertEqual(self.store.appearance(result["desktopId"])["wallpaper"], "choroni")

    def test_the_uploaded_wallpaper_is_copied_by_its_own_digest(self):
        source = self.root / "wallpaper.jpg"
        source.write_bytes(JPEG)
        result = self.run_migration(FakeSettings({"wallpaper": "custom"}), wallpaper=source)

        look = self.store.appearance(result["desktopId"])
        self.assertEqual(look["wallpaper"], "custom")
        digest = look["wallpaperAsset"]
        self.assertTrue((self.assets / f"{digest}.jpg").is_file())
        self.assertEqual((self.assets / f"{digest}.jpg").read_bytes(), JPEG)
        # The original is preserved, not moved.
        self.assertTrue(source.is_file())
        self.assertEqual(self.store.asset(digest)["bytes"], len(JPEG))

    def test_a_custom_wallpaper_with_no_image_falls_back_and_says_so(self):
        result = self.run_migration(FakeSettings({"wallpaper": "custom"}), wallpaper=None)
        self.assertEqual(self.store.appearance(result["desktopId"])["wallpaper"], "choroni")
        self.assertTrue(any("no image was stored" in note for note in result["notes"]))

    def test_no_saved_desk_gets_the_same_widgets_a_fresh_desk_has_always_had(self):
        result = self.run_migration()
        boards = self.store.boards(result["desktopId"])["widgets"]
        self.assertEqual([w["type"] for w in boards["desktop"]][:2], ["clock", "apps"])
        self.assertTrue(boards["phone"])
        self.assertTrue(any("No saved desk" in note for note in result["notes"]))

    def test_an_unreadable_desk_keeps_the_file_and_reports_the_repair(self):
        self.desk.write_text("{not json", encoding="utf-8")
        result = self.run_migration()
        self.assertTrue(result["migrated"])
        self.assertTrue(any("could not be read" in note for note in result["notes"]))
        self.assertTrue(self.desk.is_file())
        self.assertEqual(self.desk.read_text(encoding="utf-8"), "{not json")

    def test_damaged_entries_are_left_out_and_counted(self):
        self.write_desk(
            {
                "desktop": {
                    "widgets": [
                        {"i": "good", "type": "clock", "x": 0, "y": 0, "w": 2, "h": 1},
                        "not a widget",
                        {"i": "good", "type": "clock", "x": 1, "y": 1, "w": 1, "h": 1},
                        {"i": "no-size", "type": "clock", "x": 0, "y": 2, "w": 0, "h": 1},
                        {"i": "negative", "type": "clock", "x": -1, "y": 0, "w": 1, "h": 1},
                        {"i": "too-wide", "type": "clock", "x": 0, "y": 4, "w": 99, "h": 1},
                    ]
                },
                "phone": {"widgets": []},
            }
        )
        result = self.run_migration()
        widgets = self.store.boards(result["desktopId"])["widgets"]["desktop"]
        self.assertEqual([w["i"] for w in widgets], ["good", "too-wide"])
        self.assertEqual(widgets[1]["w"], 6, "a widget wider than the board is narrowed, not dropped")
        self.assertTrue(any("could not be read" in note for note in result["notes"]))

    def test_an_older_desk_file_without_boards_still_migrates(self):
        self.desk.write_text(json.dumps({"widgets": []}), encoding="utf-8")
        result = self.run_migration()
        self.assertTrue(result["migrated"])
        self.assertTrue(self.store.boards(result["desktopId"])["widgets"]["desktop"])

    def test_one_missing_board_does_not_cost_the_other(self):
        self.write_desk(
            {
                "desktop": {
                    "widgets": [{"i": "keep", "type": "clock", "x": 0, "y": 0, "w": 2, "h": 1}]
                }
            }
        )
        result = self.run_migration()
        boards = self.store.boards(result["desktopId"])["widgets"]
        self.assertEqual([w["i"] for w in boards["desktop"]], ["keep"])
        self.assertTrue(boards["phone"], "the missing phone board is seeded, not left empty")
        self.assertTrue(any("phone board was missing" in note for note in result["notes"]))

    def test_running_it_again_changes_nothing(self):
        self.write_desk(
            {"desktop": {"widgets": [{"i": "w1", "type": "clock", "x": 0, "y": 0, "w": 2, "h": 1}]},
             "phone": {"widgets": []}}
        )
        first = self.run_migration()
        # The desk changes afterwards, the way it would as someone uses Vela.
        self.store.save_boards(first["desktopId"], {"desktop": [], "phone": []}, 0)

        second = self.run_migration()
        self.assertFalse(second["migrated"])
        self.assertEqual(second["desktopId"], first["desktopId"])
        self.assertEqual(len(self.store.list()), 1)
        # The second run did not put the old widget back.
        self.assertEqual(self.store.boards(first["desktopId"])["widgets"]["desktop"], [])

    def test_a_crash_before_the_commit_leaves_one_desktop_and_reuses_the_image(self):
        source = self.root / "wallpaper.jpg"
        source.write_bytes(JPEG)
        self.write_desk({"desktop": {"widgets": []}, "phone": {"widgets": []}})

        # Interrupt exactly between preparing the file and committing the row.
        broken = DesktopStore(self.root / "desktops.sqlite")
        original = broken.create

        def explode(*args, **kwargs):
            raise KeyboardInterrupt("the machine lost power here")

        broken.create = explode
        with self.assertRaises(KeyboardInterrupt):
            self.run_migration(FakeSettings({"wallpaper": "custom"}), wallpaper=source, store=broken)

        # The image is on disk; nothing points at it and no desktop exists.
        orphans = list(self.assets.iterdir())
        self.assertEqual(len(orphans), 1)
        self.assertIsNone(broken.marker(MARKER))
        self.assertEqual(broken.list(), [])

        broken.create = original
        result = self.run_migration(FakeSettings({"wallpaper": "custom"}), wallpaper=source, store=broken)
        self.assertTrue(result["migrated"])
        self.assertEqual(len(broken.list()), 1, "a retry must not create a second Desktop 1")
        # Same content, same name: the verified file is reused rather than rewritten.
        self.assertEqual([p.name for p in self.assets.iterdir()], [p.name for p in orphans])

    def test_a_marker_written_by_another_process_wins_without_a_second_desktop(self):
        self.write_desk({"desktop": {"widgets": []}, "phone": {"widgets": []}})
        self.run_migration()
        # A second server object over the same file, as a restart race would be.
        other = DesktopStore(self.root / "desktops.sqlite")
        result = self.run_migration(store=other)
        self.assertFalse(result["migrated"])
        self.assertEqual(len(other.list()), 1)


if __name__ == "__main__":
    unittest.main()
