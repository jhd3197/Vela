"""The driven-run API: a controller drives one tool call at a time.

Everything here runs a Vela in controller mode on a disposable data directory,
with the tool surface replaced by a recording double the way
`test_agent_runs.py` replaces it for the model loop — these tests are about
the driver and its protocol, and `test_agent_tools.py` is where a real browser
proves the tools underneath it. One test keeps the real tool surface on
purpose: a policy refusal has to be the tool's own refusal, word for word.

No real browsers, no network, no real installation: the controller token and
the catalog are disposable fixtures, like the controller-mode tests this
follows.
"""

import atexit
import asyncio
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# api.py constructs its default ASGI app on import. Keep that off the user's data.
_bootstrap = tempfile.TemporaryDirectory(prefix="vela-driven-test-")
atexit.register(_bootstrap.cleanup)
os.environ["VELA_DATA_DIR"] = _bootstrap.name

import test_agent_runs as runs_base
from fastapi.testclient import TestClient
from scripts.fixture_apps import APPS as FIXTURE_APPS
from vela.agent_runs.approvals import ApprovalPending
from vela.agent_runs.store import TERMINAL, RunStore
from vela.agent_runs.tools import TOOL_NAMES, ToolError
from vela.api import create_app
from vela.config import load_config

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "controller-token-" + "x" * 40


def write_token_file(root):
    path = Path(root) / "controller-token"
    path.write_text(TOKEN + "\n", encoding="utf-8")
    return path


