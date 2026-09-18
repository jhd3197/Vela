"""Files a task was given, files it came back with, and the rules around them.

Two halves, and neither needs a browser.

The first is the store on its own: what a name from a website becomes, what a
quota refuses, what an expiry removes, and — the one that matters most — that an
artifact id from one desktop is worth nothing on another. The second is the API
the owner uses: the picker is theirs, the bytes come back behind their own
authentication, and a desktop that is deleted takes its staged files with it and
leaves the installed apps alone.

What is deliberately *not* tested here is a tool reading a path, because there is
no tool that takes one. `test_agent_tools.py` covers the surface; this covers
what is behind it.
"""

import copy
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import test_app_contract as base
from fastapi.testclient import TestClient
from vela.agent_runs.artifacts import (
    ARTIFACT_TTL_SECONDS,
    MAX_UPLOAD_BYTES,
    ArtifactError,
    Artifacts,
    display_name,
    envelope,
    stored_extension,
)
from vela.api import create_app
from vela.config import Config

ROOT = base.ROOT


class NamingTests(unittest.TestCase):
    """A name from a website is a label. It is never a location."""

    def test_a_path_is_reduced_to_its_last_segment(self):
        self.assertEqual(display_name("../../etc/passwd"), "passwd")
        self.assertEqual(display_name(r"C:\Windows\system32\cmd.exe"), "cmd.exe")
        self.assertEqual(display_name("reports/2026/q3.csv"), "q3.csv")

    def test_a_name_that_is_only_dots_falls_back(self):
        self.assertEqual(display_name(".."), "file")
        self.assertEqual(display_name(""), "file")
        self.assertEqual(display_name("   "), "file")

    def test_characters_a_filesystem_treats_specially_are_removed(self):
        self.assertEqual(display_name('re|port?.csv'), "report.csv")
        self.assertNotIn(":", display_name("C:report.csv"))

    def test_an_executable_extension_is_not_one_a_file_is_stored_under(self):
        # Not a claim about the contents — a refusal to give a file a name the
        # host operating system would treat as a program.
        for name in ("setup.exe", "run.bat", "go.sh", "thing.dll", "x.ps1", "y.scr"):
            self.assertEqual(stored_extension(name), ".bin", name)

    def test_an_ordinary_extension_survives(self):
        self.assertEqual(stored_extension("report.PDF"), ".pdf")
        self.assertEqual(stored_extension("notes.txt"), ".txt")
        self.assertEqual(stored_extension("photo.jpeg"), ".jpeg")

    def test_what_a_run_is_told_contains_no_path(self):
        record = {
            "id": "abc",
            "name": "q3.csv",
            "bytes": 10,
            "source": "download",
            "origin": "https://example.com",
            "storedName": "abc.csv",
            "digest": "deadbeef",
        }
        told = envelope(record)
        self.assertEqual(told["artifactId"], "abc")
        self.assertNotIn("storedName", told)
        self.assertNotIn("digest", told)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-artifacts-")
        self.store = Artifacts(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def add(self, desktop="d1", name="notes.txt", body=b"hello", run_id="r1"):
        return self.store.accept_upload(desktop, [body], name=name, run_id=run_id)

    def test_an_upload_is_stored_under_a_generated_name(self):
        record = self.add(name="../../secret.txt")
        self.assertEqual(record["name"], "secret.txt")
        self.assertTrue(record["storedName"].endswith(".txt"))
        self.assertNotIn("secret", record["storedName"])
        path, _ = self.store.file("d1", record["id"])
        self.assertEqual(path.read_bytes(), b"hello")

    def test_two_uploads_of_the_same_name_do_not_overwrite_each_other(self):
        first = self.add(body=b"one")
        second = self.add(body=b"two")
        self.assertNotEqual(first["storedName"], second["storedName"])
        self.assertEqual(self.store.file("d1", first["id"])[0].read_bytes(), b"one")

    def test_an_empty_file_is_refused(self):
        with self.assertRaises(ArtifactError) as caught:
            self.add(body=b"")
        self.assertEqual(caught.exception.status, 422)

    def test_an_oversized_upload_is_refused_while_it_is_being_written(self):
        chunk = b"x" * (1024 * 1024)
        with self.assertRaises(ArtifactError) as caught:
            self.store.accept_upload(
                "d1", (chunk for _ in range(30)), name="big.bin", run_id="r1"
            )
        self.assertEqual(caught.exception.status, 413)
        # Nothing half-written is left looking like a finished file.
        self.assertEqual(self.store.list("d1"), [])

    def test_the_limit_is_reported_before_a_transfer_rather_than_after_one(self):
        limits = self.store.limits("d1", "r1")
        self.assertEqual(limits["maxUploadBytes"], MAX_UPLOAD_BYTES)
        self.assertEqual(limits["expiresAfterSeconds"], ARTIFACT_TTL_SECONDS)
        self.assertEqual(limits["taskBytesUsed"], 0)
        self.add(body=b"12345")
        self.assertEqual(self.store.limits("d1", "r1")["taskBytesUsed"], 5)

    def test_an_id_from_one_desktop_is_nothing_on_another(self):
        record = self.add(desktop="d1")
        with self.assertRaises(ArtifactError) as caught:
            self.store.get("d2", record["id"])
        self.assertEqual(caught.exception.status, 404)
        with self.assertRaises(ArtifactError):
            self.store.file("d2", record["id"])

    def test_an_expired_artifact_is_gone_rather_than_old(self):
        record = self.add()
        with self.store.connection() as db:
            db.execute(
                "UPDATE agent_artifacts SET expires_at=? WHERE id=?",
                (time.time() - 1, record["id"]),
            )
        stored = self.store._folder("d1") / record["storedName"]
        self.assertTrue(stored.is_file())
        self.assertEqual(self.store.sweep(), 1)
        self.assertFalse(stored.is_file())
        with self.assertRaises(ArtifactError):
            self.store.get("d1", record["id"])

    def test_a_file_no_row_points_at_is_swept(self):
        orphan = self.store._folder("d1") / "left-behind.bin"
        orphan.write_bytes(b"x")
        self.assertGreaterEqual(self.store.sweep(), 1)
        self.assertFalse(orphan.is_file())

    def test_a_download_is_only_accepted_from_the_directory_vela_owns(self):
        elsewhere = Path(self.temp.name) / "somewhere-else.txt"
        elsewhere.write_bytes(b"not from the browser")
        with self.assertRaises(ArtifactError) as caught:
            self.store.ingest_download("d1", elsewhere, name="x.txt", run_id="r1")
        self.assertEqual(caught.exception.status, 422)
        # And the file it refused is still where it was, not moved or removed.
        self.assertTrue(elsewhere.is_file())

    def test_a_download_becomes_an_artifact_with_where_it_came_from(self):
        staged = self.store.downloads_dir() / "abc.part"
        staged.write_bytes(b"csv,data")
        record = self.store.ingest_download(
            "d1", staged, name="report.csv", run_id="r1", origin="https://example.com"
        )
        self.assertEqual(record["source"], "download")
        self.assertEqual(record["origin"], "https://example.com")
        self.assertEqual(record["name"], "report.csv")
        self.assertFalse(staged.exists(), "the staged copy is moved, not left behind")

    def test_a_desktop_that_goes_takes_its_files_with_it(self):
        self.add(desktop="d1")
        self.add(desktop="d2")
        self.assertEqual(self.store.forget_desktop("d1"), 1)
        self.assertEqual(self.store.list("d1"), [])
        self.assertEqual(len(self.store.list("d2")), 1)


class ArtifactApiTests(unittest.TestCase):
    """The owner's side: their picker, their bytes, their deletion."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-artifact-api-")
        self.root = Path(self.temp.name)
        apps = self.root / "catalog"
        shutil.copytree(ROOT / "tests/fixtures/chat-fixture", apps / "chat-fixture")
        (apps / "chat-fixture" / "app.json").write_text(json.dumps(copy.deepcopy(base.FIXTURE)))
        self.config = Config(self.root / "data", apps, ROOT / "web/dist")
        self.config.ensure_dirs()
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}
        self.desktop = self.client.get("/api/desktops", headers=self.hub).json()["defaultId"]

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def upload(self, name="notes.txt", body=b"hello", desktop=None):
        return self.client.post(
            f"/api/desktops/{desktop or self.desktop}/files",
            headers={**self.hub, "Content-Type": "text/plain", "X-Vela-Filename": name},
            content=body,
        )

    def test_a_file_the_owner_chose_is_listed_with_the_limits(self):
        added = self.upload()
        self.assertEqual(added.status_code, 201, added.text)
        listed = self.client.get(
            f"/api/desktops/{self.desktop}/files", headers=self.hub
        ).json()
        self.assertEqual(len(listed["files"]), 1)
        self.assertEqual(listed["files"][0]["name"], "notes.txt")
        self.assertEqual(listed["limits"]["maxUploadBytes"], MAX_UPLOAD_BYTES)
        self.assertEqual(listed["unresolved"], [])

    def test_the_bytes_come_back_as_a_download_and_never_inline(self):
        artifact = self.upload(body=b"the contents").json()
        got = self.client.get(
            f"/api/desktops/{self.desktop}/files/{artifact['id']}", headers=self.hub
        )
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.content, b"the contents")
        # Never rendered in the dashboard's own origin: a file that arrived from
        # a website is not something to open there.
        self.assertEqual(got.headers["content-type"], "application/octet-stream")
        self.assertIn("nosniff", got.headers["x-content-type-options"])
        self.assertIn("no-store", got.headers["cache-control"])

    def test_a_file_needs_the_owner_s_own_authentication(self):
        artifact = self.upload().json()
        anonymous = self.client.get(f"/api/desktops/{self.desktop}/files/{artifact['id']}")
        self.assertIn(anonymous.status_code, (401, 403))

    def test_a_file_belongs_to_one_desktop(self):
        artifact = self.upload().json()
        other = self.client.post("/api/desktops", headers=self.hub, json={"name": "Two"}).json()
        elsewhere = self.client.get(
            f"/api/desktops/{other['id']}/files/{artifact['id']}", headers=self.hub
        )
        self.assertEqual(elsewhere.status_code, 404)

    def test_removing_a_file_removes_it(self):
        artifact = self.upload().json()
        removed = self.client.delete(
            f"/api/desktops/{self.desktop}/files/{artifact['id']}", headers=self.hub
        )
        self.assertEqual(removed.status_code, 200)
        again = self.client.get(
            f"/api/desktops/{self.desktop}/files/{artifact['id']}", headers=self.hub
        )
        self.assertEqual(again.status_code, 404)

    def test_deleting_a_desktop_takes_its_files_and_leaves_the_apps_alone(self):
        other = self.client.post("/api/desktops", headers=self.hub, json={"name": "Two"}).json()
        self.upload(desktop=other["id"])
        installed_before = self.client.get("/api/apps", headers=self.hub).json()
        gone = self.client.delete(f"/api/desktops/{other['id']}", headers=self.hub)
        self.assertEqual(gone.status_code, 200)
        self.assertEqual(gone.json()["removedFiles"], 1)
        self.assertEqual(self.client.get("/api/apps", headers=self.hub).json(), installed_before)

    def test_a_desktop_with_no_session_says_so_rather_than_failing(self):
        state = self.client.get(f"/api/desktops/{self.desktop}/session", headers=self.hub).json()
        self.assertFalse(state["remembered"])
        self.assertFalse(state["allowed"])
        self.assertEqual(state["cookies"], 0)

    def test_keeping_a_session_is_refused_when_the_desktop_forgets_them(self):
        # Refused for the right reason and in the right order: the desktop has
        # no agent browser, which is a clearer answer than the policy.
        kept = self.client.post(f"/api/desktops/{self.desktop}/session", headers=self.hub)
        self.assertIn(kept.status_code, (409, 503))

    def test_erasing_a_session_is_safe_when_there_is_nothing_to_erase(self):
        erased = self.client.delete(f"/api/desktops/{self.desktop}/session", headers=self.hub)
        self.assertEqual(erased.status_code, 200)
        self.assertFalse(erased.json()["remembered"])


if __name__ == "__main__":
    unittest.main()
