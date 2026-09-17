"""Tasks the server carries out on its own.

The thing being established here is that a run belongs to the server. Nobody has
to be watching it, the dashboard can be closed, and what ends it is a person, a
limit or the work being done — never a viewer going away.

The model is a fixture. That is deliberate and it is a limit worth stating: a
scripted model proves the *infrastructure* is correct — that the loop dispatches,
that budgets bite, that a cancelled run stops, that an approval pauses and
resumes the same call, that events are ordered and recoverable. It proves
nothing at all about whether a real model can decide what to do. That is a
separate, separately recorded evaluation, and no number of passing fixtures
substitutes for it.

Disposable data throughout. Nothing here needs a browser: the tool surface is
replaced by a recording double, so these tests are about the supervisor and not
about Chromium. `test_agent_tools.py` is where the real browser is.
"""

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path

import test_app_contract as base
from scripts.fixture_apps import APPS as FIXTURE_APPS
from vela.agent_runs.approvals import ApprovalPending
from vela.agent_runs.budget import Budget, BudgetExhausted, MAX_FRUITLESS_STEPS, clip_output
from vela.agent_runs.model import ModelUnavailable, parse_call, tool_definitions
from vela.agent_runs.store import ACTIVE, TERMINAL, RunStore
from vela.agent_runs.supervisor import Supervisor
from vela.agent_runs.tools import TOOLS, ToolError
from vela.app_storage import AppServiceError


class BudgetTests(unittest.TestCase):
    """The floor under every run."""

    def test_each_limit_says_which_one_it_was(self):
        for field, limit in (
            ("steps_used", "steps"),
            ("model_requests_used", "modelRequests"),
        ):
            budget = Budget(steps=2, model_requests=2, active_seconds=999)
            setattr(budget, field, 2)
            with self.assertRaises(BudgetExhausted) as caught:
                budget.check()
            self.assertEqual(caught.exception.limit, limit)
            self.assertIn("without finishing", caught.exception.detail)

    def test_waiting_for_a_person_is_not_the_runs_time(self):
        budget = Budget(active_seconds=999)
        budget.resume()
        budget.suspend()
        spent = budget.elapsed
        # A long wait between suspend and resume adds nothing.
        budget.seconds_used += 0  # nothing happens while suspended
        self.assertAlmostEqual(budget.elapsed, spent, places=3)
        budget.resume()
        self.assertGreaterEqual(budget.elapsed, spent)

    def test_resuming_twice_does_not_start_two_clocks(self):
        budget = Budget()
        budget.resume()
        first = budget._running_since
        budget.resume()
        self.assertEqual(budget._running_since, first)

    def test_a_run_of_fruitless_steps_ends_it_and_a_useful_one_clears_it(self):
        budget = Budget()
        for _ in range(MAX_FRUITLESS_STEPS - 1):
            budget.spend_step(progressed=False)
        budget.check()
        budget.spend_step(progressed=True)
        self.assertEqual(budget.fruitless, 0)
        for _ in range(MAX_FRUITLESS_STEPS):
            budget.spend_step(progressed=False)
        with self.assertRaises(BudgetExhausted) as caught:
            budget.check()
        self.assertEqual(caught.exception.limit, "noProgress")

    def test_an_enormous_result_is_cut_short_and_says_so(self):
        clipped = clip_output({"text": "x" * 100_000}, limit=1000)
        self.assertTrue(clipped["truncated"])
        self.assertIn("cut short", clipped["note"])
        self.assertLess(len(str(clipped)), 2000)

    def test_a_small_result_passes_through_untouched(self):
        value = {"ok": True, "items": [1, 2, 3]}
        self.assertEqual(clip_output(value), value)


class ToolDefinitionTests(unittest.TestCase):
    """What the model is told it can do, against what it can actually do."""

    def test_the_model_is_offered_exactly_the_declared_surface(self):
        definitions = tool_definitions(TOOLS)
        offered = {entry["function"]["name"] for entry in definitions}
        self.assertEqual(offered, {tool["name"] for tool in TOOLS})

    def test_arguments_arrive_as_an_object_whatever_shape_the_model_used(self):
        self.assertEqual(
            parse_call({"function": {"name": "desktop.observe", "arguments": {"viewId": "v"}}}),
            ("desktop.observe", {"viewId": "v"}),
        )
        self.assertEqual(
            parse_call({"function": {"name": "desktop.observe", "arguments": '{"viewId": "v"}'}}),
            ("desktop.observe", {"viewId": "v"}),
        )
        # Nonsense becomes an empty object, and the tool refuses it with a
        # message, rather than the loop crashing on a model's bad JSON.
        self.assertEqual(parse_call({"function": {"name": "x", "arguments": "{{"}}), ("x", {}))
        self.assertEqual(parse_call({}), ("", {}))


