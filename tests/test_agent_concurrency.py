"""Several desktops at once, and what happens when one of them goes wrong.

Three things are being established, and none of them is about a browser.

**Independence.** Two desktops working at the same time are two desktops: one
task each, their own queues, their own budgets, and nothing either does reaching
the other. The limit on how many run at once is honest about itself — a task
waiting for a free workspace says so rather than sitting on "Starting".

**A queue that stops when something went wrong.** A task that failed, was
interrupted, or sent something nobody could confirm leaves a question the owner
has not looked at. Starting the next one on top of that turns one problem into a
row of them with the same cause, so the queue holds and says why.

**Authority that does not outlive what it was reviewed against.** An app that is
removed or replaced takes its grants and its open questions with it, and asking
again is a new task rather than the old one carrying on.

The model and the tool surface are fixtures, deliberately. What is under test is
the supervisor's decisions; `test_agent_tools.py` is where a real browser is.
"""

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path

import test_agent_runs as runs_base
import test_app_contract as base
from scripts.fixture_apps import APPS as FIXTURE_APPS
from vela.agent_runs.store import TERMINAL, RunStore
from vela.agent_runs.supervisor import ADVANCES_QUEUE, BLOCKED_REASONS, Supervisor
from vela.desktops.runtime import MAX_OPEN_DESKTOPS, RuntimeUnavailable


