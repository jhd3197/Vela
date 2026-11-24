"""Runs a controller drives one step at a time.

Vela's own loop decides by asking a model; a driven run decides by asking the
controller. Everything the decision *triggers* is the same: one call at a time
through the same tool surface, the same argument checks, the same site policy,
the same approval boundary, the same effect receipts, the same budgets, and the
same stop when a person takes the wheel. The controller is a mind in front of
the same hands, nothing more.

Three things shape this file.

**The controller is not trusted with anything the model is not.** It receives
refusals, not stack traces; it waits for approvals like a run waits; a person
taking over stops it exactly as a run stops. The one thing it adds is the
decision itself, and the decision was never the part Vela enforces.

**Every step answers one of four ways,** and the HTTP call that carries it is
idempotent so a retried call never repeats an effect and reads back what the
first call recorded: `result` for a call that ran, `refusal` for one that was
refused (with a code, a sentence, and whether looking again is worth
anything), `approval` for a change now waiting on the owner, and
`needsPerson` for a task that has handed itself back. An approval
settles when the step is read back: the read returns the pending approval
while the owner decides, and the first read after the answer settles the step
— re-executing the exact call if the answer was yes, with the grant behind it.
There is deliberately no background task in any of this: the protocol is a
request and its follow-up read, so nothing about it depends on what outlives
the call that carries it.

**Nothing here outlives the process.** Steps and their answers are held in
memory; a restart marks the run interrupted like any other run that was in
flight, and the controller learns that from the run record, not from a step.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from ..app_storage import AppServiceError
from ..desktops.models import DesktopError, validate_id
from .approvals import ApprovalPending
from .budget import Budget, BudgetExhausted, STEP_TIMEOUT_SECONDS, clip_output
from .model import tool_definitions
from .observations import StalledError
from .store import MAX_CLIENT_REQUEST_ID, TERMINAL
from .supervisor import MAX_CONCURRENT_RUNS, _approval_detail
from .tools import ToolError, tools_for

#: A controller's own id for one step, so a retried POST is the same step and
#: not a second effect. Same bound as a task submission's request id.
MAX_CLIENT_STEP_ID = MAX_CLIENT_REQUEST_ID

#: Longest finish summary kept on the run record.
MAX_SUMMARY = 2000


class Driver:
    """The driven-run surface: create, step, finish, cancel, read back.

    Holds no authority of its own. Slots, budgets, events, approvals, control
    leases and finishing all belong to the supervisor and the stores this
    shares with the model loop; what this file adds is the step protocol on
    top of them.
    """

    def __init__(self, desktops, store, supervisor):
        self.desktops = desktops
        self.store = store
        self.supervisor = supervisor
        #: run id -> lock serialising that run's steps. One tool call at a time
        #: is the whole protocol, for the driver as it is for the loop.
        self._locks: dict[str, asyncio.Lock] = {}
        #: run id -> client step id -> the step record, what idempotent retries
        #: read and what a settled step is read back from.
        self._steps: dict[str, dict[str, dict[str, Any]]] = {}
        #: run ids holding one of the shared run slots.
        self._slotted: set[str] = set()

    # ------------------------------------------------------------ creating --

    async def create(self, desktop_id: str, request: Any) -> dict[str, Any]:
        """Start one driven run, or return the one this request id already started."""
        desktop_id = validate_id(desktop_id)
        request = request if isinstance(request, dict) else {}
        title = request.get("title")
        if not isinstance(title, str) or not title.strip():
            raise AppServiceError(422, "A driven run needs a title.")
        instruction = request.get("instruction")
        client_request_id = request.get("clientRequestId")
        if client_request_id is not None and (
            not isinstance(client_request_id, str)
            or not client_request_id
            or len(client_request_id) > MAX_CLIENT_REQUEST_ID
        ):
            raise AppServiceError(422, "That is not a valid request id.")
        desktop = self.desktops.store.get(desktop_id)
        if desktop["kind"] != "agent":
            raise DesktopError(409, "This desktop is not running an agent.")
        # One active run per desktop and a shared slot, decided under the same
        # lock that guards starting any other run, so the two cannot race.
        async with self.supervisor._lock:
            if client_request_id:
                existing = self.store.find_by_request(desktop_id, client_request_id)
                if existing is not None:
                    if existing["kind"] != "driven":
                        raise AppServiceError(409, "That request id belongs to a different task.")
                    # A retried create answers with the run it first made, even
                    # after that run has ended: the retry must not start a
                    # second run beside the first.
                    return self._describe(existing)
            if self.store.active(desktop_id) is not None:
                raise AppServiceError(409, "This desktop is already carrying a task.")
            # Refuse before anything is written down, the way the loop refuses
            # a run whose desktop has no browser behind it.
            self.desktops.agent_runtime_for(desktop_id)
            if self.supervisor._slots.locked():
                raise AppServiceError(
                    429,
                    f"Vela runs {MAX_CONCURRENT_RUNS} agent desktops at once, and both "
                    "are busy. Start this when one finishes.",
                )
            await self.supervisor._slots.acquire()
            try:
                run = self.store.create_driven(
                    desktop_id,
                    title=title.strip(),
                    instruction=instruction if isinstance(instruction, str) else "",
                    client_request_id=client_request_id,
                )
            except Exception:
                self.supervisor._slots.release()
                raise
        self._slotted.add(run["id"])
        budget = Budget.from_policy(self.desktops.store.policy(desktop_id))
        self.supervisor._budgets[run["id"]] = budget
        budget.resume()
        # Never queued, never "starting" on a workspace wait: the run exists
        # because the controller is ready to send steps.
        self.supervisor.emit(desktop_id, "task.starting", {"runId": run["id"]}, run_id=run["id"])
        self.supervisor.emit(
            desktop_id, "task.running", {"runId": run["id"], "driver": True}, run_id=run["id"]
        )
        return self._describe(run)

    def tools_catalog(self, desktop_id: str) -> dict[str, Any]:
        """The tool surface as the controller can register it: names, descriptions, JSON schemas."""
        desktop_id = validate_id(desktop_id)
        desktop = self.desktops.store.get(desktop_id)
        if desktop["kind"] != "agent":
            raise DesktopError(409, "This desktop is not running an agent.")
        # The same `tool_definitions` the model loop builds its surface from,
        # so the catalogue a controller registers can never drift from what
        # `AgentTools` will actually accept — workspace and terminal tools
        # included, which exist only where their services do.
        return {"tools": tool_definitions(tools_for(self.desktops.terminals, self.desktops.editors))}

    # ------------------------------------------------------------- stepping --

    async def step(self, desktop_id: str, run_id: str, request: Any) -> dict[str, Any]:
        """Execute exactly one tool call for a driven run.

        The answer is one of `result`, `refusal`, `approval` or `needsPerson`,
        and the same client step id always reads back the recorded answer —
        budget included — without the call happening twice.
        """
        desktop_id = validate_id(desktop_id)
        self._driven_run(desktop_id, run_id)
        request = request if isinstance(request, dict) else {}
        name = request.get("tool")
        arguments = request.get("arguments")
        client_step_id = request.get("clientStepId")
        if not isinstance(client_step_id, str) or not client_step_id:
            raise AppServiceError(422, "A step needs the caller's own step id.")
        if len(client_step_id) > MAX_CLIENT_STEP_ID:
            raise AppServiceError(422, "That is not a valid step id.")
        if not isinstance(name, str) or not name:
            raise AppServiceError(422, "A step names the tool it calls.")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise AppServiceError(422, "A step's arguments are an object.")
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            run = self.store.get(run_id)
            known = self._steps.setdefault(run_id, {})
            record = known.get(client_step_id)
            if record is not None:
                # The retried POST for a step already taken: the recorded
                # answer, never a second execution of the call — and the
                # budget recorded with that answer, not the run's live
                # counters, so the same step never answers differently twice.
                if record["tool"] != name or record["arguments"] != arguments:
                    raise AppServiceError(409, "That step id was already used for a different call.")
                budget = record.get("budget") or self._budget(run).as_dict()
                return {**self._read(record), "budget": budget}
            if run["state"] in TERMINAL:
                raise AppServiceError(409, f"That task already {run['state'].replace('_', ' ')}.")
            if run["state"] == "waiting_approval":
                raise AppServiceError(
                    409,
                    "That task is waiting for an answer to its pending change. "
                    "Read that step back before sending another.",
                )
            refusal = self._held_refusal(desktop_id, run)
            record = {
                "clientStepId": client_step_id,
                "tool": name,
                "arguments": arguments,
                "status": "executing",
            }
            known[client_step_id] = record
            if refusal is not None:
                record.update(
                    status="settled",
                    refusal=refusal["refusal"],
                    budget=self._budget(run).as_dict(),
                )
                return {**self._read(record), "budget": record["budget"]}
            budget = self._budget(run)
            try:
                budget.check()
            except BudgetExhausted as exc:
                # The same ending the loop gives a run that spent its budget:
                # failed, with the limit it reached said plainly.
                self.supervisor._finish(
                    run_id,
                    desktop_id,
                    "failed",
                    detail=exc.detail,
                    outcome="not_dispatched",
                    budget=budget,
                    limit=exc.limit,
                )
                self._release(desktop_id, run_id)
                await self.supervisor.after_run_ended(desktop_id, run_id)
                raise AppServiceError(409, f"That task ended: {exc.detail}")
            await self._execute(desktop_id, run, name, arguments, budget, record)
            return {**self._read(record), "budget": record["budget"]}

    async def _execute(
        self,
        desktop_id: str,
        run: dict[str, Any],
        name: str,
        arguments: dict[str, Any],
        budget: Budget,
        record: dict[str, Any],
        *,
        allow_approval: bool = True,
    ) -> dict[str, Any]:
        """One call through the same tool surface and checks the loop uses.

        Every refusal comes back in the step vocabulary — a code, a sentence,
        whether looking again is worth anything — because that is what the
        controller, like the model, can act on. `ApprovalPending` travels up
        only when this is the first attempt; a later read settles the step.
        """
        run_id = run["id"]
        self.supervisor.emit(
            desktop_id,
            "step.started",
            {"runId": run_id, "tool": name, "step": budget.steps_used + 1},
            run_id=run_id,
        )
        refusal: dict[str, Any] | None = None
        result: dict[str, Any] | None = None
        try:
            result = await asyncio.wait_for(
                self.supervisor.tools.call(desktop_id, name, arguments, run_id=run_id),
                timeout=STEP_TIMEOUT_SECONDS,
            )
        except ApprovalPending as pending:
            if allow_approval:
                return await self._await_approval(desktop_id, run, budget, pending, record)
            refusal = {
                "code": "not_approved",
                "detail": "That change needs a new approval before it can happen.",
                "retryable": False,
            }
        except ToolError as exc:
            refusal = {"code": exc.code, "detail": exc.detail, "retryable": exc.retryable}
        except StalledError as exc:
            refusal = {"code": "no_progress", "detail": exc.detail, "retryable": False}
        except asyncio.TimeoutError:
            refusal = {
                "code": "timed_out",
                "detail": f"{name} did not finish within {STEP_TIMEOUT_SECONDS} seconds.",
                "retryable": True,
            }
        except AppServiceError as exc:
            refusal = {"code": "failed", "detail": exc.detail, "retryable": False}
        except Exception as exc:  # noqa: BLE001 - a step always answers something
            self.supervisor._log(f"driven step {run_id} ({name}) failed: {exc}")
            refusal = {
                "code": "failed",
                "detail": f"That call stopped because of an unexpected problem: {exc}",
                "retryable": False,
            }
        if refusal is not None:
            body: dict[str, Any] = {"refusal": refusal}
            progressed = False
        else:
            body, progressed = self._settled_body(desktop_id, run_id, name, result or {})
        self._settle(desktop_id, run_id, name, budget, record, body, progressed=progressed)
        return body

    def _settled_body(
        self, desktop_id: str, run_id: str, name: str, result: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        """The step answer for a call that returned, and whether it moved the run."""
        if name == "task.needs_person":
            # Not a failure and not a result: the task holds where it is until
            # a person says carry on, which is the same hold a pause takes.
            self.supervisor._control[run_id] = "pause"
            self.store.update(run_id, state="paused")
            self.supervisor.emit(desktop_id, "task.paused", {"runId": run_id}, run_id=run_id)
            self.supervisor.emit(
                desktop_id,
                "task.attention",
                {
                    "runId": run_id,
                    "reason": result["reason"],
                    "detail": result["detail"],
                    "viewId": result.get("viewId"),
                },
                run_id=run_id,
            )
            with contextlib.suppress(Exception):
                workspace = self.desktops.store.get(desktop_id)["name"]
                self.supervisor.announce(
                    f"{workspace}: this needs you",
                    result["detail"][:400],
                    tags=["question"],
                    priority=4,
                )
            return (
                {
                    "needsPerson": {
                        "reason": result["reason"],
                        "detail": result["detail"],
                        "viewId": result.get("viewId"),
                    }
                },
                True,
            )
        # Things that happened around the step: a download that finished, a
        # submission held at the boundary, a dialog the page opened. The
        # controller is told in the result; the person is told here.
        for happening in result.get("happened") or []:
            if happening.get("attention"):
                self.supervisor.emit(
                    desktop_id,
                    "task.attention",
                    {
                        "runId": run_id,
                        "reason": "blocked",
                        "detail": happening["detail"],
                        "source": happening["attention"],
                    },
                    run_id=run_id,
                )
            elif happening.get("file"):
                self.supervisor.emit(
                    desktop_id, "task.file", {"runId": run_id, **happening["file"]}, run_id=run_id
                )
        return (
            {"result": clip_output(result)},
            name != "desktop.observe" or result.get("repeats", 1) == 1,
        )

    def _settle(
        self,
        desktop_id: str,
        run_id: str,
        name: str,
        budget: Budget,
        record: dict[str, Any],
        body: dict[str, Any],
        *,
        progressed: bool,
    ) -> None:
        """The event, the budget and the record for a step that has its answer."""
        detail = (body.get("refusal") or {}).get("detail")
        self.supervisor.emit(
            desktop_id,
            "step.finished",
            {
                "runId": run_id,
                "tool": name,
                "ok": "result" in body,
                **({"detail": detail} if detail else {}),
                "budget": budget.as_dict(),
            },
            run_id=run_id,
        )
        budget.spend_step(progressed=progressed)
        # The budget is recorded with the answer: what the run had spent once
        # this step was counted is part of what a retried POST reads back.
        record.update(status="settled", budget=budget.as_dict(), **body)

    # ------------------------------------------------------------ approvals --

    async def _await_approval(
        self,
        desktop_id: str,
        run: dict[str, Any],
        budget: Budget,
        pending: ApprovalPending,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        """Hold the step at the boundary. Reading the step back settles it.

        There is no task waiting in the background on purpose: the hold is a
        record with a pending answer, and the follow-up read is what advances
        it, so the protocol is two ordinary requests and nothing that can be
        orphaned by the call that set it up ending.
        """
        run_id = run["id"]
        public = {
            "requestId": pending.record["requestId"],
            # The summary is the prompt's own words, built where values that
            # look like secrets are already described by their shape. It passes
            # through here untouched, and no value ever rides along.
            "summary": pending.record["summary"],
            "expiresAt": pending.record["expiresAt"],
        }
        # The state moves now, in the caller's lock, so a step that arrives
        # while the change waits sees "waiting" and is refused rather than
        # executing beside it.
        self.store.update(run_id, state="waiting_approval")
        self.supervisor.emit(
            desktop_id,
            "approval.waiting",
            {
                "runId": run_id,
                "requestId": public["requestId"],
                "summary": public["summary"],
                "expiresAt": public["expiresAt"],
            },
            run_id=run_id,
        )
        budget.suspend()
        # Waiting for a person is worth telling the person about, in the same
        # words the loop uses.
        with contextlib.suppress(Exception):
            name = self.desktops.store.get(desktop_id)["name"]
            self.supervisor.announce(
                f"{name}: a change needs you",
                (public["summary"] or {}).get("headline", "")[:400],
                tags=["question"],
                priority=4,
            )
        # The budget was suspended above, so the counters recorded here hold
        # still while the change waits: a retried POST reads back the same
        # answer the first call got.
        record.update(
            status="waiting_approval",
            approval=public,
            observation_spent=getattr(pending, "observation_spent", False),
            budget=budget.as_dict(),
        )
        return {"approval": public}

    async def read_step(self, desktop_id: str, run_id: str, client_step_id: str) -> dict[str, Any]:
        """One step's answer: its approval while waiting, its settled result after.

        Reading a waiting step is the settling call: the owner answers through
        the ordinary approvals route, and the first read after that answer
        re-executes the exact call with the grant behind it, or settles the
        step as the refusal the answer was.
        """
        desktop_id = validate_id(desktop_id)
        self._driven_run(desktop_id, run_id)
        record = self._steps.get(run_id, {}).get(client_step_id)
        if record is None:
            raise AppServiceError(404, "There is no step by that id on this task.")
        if record["status"] != "waiting_approval":
            return self._read(record)
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            if record["status"] != "waiting_approval":
                return self._read(record)
            run = self.store.get(run_id)
            if run["state"] in TERMINAL:
                record.update(
                    status="settled",
                    refusal={
                        "code": "cancelled",
                        "detail": "This task was stopped while that change waited for an answer.",
                        "retryable": False,
                    },
                )
                return self._read(record)
            request_id = record["approval"]["requestId"]
            try:
                answer = self.desktops.approvals.get(request_id, desktop_id=desktop_id)
            except AppServiceError:
                answer = {"state": "gone", "reason": None}
            if answer["state"] == "pending":
                return self._read(record)
            budget = self._budget(run)
            self.supervisor.emit(
                desktop_id,
                "approval.resolved",
                {"runId": run_id, "requestId": request_id, "state": answer["state"]},
                run_id=run_id,
            )
            budget.resume()
            self.store.update(run_id, state="running")
            if answer["state"] != "approved":
                code = "not_approved" if answer["state"] == "denied" else "expired"
                body: dict[str, Any] = {
                    "refusal": {
                        "code": code,
                        "detail": _approval_detail(answer),
                        "retryable": False,
                    }
                }
                self._settle(desktop_id, run_id, record["tool"], budget, record, body,
                             progressed=False)
                return self._read(record)
            if record.get("observation_spent"):
                # The click that asked already touched the page, so repeating
                # the exact call would act on a screen that has moved. The
                # controller looks again and sends a fresh call.
                body = {
                    "result": {
                        "approved": True,
                        "detail": "That is allowed now. Look at the window again and do it.",
                    }
                }
                self._settle(desktop_id, run_id, record["tool"], budget, record, body,
                             progressed=True)
                return self._read(record)
            # The same call again, now with a grant behind it — not a new
            # decision, the same one finally allowed to happen.
            await self._execute(
                desktop_id, self.store.get(run_id), record["tool"], record["arguments"],
                budget, record, allow_approval=False,
            )
            return self._read(record)

    # ----------------------------------------------------------- finishing --

    async def finish(
        self, desktop_id: str, run_id: str, outcome: str, summary: str
    ) -> dict[str, Any]:
        """The controller's declaration of how the run ended.

        Succeeded is checked against the same "something went out and nobody
        saw the answer" rule the loop checks a claimed success against: that
        ending is `outcome_unknown`, not success, because saying a thing
        succeeded when it cannot be established is how it gets sent twice.
        """
        desktop_id = validate_id(desktop_id)
        self._driven_run(desktop_id, run_id)
        summary = (summary or "").strip()
        if not summary:
            raise AppServiceError(422, "Finishing says what happened, in a sentence.")
        if len(summary) > MAX_SUMMARY:
            raise AppServiceError(422, f"A summary is at most {MAX_SUMMARY} characters.")
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            run = self.store.get(run_id)
            if run["state"] in TERMINAL:
                raise AppServiceError(409, f"That task already {run['state'].replace('_', ' ')}.")
            state = "succeeded" if outcome == "succeeded" else "failed"
            effect_outcome = "committed" if state == "succeeded" else "failed_before_commit"
            detail = summary
            unresolved = self.desktops.uncertain(desktop_id)
            if state == "succeeded" and unresolved:
                state = "outcome_unknown"
                effect_outcome = "unknown"
                detail = (
                    summary
                    + " One request to a website was sent and never answered, so Vela "
                    "cannot say whether it went through. Check before repeating it."
                ).strip()
            finished = self.supervisor._finish(
                run_id,
                desktop_id,
                state,
                detail=detail,
                result={"summary": summary},
                outcome=effect_outcome,
                budget=self.supervisor._budgets.get(run_id),
            )
            self._release(desktop_id, run_id)
            await self.supervisor.after_run_ended(desktop_id, run_id)
        return self._describe(finished)

    async def cancel(self, desktop_id: str, run_id: str) -> dict[str, Any]:
        """Stop a driven run where it is. What it did stays done."""
        desktop_id = validate_id(desktop_id)
        self._driven_run(desktop_id, run_id)
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            run = self.store.get(run_id)
            if run["state"] in TERMINAL:
                raise AppServiceError(409, f"That task already {run['state'].replace('_', ' ')}.")
            self.supervisor._control.pop(run_id, None)
            # Stopping takes the desktop back, as it does for a loop run. The
            # terminals this run started go with it; ones other runs opened
            # keep running, the way a finished run's dev server does.
            if getattr(self.desktops, "terminals", None) is not None:
                self.desktops.terminals.close_run(desktop_id, run_id)
            if self.supervisor.leases is not None:
                self.supervisor.leases.drop(desktop_id)
            finished = self.supervisor._finish(
                run_id,
                desktop_id,
                "cancelled",
                detail="You cancelled this task. Anything it had already done stays done.",
                outcome="not_dispatched",
                budget=self.supervisor._budgets.get(run_id),
            )
            self._release(desktop_id, run_id)
            await self.supervisor.after_run_ended(desktop_id, run_id)
        return self._describe(finished)

    # ------------------------------------------------------------ internals --

    def _driven_run(self, desktop_id: str, run_id: str) -> dict[str, Any]:
        run = self.store.get(run_id)
        if run["desktopId"] != desktop_id or run["kind"] != "driven":
            raise AppServiceError(404, "That task is not a driven run on this desktop.")
        return run

    def _held_refusal(self, desktop_id: str, run: dict[str, Any]) -> dict[str, Any] | None:
        """The refusal a step earns while a person has stopped the driver, or None."""
        run_id = run["id"]
        lease = (
            self.supervisor.leases.holder(desktop_id)
            if self.supervisor.leases is not None
            else None
        )
        paused = self.supervisor._control.get(run_id) == "pause"
        if lease is None and not paused:
            return None
        if run["state"] != "paused":
            # A hold this run has no loop of its own to notice: the state
            # moves here, once, so what a viewer reads and what steps are
            # refused say the same thing.
            self.store.update(run_id, state="paused")
            self.supervisor.emit(desktop_id, "task.paused", {"runId": run_id}, run_id=run_id)
        if lease is not None:
            return {
                "refusal": {
                    "code": "person_driving",
                    "detail": (
                        "A person has taken control of this desktop. The task stays paused "
                        "until they release it and somebody says carry on."
                    ),
                    "retryable": True,
                }
            }
        return {
            "refusal": {
                "code": "paused",
                "detail": "This task is paused until somebody says carry on.",
                "retryable": True,
            }
        }

    def _budget(self, run: dict[str, Any]) -> Budget:
        budget = self.supervisor._budgets.get(run["id"])
        if budget is None:
            budget = Budget.from_policy(self.desktops.store.policy(run["desktopId"]))
            self.supervisor._budgets[run["id"]] = budget
            budget.resume()
        return budget

    def _release(self, desktop_id: str, run_id: str) -> None:
        """Forget a finished run's in-memory state and free its shared slot."""
        if run_id in self._slotted:
            self._slotted.discard(run_id)
            self.supervisor._slots.release()
        self.supervisor._control.pop(run_id, None)
        self.supervisor.tools.log.forget_run(desktop_id, run_id)
        self._locks.pop(run_id, None)
        self._steps.pop(run_id, None)

    @staticmethod
    def _read(record: dict[str, Any]) -> dict[str, Any]:
        body = {
            "clientStepId": record["clientStepId"],
            "tool": record["tool"],
            "status": record["status"],
        }
        keys = ("result", "refusal", "needsPerson")
        if record["status"] == "waiting_approval":
            keys = ("approval",)
        for key in keys:
            if key in record:
                body[key] = record[key]
        return body

    def _describe(self, run: dict[str, Any]) -> dict[str, Any]:
        return {**run, "budget": self.supervisor.budget_of(run["id"]) or run.get("budget") or {}}
