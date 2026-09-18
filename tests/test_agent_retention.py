"""What an agent desktop keeps, what it throws away, and what leaves this computer.

The questions here are the ones that only matter after months of use, which is
exactly why they are worth a test now: nobody notices a cleanup that has quietly
stopped working, or a support bundle that has quietly started carrying task
text, until it is too late to have wanted either.

Four claims:

**Turning history off reaches everywhere.** Not only the task table. The events
under it, and whatever the sweep is responsible for, go with it — a task's
wording removed from a database and left in a support bundle is a task that was
not forgotten.

**A support bundle is a file somebody sends to somebody else.** Counts, versions
and health. Never an instruction, never a result, never a site, never a file.

**A backup is not a copy of a browser session.** It covers settings, desktops and
app data; a kept sign-in and a half-finished transfer are not in it, and that is
by construction rather than by a filter somebody has to remember to update.

**A restore stops the agents first.** Nothing is dispatching into app data that
is being replaced underneath it, and nothing authorized against the old data can
be used against the new.
"""

import asyncio
import copy
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

import test_app_contract as base
from fastapi.testclient import TestClient
from vela.agent_runs.retention import ARTIFACT_SECONDS, CLASSES, FRAME_SECONDS, RECEIPT_SECONDS
from vela.api import create_app
from vela.backups import BACKED_UP_FILES
from vela.config import Config

ROOT = base.ROOT


class RetentionClassTests(unittest.TestCase):
    """The rules themselves, before anything has accumulated under them."""

    def test_every_class_says_how_long_and_whether_it_is_backed_up(self):
        for entry in CLASSES:
            self.assertTrue(entry["id"], entry)
            self.assertTrue(entry["name"], entry)
            self.assertTrue(entry["note"].endswith("."), entry["id"])
            self.assertIn("backedUp", entry, entry["id"])

    def test_a_picture_of_a_window_is_the_shortest_lived_thing_here(self):
        # It is a picture of somebody's screen. Everything else outlives it.
        self.assertLess(FRAME_SECONDS, RECEIPT_SECONDS)
        self.assertLess(RECEIPT_SECONDS, ARTIFACT_SECONDS)

    def test_only_task_history_is_ever_backed_up(self):
        backed = {entry["id"] for entry in CLASSES if entry["backedUp"]}
        self.assertEqual(backed, {"history"})


class BackupScopeTests(unittest.TestCase):
    """What a backup covers is an allowlist, which is what keeps two things out."""

    def test_a_backup_does_not_carry_a_browser_session_or_a_staged_file(self):
        # By construction: the list names what goes in, so anything nobody put
        # on it is out. A filter would be a list somebody has to remember to
        # update every time a new file appears in the data directory.
        for excluded in ("agent-artifacts.sqlite", "desktop-sessions", "agent-frames"):
            self.assertNotIn(excluded, BACKED_UP_FILES, excluded)
        self.assertIn("desktops.sqlite", BACKED_UP_FILES, "the desktops themselves are covered")
        self.assertIn("app-data.sqlite", BACKED_UP_FILES, "and so is what the apps saved")


class RetentionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-retention-")
        self.root = Path(self.temp.name)
        apps = self.root / "catalog"
        shutil.copytree(ROOT / "tests/fixtures/chat-fixture", apps / "chat-fixture")
        (apps / "chat-fixture" / "app.json").write_text(json.dumps(copy.deepcopy(base.FIXTURE)))
        self.config = Config(self.root / "data", apps, ROOT / "web/dist")
        self.config.ensure_dirs()
        self.app = create_app(self.config)
        self.client = TestClient(self.app)
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}
        self.desktop = self.client.get("/api/desktops", headers=self.hub).json()["defaultId"]
        self.runs = self.app.state.agent_runs
        self.desktops = self.app.state.desktops

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_the_policy_is_readable_and_says_what_is_kept(self):
        state = self.client.get("/api/desktops/retention", headers=self.hub).json()
        self.assertEqual([entry["id"] for entry in state["classes"]],
                         ["frames", "receipts", "artifacts", "history"])
        self.assertTrue(state["keepingHistory"])
        self.assertIsNotNone(state["fileBytesLimit"])

    def test_the_sweep_can_be_run_and_reports_what_it_removed(self):
        result = self.client.post("/api/desktops/retention/sweep", headers=self.hub).json()
        self.assertEqual(result["problems"], [])
        for key in ("frames", "artifacts", "history"):
            self.assertIn(key, result["removed"])

    def test_a_staged_file_is_swept_once_it_has_expired(self):
        staged = self.client.post(
            f"/api/desktops/{self.desktop}/files",
            headers={**self.hub, "Content-Type": "text/plain", "X-Vela-Filename": "notes.txt"},
            content=b"something",
        ).json()
        store = self.desktops.artifacts
        with store.connection() as db:
            db.execute("UPDATE agent_artifacts SET expires_at=0 WHERE id=?", (staged["id"],))
        result = self.client.post("/api/desktops/retention/sweep", headers=self.hub).json()
        self.assertGreaterEqual(result["removed"]["artifacts"], 1)
        gone = self.client.get(
            f"/api/desktops/{self.desktop}/files/{staged['id']}", headers=self.hub
        )
        self.assertEqual(gone.status_code, 404)

    def test_turning_history_off_removes_the_tasks_and_their_activity(self):
        self.desktops.store.set_kind(self.desktop, "agent")
        run = self.runs.store.submit(self.desktop, "something private")
        self.runs.store.append(self.desktop, "task.queued", {"runId": run["id"]})
        self.assertTrue(self.runs.store.list(self.desktop))

        saved = self.client.patch(
            "/api/settings", headers=self.hub, json={"chat_history": False}
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        # The sweep is what makes that true of what was already written, and it
        # is the same sweep that runs on the way up.
        self.runs.retention.sweep()
        self.assertEqual(self.runs.store.list(self.desktop), [])
        self.assertEqual(self.runs.store.events(self.desktop, after=0), [])

    def test_a_support_bundle_carries_health_and_never_a_task(self):
        self.desktops.store.set_kind(self.desktop, "agent")
        self.runs.store.submit(self.desktop, "read the secret document and tell me about it")
        current = self.client.get(f"/api/desktops/{self.desktop}/policy", headers=self.hub).json()
        self.client.put(
            f"/api/desktops/{self.desktop}/policy",
            headers=self.hub,
            json={
                "revision": current["revision"],
                "apps": [],
                "sites": [{"origin": "https://private.example", "effects": "ask"}],
            },
        )
        self.client.post(
            f"/api/desktops/{self.desktop}/files",
            headers={**self.hub, "Content-Type": "text/plain", "X-Vela-Filename": "payroll.csv"},
            content=b"salary data",
        )

        made = self.client.post("/api/support-bundle", headers=self.hub)
        self.assertEqual(made.status_code, 201, made.text)
        path = self.config.data_dir / "support" / made.json()["name"]
        with zipfile.ZipFile(path) as bundle:
            everything = "\n".join(
                bundle.read(name).decode("utf-8", "replace") for name in bundle.namelist()
            )
            section = json.loads(bundle.read("agent-desktops.json"))

        # What is there: the health somebody diagnosing a failure needs.
        self.assertTrue(section["configured"])
        self.assertIn("runtime", section)
        self.assertEqual(section["desktops"]["agent"], 1)

        # What is not: any of it.
        for secret in ("secret document", "private.example", "payroll.csv", "salary data"):
            self.assertNotIn(secret, everything, secret)

    def test_restoring_a_backup_stops_the_agents_first(self):
        self.desktops.store.set_kind(self.desktop, "agent")
        self.desktops.grants.issue(
            desktop_id=self.desktop,
            effect="write",
            app_id="chat-fixture",
            installation_id="inst-1",
            contract="c",
            seconds=3600,
        )
        self.desktops._runtime_sessions[self.desktop] = "rs-1"
        self.desktops._uncertain[self.desktop] = {"d" * 64: {"url": "x", "method": "POST", "at": 0}}
        run = self.runs.store.submit(self.desktop, "in flight")
        self.runs.store.update(run["id"], state="running")

        asyncio.run(self.runs.quiesce())

        # Every kind of authority this desktop held is gone, and nothing that
        # was in flight is still claiming to be.
        self.assertEqual(self.desktops.grants.list(self.desktop), [])
        self.assertEqual(self.desktops._runtime_sessions, {})
        self.assertEqual(self.desktops._uncertain, {})
        self.assertEqual(self.runs.prepare()["interrupted"], 1)
        self.assertEqual(self.runs.store.get(run["id"])["state"], "interrupted")

    def test_health_checks_report_the_agent_runtime_without_a_stack_trace(self):
        report = self.client.post("/api/doctor/run", headers=self.hub).json()
        keys = {check["key"]: check for check in report["checks"]}
        for key in ("agent-runtime", "agent-files", "agent-sessions"):
            self.assertIn(key, keys, key)
            self.assertTrue(keys[key]["detail"], key)
            self.assertNotIn("Traceback", keys[key]["detail"], key)
            self.assertIn(keys[key]["status"], ("ok", "warn", "fail", "skipped"), key)

    def test_cleaning_up_is_something_a_person_can_ask_for_and_watch(self):
        repaired = self.client.post("/api/doctor/agent-files/repair", headers=self.hub)
        self.assertEqual(repaired.status_code, 200, repaired.text)
        self.assertIn("Removed", json.dumps(repaired.json()))


if __name__ == "__main__":
    unittest.main()