class QueueRuleTests(unittest.TestCase):
    """The rule itself, before anything runs under it."""

    def test_only_a_finished_or_stopped_task_lets_the_next_one_start(self):
        self.assertEqual(set(ADVANCES_QUEUE), {"succeeded", "cancelled"})
        for state in ("failed", "interrupted", "outcome_unknown"):
            self.assertIn(state, BLOCKED_REASONS, state)
            self.assertNotIn(state, ADVANCES_QUEUE, state)

    def test_every_blocked_reason_is_a_sentence_a_person_can_act_on(self):
        for state, reason in BLOCKED_REASONS.items():
            self.assertTrue(reason.endswith("."), state)
            self.assertNotIn("error", reason.lower(), state)


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """Two desktops, one supervisor, and the limits between them."""

    def setUp(self):
        base.ApiBoundaryTests.setUp(self)
        shutil.copytree(FIXTURE_APPS / "notes", self.apps / "notes")
        self.client.post("/api/apps/notes/install", headers=self.hub)
        self.desktops = self.client.app.state.desktops
        self.first = self.client.get("/api/desktops", headers=self.hub).json()["defaultId"]
        self.second = self.client.post(
            "/api/desktops", headers=self.hub, json={"name": "Two"}
        ).json()["id"]
        for desktop_id in (self.first, self.second):
            self.desktops.store.set_kind(desktop_id, "agent")
        self.desktops.agent_runtime_for = lambda desktop_id: None

    def tearDown(self):
        base.ApiBoundaryTests.tearDown(self)

    def supervisor(self, script, answers=None, **kwargs):
        temp = tempfile.TemporaryDirectory(prefix="vela-conc-")
        self.addCleanup(temp.cleanup)
        store = RunStore(Path(temp.name) / "runs.sqlite")
        model = runs_base.ScriptedModel(script, **kwargs)
        tools = runs_base.RecordingTools(answers)
        return Supervisor(self.desktops, store, model=model, tools=tools), store

    async def settle(self, store, run_id, timeout=10):
        for _ in range(int(timeout / 0.05)):
            if store.get(run_id)["state"] in TERMINAL:
                return store.get(run_id)
            await asyncio.sleep(0.05)
        raise AssertionError(f"{run_id} is still {store.get(run_id)['state']}")

    async def state_becomes(self, store, run_id, want, timeout=5):
        for _ in range(int(timeout / 0.05)):
            if store.get(run_id)["state"] == want:
                return
            await asyncio.sleep(0.05)
        raise AssertionError(f"{run_id} is {store.get(run_id)['state']}, not {want}")

    # ---- one at a time, per desktop

    async def test_a_second_task_on_one_desktop_waits_for_the_first(self):
        supervisor, store = self.supervisor(
            [("task.finish", {"summary": "read it", "changed": False})] * 4
        )
        first = await supervisor.submit(self.first, "one")
        second = await supervisor.submit(self.first, "two")
        self.assertEqual(second["state"], "queued")
        await self.settle(store, first["id"])
        await self.settle(store, second["id"])
        # And in the order they were given, which is the whole of the promise.
        order = [run["instruction"] for run in store.list(self.first)]
        self.assertEqual(order[-2:], ["two", "one"])

    async def test_two_desktops_do_not_wait_for_each_other(self):
        supervisor, store = self.supervisor(
            [("task.finish", {"summary": "done", "changed": False})] * 4
        )
        here = await supervisor.submit(self.first, "on one")
        there = await supervisor.submit(self.second, "on two")
        self.assertNotEqual(here["id"], there["id"])
        await self.settle(store, here["id"])
        await self.settle(store, there["id"])
        # Each desktop's record contains its own task and nothing else.
        self.assertEqual([run["id"] for run in store.list(self.first)], [here["id"]])
        self.assertEqual([run["id"] for run in store.list(self.second)], [there["id"]])

    async def test_a_task_waiting_for_a_workspace_says_so_rather_than_starting(self):
        supervisor, store = self.supervisor(
            [("task.finish", {"summary": "done", "changed": False})] * 6
        )
        # Both workspaces held by something that is not going anywhere.
        await supervisor._slots.acquire()
        await supervisor._slots.acquire()
        try:
            waiting = await supervisor.submit(self.first, "queued behind the limit")
            for _ in range(40):
                if any(
                    event["kind"] == "task.waiting"
                    for event in store.events(self.first, after=0)
                ):
                    break
                await asyncio.sleep(0.05)
            told = [
                event
                for event in store.events(self.first, after=0)
                if event["kind"] == "task.waiting"
            ]
            self.assertTrue(told, "a task held by the limit has to say so")
            self.assertIn("busy", told[0]["payload"]["detail"])
            # And it is honestly still queued, not "Starting".
            self.assertEqual(store.get(waiting["id"])["state"], "queued")
        finally:
            supervisor._slots.release()
            supervisor._slots.release()
        await self.settle(store, waiting["id"])

    async def test_cancelling_a_task_that_is_waiting_for_a_workspace_ends_it(self):
        supervisor, store = self.supervisor([("task.finish", {"summary": "x", "changed": False})])
        await supervisor._slots.acquire()
        await supervisor._slots.acquire()
        try:
            waiting = await supervisor.submit(self.first, "never starts")
            await asyncio.sleep(0.2)
            await supervisor.control(self.first, waiting["id"], "stop")
            self.assertEqual(store.get(waiting["id"])["state"], "cancelled")
        finally:
            supervisor._slots.release()
            supervisor._slots.release()
        await asyncio.sleep(0.3)
        # The slot it was waiting for is not spent on a task nobody wants.
        self.assertEqual(store.get(waiting["id"])["state"], "cancelled")

    # ---- the queue holds when something went wrong

    async def test_a_failure_stops_the_queue_and_says_why(self):
        supervisor, store = self.supervisor(
            [("task.finish", {"summary": "I saved it", "changed": True})] * 12,
            answers={
                "task.finish": {
                    "proposed": True, "summary": "I saved it", "changed": True, "actions": [],
                }
            },
        )
        first = await supervisor.submit(self.first, "the one that fails")
        second = await supervisor.submit(self.first, "the one behind it")
        ended = await self.settle(store, first["id"])
        self.assertEqual(ended["state"], "failed")

        await asyncio.sleep(0.4)
        self.assertEqual(store.get(second["id"])["state"], "queued")
        self.assertEqual(supervisor.blocked(self.first), BLOCKED_REASONS["failed"])
        blocked = [
            event for event in store.events(self.first, after=0) if event["kind"] == "queue.blocked"
        ]
        self.assertTrue(blocked)
        self.assertEqual(blocked[0]["payload"]["after"], "failed")

    async def test_the_owner_saying_carry_on_is_what_starts_the_next_one(self):
        supervisor, store = self.supervisor(
            [("task.finish", {"summary": "I saved it", "changed": True})] * 12,
            answers={
                "task.finish": {
                    "proposed": True, "summary": "I saved it", "changed": True, "actions": [],
                }
            },
        )
        first = await supervisor.submit(self.first, "fails")
        second = await supervisor.submit(self.first, "waits")
        await self.settle(store, first["id"])
        await asyncio.sleep(0.3)
        self.assertEqual(store.get(second["id"])["state"], "queued")

        result = await supervisor.resume_queue(self.first)
        self.assertEqual(result["wasBlocked"], BLOCKED_REASONS["failed"])
        await self.settle(store, second["id"])
        self.assertIsNone(supervisor.blocked(self.first))

    async def test_giving_the_desktop_something_new_also_clears_the_hold(self):
        # Submitting is the person looking at the desktop, which is what the
        # hold was waiting for. Making them press two buttons would be ceremony.
        supervisor, store = self.supervisor(
            [("task.finish", {"summary": "I saved it", "changed": True})] * 24,
            answers={
                "task.finish": {
                    "proposed": True, "summary": "I saved it", "changed": True, "actions": [],
                }
            },
        )
        first = await supervisor.submit(self.first, "fails")
        queued = await supervisor.submit(self.first, "waits")
        await self.settle(store, first["id"])
        await asyncio.sleep(0.3)
        self.assertIsNotNone(supervisor.blocked(self.first))

        await supervisor.submit(self.first, "something new")
        self.assertIsNone(supervisor.blocked(self.first))
        await self.settle(store, queued["id"], timeout=15)

    async def test_a_desktop_that_is_held_does_not_hold_another_one(self):
        # The first three attempts claim a change with nothing behind them, so
        # that task fails; anything after reports having read something, so the
        # other desktop's task succeeds normally.
        supervisor, store = self.supervisor(
            [("task.finish", {"summary": "whatever", "changed": True})] * 24,
            answers={
                "task.finish": lambda count: (
                    {"proposed": True, "summary": "I saved it", "changed": True, "actions": []}
                    if count <= 3
                    else {"proposed": True, "summary": "read it", "changed": False, "actions": []}
                )
            },
        )
        failing = await supervisor.submit(self.first, "fails")
        await supervisor.submit(self.first, "waits behind it")
        await self.settle(store, failing["id"])
        await asyncio.sleep(0.3)
        self.assertIsNotNone(supervisor.blocked(self.first))

        elsewhere = await supervisor.submit(self.second, "not affected")
        ended = await self.settle(store, elsewhere["id"])
        self.assertEqual(ended["state"], "succeeded")
        self.assertIsNone(supervisor.blocked(self.second))

    async def test_a_successful_task_lets_the_next_one_go(self):
        supervisor, store = self.supervisor(
            [("task.finish", {"summary": "read it", "changed": False})] * 8
        )
        first = await supervisor.submit(self.first, "one")
        second = await supervisor.submit(self.first, "two")
        await self.settle(store, first["id"])
        ended = await self.settle(store, second["id"])
        self.assertEqual(ended["state"], "succeeded")
        self.assertIsNone(supervisor.blocked(self.first))

    # ---- how far it got

    async def test_a_task_that_did_not_succeed_records_the_last_step_with_a_receipt(self):
        class Evidence(runs_base._NullLog):
            def evidence(self, *args, **kwargs):
                return [
                    {"tool": "desktop.click", "target": "f0:e1", "outcome": "committed", "at": 1},
                    {"tool": "app.invoke_action", "target": "notes.save", "outcome": "committed", "at": 2},
                    {"tool": "desktop.type", "target": "f0:e2", "outcome": "failed_before_commit", "at": 3},
                ]

        supervisor, store = self.supervisor(
            [("task.finish", {"summary": "I saved it", "changed": True})] * 12,
            answers={
                "task.finish": {
                    "proposed": True, "summary": "I saved it", "changed": True, "actions": [],
                }
            },
        )
        supervisor.tools.log = Evidence()
        run = await supervisor.submit(self.first, "stops short")
        ended = await self.settle(store, run["id"])
        self.assertEqual(ended["state"], "failed")
        self.assertEqual(ended["result"]["lastConfirmed"]["tool"], "app.invoke_action")
        self.assertEqual(ended["result"]["lastConfirmed"]["target"], "notes.save")


