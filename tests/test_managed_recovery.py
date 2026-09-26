"""Data that outlives the code, and operations that survive being interrupted.

The dangerous moments in hosting someone else's application are all here: taking
a copy of a database while something might be writing to it, replacing a binary
over data an older version wrote, putting a backup back, and being killed part
way through any of those. Each test below is one of those moments.

The fixture keeps notes in SQLite with a write-ahead log and attachments as
files, so "consistent snapshot" means something measurable rather than a copied
directory that happens to open.

Run with: python -m unittest discover -s tests. Uses disposable data only.
"""

import shutil
import sqlite3
import unittest
from pathlib import Path

from managed_support import APP_ID, ManagedTestCase


class DataTestCase(ManagedTestCase):
    """An installed, running, signed-in app with something worth keeping."""

    def setUp(self):
        super().setUp()
        self.install()
        self.start()
        self.enter()
        self.token = self.sign_in()

    def sign_in(self):
        response = self.app_post(
            "/api/login", json={"user": "tester", "password": "fixture-pw"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": "Bearer " + response.json()["token"]}

    def write_note(self, body):
        response = self.app_post("/api/notes", headers=self.token, json={"body": body})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def attach(self, name, payload):
        response = self.client.post(
            self.url("/api/files"), content=payload,
            headers={"X-Filename": name, "Origin": self.origin(),
                     "Sec-Fetch-Site": "same-origin"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def url(self, path):
        return self.service.origin_for(APP_ID) + path

    def origin(self):
        return self.service.origin_for(APP_ID)

    def notes(self):
        response = self.app_get("/api/notes")
        self.assertEqual(response.status_code, 200, response.text)
        return [note["body"] for note in response.json()["notes"]]

    def reopen(self):
        """Open the app again after something revoked the session."""
        self.enter()
        self.token = self.sign_in()

    def data_dir(self) -> Path:
        record = self.service.store.require(APP_ID)
        manifest = self.service.manifest(APP_ID)
        return self.service.store.paths(APP_ID).data_directory(manifest.data["directory"])

    def snapshot(self, note="test", *, reopen=True):
        """Take a backup. It stops the service, so the window closes with it."""
        response = self.client.post(
            f"/api/managed/{APP_ID}/snapshots", headers=self.hub, json={"note": note}
        )
        self.assertEqual(response.status_code, 200, response.text)
        if reopen and self.status()["managed"]["state"] == "ready":
            self.reopen()
        return response.json()


class SnapshotTests(DataTestCase):
    def test_a_snapshot_is_taken_with_the_service_stopped_and_verifies(self):
        self.write_note("first")
        self.attach("one.txt", b"attachment one")
        # A write-ahead log with uncommitted pages in it is exactly the state a
        # naive file copy gets wrong.
        self.assertTrue((self.data_dir() / "fixture.db-wal").exists())

        snapshot = self.snapshot("before anything")
        self.assertGreater(snapshot["files"], 0)
        self.assertGreater(snapshot["size"], 0)
        payload = self.service.store.paths(APP_ID).snapshot(snapshot["id"]) / "data"
        self.assertTrue((payload / "attachments/one.txt").is_file())

        # The copy is a database that opens and contains the note, on its own.
        db = sqlite3.connect(payload / "fixture.db")
        try:
            rows = [row[0] for row in db.execute("SELECT body FROM notes ORDER BY id")]
        finally:
            db.close()
        self.assertEqual(rows, ["first"])

    def test_taking_a_backup_puts_the_app_back_the_way_it_found_it(self):
        self.write_note("first")
        self.assertEqual(self.status()["managed"]["state"], "ready")
        self.snapshot()
        self.assertEqual(self.status()["managed"]["state"], "ready")
        self.reopen()
        self.assertEqual(self.notes(), ["first"])

    def test_restoring_brings_back_the_database_and_the_attachments(self):
        self.write_note("keep")
        self.attach("keep.txt", b"keep me")
        snapshot = self.snapshot("good state")

        self.write_note("added later")
        self.attach("later.txt", b"added later")
        self.assertEqual(self.notes(), ["keep", "added later"])

        response = self.client.post(
            f"/api/managed/{APP_ID}/snapshots/{snapshot['id']}/restore", headers=self.hub
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNotNone(response.json()["safety"])

        self.reopen()
        self.assertEqual(self.notes(), ["keep"])
        self.assertEqual(self.app_get("/api/files/keep.txt").content, b"keep me")
        self.assertEqual(self.app_get("/api/files/later.txt").status_code, 404)

    def test_a_restore_keeps_a_safety_copy_of_what_it_replaced(self):
        self.write_note("original")
        first = self.snapshot("original")
        self.write_note("second")
        second = self.snapshot("with second")

        self.client.post(
            f"/api/managed/{APP_ID}/snapshots/{first['id']}/restore", headers=self.hub
        )
        self.reopen()
        self.assertEqual(self.notes(), ["original"])

        # And the state before the restore can be put back in turn.
        self.client.post(
            f"/api/managed/{APP_ID}/snapshots/{second['id']}/restore", headers=self.hub
        )
        self.reopen()
        self.assertEqual(self.notes(), ["original", "second"])

    def test_a_damaged_backup_is_refused_and_changes_nothing(self):
        self.write_note("live")
        snapshot = self.snapshot()
        payload = self.service.store.paths(APP_ID).snapshot(snapshot["id"]) / "data"
        (payload / "tampered.txt").write_text("edited after the fact", encoding="utf-8")

        response = self.client.post(
            f"/api/managed/{APP_ID}/snapshots/{snapshot['id']}/restore", headers=self.hub
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("Nothing changed", response.json()["detail"])
        self.reopen()
        self.assertEqual(self.notes(), ["live"])


class UpdateTests(DataTestCase):
    def update(self, version="1.1.0", **options):
        review = self.review(folder=self.build_package(version=version, **options))
        self.assertEqual(review["operation"], "update")
        self.assertEqual(review["installedVersion"], "1.0.0")
        self.assertTrue(review["dataPreserved"])
        return self.install(review)

    def test_an_update_keeps_the_data_and_takes_a_checkpoint_first(self):
        self.write_note("written by 1.0.0")
        self.attach("kept.txt", b"kept across the update")

        result = self.update()
        self.assertEqual(result["operation"], "update")
        self.assertEqual(result["version"], "1.1.0")
        self.assertIsNotNone(result["snapshot"])
        self.assertTrue(result["restarted"])

        self.reopen()
        self.assertEqual(self.notes(), ["written by 1.0.0"])
        self.assertEqual(self.app_get("/api/files/kept.txt").content, b"kept across the update")
        self.assertIn("version 1.1.0", self.app_get("/").text)

    def test_an_update_gives_the_app_a_new_generation_and_keeps_its_identity(self):
        before = self.status()["managed"]
        self.update()
        after = self.status()["managed"]
        self.assertEqual(after["installationId"], before["installationId"])
        self.assertEqual(after["generation"], before["generation"] + 1)
        self.assertNotEqual(after["releaseId"], before["releaseId"])

    def test_an_update_that_cannot_start_is_reported_rather_than_hidden(self):
        self.write_note("before the bad update")
        result = self.update(
            version="1.2.0", environment={"FIXTURE_REFUSE_START": "1"}, start_timeout=5
        )
        self.assertFalse(result["restarted"])
        self.assertIn("refusing to start", result["startError"])
        state = self.status()["managed"]
        self.assertNotEqual(state["state"], "ready")
        # The data is untouched, and so is the checkpoint taken before it.
        snapshots = self.client.get(
            f"/api/managed/{APP_ID}", headers=self.hub
        ).json()["managed"]["snapshots"]
        self.assertTrue(any(item["kind"] == "before-update" for item in snapshots))

    def test_going_back_restores_the_matching_code_and_data_together(self):
        """Old code against a database newer code migrated is the failure here."""
        self.write_note("written by 1.0.0")
        first_release = self.status()["managed"]["releaseId"]
        self.update()
        self.reopen()
        self.write_note("written by 1.1.0")
        self.assertEqual(self.notes(), ["written by 1.0.0", "written by 1.1.0"])

        response = self.client.post(
            f"/api/managed/{APP_ID}/releases/{first_release}/rollback", headers=self.hub
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNotNone(response.json()["safety"])

        self.assertEqual(self.status()["version"], "1.0.0")
        self.reopen()
        self.assertIn("version 1.0.0", self.app_get("/").text)
        # The note written by the newer version is gone with it, which is what
        # the dashboard warns about before asking.
        self.assertEqual(self.notes(), ["written by 1.0.0"])

    def test_going_back_works_without_the_network(self):
        """Recovery uses the retained local release, never a fresh download."""
        self.write_note("one")
        first_release = self.status()["managed"]["releaseId"]
        self.update()

        def refuse(*args, **kwargs):
            raise AssertionError("recovery tried to fetch code from the network")

        from vela.managed import packages

        original = packages.download_artifact
        packages.download_artifact = refuse
        self.addCleanup(lambda: setattr(packages, "download_artifact", original))
        response = self.client.post(
            f"/api/managed/{APP_ID}/releases/{first_release}/rollback", headers=self.hub
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_a_retained_release_that_was_damaged_is_refused(self):
        self.write_note("one")
        first_release = self.status()["managed"]["releaseId"]
        self.update()
        code = self.service.store.paths(APP_ID).release(first_release) / "code"
        (code / "extra.txt").write_text("edited", encoding="utf-8")

        response = self.client.post(
            f"/api/managed/{APP_ID}/releases/{first_release}/rollback", headers=self.hub
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("integrity", response.json()["detail"])
        self.assertEqual(self.status()["version"], "1.1.0")


class InterruptionTests(DataTestCase):
    """What the next start finds, when the last one did not finish."""

    def rebuild(self):
        """Start a second server on the same data, the way a restart does."""
        from fastapi.testclient import TestClient

        from vela.api import create_app

        app = create_app(self.config)
        client = TestClient(app)
        client.__enter__()
        self.addCleanup(lambda: client.__exit__(None, None, None))
        token = client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        return app, client, {"Authorization": "Bearer " + token}

    def test_an_unfinished_install_leaves_nothing_behind(self):
        """A release with files and no committed row is undone on the way up."""
        paths = self.service.store.paths(APP_ID)
        orphan = paths.release("11111111-1111-1111-1111-111111111111")
        (orphan / "code").mkdir(parents=True)
        (orphan / "code/leftover.txt").write_text("half an install", encoding="utf-8")
        self.service.store.write_journal(APP_ID, {
            "kind": "install", "release_id": "11111111-1111-1111-1111-111111111111",
            "stage": "staging", "previous_release": None, "snapshot_id": None,
        })
        self.client.__exit__(None, None, None)

        app, client, hub = self.rebuild()
        self.assertFalse(orphan.exists(), "the unfinished release was kept")
        self.assertFalse(paths.journal.exists())
        state = client.get(f"/api/managed/{APP_ID}/status", headers=hub).json()
        self.assertEqual(state["version"], "1.0.0")

    def test_a_restore_interrupted_before_the_swap_keeps_the_live_data(self):
        self.write_note("live data")
        snapshot = self.snapshot(reopen=False)
        self.stop()
        data = self.data_dir()
        staged = data.with_name(data.name + ".restoring")
        shutil.copytree(
            self.service.store.paths(APP_ID).snapshot(snapshot["id"]) / "data", staged
        )
        self.service.store.write_journal(APP_ID, {
            "kind": "restore", "snapshot_id": snapshot["id"],
            "release_id": self.service.store.require(APP_ID)["release_id"],
            "safety_id": snapshot["id"], "stage": "replacing",
            "previous_release": self.service.store.require(APP_ID)["release_id"],
        })
        self.client.__exit__(None, None, None)

        app, client, hub = self.rebuild()
        self.assertFalse(staged.exists(), "the staged copy was left lying around")
        self.assertTrue((data / "fixture.db").is_file())
        state = client.get(f"/api/managed/{APP_ID}/status", headers=hub).json()["managed"]
        self.assertEqual(state["desired"], "stopped")

    def test_a_restore_interrupted_between_the_two_renames_finishes(self):
        """The old directory has moved aside and the new one has not moved in."""
        self.write_note("live data")
        snapshot = self.snapshot(reopen=False)
        self.stop()
        data = self.data_dir()
        staged = data.with_name(data.name + ".restoring")
        displaced = data.with_name(data.name + ".replaced")
        shutil.copytree(
            self.service.store.paths(APP_ID).snapshot(snapshot["id"]) / "data", staged
        )
        data.rename(displaced)
        self.service.store.write_journal(APP_ID, {
            "kind": "restore", "snapshot_id": snapshot["id"],
            "release_id": self.service.store.require(APP_ID)["release_id"],
            "safety_id": snapshot["id"], "stage": "replacing",
            "previous_release": self.service.store.require(APP_ID)["release_id"],
        })
        self.client.__exit__(None, None, None)

        app, client, hub = self.rebuild()
        self.assertTrue((data / "fixture.db").is_file())
        self.assertFalse(staged.exists())
        self.assertFalse(displaced.exists())

    def test_a_restore_that_lost_both_directories_is_recovered_from_the_safety_copy(self):
        self.write_note("live data")
        snapshot = self.snapshot(reopen=False)
        data = self.data_dir()
        self.stop()
        shutil.rmtree(data)
        self.service.store.write_journal(APP_ID, {
            "kind": "restore", "snapshot_id": snapshot["id"],
            "release_id": self.service.store.require(APP_ID)["release_id"],
            "safety_id": snapshot["id"], "stage": "replacing",
            "previous_release": self.service.store.require(APP_ID)["release_id"],
        })
        self.client.__exit__(None, None, None)

        app, client, hub = self.rebuild()
        self.assertTrue((data / "fixture.db").is_file())
        state = client.get(f"/api/managed/{APP_ID}/status", headers=hub).json()["managed"]
        self.assertEqual(state["desired"], "stopped")
        self.assertEqual(state["state"], "stopped")

    def test_an_operation_that_died_holding_the_lock_does_not_hold_it_for_ever(self):
        self.service.store.begin_operation(APP_ID, "update", "stuck")
        refused = self.client.post(f"/api/managed/{APP_ID}/start", headers=self.hub)
        self.assertEqual(refused.status_code, 423, refused.text)
        self.client.__exit__(None, None, None)

        app, client, hub = self.rebuild()
        self.assertIsNone(self.service.store.operation(APP_ID))
        allowed = client.post(f"/api/managed/{APP_ID}/start", headers=hub)
        self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_two_operations_at_once_are_refused_rather_than_raced(self):
        self.service.store.begin_operation(APP_ID, "backup", "in progress")
        self.addCleanup(lambda: self.service.store.end_operation(APP_ID))
        response = self.client.post(
            f"/api/managed/{APP_ID}/snapshots", headers=self.hub, json={"note": "second"}
        )
        self.assertEqual(response.status_code, 423, response.text)
        self.assertIn("backup is already running", response.json()["detail"])


class HubBackupTests(DataTestCase):
    """A Vela backup records that a managed app exists; its data is its own."""

    def test_a_hub_backup_carries_the_managed_installation_record(self):
        self.write_note("something")
        response = self.client.post("/api/backups", headers=self.hub)
        self.assertEqual(response.status_code, 201, response.text)
        name = response.json()["name"]
        copied = self.config.data_dir / "backups" / name / "managed.sqlite"
        self.assertTrue(copied.is_file(), "the managed record was not backed up")

        db = sqlite3.connect(copied)
        try:
            rows = db.execute("SELECT app_id, version FROM installations").fetchall()
        finally:
            db.close()
        self.assertEqual(rows, [(APP_ID, "1.0.0")])

    def test_a_hub_backup_verifies_with_the_managed_record_in_it(self):
        name = self.client.post("/api/backups", headers=self.hub).json()["name"]
        verified = self.client.post(f"/api/backups/{name}/verify", headers=self.hub)
        self.assertEqual(verified.status_code, 200, verified.text)
        self.assertTrue(verified.json()["ok"], verified.text)

    def test_a_hub_backup_does_not_copy_the_apps_own_data(self):
        """App data is backed up per app, on purpose: a hub backup that dragged
        a hundred megabytes of somebody's notes along would stop being taken."""
        self.write_note("something")
        self.attach("big.bin", b"0" * 100_000)
        name = self.client.post("/api/backups", headers=self.hub).json()["name"]
        copied = self.config.data_dir / "backups" / name
        self.assertEqual(list(copied.rglob("fixture.db")), [])
        self.assertEqual(list(copied.rglob("big.bin")), [])


if __name__ == "__main__":
    unittest.main()
