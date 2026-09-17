"""The loop that actually does the work, and the server that owns it.

The one structural claim this file makes: **a task belongs to the server, not to
whoever is watching it.** Closing the dashboard, losing the network, switching to
another desktop — none of those touch a run. The HTTP handlers here observe; the
`asyncio.Task` is held by this supervisor and is ended by the person asking for
it to end, by a limit, or by the server stopping.

The loop is the same shape every time: look, ask, act, check. Observe the view,
ask the model for one typed step, validate the name against the declared surface,
dispatch it, and compare what came back with what was claimed. A tool refusing is
a normal part of that — the refusal goes back to the model as an observation with
its reason, because a model that is told "that control is gone, observe again"
can do something useful and a model told nothing will do the same thing again.

Three things end a run besides finishing: a limit, a person, and a question with
no answer. All three end it with a sentence.

What this never does is resume by itself. A server that restarts marks what was
in flight `interrupted` and leaves queued work queued, because the effects a run
has already had cannot be undone by starting it again.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from ..app_storage import AppServiceError
from .approvals import ApprovalPending
from .budget import Budget, BudgetExhausted, STEP_TIMEOUT_SECONDS, clip_output
from .model import ModelError, ModelUnavailable, SYSTEM_PROMPT, parse_call, tool_definitions
from .observations import StalledError
from .tools import TOOLS, TOOL_NAMES, ToolError

#: Runs working at once, across every desktop on this server. Two browsers cost
#: about a gigabyte on the machine this was measured on, so two is the starting
#: number and the one to tune with evidence rather than optimism.
MAX_CONCURRENT_RUNS = 2

#: Model requests in flight at once. Lower than the run limit on purpose: a
#: local model is one process and queueing at Vela is kinder than queueing
#: inside it. Ask is deliberately *not* behind this — a person waiting on a
#: reply must never sit behind an agent's hundredth step.
MAX_CONCURRENT_MODEL_REQUESTS = 1

#: How long a run waits for an approval before giving up on it. The approval
#: itself expires sooner; this is the backstop for one that somehow does not.
APPROVAL_WAIT_SECONDS = 1500

#: How often the run checks whether its pending question has been answered.
APPROVAL_POLL_SECONDS = 1.0

#: Rejected attempts to finish before the run is ended for it. A model that
#: cannot produce a result Vela will stand behind should stop after being told
#: twice, not after spending every step it was given saying the same thing.
MAX_REFUSED_FINISHES = 3


class Supervisor:
    """Every agent run on this server."""

    def __init__(self, desktops, store, *, model, log=None, tools=None, notifier=None):
        self.desktops = desktops
        self.store = store
        self.model = model
        # Optional, and used for one line per transition that matters. A result
        # nobody asked to be told about is not a reason to send a message off
        # this computer.
        self.notifier = notifier
        #: Attached by the run service. Resuming has to know whether a person
        #: still has control, and stopping has to take it back.
        self.leases = None
        self.tools = tools if tools is not None else desktops.tools
        self._log = log or (lambda message: None)
        self._runs: dict[str, asyncio.Task] = {}
        self._control: dict[str, str] = {}
        self._budgets: dict[str, Budget] = {}
        self._slots = asyncio.Semaphore(MAX_CONCURRENT_RUNS)
        self._model_slots = asyncio.Semaphore(MAX_CONCURRENT_MODEL_REQUESTS)
        self._lock = asyncio.Lock()
        self._stopping = False

    # ------------------------------------------------------------ events --

    def emit(self, desktop_id: str, kind: str, payload: dict[str, Any], *, run_id=None) -> dict:
        return self.store.append(desktop_id, kind, payload, run_id=run_id)

    def announce(self, title: str, message: str, *, tags=None, priority: int = 3) -> None:
        """One line to the person, through the channel they already configured.

        Deliberately rare: a message per click or per observation would train
        somebody to ignore the one that mattered. Only a task ending, or one
        that has stopped and needs them, is worth an interruption.

        Nothing leaves this computer that the owner has not set up and left
        switched on, and a delivery failure is never allowed to affect the task.
        """
        if self.notifier is None:
            return
        config = self.notifier.config()
        if not config["server"] or not config["topic"]:
            return
        if config["events"].get("agent_tasks") is False:
            return

        async def send():
            try:
                await self.notifier.publish(
                    title, message, tags=tags or ["robot"], priority=priority, kind="agent"
                )
            except Exception:  # noqa: BLE001 - a notification is never the work
                self._log(f"could not send a task notification: {title}")

        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(send())

    # ------------------------------------------------------- the queue --

    async def submit(self, desktop_id: str, instruction: str, **options) -> dict[str, Any]:
        """Queue a task and make sure something is working on this desktop."""
        run = self.store.submit(desktop_id, instruction, **options)
        if run["state"] == "queued":
            self.emit(
                desktop_id,
                "task.queued",
                {"runId": run["id"], "instruction": run["instruction"]},
                run_id=run["id"],
            )
            await self._pump(desktop_id)
        return run

    async def _pump(self, desktop_id: str) -> None:
        """Start the next queued task, if this desktop is free and so is a slot."""
        async with self._lock:
            if self._stopping:
                return
            if self.store.active(desktop_id) is not None:
                return  # One task at a time per desktop. That is the whole rule.
            queued = self.store.next_queued(desktop_id)
            if queued is None:
                return
            self.store.update(queued["id"], state="starting")
            task = asyncio.create_task(self._carry(queued["id"], desktop_id))
            self._runs[queued["id"]] = task
            task.add_done_callback(lambda _t, run_id=queued["id"]: self._runs.pop(run_id, None))

    # ----------------------------------------------------------- control --

    async def control(self, desktop_id: str, run_id: str, action: str) -> dict[str, Any]:
        """Pause, resume, stop, or cancel something still in the queue."""
        run = self.store.get(run_id)
        if run["desktopId"] != desktop_id:
            raise AppServiceError(404, "That task is not on this desktop.")
        if action not in ("pause", "resume", "stop"):
            raise AppServiceError(422, "A task is paused, resumed or stopped.")

        if action == "stop":
            if run["state"] == "queued":
                # Nothing was ever started, so nothing has to be unwound.
                updated = self.store.update(
                    run_id, state="cancelled", detail="You cancelled this before it started."
                )
                self.emit(desktop_id, "task.cancelled", {"runId": run_id}, run_id=run_id)
                return updated
            self._control[run_id] = "stop"
            task = self._runs.get(run_id)
            if task:
                task.cancel()
            # Dispatch stops now; what already committed stays committed, and an
            # external result nobody could confirm stays unknown.
            self.desktops.revoke_run(desktop_id, run_id, reason="you stopped this task")
            if self.leases is not None:
                self.leases.drop(desktop_id)
            return self.store.get(run_id)

        if action == "pause":
            if run["state"] not in ("running", "starting"):
                raise AppServiceError(409, "That task is not running.")
            self._control[run_id] = "pause"
            return self.store.get(run_id)

        if run["state"] != "paused":
            raise AppServiceError(409, "That task is not paused.")
        if self.leases is not None and self.leases.holder(desktop_id):
            raise AppServiceError(
                409,
                "Somebody still has control of this desktop. Give it back before the "
                "task carries on.",
            )
        self._control.pop(run_id, None)
        # A new generation on the way back in, so every observation the run was
        # holding is invalid and it has to look again before it can act. What it
        # remembers is not what a person left on the screen.
        if self.leases is not None:
            with contextlib.suppress(Exception):
                await self.desktops.runtime.command(
                    "control.take", desktopId=desktop_id, timeout=15.0
                )
        self.store.update(run_id, state="running")
        self.emit(desktop_id, "task.resumed", {"runId": run_id}, run_id=run_id)
        return self.store.get(run_id)

    def budget_of(self, run_id: str) -> dict[str, Any] | None:
        budget = self._budgets.get(run_id)
        return budget.as_dict() if budget else None

    async def stop_all(self) -> None:
        """Called when Vela stops. Nothing is left running behind the process."""
        self._stopping = True
        tasks = list(self._runs.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # --------------------------------------------------------- the run --

    async def _carry(self, run_id: str, desktop_id: str) -> None:
        """One task, from starting to a terminal state, whatever happens."""
        budget = Budget.from_policy(self.desktops.store.policy(desktop_id))
        self._budgets[run_id] = budget
        try:
            async with self._slots:
                await self._execute(run_id, desktop_id, budget)
        except asyncio.CancelledError:
            reason = self._control.get(run_id)
            self._finish(
                run_id,
                desktop_id,
                "cancelled" if reason == "stop" else "interrupted",
                detail=(
                    "You stopped this task. Anything it had already done stays done."
                    if reason == "stop"
                    else "This task was interrupted."
                ),
                outcome="not_dispatched",
                budget=budget,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - a run must always end somewhere
            self._log(f"agent run {run_id} failed: {exc}")
            self._finish(
                run_id,
                desktop_id,
                "failed",
                detail=f"This task stopped because of an unexpected problem: {exc}",
                outcome="failed_before_commit",
                budget=budget,
            )
        finally:
            self._control.pop(run_id, None)
            self.desktops.tools.log.forget_run(desktop_id, run_id)
            # Whatever happened here, the next queued task on this desktop gets
            # its turn rather than waiting for somebody to notice.
            if not self._stopping:
                with contextlib.suppress(Exception):
                    await self._pump(desktop_id)

    async def _execute(self, run_id: str, desktop_id: str, budget: Budget) -> None:
        run = self.store.get(run_id)
        self.emit(desktop_id, "task.starting", {"runId": run_id}, run_id=run_id)

        # Everything that could make this task impossible is checked before any
        # window opens or any effect is possible.
        try:
            self.desktops.agent_runtime_for(desktop_id)
        except Exception as exc:  # noqa: BLE001 - reported, not raised onward
            return self._finish(
                run_id, desktop_id, "failed",
                detail=getattr(exc, "detail", str(exc)),
                outcome="not_dispatched", budget=budget,
            )
        model_name = run["model"] or self.model.default_model()
        try:
            capabilities = await self.model.capabilities(model_name)
            self.model.require_usable(capabilities)
        except ModelUnavailable as exc:
            return self._finish(
                run_id, desktop_id, "failed", detail=str(exc),
                outcome="not_dispatched", budget=budget,
            )

        self.store.update(run_id, state="running", model=model_name, profile=capabilities)
        self.emit(
            desktop_id, "task.running",
            {"runId": run_id, "model": model_name, "vision": capabilities.get("vision")},
            run_id=run_id,
        )
        budget.resume()

        definitions = tool_definitions(TOOLS)
        refused_finishes = 0
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            # What this desktop actually has. Not decoration: a real evaluation
            # had a model ask for "Notes" when the id is "notes", then guess at a
            # website when that was refused. Telling it what exists up front is
            # cheaper than letting it find out by being refused twice.
            {"role": "user", "content": self._what_is_here(desktop_id)},
            {"role": "user", "content": run["instruction"]},
        ]

        while True:
            if self._control.get(run_id) == "stop":
                raise asyncio.CancelledError()
            if self._control.get(run_id) == "pause":
                await self._hold_paused(run_id, desktop_id, budget)
                if self._control.get(run_id) == "stop":
                    raise asyncio.CancelledError()

            try:
                budget.check()
            except BudgetExhausted as exc:
                return self._finish(
                    run_id, desktop_id, "failed", detail=exc.detail,
                    outcome="not_dispatched", budget=budget, limit=exc.limit,
                )

            try:
                budget.spend_model_request()
                async with self._model_slots:
                    answer = await self.model.propose(
                        model=model_name, messages=messages, tools=definitions
                    )
            except ModelError as exc:
                return self._finish(
                    run_id, desktop_id, "failed",
                    detail=f"The model stopped working on this task: {exc}",
                    outcome="failed_before_commit", budget=budget,
                )

            if not answer["calls"]:
                # Prose where a tool call belongs. Said once, plainly, and
                # counted — a model that keeps doing it runs out of requests
                # rather than filling a transcript forever.
                messages.append({"role": "assistant", "content": answer["content"]})
                messages.append(
                    {
                        "role": "user",
                        "content": "Call one of your tools. Answering in words does nothing.",
                    }
                )
                budget.spend_step(progressed=False)
                continue

            name, arguments = parse_call(answer["calls"][0])
            messages.append({"role": "assistant", "content": answer["content"], "tool_calls": answer["calls"][:1]})

            if name not in TOOL_NAMES:
                messages.append(_tool_message(name, {"error": "unknown_tool", "detail": f"There is no tool called {name}."}))
                budget.spend_step(progressed=False)
                continue

            self.emit(
                desktop_id, "step.started",
                {"runId": run_id, "tool": name, "step": budget.steps_used + 1},
                run_id=run_id,
            )

            outcome = await self._dispatch(run_id, desktop_id, name, arguments, budget)
            messages.append(_tool_message(name, outcome["message"]))
            budget.spend_step(progressed=outcome["progressed"])
            self.emit(
                desktop_id, "step.finished",
                {
                    "runId": run_id,
                    "tool": name,
                    "ok": outcome["ok"],
                    "detail": outcome.get("detail"),
                    "budget": budget.as_dict(),
                },
                run_id=run_id,
            )

            if name == "task.finish" and not outcome["ok"]:
                refused_finishes += 1
                if refused_finishes >= MAX_REFUSED_FINISHES:
                    return self._finish(
                        run_id, desktop_id, "failed",
                        detail=(
                            "This task could not produce a result Vela could stand "
                            "behind. " + (outcome.get("detail") or "")
                        ).strip(),
                        outcome="not_dispatched", budget=budget,
                    )

            if outcome.get("terminal"):
                return self._finish(
                    run_id, desktop_id, outcome["terminal"],
                    detail=outcome.get("detail"),
                    result=outcome.get("result"),
                    outcome=outcome.get("effect_outcome", "committed"),
                    budget=budget,
                )

    def _what_is_here(self, desktop_id: str) -> str:
        """The desktop's own contents, in the model's first message.

        The owner's policy and the windows that are open — facts Vela knows,
        which is what makes them safe to state plainly. Nothing from a page goes
        in here.
        """
        policy = self.desktops.store.policy(desktop_id)
        apps = policy.get("apps") or []
        sites = [rule["origin"] for rule in policy.get("sites") or []]
        lines = ["This desktop is set up as follows."]
        lines.append(
            "Apps you may open, by id: " + (", ".join(apps) if apps else "none") + "."
        )
        lines.append(
            "Websites you may open: " + (", ".join(sites) if sites else "none") + "."
        )
        try:
            views = self.desktops.views(desktop_id)["views"]
        except Exception:  # noqa: BLE001 - a listing failure is not the run's problem
            views = []
        open_now = [
            f"{view['id']} ({view.get('appId') or view.get('url') or view['kind']})"
            for view in views
            if view.get("agentViewable")
        ]
        lines.append(
            "Windows already open: " + (", ".join(open_now) if open_now else "none") + "."
        )
        if policy.get("approvals") == "ask":
            lines.append(
                "Changes to app data need the owner's approval, which you will be told "
                "about and which may take a while. That is normal."
            )
        return "\n".join(lines)

    # -------------------------------------------------------- dispatching --

    async def _dispatch(self, run_id, desktop_id, name, arguments, budget) -> dict[str, Any]:
        """One tool call, with everything that can go wrong turned into an answer."""
        try:
            result = await asyncio.wait_for(
                self.tools.call(desktop_id, name, arguments, run_id=run_id),
                timeout=STEP_TIMEOUT_SECONDS,
            )
        except ApprovalPending as pending:
            answered = await self._wait_for_approval(run_id, desktop_id, pending.record, budget)
            if answered["state"] == "approved":
                # The same call again, now with a grant behind it. Not a new
                # decision — the same one, finally allowed to happen.
                return await self._dispatch(run_id, desktop_id, name, arguments, budget)
            return {
                "ok": False,
                "progressed": False,
                "detail": answered["detail"],
                "message": {"error": "not_approved", "detail": answered["detail"]},
            }
        except ToolError as exc:
            return {
                "ok": False,
                "progressed": False,
                "detail": exc.detail,
                "message": exc.as_dict(),
            }
        except StalledError as exc:
            return {"ok": False, "progressed": False, "detail": exc.detail,
                    "message": {"error": "no_progress", "detail": exc.detail}}
        except asyncio.TimeoutError:
            detail = f"{name} did not finish within {STEP_TIMEOUT_SECONDS} seconds."
            return {"ok": False, "progressed": False, "detail": detail,
                    "message": {"error": "timed_out", "detail": detail}}
        except AppServiceError as exc:
            return {"ok": False, "progressed": False, "detail": exc.detail,
                    "message": {"error": "failed", "detail": exc.detail}}

        if name == "task.finish":
            verdict = self._verify(desktop_id, run_id, result)
            return {
                "ok": verdict["accepted"],
                "progressed": True,
                "detail": verdict["detail"],
                "result": verdict["result"],
                "message": {"accepted": verdict["accepted"], "detail": verdict["detail"]},
                "terminal": "succeeded" if verdict["accepted"] else None,
                "effect_outcome": "committed" if verdict["accepted"] else "not_dispatched",
            }

        return {
            "ok": True,
            # Observing is not progress on its own: a run that looks at the same
            # unchanging window forever is a run getting nowhere, and the
            # fruitless-step count is what notices.
            "progressed": name != "desktop.observe" or bool(result.get("repeats", 1) == 1),
            "message": clip_output(result),
        }

    async def _wait_for_approval(self, run_id, desktop_id, record, budget) -> dict[str, Any]:
        """Hold the run at the boundary while somebody decides.

        The budget's clock stops. Waiting for a person is not the task's time to
        spend, and charging it would mean a slow human ends a task. The model
        slot is released by leaving the request, so another desktop and ordinary
        Ask keep working while this one waits.
        """
        self.store.update(run_id, state="waiting_approval")
        self.emit(
            desktop_id, "approval.waiting",
            {
                "runId": run_id,
                "requestId": record["requestId"],
                "summary": record["summary"],
                "expiresAt": record["expiresAt"],
            },
            run_id=run_id,
        )
        budget.suspend()
        # Waiting for a person is worth telling the person about. The message
        # names the desktop and the change; resolving it is still only possible
        # in Vela's own controls.
        with contextlib.suppress(Exception):
            name = self.desktops.store.get(desktop_id)["name"]
            self.announce(
                f"{name}: a change needs you",
                (record["summary"] or {}).get("headline", "")[:400],
                tags=["question"],
                priority=4,
            )
        deadline = time.monotonic() + APPROVAL_WAIT_SECONDS
        try:
            while time.monotonic() < deadline:
                if self._control.get(run_id) == "stop":
                    raise asyncio.CancelledError()
                await asyncio.sleep(APPROVAL_POLL_SECONDS)
                try:
                    state = self.desktops.approvals.get(
                        record["requestId"], desktop_id=desktop_id
                    )
                except AppServiceError:
                    detail = "That request is no longer waiting for an answer."
                    self.emit(desktop_id, "approval.resolved",
                              {"runId": run_id, "requestId": record["requestId"], "state": "gone"},
                              run_id=run_id)
                    return {"state": "gone", "detail": detail}
                if state["state"] == "pending":
                    continue
                self.emit(
                    desktop_id, "approval.resolved",
                    {"runId": run_id, "requestId": record["requestId"], "state": state["state"]},
                    run_id=run_id,
                )
                return {
                    "state": state["state"],
                    "detail": _approval_detail(state),
                }
            return {"state": "expired", "detail": "Nobody answered this in time."}
        finally:
            self.store.update(run_id, state="running")
            budget.resume()

    async def _hold_paused(self, run_id, desktop_id, budget) -> None:
        """Wait here until somebody resumes or stops it."""
        self.store.update(run_id, state="paused")
        self.emit(desktop_id, "task.paused", {"runId": run_id}, run_id=run_id)
        budget.suspend()
        try:
            while self._control.get(run_id) == "pause":
                await asyncio.sleep(0.2)
        finally:
            budget.resume()

    # --------------------------------------------------------- finishing --

    def _verify(self, desktop_id: str, run_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        """Whether what the run claims matches what it actually did.

        A qualitative summary — "I read the page and here is what it said" — is a
        fine result and is accepted as one. What must never happen is a result
        that *implies* a write that did not occur.

        The check is against a structured claim, not against the words. An
        earlier version looked for "created", "saved" and the like in the
        summary, and a real evaluation run showed exactly why that is wrong: a
        model reporting "no notes have been created" was refused for a change it
        was explicitly saying it had not made, and then repeated itself until the
        step budget ended the task. Prose is not evidence and it is not a claim
        either. `changed` is the claim; the receipts are the evidence; and
        whatever the summary says, `changed` in the stored result comes from the
        receipts rather than from the model.
        """
        evidence = proposal.get("actions") or []
        committed = [
            record
            for record in evidence
            if record.get("outcome") == "committed"
            and record.get("tool")
            in ("app.invoke_action", "desktop.click", "desktop.type", "desktop.keypress")
        ]
        claimed = proposal.get("changed")
        if claimed is True and not committed:
            return {
                "accepted": False,
                "detail": (
                    "You say this changed something, but no tool result confirms one. "
                    "Either make the change, or finish with changed=false and describe "
                    "what you found."
                ),
                "result": None,
            }
        return {
            "accepted": True,
            "detail": "Finished.",
            "result": {
                "summary": proposal.get("summary") or "",
                "evidence": proposal.get("evidence") or [],
                "actions": evidence,
                # From the receipts, always. A summary that reads like a change
                # sits beside a `changed` that says otherwise, and the interface
                # shows the second.
                "changed": bool(committed),
                "claimedChange": bool(claimed),
            },
        }

    def _finish(self, run_id, desktop_id, state, *, detail=None, result=None, outcome=None,
                budget=None, limit=None) -> dict[str, Any]:
        with contextlib.suppress(AppServiceError):
            self.store.update(
                run_id,
                state=state,
                detail=detail,
                result=result,
                outcome=outcome,
                budget=budget.as_dict() if budget else None,
            )
        self.emit(
            desktop_id,
            f"task.{state}",
            {
                "runId": run_id,
                "detail": detail,
                "limit": limit,
                "budget": budget.as_dict() if budget else None,
            },
            run_id=run_id,
        )
        # One line, once, for a transition somebody would want to know about.
        # Not for every step, and not for a task they cancelled themselves.
        if state != "cancelled":
            name = self.desktops.store.get(desktop_id)["name"]
            with contextlib.suppress(Exception):
                self.announce(
                    f"{name}: {'task finished' if state == 'succeeded' else 'task stopped'}",
                    (detail or (result or {}).get("summary") or "")[:400],
                    priority=3 if state == "succeeded" else 4,
                )
        # Whatever this run was allowed to do, it is not allowed to do any more.
        with contextlib.suppress(Exception):
            self.desktops.revoke_run(desktop_id, run_id, reason="the task ended")
        self._budgets.pop(run_id, None)
        return self.store.get(run_id)


def _tool_message(name: str, payload: Any) -> dict[str, Any]:
    import json

    return {
        "role": "tool",
        "name": name,
        "content": json.dumps(payload, ensure_ascii=False, default=str)[:24_000],
    }


def _approval_detail(state: dict[str, Any]) -> str:
    if state["state"] == "denied":
        return "The owner did not approve this change."
    if state["state"] == "expired":
        return "Nobody answered this in time, so nothing changed."
    return f"This change was cancelled: {state.get('reason') or 'it is no longer waiting'}."