class RetryTests(unittest.IsolatedAsyncioTestCase):
    """Asking again is a new task. It is never the old one carrying on."""

    def setUp(self):
        base.ApiBoundaryTests.setUp(self)
        shutil.copytree(FIXTURE_APPS / "notes", self.apps / "notes")
        self.client.post("/api/apps/notes/install", headers=self.hub)
        self.desktops = self.client.app.state.desktops
        self.runs = self.client.app.state.agent_runs
        self.desktop = self.client.get("/api/desktops", headers=self.hub).json()["defaultId"]
        self.desktops.store.set_kind(self.desktop, "agent")
        self.desktops.agent_runtime_for = lambda desktop_id: None

    def tearDown(self):
        base.ApiBoundaryTests.tearDown(self)

    def ended(self, state, instruction="do the thing"):
        run = self.runs.store.submit(self.desktop, instruction)
        self.runs.store.update(run["id"], state="running")
        self.runs.store.update(run["id"], state=state, detail="it stopped")
        return self.runs.store.get(run["id"])

    async def test_a_retry_is_a_new_run_with_the_same_instruction(self):
        original = self.ended("failed", "make a shopping list")
        again = await self.runs.retry(self.desktop, original["id"])
        self.assertNotEqual(again["id"], original["id"])
        self.assertEqual(again["instruction"], "make a shopping list")
        self.assertEqual(again["state"], "queued")
        # The original is left exactly as it ended. A record of a task that
        # failed is worth something only if it stays a record of that.
        self.assertEqual(self.runs.store.get(original["id"])["state"], "failed")
        linked = [
            event
            for event in self.runs.store.events(self.desktop, after=0)
            if event["kind"] == "task.retried"
        ]
        self.assertEqual(linked[-1]["payload"]["of"], original["id"])

    async def test_a_task_still_going_cannot_be_retried(self):
        run = self.runs.store.submit(self.desktop, "in flight")
        self.runs.store.update(run["id"], state="running")
        with self.assertRaises(Exception) as caught:
            await self.runs.retry(self.desktop, run["id"])
        self.assertIn("has not finished", str(caught.exception))

    async def test_a_desktop_with_something_unaccounted_for_refuses_to_repeat_it(self):
        original = self.ended("failed")
        self.desktops._uncertain[self.desktop] = {
            "d" * 64: {"url": "https://example.test/orders", "method": "POST", "at": 0}
        }
        with self.assertRaises(Exception) as caught:
            await self.runs.retry(self.desktop, original["id"])
        self.assertIn("not been accounted for", str(caught.exception))

        # Saying it has been checked is what makes asking again possible.
        self.desktops.resolve_uncertain(self.desktop)
        again = await self.runs.retry(self.desktop, original["id"])
        self.assertEqual(again["state"], "queued")

    async def test_the_route_reports_what_is_queued_and_why_it_is_held(self):
        original = self.ended("failed")
        response = self.client.post(
            f"/api/desktops/{self.desktop}/tasks/{original['id']}/retry", headers=self.hub
        )
        self.assertEqual(response.status_code, 201, response.text)
        listing = self.client.get(
            f"/api/desktops/{self.desktop}/tasks", headers=self.hub
        ).json()
        self.assertIn("blocked", listing)
        resumed = self.client.post(
            f"/api/desktops/{self.desktop}/queue/resume", headers=self.hub
        )
        self.assertEqual(resumed.status_code, 200, resumed.text)