class StoreTests(unittest.TestCase):
    """The record, and what a restart does to it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-runs-")
        self.store = RunStore(Path(self.temp.name) / "runs.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def test_one_request_id_queues_one_task(self):
        first = self.store.submit("d1", "do the thing", client_request_id="c1")
        second = self.store.submit("d1", "do the thing", client_request_id="c1")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.store.list("d1")), 1)

    def test_without_a_request_id_two_submissions_are_two_tasks(self):
        self.store.submit("d1", "one")
        self.store.submit("d1", "one")
        self.assertEqual(len(self.store.list("d1")), 2)

    def test_the_queue_is_first_in_first_out(self):
        first = self.store.submit("d1", "one")
        self.store.submit("d1", "two")
        self.assertEqual(self.store.next_queued("d1")["id"], first["id"])

    def test_a_restart_marks_what_was_in_flight_and_leaves_the_queue_alone(self):
        running = self.store.submit("d1", "one")
        self.store.update(running["id"], state="running")
        queued = self.store.submit("d1", "two")

        self.assertEqual(self.store.reconcile(), 1)
        self.assertEqual(self.store.get(running["id"])["state"], "interrupted")
        self.assertIn("not resumed", self.store.get(running["id"])["detail"])
        self.assertEqual(
            self.store.get(queued["id"])["state"],
            "queued",
            "queued work waits for the person, rather than starting itself",
        )

    def test_a_finished_task_does_not_come_back_to_life(self):
        run = self.store.submit("d1", "one")
        self.store.update(run["id"], state="succeeded")
        with self.assertRaises(AppServiceError) as caught:
            self.store.update(run["id"], state="running")
        self.assertEqual(caught.exception.status, 409)

    def test_every_active_state_is_reconciled_and_no_terminal_one_is(self):
        self.assertFalse(set(ACTIVE) & set(TERMINAL))
        for state in ACTIVE:
            run = self.store.submit("d1", state)
            self.store.update(run["id"], state=state)
        self.assertEqual(self.store.reconcile(), len(ACTIVE))

    def test_events_are_numbered_per_desktop_and_recoverable_from_a_cursor(self):
        for index in range(5):
            self.store.append("d1", "step.finished", {"n": index})
        self.store.append("d2", "step.finished", {"n": 0})
        numbers = [event["sequence"] for event in self.store.events("d1")]
        self.assertEqual(numbers, [1, 2, 3, 4, 5])
        self.assertEqual(self.store.events("d2")[0]["sequence"], 1, "each desktop counts its own")
        later = self.store.events("d1", after=3)
        self.assertEqual([event["payload"]["n"] for event in later], [3, 4])

    def test_with_history_off_nothing_is_written_down(self):
        temp = tempfile.TemporaryDirectory(prefix="vela-runs-off-")
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "runs.sqlite"
        store = RunStore(path, history=lambda: False)
        run = store.submit("d1", "one")
        store.append("d1", "task.queued", {"runId": run["id"]})
        self.assertEqual(len(store.list("d1")), 1, "it still works while the process lives")
        # And a second store over the same file — which is what a restart is —
        # finds nothing, because nothing was written.
        self.assertEqual(RunStore(path).list("d1"), [])

    def test_purging_removes_the_transcripts_and_the_events(self):
        run = self.store.submit("d1", "one")
        self.store.append("d1", "task.queued", {"runId": run["id"]})
        self.store.purge()
        self.assertEqual(self.store.list("d1"), [])
        self.assertEqual(self.store.events("d1"), [])


class ScriptedModel:
    """A model that does exactly what the test told it to.

    Each entry is either a tool name and arguments, or a string the model
    "says". `asked` records every call, so a test can assert that a run stopped
    asking rather than that it merely stopped.
    """

    def __init__(self, script, *, vision=False, tools=True):
        self.script = list(script)
        self.asked = []
        self._vision = vision
        self._tools = tools

    def default_model(self):
        return "fixture-model"

    async def capabilities(self, model):
        return {"model": model, "url": "fixture", "tools": self._tools, "vision": self._vision}

    def require_usable(self, capabilities):
        if not capabilities.get("tools"):
            raise ModelUnavailable(f"{capabilities['model']} cannot call tools.")

    async def propose(self, *, model, messages, tools):
        self.asked.append(messages[-1])
        if not self.script:
            return {"content": "I have run out of ideas.", "calls": []}
        step = self.script.pop(0)
        if isinstance(step, str):
            return {"content": step, "calls": []}
        name, arguments = step
        return {
            "content": "",
            "calls": [{"function": {"name": name, "arguments": arguments}}],
        }


class RecordingTools:
    """A tool surface that answers however the test needs it to."""

    def __init__(self, answers=None, log=None):
        self.answers = answers or {}
        self.calls = []
        self.log = log or _NullLog()

    async def call(self, desktop_id, name, arguments, *, run_id, actor_id=None):
        self.calls.append((name, arguments))
        answer = self.answers.get(name, {"ok": True})
        if callable(answer):
            answer = answer(len([call for call in self.calls if call[0] == name]))
        if isinstance(answer, Exception):
            raise answer
        return answer


class _NullLog:
    def forget_run(self, *args, **kwargs):
        pass

    def evidence(self, *args, **kwargs):
        return []


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    """The loop, with everything under it replaced by something predictable."""

    def setUp(self):
        base.ApiBoundaryTests.setUp(self)
        shutil.copytree(FIXTURE_APPS / "notes", self.apps / "notes")
        self.client.post("/api/apps/notes/install", headers=self.hub)
        self.desktops = self.client.app.state.desktops
        self.desktop = self.client.get("/api/desktops", headers=self.hub).json()["defaultId"]
        # A desktop that is an agent's, without needing a browser for it: the
        # supervisor's runtime check is what a real conversion satisfies, and
        # here it is satisfied directly.
        self.desktops.store.set_kind(self.desktop, "agent")
        self.desktops.agent_runtime_for = lambda desktop_id: None

    def tearDown(self):
        base.ApiBoundaryTests.tearDown(self)

    def supervisor(self, script, answers=None, **kwargs):
        temp = tempfile.TemporaryDirectory(prefix="vela-sup-")
        self.addCleanup(temp.cleanup)
        store = RunStore(Path(temp.name) / "runs.sqlite")
        model = ScriptedModel(script, **kwargs)
        tools = RecordingTools(answers)
        supervisor = Supervisor(self.desktops, store, model=model, tools=tools)
        return supervisor, store, model, tools

    async def run_to_end(self, supervisor, store, instruction="do the thing", timeout=10):
        run = await supervisor.submit(self.desktop, instruction)
        for _ in range(int(timeout / 0.05)):
            current = store.get(run["id"])
            if current["state"] in TERMINAL:
                return current
            await asyncio.sleep(0.05)
        raise AssertionError(f"the task never finished; it is {store.get(run['id'])['state']}")

    # ---- the ordinary path

    async def test_a_task_runs_to_a_result_without_anybody_watching(self):
        supervisor, store, _, tools = self.supervisor(
            [
                ("desktop.observe", {}),
                ("app.invoke_action", {"appId": "notes", "actionId": "create-note",
                                       "value": {"title": "Milk"}, "requestKey": "k1"}),
                ("task.finish", {"summary": "Saved a note called Milk"}),
            ],
            answers={
                "desktop.observe": {"observationId": "o1", "page": {"controls": []}},
                "app.invoke_action": {"result": {"status": "succeeded"}},
                "task.finish": {
                    "proposed": True,
                    "summary": "Saved a note called Milk",
                    "evidence": ["the note is in the list"],
                    "actions": [{"tool": "app.invoke_action", "outcome": "committed"}],
                },
            },
        )
        finished = await self.run_to_end(supervisor, store)
        self.assertEqual(finished["state"], "succeeded", finished["detail"])
        self.assertTrue(finished["result"]["changed"])
        self.assertEqual([name for name, _ in tools.calls],
                         ["desktop.observe", "app.invoke_action", "task.finish"])

    async def test_the_events_are_ordered_and_readable_from_a_cursor(self):
        supervisor, store, _, _ = self.supervisor(
            [("task.finish", {"summary": "Read the page"})],
            answers={"task.finish": {"proposed": True, "summary": "Read the page", "actions": []}},
        )
        await self.run_to_end(supervisor, store)
        events = store.events(self.desktop)
        kinds = [event["kind"] for event in events]
        self.assertEqual(kinds[0], "task.queued")
        self.assertIn("task.running", kinds)
        self.assertEqual(kinds[-1], "task.succeeded")
        self.assertEqual(
            [event["sequence"] for event in events], list(range(1, len(events) + 1))
        )
        # A viewer that saw the first three asks for the rest and gets exactly them.
        rest = store.events(self.desktop, after=3)
        self.assertEqual([event["sequence"] for event in rest], list(range(4, len(events) + 1)))

    # ---- claims and evidence

    async def test_a_claim_of_changing_something_needs_something_that_changed(self):
        supervisor, store, model, _ = self.supervisor(
            [
                ("task.finish", {"summary": "I saved the note", "changed": True}),
                ("task.finish", {"summary": "The page lists three notes", "changed": False}),
            ],
            answers={
                "task.finish": lambda count: {
                    "proposed": True,
                    "summary": "I saved the note" if count == 1 else "The page lists three notes",
                    "changed": count == 1,
                    "actions": [],
                }
            },
        )
        finished = await self.run_to_end(supervisor, store)
        # The first claim was refused for having no committed effect behind it;
        # the second, which only describes what was read, is accepted.
        self.assertEqual(finished["state"], "succeeded")
        self.assertFalse(finished["result"]["changed"])
        self.assertEqual(finished["result"]["summary"], "The page lists three notes")

    async def test_a_summary_that_merely_sounds_like_a_change_is_not_refused(self):
        """The failure a real evaluation run found.

        An earlier version looked for words like "created" in the summary. A
        model reporting "no notes have been created" was refused for a change it
        was explicitly saying it had not made, and repeated itself until the step
        budget ended the task. Prose is not a claim; `changed` is.
        """
        supervisor, store, _, _ = self.supervisor(
            [("task.finish", {"summary": "No notes have been created yet.", "changed": False})],
            answers={
                "task.finish": {
                    "proposed": True,
                    "summary": "No notes have been created yet.",
                    "changed": False,
                    "actions": [],
                }
            },
        )
        finished = await self.run_to_end(supervisor, store)
        self.assertEqual(finished["state"], "succeeded", finished["detail"])
        self.assertFalse(finished["result"]["changed"])

    async def test_a_run_that_cannot_finish_acceptably_is_ended_rather_than_looping(self):
        supervisor, store, model, _ = self.supervisor(
            [("task.finish", {"summary": "I saved it", "changed": True})] * 30,
            answers={
                "task.finish": {
                    "proposed": True, "summary": "I saved it", "changed": True, "actions": [],
                }
            },
        )
        finished = await self.run_to_end(supervisor, store)
        self.assertEqual(finished["state"], "failed")
        self.assertIn("could not produce a result", finished["detail"])
        self.assertLess(
            finished["budget"]["steps"]["used"],
            finished["budget"]["steps"]["limit"],
            "it stops after being told, not after spending every step it was given",
        )

    # ---- limits

    async def test_a_model_that_only_talks_runs_out_rather_than_going_forever(self):
        supervisor, store, model, tools = self.supervisor(["thinking about it"] * 40)
        self.desktops.store.save_policy(
            self.desktop,
            {**self.desktops.store.policy(self.desktop), "apps": ["notes"],
             "budget": {"steps": 3, "activeSeconds": 60, "modelRequests": 3}},
            self.desktops.store.policy(self.desktop)["revision"],
        )
        finished = await self.run_to_end(supervisor, store)
        self.assertEqual(finished["state"], "failed")
        self.assertIn("without finishing", finished["detail"])
        self.assertEqual(tools.calls, [], "nothing was dispatched, because nothing was asked for")
        self.assertLessEqual(len(model.asked), 4)

    async def test_a_tool_that_refuses_is_told_to_the_model_rather_than_swallowed(self):
        supervisor, store, model, tools = self.supervisor(
            [
                ("desktop.click", {"viewId": "v1", "observationId": "gone", "ref": "f0:e1"}),
                ("task.finish", {"summary": "Could not find the control"}),
            ],
            answers={
                "desktop.click": ToolError("stale_observation", "observe again", retryable=True),
                "task.finish": {"proposed": True, "summary": "Could not find the control",
                                "actions": []},
            },
        )
        finished = await self.run_to_end(supervisor, store)
        self.assertEqual(finished["state"], "succeeded")
        # The refusal reached the model as a tool result it could read.
        told = [message for message in model.asked if message.get("role") == "tool"]
        self.assertTrue(any("stale_observation" in message["content"] for message in told), told)

    async def test_an_unknown_tool_is_refused_without_reaching_anything(self):
        supervisor, store, model, tools = self.supervisor(
            [
                ("desktop.run_shell", {"command": "rm -rf /"}),
                ("task.finish", {"summary": "Nothing to report"}),
            ],
            answers={"task.finish": {"proposed": True, "summary": "Nothing to report",
                                     "actions": []}},
        )
        finished = await self.run_to_end(supervisor, store)
        self.assertEqual(finished["state"], "succeeded")
        self.assertEqual([name for name, _ in tools.calls], ["task.finish"])
        self.assertTrue(
            any("no tool called" in str(message.get("content")) for message in model.asked)
        )

    # ---- the model itself

    async def test_a_model_that_cannot_call_tools_is_refused_before_anything_opens(self):
        supervisor, store, _, tools = self.supervisor([], tools=False)
        finished = await self.run_to_end(supervisor, store)
        self.assertEqual(finished["state"], "failed")
        self.assertIn("cannot call tools", finished["detail"])
        self.assertEqual(tools.calls, [], "no window was opened for a task that could not run")

    # ---- stopping

    async def test_stopping_ends_dispatch_and_says_what_it_leaves_behind(self):
        started = asyncio.Event()

        async def slow(count):
            started.set()
            await asyncio.sleep(30)
            return {"ok": True}

        supervisor, store, _, tools = self.supervisor(
            [("desktop.wait", {"viewId": "v1", "condition": {"type": "idle"}})] * 5,
            answers={"desktop.wait": lambda count: {"ok": True}},
        )

        class Slow(RecordingTools):
            async def call(self, *args, **kwargs):
                started.set()
                await asyncio.sleep(30)

        supervisor.tools = Slow()
        run = await supervisor.submit(self.desktop, "wait around")
        await asyncio.wait_for(started.wait(), timeout=5)
        await supervisor.control(self.desktop, run["id"], "stop")
        for _ in range(100):
            current = store.get(run["id"])
            if current["state"] in TERMINAL:
                break
            await asyncio.sleep(0.05)
        self.assertEqual(store.get(run["id"])["state"], "cancelled")
        self.assertIn("stays done", store.get(run["id"])["detail"])

    async def test_cancelling_something_queued_never_starts_it(self):
        supervisor, store, _, tools = self.supervisor([("task.finish", {"summary": "done"})])
        first = await supervisor.submit(self.desktop, "one")
        # A second submission queues behind the first, which holds the desktop.
        second = store.submit(self.desktop, "two")
        cancelled = await supervisor.control(self.desktop, second["id"], "stop")
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertIn("before it started", cancelled["detail"])
        del first

    async def test_one_desktop_carries_one_task_at_a_time(self):
        supervisor, store, _, _ = self.supervisor(
            [("task.finish", {"summary": "done"})],
            answers={"task.finish": {"proposed": True, "summary": "done", "actions": []}},
        )
        await supervisor.submit(self.desktop, "one")
        second = store.submit(self.desktop, "two")
        await supervisor._pump(self.desktop)
        self.assertIn(store.get(second["id"])["state"], ("queued", "starting", "running"))
        active = [
            run for run in store.list(self.desktop) if run["state"] in ("running", "starting")
        ]
        self.assertLessEqual(len(active), 1)

    # ---- approvals

    async def test_a_change_that_needs_approval_pauses_the_run_and_then_continues(self):
        record = {
            "requestId": "req-1",
            "summary": {"headline": "Notes wants to save a change.", "detail": []},
            "expiresAt": 0,
        }
        state = {"answered": False}

        class Approving(RecordingTools):
            async def call(self, desktop_id, name, arguments, *, run_id, actor_id=None):
                self.calls.append((name, arguments))
                if name == "app.invoke_action" and not state["answered"]:
                    raise ApprovalPending(record)
                if name == "app.invoke_action":
                    return {"result": {"status": "succeeded"}}
                return {
                    "proposed": True,
                    "summary": "Saved it",
                    "actions": [{"tool": "app.invoke_action", "outcome": "committed"}],
                }

        supervisor, store, _, _ = self.supervisor(
            [
                ("app.invoke_action", {"appId": "notes", "actionId": "create-note",
                                       "value": {"title": "Milk"}, "requestKey": "k1"}),
                ("task.finish", {"summary": "Saved it"}),
            ]
        )
        supervisor.tools = Approving()

        class Answers:
            def get(self, request_id, *, desktop_id=None):
                if state["answered"]:
                    return {"state": "approved", "requestId": request_id}
                return {"state": "pending", "requestId": request_id}

        self.desktops._approvals = Answers()
        run = await supervisor.submit(self.desktop, "save a note")
        # It is waiting, not failing.
        for _ in range(100):
            if store.get(run["id"])["state"] == "waiting_approval":
                break
            await asyncio.sleep(0.05)
        self.assertEqual(store.get(run["id"])["state"], "waiting_approval")
        state["answered"] = True

        for _ in range(200):
            if store.get(run["id"])["state"] in TERMINAL:
                break
            await asyncio.sleep(0.05)
        finished = store.get(run["id"])
        self.assertEqual(finished["state"], "succeeded", finished["detail"])
        self.assertEqual(
            [name for name, _ in supervisor.tools.calls],
            ["app.invoke_action", "app.invoke_action", "task.finish"],
            "the same call again once it was allowed, not a different one",
        )
        waiting = [
            event for event in store.events(self.desktop) if event["kind"] == "approval.waiting"
        ]
        self.assertEqual(len(waiting), 1)
        self.assertEqual(waiting[0]["payload"]["requestId"], "req-1")

    async def test_a_denied_change_is_told_to_the_model_and_does_not_end_the_run(self):
        record = {"requestId": "req-2", "summary": {"headline": "x", "detail": []}, "expiresAt": 0}

        class Denied(RecordingTools):
            async def call(self, desktop_id, name, arguments, *, run_id, actor_id=None):
                self.calls.append((name, arguments))
                if name == "app.invoke_action":
                    raise ApprovalPending(record)
                return {"proposed": True, "summary": "It was not approved", "actions": []}

        supervisor, store, model, _ = self.supervisor(
            [
                ("app.invoke_action", {"appId": "notes", "actionId": "create-note",
                                       "value": {}, "requestKey": "k1"}),
                ("task.finish", {"summary": "It was not approved"}),
            ]
        )
        supervisor.tools = Denied()

        class Answers:
            def get(self, request_id, *, desktop_id=None):
                return {"state": "denied", "requestId": request_id, "reason": "you said no"}

        self.desktops._approvals = Answers()
        finished = await self.run_to_end(supervisor, store, timeout=20)
        self.assertEqual(finished["state"], "succeeded")
        told = [message for message in model.asked if message.get("role") == "tool"]
        self.assertTrue(any("not_approved" in message["content"] for message in told), told)


if __name__ == "__main__":
    unittest.main()


class TaskApiTests(unittest.IsolatedAsyncioTestCase):
    """The routes, against the service the server actually builds.

    The model and the tool surface are replaced; everything else — the store on
    disk, the queue, the event numbering, the desktop checks — is the real
    thing, because those are what an HTTP caller is relying on.
    """

    def setUp(self):
        base.ApiBoundaryTests.setUp(self)
        shutil.copytree(FIXTURE_APPS / "notes", self.apps / "notes")
        self.client.post("/api/apps/notes/install", headers=self.hub)
        self.desktops = self.client.app.state.desktops
        self.runs = self.client.app.state.agent_runs
        self.desktop = self.client.get("/api/desktops", headers=self.hub).json()["defaultId"]
        self.desktops.agent_runtime_for = lambda desktop_id: None
        self.runs.supervisor.model = ScriptedModel(
            [("task.finish", {"summary": "Read the page"})]
        )
        self.runs.supervisor.tools = RecordingTools(
            {"task.finish": {"proposed": True, "summary": "Read the page", "actions": []}}
        )

    def tearDown(self):
        base.ApiBoundaryTests.tearDown(self)

    def make_agent(self):
        self.desktops.store.set_kind(self.desktop, "agent")

    def submit(self, instruction="do the thing", **extra):
        return self.client.post(
            f"/api/desktops/{self.desktop}/tasks",
            headers=self.hub,
            json={"instruction": instruction, **extra},
        )

    def settle(self, run_id, timeout=10):
        import time as clock

        deadline = clock.time() + timeout
        while clock.time() < deadline:
            run = self.client.get(
                f"/api/desktops/{self.desktop}/tasks/{run_id}", headers=self.hub
            ).json()
            if run["state"] in TERMINAL:
                return run
            clock.sleep(0.05)
        raise AssertionError(f"the task never finished; it is {run['state']}")

    async def test_a_personal_desktop_takes_no_tasks(self):
        refused = self.submit()
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertIn("not running an agent", refused.json()["detail"])

    async def test_submitting_returns_a_queued_task_rather_than_a_result(self):
        self.make_agent()
        accepted = self.submit()
        self.assertEqual(accepted.status_code, 202, accepted.text)
        run = accepted.json()
        self.assertEqual(run["state"], "queued")
        self.assertEqual(run["instruction"], "do the thing")
        self.settle(run["id"])

    async def test_the_same_request_id_twice_is_one_task(self):
        self.make_agent()
        first = self.submit(clientRequestId="c1").json()
        second = self.submit(clientRequestId="c1").json()
        self.assertEqual(first["id"], second["id"])
        self.settle(first["id"])
        listed = self.client.get(
            f"/api/desktops/{self.desktop}/tasks", headers=self.hub
        ).json()["runs"]
        self.assertEqual(len(listed), 1)

    async def test_the_task_finishes_with_nobody_watching_and_is_there_afterwards(self):
        self.make_agent()
        run = self.submit("read the page").json()
        # No viewer polls, no event stream is open: this is the whole point.
        finished = self.settle(run["id"])
        self.assertEqual(finished["state"], "succeeded", finished["detail"])
        # And a viewer arriving afterwards can read the result and the events.
        events = self.client.get(
            f"/api/desktops/{self.desktop}/events", headers=self.hub
        ).json()
        self.assertEqual(events["events"][0]["kind"], "task.queued")
        self.assertEqual(events["events"][-1]["kind"], "task.succeeded")
        self.assertEqual(events["cursor"], events["latest"])

    async def test_a_viewer_catches_up_from_the_cursor_it_had(self):
        self.make_agent()
        run = self.submit().json()
        self.settle(run["id"])
        everything = self.client.get(
            f"/api/desktops/{self.desktop}/events", headers=self.hub
        ).json()["events"]
        halfway = everything[1]["sequence"]
        rest = self.client.get(
            f"/api/desktops/{self.desktop}/events?after={halfway}", headers=self.hub
        ).json()
        self.assertEqual(
            [event["sequence"] for event in rest["events"]],
            [event["sequence"] for event in everything if event["sequence"] > halfway],
        )
        self.assertFalse(rest["gap"])

    async def test_an_instruction_has_to_be_one(self):
        self.make_agent()
        self.assertEqual(self.submit("").status_code, 422)
        self.assertEqual(self.submit("x" * 5000).status_code, 422)

    async def test_control_refuses_a_transition_that_makes_no_sense(self):
        self.make_agent()
        run = self.submit().json()
        self.settle(run["id"])
        refused = self.client.post(
            f"/api/desktops/{self.desktop}/tasks/{run['id']}/control",
            headers=self.hub,
            json={"action": "pause"},
        )
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(
            self.client.post(
                f"/api/desktops/{self.desktop}/tasks/{run['id']}/control",
                headers=self.hub,
                json={"action": "sabotage"},
            ).status_code,
            422,
        )

    async def test_a_task_belongs_to_its_desktop(self):
        self.make_agent()
        run = self.submit().json()
        self.settle(run["id"])
        other = self.client.post("/api/desktops", headers=self.hub, json={}).json()["id"]
        self.assertEqual(
            self.client.get(
                f"/api/desktops/{other}/tasks/{run['id']}", headers=self.hub
            ).status_code,
            404,
        )

    async def test_deleting_a_desktop_takes_its_tasks_with_it(self):
        self.make_agent()
        run = self.submit().json()
        self.settle(run["id"])
        self.client.post("/api/desktops", headers=self.hub, json={})
        self.assertEqual(
            self.client.delete(f"/api/desktops/{self.desktop}", headers=self.hub).status_code, 200
        )
        self.assertEqual(self.runs.store.list(self.desktop), [])

    async def test_turning_history_off_removes_what_was_kept(self):
        self.make_agent()
        run = self.submit().json()
        self.settle(run["id"])
        self.assertTrue(self.runs.store.list(self.desktop))
        self.assertEqual(
            self.client.patch(
                "/api/settings", headers=self.hub, json={"chat_history": False}
            ).status_code,
            200,
        )
        self.assertEqual(self.runs.store.list(self.desktop), [])
        listed = self.client.get(
            f"/api/desktops/{self.desktop}/tasks", headers=self.hub
        ).json()
        self.assertFalse(listed["keepingHistory"])


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    """One line to the person, and only when it is worth interrupting them."""

    def setUp(self):
        base.ApiBoundaryTests.setUp(self)
        self.desktops = self.client.app.state.desktops
        self.desktop = self.client.get("/api/desktops", headers=self.hub).json()["defaultId"]
        self.desktops.store.set_kind(self.desktop, "agent")
        self.desktops.agent_runtime_for = lambda desktop_id: None
        self.sent = []

        outer = self

        class Recording:
            def __init__(self, configured=True, enabled=True):
                self.configured = configured
                self.enabled = enabled

            def config(self):
                return {
                    "server": "https://ntfy.example" if self.configured else "",
                    "topic": "vela" if self.configured else "",
                    "user": "",
                    "pass": "",
                    "events": {"agent_tasks": self.enabled},
                }

            async def publish(self, title, message, **kwargs):
                outer.sent.append({"title": title, "message": message, **kwargs})
                return {"id": "n1", "accepted_at": "now"}

        self.Recording = Recording

    def tearDown(self):
        base.ApiBoundaryTests.tearDown(self)

    def supervisor(self, script, answers=None, notifier=None):
        temp = tempfile.TemporaryDirectory(prefix="vela-note-")
        self.addCleanup(temp.cleanup)
        store = RunStore(Path(temp.name) / "runs.sqlite")
        supervisor = Supervisor(
            self.desktops,
            store,
            model=ScriptedModel(script),
            tools=RecordingTools(answers),
            notifier=notifier,
        )
        return supervisor, store

    async def settle(self, supervisor, store, instruction="do it"):
        run = await supervisor.submit(self.desktop, instruction)
        for _ in range(200):
            if store.get(run["id"])["state"] in TERMINAL:
                break
            await asyncio.sleep(0.05)
        # The notification is sent from a task of its own, so let it land.
        await asyncio.sleep(0.2)
        return store.get(run["id"])

    FINISH = [("task.finish", {"summary": "Read it", "changed": False})]
    ANSWER = {"task.finish": {"proposed": True, "summary": "Read it", "changed": False, "actions": []}}

    async def test_one_line_when_a_task_ends(self):
        supervisor, store = self.supervisor(self.FINISH, self.ANSWER, notifier=self.Recording())
        await self.settle(supervisor, store)
        self.assertEqual(len(self.sent), 1, self.sent)
        self.assertIn("task finished", self.sent[0]["title"])

    async def test_nothing_is_sent_when_notifications_are_not_set_up(self):
        supervisor, store = self.supervisor(
            self.FINISH, self.ANSWER, notifier=self.Recording(configured=False)
        )
        await self.settle(supervisor, store)
        self.assertEqual(self.sent, [], "a result is not a reason to configure anything")

    async def test_nothing_is_sent_when_the_person_turned_it_off(self):
        supervisor, store = self.supervisor(
            self.FINISH, self.ANSWER, notifier=self.Recording(enabled=False)
        )
        await self.settle(supervisor, store)
        self.assertEqual(self.sent, [])

    async def test_a_task_you_stopped_yourself_does_not_tell_you_about_it(self):
        started = asyncio.Event()

        class Slow(RecordingTools):
            async def call(self, *args, **kwargs):
                started.set()
                await asyncio.sleep(30)

        supervisor, store = self.supervisor(
            [("desktop.observe", {})], notifier=self.Recording()
        )
        supervisor.tools = Slow()
        run = await supervisor.submit(self.desktop, "wait around")
        await asyncio.wait_for(started.wait(), timeout=5)
        await supervisor.control(self.desktop, run["id"], "stop")
        for _ in range(100):
            if store.get(run["id"])["state"] in TERMINAL:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)
        self.assertEqual(self.sent, [], "you already know; you did it")

    async def test_a_delivery_failure_never_affects_the_task(self):
        class Broken(self.Recording):
            async def publish(self, *args, **kwargs):
                raise RuntimeError("the notification server is gone")

        supervisor, store = self.supervisor(self.FINISH, self.ANSWER, notifier=Broken())
        finished = await self.settle(supervisor, store)
        self.assertEqual(finished["state"], "succeeded", finished["detail"])