def wait_for(condition, timeout=15, interval=0.05):
    """Poll until a condition holds, for things settled by a background task."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("the condition never held")


class AskThenAnswer(runs_base.RecordingTools):
    """A tool surface that raises ApprovalPending once, then answers."""

    def __init__(self, record, answer=None):
        super().__init__()
        self.record = record
        self.answer = answer or {"result": {"status": "succeeded"}}
        self.spent = False

    async def call(self, desktop_id, name, arguments, *, run_id, actor_id=None):
        self.calls.append((name, arguments))
        if not self.spent:
            self.spent = True
            raise ApprovalPending(self.record)
        return self.answer


class ControllerDrivenTests(unittest.TestCase):
    """The driver protocol, end to end over HTTP in controller mode."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="vela-driven-")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.catalog = root / "catalog"
        shutil.copytree(FIXTURE_APPS / "notes", self.catalog / "notes")
        self.token_file = write_token_file(root)
        self.environment = patch.dict(os.environ, {
            "VELA_DATA_DIR": str(root / "data"),
            "VELA_APPS_DIR": str(self.catalog),
            "VELA_CONTROLLER_MODE": "1",
            "VELA_CONTROLLER_TOKEN_FILE": str(self.token_file),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.app = create_app(load_config())
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": f"Bearer {TOKEN}"}
        self.desktops = self.app.state.desktops
        self.runs = self.app.state.agent_runs
        self.driver = self.runs.driver
        self.assertEqual(
            self.client.post("/api/apps/notes/install", headers=self.headers).status_code, 200
        )
        self.desktop = self.client.get("/api/desktops", headers=self.headers).json()["defaultId"]
        self.desktops.store.set_kind(self.desktop, "agent")
        self.desktops.agent_runtime_for = lambda desktop_id: None
        self.runs.supervisor.tools = runs_base.RecordingTools()

    # ---- plumbing

    def create(self, title="Sort the inbox", instruction=None, **extra):
        body = {"title": title, **extra}
        if instruction is not None:
            body["instruction"] = instruction
        return self.client.post(
            f"/api/desktops/{self.desktop}/driven-runs", headers=self.headers, json=body
        )

    def step(self, run_id, tool, arguments=None, client_step_id="step-1"):
        return self.client.post(
            f"/api/desktops/{self.desktop}/driven-runs/{run_id}/steps",
            headers=self.headers,
            json={"tool": tool, "arguments": arguments or {}, "clientStepId": client_step_id},
        )

    def read_step(self, run_id, client_step_id):
        return self.client.get(
            f"/api/desktops/{self.desktop}/driven-runs/{run_id}/steps/{client_step_id}",
            headers=self.headers,
        )

    def settled_step(self, run_id, client_step_id, timeout=15):
        """Poll the read-back route until the step has its settled answer."""
        response = wait_for(
            lambda: (
                (lambda current: current if current.json()["status"] == "settled" else None)(
                    self.read_step(run_id, client_step_id)
                )
            ),
            timeout=timeout,
        )
        return response.json()

    def task(self, run_id):
        return self.client.get(
            f"/api/desktops/{self.desktop}/tasks/{run_id}", headers=self.headers
        ).json()

    def events(self):
        return [
            event["kind"]
            for event in self.client.get(
                f"/api/desktops/{self.desktop}/events", headers=self.headers
            ).json()["events"]
        ]

    def finish(self, run_id, outcome="succeeded", summary="Done."):
        return self.client.post(
            f"/api/desktops/{self.desktop}/driven-runs/{run_id}/finish",
            headers=self.headers,
            json={"outcome": outcome, "summary": summary},
        )

    # ---- creating

    def test_creating_starts_a_run_for_the_record(self):
        created = self.create(instruction="Answer the support mail")
        self.assertEqual(created.status_code, 201, created.text)
        run = created.json()
        self.assertEqual(run["kind"], "driven")
        self.assertEqual(run["state"], "running")
        self.assertEqual(run["title"], "Sort the inbox")
        self.assertEqual(run["instruction"], "Answer the support mail")
        self.assertIn("budget", run)
        # The events tell the same story a model run's would.
        kinds = self.events()
        self.assertIn("task.starting", kinds)
        self.assertIn("task.running", kinds)
        running = self.client.get(
            f"/api/desktops/{self.desktop}/events", headers=self.headers
        ).json()["events"]
        flag = next(event for event in running if event["kind"] == "task.running")
        self.assertTrue(flag["payload"]["driver"])

    def test_the_same_request_id_twice_is_one_run_even_after_it_ends(self):
        first = self.create(clientRequestId="run-9").json()
        second = self.create(clientRequestId="run-9")
        self.assertEqual(second.status_code, 201)
        self.assertEqual(second.json()["id"], first["id"])
        self.assertEqual(self.finish(first["id"]).status_code, 200)
        retried = self.create(clientRequestId="run-9")
        self.assertEqual(retried.status_code, 201)
        self.assertEqual(retried.json()["id"], first["id"])
        self.assertEqual(retried.json()["state"], "succeeded")

    def test_one_active_run_per_desktop(self):
        first = self.create().json()
        refused = self.create(title="Something else")
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertIn("already carrying", refused.json()["detail"])
        self.assertEqual(self.task(first["id"])["state"], "running")
        # And a model task submitted by anyone waits for it, then starts.
        self.runs.supervisor.model = runs_base.ScriptedModel(
            [("task.finish", {"summary": "Read it"})]
        )
        self.runs.supervisor.tools = runs_base.RecordingTools(
            {"task.finish": {"proposed": True, "summary": "Read it", "actions": []}}
        )
        queued = self.client.post(
            f"/api/desktops/{self.desktop}/tasks",
            headers=self.headers,
            json={"instruction": "after it"},
        )
        self.assertEqual(queued.status_code, 202, queued.text)
        self.assertEqual(queued.json()["state"], "queued")
        self.assertEqual(self.finish(first["id"]).status_code, 200)
        wait_for(lambda: self.task(queued.json()["id"])["state"] in TERMINAL)
        self.assertEqual(self.task(queued.json()["id"])["state"], "succeeded")

    def test_the_shared_run_limit_is_honest(self):
        # Two driven runs hold the two shared slots the model loop also draws
        # from; a third desktop is told the truth rather than queueing.
        second = self.client.post("/api/desktops", headers=self.headers, json={"name": "Two"}).json()["id"]
        third = self.client.post("/api/desktops", headers=self.headers, json={"name": "Three"}).json()["id"]
        for desktop_id in (second, third):
            self.desktops.store.set_kind(desktop_id, "agent")
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(
            self.client.post(
                f"/api/desktops/{second}/driven-runs", headers=self.headers,
                json={"title": "on two"},
            ).status_code,
            201,
        )
        refused = self.client.post(
            f"/api/desktops/{third}/driven-runs", headers=self.headers, json={"title": "on three"}
        )
        self.assertEqual(refused.status_code, 429, refused.text)
        self.assertIn("busy", refused.json()["detail"])
        # A slot frees when a run ends, honestly again.
        runs_here = self.client.get(
            f"/api/desktops/{second}/tasks", headers=self.headers
        ).json()["runs"]
        ended = self.client.post(
            f"/api/desktops/{second}/driven-runs/{runs_here[0]['id']}/finish",
            headers=self.headers,
            json={"outcome": "succeeded", "summary": "Done."},
        )
        self.assertEqual(ended.status_code, 200, ended.text)
        self.assertEqual(
            self.client.post(
                f"/api/desktops/{third}/driven-runs", headers=self.headers,
                json={"title": "on three"},
            ).status_code,
            201,
        )

    def test_creating_needs_an_agent_desktop(self):
        other = self.client.post("/api/desktops", headers=self.headers, json={}).json()["id"]
        response = self.client.post(
            f"/api/desktops/{other}/driven-runs",
            headers=self.headers,
            json={"title": "no agent here"},
        )
        self.assertEqual(response.status_code, 409, response.text)

    # ---- the tool catalogue

    def test_the_catalogue_is_the_declared_surface_as_json_schemas(self):
        response = self.client.get(
            f"/api/desktops/{self.desktop}/driven-runs/tools", headers=self.headers
        )
        self.assertEqual(response.status_code, 200, response.text)
        offered = {entry["function"]["name"]: entry["function"] for entry in response.json()["tools"]}
        self.assertEqual(set(offered), set(TOOL_NAMES))
        for name, function in offered.items():
            self.assertTrue(function["description"], name)
            self.assertEqual(function["parameters"]["type"], "object", name)
            self.assertTrue(function["parameters"]["properties"], name)

    # ---- stepping

    def test_a_step_executes_and_the_events_mark_it(self):
        run = self.create().json()
        answered = self.step(run["id"], "desktop.observe", {"viewId": "v1"}, "look-1")
        self.assertEqual(answered.status_code, 200, answered.text)
        body = answered.json()
        self.assertEqual(body["result"], {"ok": True})
        self.assertIn("budget", body)
        tools = self.runs.supervisor.tools
        self.assertEqual(tools.calls, [("desktop.observe", {"viewId": "v1"})])
        kinds = self.events()
        self.assertEqual(kinds.count("step.started"), 1)
        self.assertEqual(kinds.count("step.finished"), 1)

    def test_a_retried_step_is_the_same_step_not_a_second_effect(self):
        run = self.create().json()
        first = self.step(run["id"], "app.invoke_action", {"appId": "notes"}, "save-1")
        second = self.step(run["id"], "app.invoke_action", {"appId": "notes"}, "save-1")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json(), first.json())
        self.assertEqual(
            len(self.runs.supervisor.tools.calls), 1, "a retried POST repeats no effect"
        )
        # The same id naming a different call is a conflict, not a retry.
        conflict = self.step(run["id"], "desktop.observe", {}, "save-1")
        self.assertEqual(conflict.status_code, 409, conflict.text)

    def test_a_refused_call_comes_back_as_a_refusal_a_controller_can_act_on(self):
        self.runs.supervisor.tools = runs_base.RecordingTools(
            {"desktop.click": ToolError(
                "stale_observation", "That control has moved. Observe again.", retryable=True
            )}
        )
        run = self.create().json()
        answered = self.step(run["id"], "desktop.click", {"ref": "f0:e1"}, "click-1")
        self.assertEqual(answered.status_code, 200, answered.text)
        refusal = answered.json()["refusal"]
        self.assertEqual(refusal["code"], "stale_observation")
        self.assertIn("Observe again", refusal["detail"])
        self.assertTrue(refusal["retryable"])
        finished = [
            event
            for event in self.client.get(
                f"/api/desktops/{self.desktop}/events", headers=self.headers
            ).json()["events"]
            if event["kind"] == "step.finished"
        ]
        self.assertEqual(finished[0]["payload"]["ok"], False)

    def test_an_undeclared_tool_is_refused_before_anything_is_touched(self):
        # The real surface stays on: the refusal is the tool boundary's own.
        self.runs.supervisor.tools = self.desktops.tools
        run = self.create().json()
        answered = self.step(run["id"], "desktop.run_shell", {"command": "id"}, "nope-1")
        self.assertEqual(answered.status_code, 200, answered.text)
        refusal = answered.json()["refusal"]
        self.assertEqual(refusal["code"], "unknown_tool")

    def test_a_policy_refusal_is_the_tool_s_own_refusal_word_for_word(self):
        # The real surface stays on for this one: the check being made is that
        # the driver adds nothing and takes nothing away.
        self.runs.supervisor.tools = self.desktops.tools
        run = self.create().json()
        expected = None
        try:
            asyncio.run(
                self.desktops.tools.call(self.desktop, "desktop.open_app", {"appId": "notes"},
                                         run_id=run["id"])
            )
        except ToolError as exc:
            expected = exc
        self.assertIsNotNone(expected)
        answered = self.step(run["id"], "desktop.open_app", {"appId": "notes"}, "open-1")
        refusal = answered.json()["refusal"]
        self.assertEqual(refusal["code"], expected.code)
        self.assertEqual(refusal["detail"], expected.detail)
        self.assertEqual(self.task(run["id"])["state"], "running")

    def test_a_new_step_while_one_waits_for_approval_is_refused(self):
        record = self._ask("write-1")
        tools = AskThenAnswer(record)
        self.runs.supervisor.tools = tools
        run = self.create().json()
        answered = self.step(run["id"], "app.invoke_action", {"appId": "notes"}, "write-1")
        self.assertIn("approval", answered.json())
        self.assertEqual(self.task(run["id"])["state"], "waiting_approval")
        refused = self.step(run["id"], "desktop.observe", {}, "look-2")
        self.assertEqual(refused.status_code, 409, refused.text)

    # ---- approvals

    def _ask(self, marker):
        """Open a real pending approval and hand its record to a fake tool surface."""
        return self.desktops.approvals.request(
            desktop_id=self.desktop,
            run_id=None,
            view_id=None,
            effect="action",
            app_id="notes",
            app_name="Notes",
            installation_id="inst-1",
            contract="a" * 64,
            request_digest="b" * 64,
            scope={"app": "notes", "action": "save"},
            summary={"effect": "action", "headline": f"Notes wants to run “save” ({marker}).",
                     "detail": ["with no values"]},
        )

    def test_an_approved_change_settles_the_same_step_with_its_result(self):
        record = self._ask("approved")
        tools = AskThenAnswer(record)
        self.runs.supervisor.tools = tools
        run = self.create().json()
        asked = self.step(run["id"], "app.invoke_action", {"appId": "notes"}, "write-1")
        self.assertEqual(asked.status_code, 200, asked.text)
        approval = asked.json()["approval"]
        self.assertEqual(approval["requestId"], record["requestId"])
        self.assertIn("headline", approval["summary"])
        # While it waits, the read-back route says so.
        pending = self.read_step(run["id"], "write-1").json()
        self.assertEqual(pending["status"], "waiting_approval")
        self.assertEqual(pending["approval"]["requestId"], record["requestId"])
        # The owner answers through the ordinary approvals route — the same
        # one a person clicking in Vela uses.
        answered = self.client.post(
            f"/api/desktops/{self.desktop}/approvals/{record['requestId']}",
            headers=self.headers,
            json={"decision": "approve"},
        )
        self.assertEqual(answered.status_code, 200, answered.text)
        settled = self.settled_step(run["id"], "write-1")
        self.assertEqual(settled["result"], {"result": {"status": "succeeded"}})
        # The same call ran again with the grant behind it — not a new call.
        self.assertEqual([name for name, _ in tools.calls], ["app.invoke_action"] * 2)
        self.assertEqual(self.task(run["id"])["state"], "running")
        kinds = self.events()
        self.assertEqual(kinds.count("approval.waiting"), 1)
        self.assertEqual(kinds.count("approval.resolved"), 1)

    def test_a_denied_change_settles_the_step_as_a_refusal(self):
        record = self._ask("denied")
        tools = AskThenAnswer(record)
        self.runs.supervisor.tools = tools
        run = self.create().json()
        self.step(run["id"], "app.invoke_action", {"appId": "notes"}, "write-1")
        denied = self.client.post(
            f"/api/desktops/{self.desktop}/approvals/{record['requestId']}",
            headers=self.headers,
            json={"decision": "deny"},
        )
        self.assertEqual(denied.status_code, 200, denied.text)
        settled = self.settled_step(run["id"], "write-1")
        refusal = settled["refusal"]
        self.assertEqual(refusal["code"], "not_approved")
        self.assertIn("did not approve", refusal["detail"])
        self.assertFalse(refusal["retryable"])
        self.assertEqual(len(tools.calls), 1, "a denied change is never dispatched")

    def test_cancelling_while_a_change_waits_ends_both(self):
        record = self._ask("cancelled")
        self.runs.supervisor.tools = AskThenAnswer(record)
        run = self.create().json()
        self.step(run["id"], "app.invoke_action", {"appId": "notes"}, "write-1")
        cancelled = self.client.post(
            f"/api/desktops/{self.desktop}/driven-runs/{run['id']}/cancel",
            headers=self.headers,
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["state"], "cancelled")
        self.assertEqual(self.task(run["id"])["state"], "cancelled")

    # ---- handing back, and a person taking over

    def test_needs_person_hands_back_and_steps_wait_for_carry_on(self):
        self.runs.supervisor.tools = runs_base.RecordingTools(
            {
                "task.needs_person": {
                    "reason": "login",
                    "detail": "Sign in and then say carry on.",
                    "viewId": None,
                    "handedBack": True,
                }
            }
        )
        run = self.create().json()
        answered = self.step(
            run["id"], "task.needs_person",
            {"reason": "login", "detail": "Sign in and then say carry on."}, "ask-1",
        )
        self.assertEqual(answered.status_code, 200, answered.text)
        need = answered.json()["needsPerson"]
        self.assertEqual(need["reason"], "login")
        self.assertIn("task.attention", self.events())
        self.assertEqual(self.task(run["id"])["state"], "paused")
        # Steps hold until somebody says carry on, through the ordinary task
        # control route.
        held = self.step(run["id"], "desktop.observe", {}, "look-1")
        self.assertEqual(held.json()["refusal"]["code"], "paused")
        resumed = self.client.post(
            f"/api/desktops/{self.desktop}/tasks/{run['id']}/control",
            headers=self.headers,
            json={"action": "resume"},
        )
        self.assertEqual(resumed.status_code, 200, resumed.text)
        worked = self.step(run["id"], "desktop.observe", {}, "look-2")
        self.assertIn("result", worked.json())

    def test_a_person_taking_over_stops_the_driver_mid_run(self):
        # Take-over asks the runtime for a fresh control generation; stand in
        # for it the way the browser-worker contract says it answers.
        async def fake_command(name, **fields):
            return {"controlEpoch": 1}

        self.desktops.runtime.command = fake_command
        run = self.create().json()
        taken = self.client.post(
            f"/api/desktops/{self.desktop}/takeover", headers=self.headers
        )
        self.assertEqual(taken.status_code, 201, taken.text)
        lease = taken.json()
        self.assertEqual(self.task(run["id"])["state"], "paused")
        refused = self.step(run["id"], "desktop.observe", {}, "look-1")
        refusal = refused.json()["refusal"]
        self.assertEqual(refusal["code"], "person_driving")
        self.assertTrue(refusal["retryable"])
        # The run is still there when the person is done; releasing is not
        # resuming, so the controller says carry on before stepping again.
        released = self.client.delete(
            f"/api/desktops/{self.desktop}/takeover/{lease['leaseId']}", headers=self.headers
        )
        self.assertEqual(released.status_code, 200)
        self.client.post(
            f"/api/desktops/{self.desktop}/tasks/{run['id']}/control",
            headers=self.headers,
            json={"action": "resume"},
        )
        worked = self.step(run["id"], "desktop.observe", {}, "look-2")
        self.assertIn("result", worked.json())

    # ---- finishing and cancelling

    def test_finishing_succeeded_records_the_result(self):
        run = self.create().json()
        finished = self.finish(run["id"], summary="Sorted it into three folders.")
        self.assertEqual(finished.status_code, 200, finished.text)
        done = finished.json()
        self.assertEqual(done["state"], "succeeded")
        self.assertEqual(done["result"]["summary"], "Sorted it into three folders.")
        self.assertEqual(done["outcome"], "committed")
        self.assertIn("task.succeeded", self.events())
        after = self.step(run["id"], "desktop.observe", {}, "late-1")
        self.assertEqual(after.status_code, 409, after.text)

    def test_finishing_failed_records_why(self):
        run = self.create().json()
        finished = self.finish(run["id"], outcome="failed", summary="The page never loaded.")
        self.assertEqual(finished.status_code, 200, finished.text)
        self.assertEqual(finished.json()["state"], "failed")
        self.assertEqual(finished.json()["detail"], "The page never loaded.")
        self.assertIn("task.failed", self.events())

    def test_finishing_over_an_unanswered_request_is_outcome_unknown(self):
        run = self.create().json()
        self.desktops._uncertain[self.desktop] = {
            "d" * 64: {"url": "https://example.test/orders", "method": "POST", "at": 0}
        }
        finished = self.finish(run["id"], summary="Sent the order.")
        self.assertEqual(finished.json()["state"], "outcome_unknown")
        self.assertEqual(finished.json()["outcome"], "unknown")
        self.assertIn("never answered", finished.json()["detail"])

    def test_cancelling_stops_where_it_is(self):
        run = self.create().json()
        self.step(run["id"], "desktop.observe", {}, "look-1")
        cancelled = self.client.post(
            f"/api/desktops/{self.desktop}/driven-runs/{run['id']}/cancel",
            headers=self.headers,
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["state"], "cancelled")
        self.assertIn("stays done", cancelled.json()["detail"])
        self.assertIn("task.cancelled", self.events())
        again = self.finish(run["id"])
        self.assertEqual(again.status_code, 409)

    # ---- limits and endings

    def test_the_step_budget_still_ends_the_run(self):
        policy = self.desktops.store.policy(self.desktop)
        self.desktops.store.save_policy(
            self.desktop,
            {**policy, "budget": {"steps": 1, "activeSeconds": 600, "modelRequests": 5}},
            policy["revision"],
        )
        run = self.create().json()
        self.assertIn("result", self.step(run["id"], "desktop.observe", {}, "one").json())
        ended = self.step(run["id"], "desktop.observe", {}, "two")
        self.assertEqual(ended.status_code, 409, ended.text)
        self.assertIn("ended", ended.json()["detail"])
        finished = self.task(run["id"])
        self.assertEqual(finished["state"], "failed")
        self.assertIn("without finishing", finished["detail"])

    def test_a_restart_marks_a_driven_run_interrupted_never_resumed(self):
        run = self.create().json()
        self.assertEqual(self.task(run["id"])["state"], "running")
        # The next start over the same data directory is the restart: it says
        # what became of the run rather than picking it up.
        restarted = create_app(load_config())
        try:
            seen = restarted.state.agent_runs.get(self.desktop, run["id"])
            self.assertEqual(seen["state"], "interrupted")
            self.assertIn("not resumed", seen["detail"])
        finally:
            # Nothing was started: close the transport without ceremony.
            TestClient(restarted).close()


class DrivenStoreTests(unittest.TestCase):
    """The record a driven run leaves, and what a restart does to it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-driven-store-")
        self.addCleanup(self.temp.cleanup)

    def test_a_database_from_before_the_driver_gains_the_new_columns(self):
        path = Path(self.temp.name) / "runs.sqlite"
        db = sqlite3.connect(path)
        # The shape this table had before driven runs existed.
        db.execute(
            "CREATE TABLE agent_runs (id TEXT PRIMARY KEY, desktop_id TEXT NOT NULL,"
            " instruction TEXT NOT NULL, state TEXT NOT NULL, client_request_id TEXT,"
            " model TEXT, profile TEXT NOT NULL DEFAULT '{}', result TEXT, detail TEXT,"
            " outcome TEXT, budget TEXT NOT NULL DEFAULT '{}', position INTEGER NOT NULL,"
            " created_at TEXT NOT NULL, started_at TEXT, ended_at TEXT)"
        )
        db.execute(
            "INSERT INTO agent_runs (id, desktop_id, instruction, state, position, created_at)"
            " VALUES ('r1', 'd1', 'old task', 'succeeded', 1, 'now')"
        )
        db.commit()
        db.close()
        store = RunStore(path)
        existing = store.get("r1")
        self.assertEqual(existing["kind"], "model")
        self.assertEqual(existing["title"], "")
        self.assertEqual(existing["instruction"], "old task")
        driven = store.create_driven("d1", title="new way")
        self.assertEqual(driven["kind"], "driven")
        self.assertEqual(driven["state"], "running")

    def test_driven_runs_reconcile_like_any_other_run(self):
        store = RunStore(Path(self.temp.name) / "runs.sqlite")
        run = store.create_driven("d1", title="in flight")
        self.assertEqual(store.reconcile(), 1)
        self.assertEqual(store.get(run["id"])["state"], "interrupted")
        self.assertEqual(store.get(run["id"])["outcome"], "unknown")


class ModeBoundaryTests(unittest.TestCase):
    """Outside controller mode the driver routes simply do not exist."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="vela-driven-mode-")
        self.addCleanup(self.directory.cleanup)
        environment = patch.dict(os.environ, {"VELA_DATA_DIR": self.directory.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.client = TestClient(create_app(load_config()))
        self.addCleanup(self.client.close)
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.headers = {"Authorization": f"Bearer {token}"}
        self.desktop = self.client.get("/api/desktops", headers=self.headers).json()["defaultId"]

    def test_every_driver_route_answers_404_outside_controller_mode(self):
        base = f"/api/desktops/{self.desktop}/driven-runs"
        routes = [
            ("POST", base, {"title": "x"}),
            ("GET", f"{base}/tools", None),
            ("POST", f"{base}/{'a' * 32}/steps",
             {"tool": "desktop.observe", "arguments": {}, "clientStepId": "s1"}),
            ("GET", f"{base}/{'a' * 32}/steps/s1", None),
            ("POST", f"{base}/{'a' * 32}/finish", {"outcome": "succeeded", "summary": "x"}),
            ("POST", f"{base}/{'a' * 32}/cancel", None),
        ]
        for method, path, body in routes:
            with self.subTest(method=method, path=path):
                response = self.client.request(method, path, headers=self.headers, json=body)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertIn("controller mode", response.json()["detail"])

    def test_the_driver_routes_require_the_controller_token(self):
        directory = tempfile.TemporaryDirectory(prefix="vela-driven-auth-")
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        shutil.copytree(ROOT / "tests/fixtures/chat-fixture", root / "catalog/chat-fixture")
        environment = patch.dict(os.environ, {
            "VELA_DATA_DIR": str(root / "data"),
            "VELA_APPS_DIR": str(root / "catalog"),
            "VELA_CONTROLLER_MODE": "1",
            "VELA_CONTROLLER_TOKEN_FILE": str(write_token_file(root)),
        })
        environment.start()
        self.addCleanup(environment.stop)
        client = TestClient(create_app(load_config()))
        self.addCleanup(client.close)
        desktop = client.get("/api/desktops", headers={
            "Authorization": f"Bearer {TOKEN}"}).json()["defaultId"]
        refused = client.post(
            f"/api/desktops/{desktop}/driven-runs", json={"title": "no token"}
        )
        self.assertEqual(refused.status_code, 401, refused.text)


if __name__ == "__main__":
    unittest.main()