class FocusTests(unittest.TestCase):
    """One desktop's attention is not another's."""

    def setUp(self):
        base.ApiBoundaryTests.setUp(self)
        shutil.copytree(FIXTURE_APPS / "notes", self.apps / "notes")
        self.client.post("/api/apps/notes/install", headers=self.hub)
        self.desktops = self.client.app.state.desktops
        self.first = self.client.get("/api/desktops", headers=self.hub).json()["defaultId"]
        self.second = self.client.post(
            "/api/desktops", headers=self.hub, json={"name": "Two"}
        ).json()["id"]

    def tearDown(self):
        base.ApiBoundaryTests.tearDown(self)

    def test_selecting_a_window_on_one_desktop_leaves_the_other_alone(self):
        here = self.desktops.open_view(self.first, "app", {"appId": "notes"})
        there = self.desktops.open_view(self.second, "app", {"appId": "notes"})
        self.desktops.select_view(self.first, here["id"])
        self.desktops.select_view(self.second, there["id"])
        self.assertEqual(self.desktops.layout(self.first)["selectedView"], here["id"])
        self.assertEqual(self.desktops.layout(self.second)["selectedView"], there["id"])

        # And a view id from one is not addressable from the other at all.
        with self.assertRaises(Exception):
            self.desktops.select_view(self.first, there["id"])
        self.assertEqual(self.desktops.layout(self.first)["selectedView"], here["id"])


