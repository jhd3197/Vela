"""The Files app's boundary: what is inside a share, and nothing else.

Vela can read whatever the person running it can read, so the whole safety of
this feature is one rule — a path is served only when it resolves inside a
configured share. Most of this file is that rule, tried from every direction:
`..`, an absolute path, a drive letter, a UNC path, a symlink pointing out, and
a name that tries to reintroduce a path during a rename or an upload.

The rest covers the promises around it: deletes go to the trash and are cleared
after thirty days, uploads stop at the cap, and a read-only share refuses every
change. Everything uses disposable directories.
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from unittest import mock

import test_app_contract as base
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.config import Config
from vela.files import (
    MAX_UPLOAD_BYTES,
    TRASH_DAYS,
    FileError,
    Files,
    validate_name,
    validate_shares,
)
from vela.settings import SettingsStore

ROOT = base.ROOT


def build(tmp: Path, *, writable=True, extra=None):
    """A server with one share full of disposable files."""
    config = Config(tmp / "data", tmp / "catalog", ROOT / "web/dist")
    config.ensure_dirs()
    share = tmp / "share"
    (share / "notes").mkdir(parents=True)
    (share / "notes" / "todo.txt").write_text("milk", encoding="utf-8")
    (share / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0not-really-a-jpeg")
    # Something the share must never reach.
    (tmp / "secret.txt").write_text("not yours", encoding="utf-8")
    settings = SettingsStore(config.settings_file)
    shares = [{"id": "docs", "label": "Docs", "path": str(share), "writable": writable}]
    settings.set("files", {"shares": shares + list(extra or [])})
    return Files(config, settings), config, share


class ShareValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-files-cfg-")
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        (self.root / "folder").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def check(self, shares):
        return validate_shares(shares, self.root / "data")

    def test_a_share_names_a_folder_that_exists_now(self):
        stored = self.check([{"id": "docs", "label": "Docs", "path": str(self.root / "folder")}])
        self.assertEqual(stored[0]["id"], "docs")
        self.assertEqual(stored[0]["writable"], True)
        with self.assertRaises(ValueError):
            self.check([{"id": "docs", "path": str(self.root / "nowhere")}])
        # A file is not a folder.
        (self.root / "a-file").write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.check([{"id": "docs", "path": str(self.root / "a-file")}])

    def test_velas_own_data_folder_cannot_be_shared(self):
        # Browsing it would be a way to delete every app's data at once.
        with self.assertRaises(ValueError):
            self.check([{"id": "data", "path": str(self.root / "data")}])
        (self.root / "data" / "installed").mkdir(exist_ok=True)
        with self.assertRaises(ValueError):
            self.check([{"id": "installed", "path": str(self.root / "data" / "installed")}])

    def test_the_default_downloads_share_under_the_data_dir_is_allowed(self):
        downloads = self.root / "data" / "shares" / "downloads"
        downloads.mkdir(parents=True)
        stored = self.check([{"id": "downloads", "path": str(downloads)}])
        self.assertEqual(stored[0]["id"], "downloads")

    def test_ids_are_simple_unique_and_bounded(self):
        for bad in ("", "has space", "has/slash", "x" * 41, ".."):
            with self.subTest(id=bad), self.assertRaises(ValueError):
                self.check([{"id": bad, "path": str(self.root / "folder")}])
        with self.assertRaises(ValueError):
            self.check(
                [
                    {"id": "docs", "path": str(self.root / "folder")},
                    {"id": "docs", "path": str(self.root / "folder")},
                ]
            )

    def test_a_label_falls_back_to_the_folder_name(self):
        stored = self.check([{"id": "docs", "path": str(self.root / "folder")}])
        self.assertEqual(stored[0]["label"], "folder")


class NameTests(unittest.TestCase):
    def test_a_name_vela_creates_is_one_plain_segment(self):
        # Surrounding space is trimmed rather than refused: it is almost always
        # a paste, and the trimmed name is what the user meant.
        self.assertEqual(validate_name("  Report.txt  "), "Report.txt")
        self.assertEqual(validate_name("trailing "), "trailing")
        for bad in ("", "   ", "..", ".", "a/b", "a\\b", "a:b", 'a"b', "a?b", "a*b", "trailing."):
            with self.subTest(name=bad), self.assertRaises(FileError):
                validate_name(bad)

    def test_windows_reserved_names_are_refused_on_every_platform(self):
        # So a share stays usable when it is copied to another machine.
        for bad in ("con", "CON", "com1", "lpt9.txt", "nul"):
            with self.subTest(name=bad), self.assertRaises(FileError):
                validate_name(bad)

    def test_a_control_character_is_refused(self):
        with self.assertRaises(FileError):
            validate_name("bad\x00name")


class BoundaryTests(unittest.TestCase):
    """The whole point of the feature: nothing outside a share is served."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-files-")
        self.root = Path(self.temp.name)
        self.files, self.config, self.share = build(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_a_path_inside_the_share_resolves(self):
        _, target = self.files.resolve("docs", "notes/todo.txt")
        self.assertEqual(target, (self.share / "notes" / "todo.txt").resolve())
        # The share root itself is inside the share.
        _, root = self.files.resolve("docs", "")
        self.assertEqual(root, self.share.resolve())

    def test_dot_dot_is_refused_rather_than_clamped(self):
        # Clamping would quietly turn a request that tried to leave into a
        # request for the folder above; it is refused instead.
        for attempt in (
            "..",
            "../secret.txt",
            "notes/../../secret.txt",
            "notes/../..",
            "./../../secret.txt",
            r"..\secret.txt",
            "notes/%2e%2e/secret.txt".replace("%2e", "."),
        ):
            with self.subTest(path=attempt), self.assertRaises(FileError) as refused:
                self.files.resolve("docs", attempt)
            self.assertEqual(refused.exception.status, 403)

    def test_an_absolute_path_or_a_drive_letter_is_refused(self):
        for attempt in (
            "/etc/passwd",
            "//server/share/file",
            "C:/Windows/System32/drivers/etc/hosts",
            "C:\\Windows\\win.ini",
            str(self.root / "secret.txt"),
        ):
            with self.subTest(path=attempt):
                try:
                    _, target = self.files.resolve("docs", attempt)
                except FileError as exc:
                    self.assertEqual(exc.status, 403)
                    continue
                # If it resolved at all it must still be inside the share.
                self.assertTrue(
                    self.share.resolve() == target or self.share.resolve() in target.parents,
                    f"{attempt} escaped to {target}",
                )

    def test_a_symlink_pointing_out_of_the_share_is_refused(self):
        link = self.share / "escape"
        try:
            link.symlink_to(self.root / "secret.txt")
        except (OSError, NotImplementedError):
            self.skipTest("this platform will not create symlinks without extra rights")
        with self.assertRaises(FileError) as refused:
            self.files.resolve("docs", "escape")
        self.assertEqual(refused.exception.status, 403)
        # And it cannot be read through the listing either.
        listed = self.files.list("docs", "")
        self.assertNotIn("not yours", json.dumps(listed))

    def test_an_unknown_share_is_a_404_and_names_nothing(self):
        with self.assertRaises(FileError) as refused:
            self.files.resolve("nope", "")
        self.assertEqual(refused.exception.status, 404)
        self.assertNotIn(str(self.share), refused.exception.detail)

    def test_a_rename_cannot_reintroduce_a_path(self):
        for bad in ("../escaped.txt", "notes/deep.txt", "/abs.txt"):
            with self.subTest(name=bad), self.assertRaises(FileError):
                self.files.rename("docs", "photo.jpg", bad)
        self.assertTrue((self.share / "photo.jpg").is_file())

    def test_an_upload_name_cannot_reintroduce_a_path(self):
        for bad in ("../escaped.txt", "notes/deep.txt"):
            with self.subTest(name=bad), self.assertRaises(FileError):
                self.files.save_upload("docs", "", bad, [b"x"])
        self.assertFalse((self.root / "escaped.txt").exists())


class ListingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-files-list-")
        self.root = Path(self.temp.name)
        self.files, self.config, self.share = build(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_a_listing_names_what_is_there_folders_first(self):
        listed = self.files.list("docs", "")
        self.assertEqual([e["name"] for e in listed["entries"]], ["notes", "photo.jpg"])
        folder, photo = listed["entries"]
        self.assertEqual(folder["kind"], "folder")
        self.assertIsNone(folder["size"])
        self.assertEqual(photo["kind"], "image")
        self.assertEqual(photo["size"], len(b"\xff\xd8\xff\xe0not-really-a-jpeg"))
        self.assertTrue(photo["modified"])
        self.assertEqual(listed["path"], "")
        self.assertEqual(listed["share"]["label"], "Docs")

    def test_a_subfolder_reports_its_path_relative_to_the_share(self):
        listed = self.files.list("docs", "notes")
        self.assertEqual(listed["path"], "notes")
        self.assertEqual([e["name"] for e in listed["entries"]], ["todo.txt"])
        self.assertEqual(listed["entries"][0]["path"], "notes/todo.txt")

    def test_listing_a_file_is_a_404_not_a_crash(self):
        with self.assertRaises(FileError) as refused:
            self.files.list("docs", "photo.jpg")
        self.assertEqual(refused.exception.status, 404)

    def test_a_share_that_is_not_plugged_in_is_listed_and_marked(self):
        settings = SettingsStore(self.config.settings_file)
        settings.set(
            "files",
            {
                "shares": [
                    {"id": "docs", "label": "Docs", "path": str(self.share), "writable": True},
                    {"id": "usb", "label": "USB", "path": str(self.root / "gone"), "writable": True},
                ]
            },
        )
        shares = {s["id"]: s for s in Files(self.config, settings).shares()}
        self.assertTrue(shares["docs"]["reachable"])
        self.assertFalse(shares["usb"]["reachable"])
        with self.assertRaises(FileError) as refused:
            Files(self.config, settings).list("usb", "")
        self.assertEqual(refused.exception.status, 409)


class ChangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-files-change-")
        self.root = Path(self.temp.name)
        self.files, self.config, self.share = build(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_a_folder_is_created_and_a_duplicate_is_refused(self):
        self.assertEqual(self.files.mkdir("docs", "", "Trips")["name"], "Trips")
        self.assertTrue((self.share / "Trips").is_dir())
        with self.assertRaises(FileError) as clash:
            self.files.mkdir("docs", "", "Trips")
        self.assertEqual(clash.exception.status, 409)

    def test_renaming_moves_nothing_out_of_its_folder(self):
        self.files.rename("docs", "notes/todo.txt", "shopping.txt")
        self.assertTrue((self.share / "notes" / "shopping.txt").is_file())
        self.assertFalse((self.share / "notes" / "todo.txt").exists())

    def test_moving_puts_something_in_another_folder(self):
        self.files.move("docs", "photo.jpg", "notes")
        self.assertTrue((self.share / "notes" / "photo.jpg").is_file())
        self.assertFalse((self.share / "photo.jpg").exists())

    def test_a_folder_cannot_be_moved_inside_itself(self):
        with self.assertRaises(FileError) as refused:
            self.files.move("docs", "notes", "notes")
        self.assertEqual(refused.exception.status, 422)

    def test_a_share_root_can_be_neither_renamed_nor_deleted(self):
        for call in (lambda: self.files.rename("docs", "", "x"), lambda: self.files.delete("docs", "")):
            with self.subTest(call=call), self.assertRaises(FileError) as refused:
                call()
            self.assertEqual(refused.exception.status, 403)
        self.assertTrue(self.share.is_dir())

    def test_a_read_only_share_refuses_every_change(self):
        files, _, share = build(self.root / "ro", writable=False)
        for call, name in (
            (lambda: files.mkdir("docs", "", "New"), "mkdir"),
            (lambda: files.rename("docs", "photo.jpg", "other.jpg"), "rename"),
            (lambda: files.move("docs", "photo.jpg", "notes"), "move"),
            (lambda: files.delete("docs", "photo.jpg"), "delete"),
            (lambda: files.save_upload("docs", "", "new.txt", [b"x"]), "upload"),
        ):
            with self.subTest(call=name), self.assertRaises(FileError) as refused:
                call()
            self.assertEqual(refused.exception.status, 403)
        # Reading it still works.
        self.assertTrue(files.list("docs", "")["entries"])


class TrashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-files-trash-")
        self.root = Path(self.temp.name)
        self.files, self.config, self.share = build(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_deleting_moves_to_the_trash_rather_than_removing(self):
        removed = self.files.delete("docs", "photo.jpg")
        self.assertFalse((self.share / "photo.jpg").exists())
        trashed = self.config.data_dir / "trash" / removed["trashed"]
        self.assertTrue(trashed.is_file())
        listed = self.files.trash()
        self.assertEqual([e["name"] for e in listed["entries"]], [removed["trashed"]])
        self.assertEqual(listed["days"], TRASH_DAYS)

    def test_two_deletes_of_the_same_name_do_not_overwrite_each_other(self):
        first = self.files.delete("docs", "photo.jpg")
        (self.share / "photo.jpg").write_bytes(b"second")
        second = self.files.delete("docs", "photo.jpg")
        self.assertNotEqual(first["trashed"], second["trashed"])
        self.assertEqual(len(list((self.config.data_dir / "trash").iterdir())), 2)

    def test_the_trash_is_cleared_after_thirty_days_and_not_before(self):
        self.files.delete("docs", "photo.jpg")
        self.files.delete("docs", "notes")
        trash = self.config.data_dir / "trash"
        self.assertEqual(len(list(trash.iterdir())), 2)
        # Nothing is old enough yet.
        self.assertEqual(self.files.sweep_trash(), 0)
        # A month later both go, the folder included.
        later = time.time() + (TRASH_DAYS + 1) * 86400
        self.assertEqual(self.files.sweep_trash(now=later), 2)
        self.assertEqual(list(trash.iterdir()), [])

    def test_the_trash_reads_as_empty_before_anything_is_deleted(self):
        self.assertEqual(self.files.trash()["entries"], [])
        self.assertEqual(self.files.sweep_trash(), 0)


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-files-upload-")
        self.root = Path(self.temp.name)
        self.files, self.config, self.share = build(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_an_upload_is_streamed_into_place(self):
        saved = self.files.save_upload("docs", "notes", "report.txt", [b"one ", b"two"])
        self.assertEqual(saved["bytes"], 7)
        self.assertEqual((self.share / "notes" / "report.txt").read_text("utf-8"), "one two")

    def test_an_upload_over_the_cap_is_refused_and_leaves_nothing_behind(self):
        oversized = (b"x" * 1024 for _ in range((MAX_UPLOAD_BYTES // 1024) + 2))
        with self.assertRaises(FileError) as refused:
            self.files.save_upload("docs", "", "huge.bin", oversized)
        self.assertEqual(refused.exception.status, 413)
        # Neither the file nor the half-written temporary copy survives.
        self.assertEqual(
            sorted(p.name for p in self.share.iterdir()), ["notes", "photo.jpg"]
        )

    def test_an_upload_never_silently_replaces_a_file(self):
        with self.assertRaises(FileError) as clash:
            self.files.save_upload("docs", "", "photo.jpg", [b"different"])
        self.assertEqual(clash.exception.status, 409)
        self.assertEqual((self.share / "photo.jpg").read_bytes()[:4], b"\xff\xd8\xff\xe0")


class FilesApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-files-api-")
        self.root = Path(self.temp.name)
        apps = self.root / "catalog"
        shutil.copytree(ROOT / "tests/fixtures/chat-fixture", apps / "chat-fixture")
        self.config = Config(self.root / "data", apps, ROOT / "web/dist")
        self.config.ensure_dirs()
        self.share = self.root / "share"
        (self.share / "notes").mkdir(parents=True)
        (self.share / "notes" / "todo.txt").write_text("milk", encoding="utf-8")
        (self.root / "secret.txt").write_text("not yours", encoding="utf-8")
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}
        saved = self.client.patch(
            "/api/settings",
            headers=self.hub,
            json={
                "files": {
                    "shares": [
                        {"id": "docs", "label": "Docs", "path": str(self.share), "writable": True}
                    ]
                }
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_files_need_a_hub_session(self):
        self.assertEqual(self.client.get("/api/files").status_code, 401)
        self.assertEqual(self.client.get("/api/files/docs").status_code, 401)
        self.assertEqual(self.client.delete("/api/files/docs?path=notes").status_code, 401)

    def test_a_share_is_listed_and_browsed(self):
        shares = self.client.get("/api/files", headers=self.hub).json()
        self.assertEqual([s["id"] for s in shares["shares"]], ["docs"])
        self.assertEqual(shares["trashDays"], TRASH_DAYS)
        listed = self.client.get("/api/files/docs", headers=self.hub).json()
        self.assertEqual([e["name"] for e in listed["entries"]], ["notes"])

    def test_traversal_over_the_api_is_refused(self):
        for attempt in ("../secret.txt", "notes/../../secret.txt", "/etc/passwd"):
            with self.subTest(path=attempt):
                answer = self.client.get(
                    "/api/files/docs", headers=self.hub, params={"path": attempt}
                )
                self.assertIn(answer.status_code, (403, 404))
                self.assertNotIn("not yours", answer.text)

    def test_a_file_downloads_and_an_unknown_one_is_a_404(self):
        got = self.client.get(
            "/api/files/docs/download", headers=self.hub, params={"path": "notes/todo.txt"}
        )
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.content, b"milk")
        self.assertIn("attachment", got.headers["content-disposition"])
        missing = self.client.get(
            "/api/files/docs/download", headers=self.hub, params={"path": "nope.txt"}
        )
        self.assertEqual(missing.status_code, 404)

    def test_an_svg_is_never_shown_inline_even_when_asked(self):
        # An SVG is an image that can carry script, so it downloads instead.
        (self.share / "art.svg").write_text("<svg/>", encoding="utf-8")
        shown = self.client.get(
            "/api/files/docs/download",
            headers=self.hub,
            params={"path": "art.svg", "inline": "true"},
        )
        self.assertEqual(shown.status_code, 200)
        self.assertIn("attachment", shown.headers["content-disposition"])
        # A picture that cannot is shown.
        (self.share / "art.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        inline = self.client.get(
            "/api/files/docs/download",
            headers=self.hub,
            params={"path": "art.png", "inline": "true"},
        )
        self.assertIn("inline", inline.headers["content-disposition"])

    def test_uploading_storing_and_deleting_over_the_api(self):
        sent = self.client.post(
            "/api/files/docs/upload",
            headers=self.hub,
            params={"path": "notes", "name": "report.txt"},
            content=b"written",
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        self.assertEqual((self.share / "notes" / "report.txt").read_text("utf-8"), "written")

        removed = self.client.delete(
            "/api/files/docs", headers=self.hub, params={"path": "notes/report.txt"}
        )
        self.assertEqual(removed.status_code, 200)
        self.assertFalse((self.share / "notes" / "report.txt").exists())
        trash = self.client.get("/api/files-trash", headers=self.hub).json()
        self.assertEqual(len(trash["entries"]), 1)

    def test_a_folder_is_created_and_renamed_over_the_api(self):
        made = self.client.post(
            "/api/files/docs/folder", headers=self.hub, json={"path": "", "name": "Trips"}
        )
        self.assertEqual(made.status_code, 200, made.text)
        renamed = self.client.post(
            "/api/files/docs/rename", headers=self.hub, json={"path": "Trips", "name": "Travel"}
        )
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertTrue((self.share / "Travel").is_dir())

    def test_every_change_to_a_share_is_audited(self):
        # The handlers that put this on disk are configured when the server is
        # started, not by the test client, so the call itself is what is checked.
        with mock.patch("vela.files.audit") as audited:
            self.client.post(
                "/api/files/docs/folder", headers=self.hub, json={"path": "", "name": "Trips"}
            )
            self.client.post(
                "/api/files/docs/upload",
                headers=self.hub,
                params={"path": "", "name": "note.txt"},
                content=b"x",
            )
            self.client.post(
                "/api/files/docs/rename",
                headers=self.hub,
                json={"path": "Trips", "name": "Travel"},
            )
            self.client.delete("/api/files/docs", headers=self.hub, params={"path": "Travel"})
        events = [call.args[1].split()[0] for call in audited.call_args_list]
        self.assertEqual(events, ["mkdir", "upload", "rename", "delete"])
        self.assertTrue(all(call.args[0] == "files" for call in audited.call_args_list))

    def test_a_bad_share_configuration_is_refused(self):
        refused = self.client.patch(
            "/api/settings",
            headers=self.hub,
            json={"files": {"shares": [{"id": "nope", "path": str(self.root / "missing")}]}},
        )
        self.assertEqual(refused.status_code, 422)
        # The good configuration is still in place.
        shares = self.client.get("/api/files", headers=self.hub).json()["shares"]
        self.assertEqual([s["id"] for s in shares], ["docs"])


if __name__ == "__main__":
    unittest.main()