class RuntimeLimitTests(unittest.IsolatedAsyncioTestCase):
    """Refusing a third browser is the version of resource pressure Vela can keep."""

    async def test_a_third_desktop_is_refused_with_a_sentence(self):
        from vela.desktops.runtime import BrowserRuntime

        runtime = BrowserRuntime(Path(tempfile.gettempdir()) / "vela-limit-frames")
        sent = []

        async def fake_command(name, **fields):
            sent.append((name, fields))
            return {"runtimeSessionId": fields.get("runtimeSessionId")}

        runtime.command = fake_command
        for index in range(MAX_OPEN_DESKTOPS):
            await runtime.open_desktop(f"d{index}", f"rs{index}", {})
        self.assertEqual(len(runtime.desktops), MAX_OPEN_DESKTOPS)

        with self.assertRaises(RuntimeUnavailable) as caught:
            await runtime.open_desktop("one-too-many", "rs-x", {})
        self.assertIn("Turn one of the others off", str(caught.exception))
        self.assertEqual(len(sent), MAX_OPEN_DESKTOPS, "nothing was sent for the refused one")

        # Reopening one it already holds is not a third browser.
        await runtime.open_desktop("d0", "rs0", {})
        self.assertEqual(len(runtime.desktops), MAX_OPEN_DESKTOPS)


class AuthorityTests(unittest.TestCase):
    """An app that changed underneath a desktop takes its authority with it."""

    def setUp(self):
        base.ApiBoundaryTests.setUp(self)
        shutil.copytree(FIXTURE_APPS / "notes", self.apps / "notes")
        self.client.post("/api/apps/notes/install", headers=self.hub)
        self.desktops = self.client.app.state.desktops
        self.desktop = self.client.get("/api/desktops", headers=self.hub).json()["defaultId"]
        self.desktops.store.set_kind(self.desktop, "agent")

    def tearDown(self):
        base.ApiBoundaryTests.tearDown(self)

    def grant(self, app_id="notes"):
        installation = self.desktops._storage.installation(app_id)
        return self.desktops.grants.issue(
            desktop_id=self.desktop,
            effect="write",
            app_id=app_id,
            installation_id=installation,
            contract="whatever-it-was",
            seconds=3600,
        )

    def test_removing_an_app_removes_what_was_granted_against_it(self):
        self.grant()
        self.assertEqual(len(self.desktops.grants.list(self.desktop)), 1)
        removed = self.client.delete("/api/apps/notes", headers=self.hub)
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(self.desktops.grants.list(self.desktop), [])

    def test_a_question_about_an_app_that_went_away_can_no_longer_be_answered(self):
        record = self.desktops.approvals.request(
            desktop_id=self.desktop,
            run_id="r1",
            view_id=None,
            effect="write",
            app_id="notes",
            app_name="Notes",
            installation_id=self.desktops._storage.installation("notes"),
            contract="whatever-it-was",
            request_digest="a" * 64,
            scope=None,
            summary={"effect": "write", "headline": "Notes wants to save.", "detail": []},
        )
        self.client.delete("/api/apps/notes", headers=self.hub)
        answered = self.client.post(
            f"/api/desktops/{self.desktop}/approvals/{record['requestId']}",
            headers=self.hub,
            json={"decision": "approve", "requestDigest": "a" * 64},
        )
        self.assertEqual(answered.status_code, 409, answered.text)

    def test_another_app_s_grants_are_left_alone(self):
        self.grant()
        other = self.desktops.grants.issue(
            desktop_id=self.desktop,
            effect="write",
            app_id="something-else",
            installation_id="inst-2",
            contract="c2",
            seconds=3600,
        )
        self.client.delete("/api/apps/notes", headers=self.hub)
        remaining = {entry["id"] for entry in self.desktops.grants.list(self.desktop)}
        self.assertEqual(remaining, {other["id"]})


if __name__ == "__main__":
    unittest.main()
