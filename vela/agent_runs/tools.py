"""Everything an agent run can do, and nothing else.

This is the whole surface. Thirteen tools: look at a view, open an app or an
approved site, choose which view is in front, click, type, press one of a dozen
keys, scroll, wait for one named condition, attach one of this desktop's own
files, hand the task back to a person, invoke a named app action, and declare
the task finished. There is no evaluate, no shell, no file read and no raw
request, and there is no path from anything a model says to a script that runs
in a page — the only inspection code that runs in a controlled view is the
host's own, in `scripts/browser-worker/src/observe.mjs`.

Files are the newest place that rule had to be defended. An agent names an
artifact id and never a path; Vela turns the id into the file it stored under a
name it generated; the page receives that. There is no file picker an agent can
open and no directory it can name.

Two rules shape almost every argument check below.

**An action names the observation it was decided from.** A click that does not
say which screen it came from is a click on whatever happens to be there now.
The worker refuses a reference from a replaced observation, and re-checks that
the control still reads the way the agent was told before touching it.

**Nothing here is a second way into an effect.** Opening a window and clicking a
button are presentation and input; the moment something would change data it
goes through the same service, the same session and the same grant check a
person's click goes through. A tool that could write on its own would make the
rest of the enforcement decoration.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlparse

from ..app_storage import AppServiceError
from ..desktops.models import DesktopError
from ..desktops.policy import allows_app, allows_site
from ..desktops.runtime import RuntimeUnavailable, WorkerRefused
from .approvals import ApprovalPending
from .observations import ObservationLog, StalledError

#: The declared surface, in the order a task tends to need it. The model adapter
#: in Phase 8 turns this into whatever shape its model wants; keeping the
#: descriptions here means there is one answer to "what can a run do".
TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "desktop.observe",
        "summary": "Look at a view and get its current controls and text.",
        "arguments": {"viewId": "string, optional — defaults to the selected view"},
        "changes": False,
    },
    {
        "name": "desktop.open_app",
        "summary": "Open one of this desktop's allowed apps in a window.",
        "arguments": {"appId": "string"},
        "changes": False,
    },
    {
        "name": "desktop.open_site",
        "summary": "Open an approved website in a window.",
        "arguments": {"url": "string"},
        "changes": False,
    },
    {
        "name": "desktop.select_view",
        "summary": "Bring one of this desktop's windows to the front.",
        "arguments": {"viewId": "string"},
        "changes": False,
    },
    {
        "name": "desktop.click",
        "summary": "Click a control from the latest observation of a view.",
        "arguments": {
            "viewId": "string",
            "observationId": "string",
            "ref": "string, a control reference from that observation",
            "point": "{x, y} in CSS pixels, only when no control fits",
        },
        "changes": True,
    },
    {
        "name": "desktop.type",
        "summary": "Type into an editable control, replacing or appending.",
        "arguments": {
            "viewId": "string",
            "observationId": "string",
            "ref": "string",
            "text": "string",
            "mode": "'replace' or 'append'",
        },
        "changes": True,
    },
    {
        "name": "desktop.keypress",
        "summary": "Press one allowed key or combination in a view.",
        "arguments": {"viewId": "string", "observationId": "string", "key": "string"},
        "changes": True,
    },
    {
        "name": "desktop.scroll",
        "summary": "Scroll a view, or one scrollable control inside it.",
        "arguments": {
            "viewId": "string",
            "observationId": "string",
            "ref": "string, optional",
            "dx": "number",
            "dy": "number",
        },
        "changes": True,
    },
    {
        "name": "desktop.wait",
        "summary": "Wait for one named condition, for a bounded time.",
        "arguments": {
            "viewId": "string, optional — defaults to the selected view",
            "condition": "{type: 'ready'|'text'|'idle', text?}",
            "timeoutMs": "number, at most 15000",
        },
        "changes": False,
    },
    {
        "name": "desktop.attach_file",
        "summary": "Attach one of this desktop's files to a file field or an "
        "upload button. Files are named by id; there are no paths.",
        "arguments": {
            "viewId": "string",
            "observationId": "string",
            "ref": "string, the file field or the button that asks for a file",
            "artifactId": "string, from the files this desktop has",
        },
        "changes": True,
    },
    {
        "name": "task.needs_person",
        "summary": "Stop and ask the owner to take over — for a sign-in, a "
        "challenge, or anything you are not allowed to do yourself.",
        "arguments": {
            "reason": "'login' | 'challenge' | 'confirm' | 'blocked'",
            "detail": "string, what they need to do",
            "viewId": "string, optional — the window it is about",
        },
        "changes": False,
    },
    {
        "name": "app.invoke_action",
        "summary": "Ask an app to run one of its named actions. Preferred over "
        "clicking through a form when an action fits.",
        "arguments": {
            "appId": "string",
            "actionId": "string",
            "value": "object matching the action's input schema",
            "requestKey": "string, so a repeated call is not a repeated effect",
        },
        "changes": True,
    },
    {
        "name": "task.finish",
        "summary": "Report the task done, with what was actually changed.",
        "arguments": {
            "summary": "string, what you did or found",
            "changed": "true only if you actually changed something; false if you only read",
            "evidence": "array of short strings",
        },
        "changes": False,
    },
)

TOOL_NAMES = tuple(tool["name"] for tool in TOOLS)

#: Bounds on what may arrive as an argument, before anything is done with it.
MAX_TEXT = 4000
MAX_SUMMARY = 2000
MAX_EVIDENCE = 12
MAX_ACTION_VALUE_BYTES = 64 * 1024


#: The worker's error vocabulary, in the words a run is answered in. Translated
#: rather than passed through, so the model reads one vocabulary instead of the
#: browser's, the service's and the API's.
WORKER_CODES = {
    "stale_observation": "stale_observation",
    "view_not_ready": "view_not_ready",
    "unknown_view": "no_view",
    "unknown_command": "unknown_tool",
    "identity_mismatch": "conflict",
    "stale_control_epoch": "control_lost",
    "navigation_denied": "not_allowed",
    "network_denied": "not_allowed",
    "protocol_error": "protocol_error",
    "payload_too_large": "too_large",
    "command_timeout": "timed_out",
    "capture_unsupported": "unsupported",
    "runtime_unavailable": "runtime_unavailable",
    "worker_error": "failed",
}

#: Refusals where looking again and trying once more is worth something. A run
#: that retried the others would be retrying a decision, not a hiccup.
RETRYABLE = frozenset({"stale_observation", "view_not_ready", "timed_out", "runtime_unavailable"})

#: Reasons a task can hand itself back to the person. Deliberately short and
#: named: "it did not work" is not a reason anybody can act on, and a free-text
#: reason would become one.
PERSON_REASONS = ("login", "challenge", "confirm", "blocked")


class ToolError(Exception):
    """A tool refused, with a reason a run can act on.

    `code` is the vocabulary the supervisor and the Agent window report against;
    `retryable` says whether looking again is worth anything, so a run does not
    sit in a loop against a refusal that will never change.
    """

    def __init__(self, code: str, detail: str, *, retryable: bool = False):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.retryable = retryable

    def as_dict(self) -> dict[str, Any]:
        return {"error": self.code, "detail": self.detail, "retryable": self.retryable}


class AgentTools:
    """The tool surface for one Vela, across every agent desktop on it."""

    def __init__(self, desktops, *, log: ObservationLog | None = None):
        self.desktops = desktops
        self.log = log or ObservationLog()

    # ------------------------------------------------------------ calling --

    async def call(
        self,
        desktop_id: str,
        name: str,
        arguments: Any = None,
        *,
        run_id: str,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        """Run one tool for one run on one desktop.

        Every refusal comes back as a `ToolError` rather than as whatever the
        layer underneath raised. A run reads these; a stack of service
        exceptions with three different vocabularies is not something a model
        can be expected to act on sensibly.
        """
        if name not in TOOL_NAMES:
            raise ToolError("unknown_tool", f"{name} is not a tool this desktop has.")
        arguments = arguments if isinstance(arguments, dict) else {}
        if not isinstance(run_id, str) or not run_id:
            raise ToolError("protocol_error", "A tool call belongs to a run.")
        handler = getattr(self, "_" + name.replace(".", "_"))
        try:
            return await handler(desktop_id, arguments, run_id=run_id, actor_id=actor_id or run_id)
        except ToolError:
            raise
        except ApprovalPending:
            # Travels up untouched. A question is not a tool failure, and the
            # supervisor is what knows how to wait for an answer.
            raise
        except StalledError as exc:
            raise ToolError("no_progress", exc.detail) from exc
        except DesktopError as exc:
            raise _refusal(exc) from exc
        except AppServiceError as exc:
            raise _refusal(exc) from exc
        except RuntimeUnavailable as exc:
            raise ToolError("runtime_unavailable", str(exc)) from exc

    # ------------------------------------------------------ perceiving --

    async def _desktop_observe(self, desktop_id, arguments, *, run_id, actor_id):
        view = self._view(desktop_id, arguments.get("viewId"), allow_default=True)
        observation = await self._runtime_command(
            "view.observe", desktopId=desktop_id, viewId=view["id"], timeout=30.0
        )
        try:
            progress = self.log.record(desktop_id, view["id"], observation)
        except StalledError as exc:
            self.log.note_action(
                desktop_id, run_id, tool="desktop.observe", target=view["id"],
                outcome="not_dispatched", result={"stalled": exc.repeats},
            )
            raise
        self.log.note_action(
            desktop_id,
            run_id,
            tool="desktop.observe",
            target=view["id"],
            expected="see what this view shows",
            result={"observationId": observation.get("observationId")},
            outcome="committed",
        )
        happened = self.desktops.collect_notices(
            desktop_id, observation.get("notices"), run_id=run_id
        )
        return {
            **{key: value for key, value in observation.items() if key != "notices"},
            "repeats": progress["repeats"],
            "viewId": view["id"],
            **({"happened": happened} if happened else {}),
        }

    # ----------------------------------------------------------- opening --

    async def _desktop_open_app(self, desktop_id, arguments, *, run_id, actor_id):
        self.desktops.agent_runtime_for(desktop_id)
        app_id = _string(arguments.get("appId"), "appId", 64)
        policy = self.desktops.store.policy(desktop_id)
        if not allows_app(policy, app_id):
            # Say what *is* allowed. A model asked for "Notes" when the id is
            # "notes" can fix that from this sentence; "no" on its own leaves it
            # guessing, and a real run showed it guessing at a website next.
            allowed = ", ".join(policy.get("apps") or []) or "nothing yet"
            raise ToolError(
                "not_allowed",
                f"This desktop is not allowed to use {app_id}. It may use: {allowed}. "
                "Apps are named by their id, in lower case.",
            )
        view = self.desktops.open_view(desktop_id, "app", {"appId": app_id}, opened_by="agent")
        if not view["available"]:
            raise ToolError("view_unavailable", f"{app_id} is not installed on this computer.")
        await self.desktops.open_in_browser(desktop_id, self.desktops.store.view(view["id"]))
        self.desktops.select_view(desktop_id, view["id"])
        self.log.note_action(
            desktop_id, run_id, tool="desktop.open_app", target=app_id,
            expected="open this app in a window", result={"viewId": view["id"]},
        )
        return {"view": _view_summary(view)}

    async def _desktop_open_site(self, desktop_id, arguments, *, run_id, actor_id):
        self.desktops.agent_runtime_for(desktop_id)
        url = _string(arguments.get("url"), "url", 2000)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ToolError("protocol_error", "A site is an http or https address.")
        origin = f"{parsed.scheme}://{parsed.netloc}"
        policy = self.desktops.store.policy(desktop_id)
        if not allows_site(policy, origin):
            approved = ", ".join(rule["origin"] for rule in policy.get("sites") or [])
            raise ToolError(
                "not_allowed",
                f"{origin} is not one of this desktop's approved sites. "
                + (f"It may open: {approved}." if approved else "No sites are approved."),
            )
        view = self.desktops.open_view(desktop_id, "web", {"url": url}, opened_by="agent")
        await self.desktops.open_in_browser(desktop_id, self.desktops.store.view(view["id"]))
        self.desktops.select_view(desktop_id, view["id"])
        self.log.note_action(
            desktop_id, run_id, tool="desktop.open_site", target=origin,
            expected="open this site in a window", result={"viewId": view["id"]},
        )
        return {"view": _view_summary(view)}

    async def _desktop_select_view(self, desktop_id, arguments, *, run_id, actor_id):
        view = self._view(desktop_id, arguments.get("viewId"))
        # Only this desktop's attention changes. Another desktop's selected view
        # and another desktop's running task are not this run's to touch.
        self.desktops.select_view(desktop_id, view["id"])
        self.log.note_action(
            desktop_id, run_id, tool="desktop.select_view", target=view["id"],
            expected="bring this window to the front",
        )
        return {"view": _view_summary(view)}

    # ------------------------------------------------------------ acting --

    async def _desktop_click(self, desktop_id, arguments, *, run_id, actor_id):
        view = self._view(desktop_id, arguments.get("viewId"))
        action: dict[str, Any] = {
            "action": "click",
            "observationId": _string(arguments.get("observationId"), "observationId", 64),
        }
        if arguments.get("ref") is not None:
            action["ref"] = _string(arguments.get("ref"), "ref", 32)
        elif isinstance(arguments.get("point"), dict):
            action["point"] = {
                "x": _number(arguments["point"].get("x"), "x"),
                "y": _number(arguments["point"].get("y"), "y"),
            }
        else:
            raise ToolError("protocol_error", "A click names a control or a point in the view.")
        if arguments.get("button") is not None:
            action["button"] = _string(arguments.get("button"), "button", 10)
        return await self._act(desktop_id, view, action, run_id=run_id, expected="click it")

    async def _desktop_type(self, desktop_id, arguments, *, run_id, actor_id):
        view = self._view(desktop_id, arguments.get("viewId"))
        text = arguments.get("text")
        if not isinstance(text, str):
            raise ToolError("protocol_error", "Typing needs text.")
        if len(text) > MAX_TEXT:
            raise ToolError("too_large", f"Type at most {MAX_TEXT} characters at once.")
        mode = arguments.get("mode") or "replace"
        if mode not in ("replace", "append"):
            raise ToolError("protocol_error", "Typing either replaces what is there or appends.")
        action = {
            "action": "type",
            "observationId": _string(arguments.get("observationId"), "observationId", 64),
            "ref": _string(arguments.get("ref"), "ref", 32),
            "text": text,
            "mode": mode,
        }
        return await self._act(
            desktop_id, view, action, run_id=run_id, expected=f"{mode} the text in this field"
        )

    async def _desktop_keypress(self, desktop_id, arguments, *, run_id, actor_id):
        view = self._view(desktop_id, arguments.get("viewId"))
        action = {
            "action": "key",
            "observationId": _string(arguments.get("observationId"), "observationId", 64),
            "key": _string(arguments.get("key"), "key", 32),
        }
        return await self._act(
            desktop_id, view, action, run_id=run_id, expected=f"press {action['key']}"
        )

    async def _desktop_scroll(self, desktop_id, arguments, *, run_id, actor_id):
        view = self._view(desktop_id, arguments.get("viewId"))
        action: dict[str, Any] = {
            "action": "scroll",
            "observationId": _string(arguments.get("observationId"), "observationId", 64),
            "dx": _number(arguments.get("dx", 0), "dx"),
            "dy": _number(arguments.get("dy", 0), "dy"),
        }
        if arguments.get("ref") is not None:
            action["ref"] = _string(arguments.get("ref"), "ref", 32)
        return await self._act(desktop_id, view, action, run_id=run_id, expected="scroll the view")

    async def _desktop_wait(self, desktop_id, arguments, *, run_id, actor_id):
        # Waiting touches nothing, so it may leave the window unsaid and mean
        # the one in front. Clicking may not: a click on "whatever is selected"
        # is the ambiguity this surface exists to remove.
        view = self._view(desktop_id, arguments.get("viewId"), allow_default=True)
        condition = arguments.get("condition")
        if not isinstance(condition, dict) or condition.get("type") not in ("ready", "text", "idle"):
            raise ToolError(
                "protocol_error",
                "A wait names what it is waiting for: 'ready', 'text' or 'idle'.",
            )
        action = {
            "action": "wait",
            # Optional here alone: waiting touches nothing, and waiting for a
            # freshly opened view to be ready happens before there is anything to
            # observe. Given one, it is still required to be the current one.
            "observationId": (
                _string(arguments.get("observationId"), "observationId", 64)
                if arguments.get("observationId")
                else None
            ),
            "condition": {
                "type": condition["type"],
                "text": _string(condition.get("text"), "text", 200) if condition.get("text") else None,
            },
            "timeoutMs": _number(arguments.get("timeoutMs", 5000), "timeoutMs"),
        }
        return await self._act(
            desktop_id, view, action, run_id=run_id, expected=f"wait for {condition['type']}"
        )

    async def _act(self, desktop_id, view, action, *, run_id, expected):
        self.desktops.agent_runtime_for(desktop_id)
        result = await self._runtime_command(
            "view.act", desktopId=desktop_id, viewId=view["id"], action=action, timeout=40.0
        )
        after = result.get("after") or {}
        # What the action set off besides changing the page: a download that
        # finished, a submission held for approval, a dialog the page opened.
        # Recorded before anything else, so a refusal further down still leaves
        # the file that did arrive accounted for.
        happened = self.desktops.collect_notices(
            desktop_id, result.get("notices"), run_id=run_id
        )
        waiting = next(
            (
                notice
                for notice in (result.get("notices") or [])
                if isinstance(notice, dict)
                and notice.get("type") == "effect_pending"
                and notice.get("requestId")
            ),
            None,
        )
        if result.get("observationSpent"):
            # Something was touched, so the run of identical observations is
            # over whether or not the page has visibly reacted yet.
            self.log.progressed(desktop_id, view["id"])
        self.log.note_action(
            desktop_id,
            run_id,
            tool="desktop." + action["action"],
            target=action.get("ref") or view["id"],
            expected=expected,
            result={
                "changed": after.get("changed"),
                "navigated": after.get("navigated"),
                "url": after.get("url"),
            },
            outcome="committed",
        )
        if waiting is not None:
            # The request was not sent. The run stops here rather than reading a
            # page that did not change and concluding the site refused it.
            pending = ApprovalPending(
                self.desktops.approvals.get(waiting["requestId"], desktop_id=desktop_id)
            )
            # The click is spent whatever the answer turns out to be, so the
            # supervisor must not repeat this exact call: it has to look again.
            pending.observation_spent = True
            raise pending
        return {
            **{key: value for key, value in result.items() if key != "notices"},
            "viewId": view["id"],
            **({"happened": happened} if happened else {}),
            # Saying this plainly matters: every reference from that observation
            # is gone, and the next step has to look again.
            "observationInvalidated": bool(result.get("observationSpent")),
        }

    # ------------------------------------------------------------- files --

    async def _desktop_attach_file(self, desktop_id, arguments, *, run_id, actor_id):
        """Put one of this desktop's files into a page that is asking for one.

        The agent names an artifact id. Vela turns that into the file it stored
        under a name it generated, in a directory it owns, and hands the page
        that — so there is no argument here that could name anything else on
        this computer, and no file picker the agent can open on its own.
        """
        view = self._view(desktop_id, arguments.get("viewId"))
        artifact_id = _string(arguments.get("artifactId"), "artifactId", 64)
        try:
            resolved = self.desktops.resolve_artifacts(desktop_id, [artifact_id])
        except AppServiceError as exc:
            raise ToolError(_code_for(exc.status), exc.detail) from exc
        paths = [path for path, _record in resolved]
        names = [record["name"] for _path, record in resolved]
        action = {
            "action": "attach",
            "observationId": _string(arguments.get("observationId"), "observationId", 64),
            "ref": _string(arguments.get("ref"), "ref", 32),
            "paths": paths,
        }
        result = await self._act(
            desktop_id, view, action, run_id=run_id, expected=f"attach {names[0]}"
        )
        # The file's name goes back; its location does not. A run that knew
        # where a file was would be a run with a path to put somewhere else.
        return {**result, "attached": names}

    # ------------------------------------------------------ handing back --

    async def _task_needs_person(self, desktop_id, arguments, *, run_id, actor_id):
        """Stop and ask for a person, with a named reason.

        The honest end of several roads: a sign-in Vela will not attempt, a
        challenge that exists to tell people and programs apart, a site whose
        changes need a human at the keyboard. None of them is a failure and none
        of them is something to work around — simulating a login or defeating a
        challenge are both things this deliberately cannot do.
        """
        reason = _string(arguments.get("reason"), "reason", 20)
        if reason not in PERSON_REASONS:
            raise ToolError(
                "protocol_error",
                "A reason is one of: " + ", ".join(PERSON_REASONS) + ".",
            )
        detail = _string(arguments.get("detail"), "detail", 400)
        view_id = None
        if arguments.get("viewId"):
            view_id = self._view(desktop_id, arguments.get("viewId"))["id"]
        self.log.note_action(
            desktop_id, run_id, tool="task.needs_person", target=view_id or desktop_id,
            expected="hand this back to the owner", outcome="not_dispatched",
            result={"reason": reason},
        )
        return {"reason": reason, "detail": detail, "viewId": view_id, "handedBack": True}

    # ----------------------------------------------------- named actions --

    async def _app_invoke_action(self, desktop_id, arguments, *, run_id, actor_id):
        """Ask an app to do something by name rather than by clicking at it.

        Preferred wherever an action fits, because the app validated the input,
        the result is a receipt, and a repeated request key returns the first
        answer instead of doing it twice. What it is not is a shortcut past
        permission: the grant is checked inside the write's own transaction, the
        same as any other effect.
        """
        desktop = self.desktops.store.get(desktop_id)
        if desktop["kind"] != "agent":
            raise ToolError("conflict", "This desktop is not running an agent.")
        # Deliberately no browser check: a named action is a call to a service,
        # not something clicked in a window, and it works whether or not the app
        # happens to be on screen.
        desktop_id = desktop["id"]
        app_id = _string(arguments.get("appId"), "appId", 64)
        action_id = _string(arguments.get("actionId"), "actionId", 64)
        request_key = _string(arguments.get("requestKey"), "requestKey", 64)
        value = arguments.get("value")
        encoded = _encode(value)
        if len(encoded.encode("utf-8")) > MAX_ACTION_VALUE_BYTES:
            raise ToolError("too_large", "That action input is too large to send.")

        policy = self.desktops.store.policy(desktop_id)
        if not allows_app(policy, app_id):
            raise ToolError("not_allowed", f"This desktop is not allowed to use {app_id}.")
        actions = self.desktops.actions
        if actions is None or self.desktops.grants is None:
            raise ToolError("unsupported", "Named actions are not available on this server.")

        from ..actions import fingerprint

        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        scope = {"app": app_id, "action": action_id}

        def authorize(target_manifest, target_identity):
            # The same rule an app's own click follows, reached from the other
            # door. Bound to this installation and this exact manifest, so
            # reinstalling or updating the app stops it matching rather than
            # carrying an old approval onto new code — and, with no grant, it
            # opens the owner's question instead of refusing outright.
            check = self.desktops.require_or_ask(
                {
                    "desktop_id": desktop_id,
                    "effect": "action",
                    "app_id": app_id,
                    "installation_id": target_identity,
                    "contract": fingerprint(target_manifest),
                    "run_id": run_id,
                    "request_digest": digest,
                    "scope": scope,
                },
                policy=policy,
                app_name=target_manifest.name,
                view_id=None,
                proposal=value,
                scope=scope,
            )
            with self.desktops.grants.storage.connection() as db:
                check(db)

        try:
            result = actions.invoke_for_agent(
                f"{desktop_id}:{run_id}", app_id, action_id, value, request_key, authorize
            )
        except ApprovalPending as exc:
            # Not a failure. The supervisor waits on it and tries the same call
            # again once there is an answer, so the run pauses at the boundary
            # rather than deciding the change was refused.
            self.log.note_action(
                desktop_id, run_id, tool="app.invoke_action", target=f"{app_id}.{action_id}",
                expected="run this named action", outcome="not_dispatched",
                result={"awaiting": exc.record["requestId"]},
            )
            raise
        except AppServiceError as exc:
            self.log.note_action(
                desktop_id, run_id, tool="app.invoke_action", target=f"{app_id}.{action_id}",
                expected="run this named action", outcome="denied" if exc.status == 403 else
                "failed_before_commit", result={"detail": exc.detail},
            )
            raise
        self.log.note_action(
            desktop_id, run_id, tool="app.invoke_action", target=f"{app_id}.{action_id}",
            expected="run this named action", outcome="committed",
            result={"status": result.get("status"), "replayed": result.get("replayed")},
        )
        return {"result": result}

    # ---------------------------------------------------------- finishing --

    async def _task_finish(self, desktop_id, arguments, *, run_id, actor_id):
        """A proposal, not a verdict.

        The run says it is done and what it changed; the supervisor in Phase 8
        decides whether the evidence matches what was asked for. A tool that
        could declare its own success would make the result meaningless.
        """
        summary = _string(arguments.get("summary"), "summary", MAX_SUMMARY)
        evidence = arguments.get("evidence")
        if evidence is None:
            evidence = []
        if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE:
            raise ToolError("protocol_error", f"Evidence is a list of at most {MAX_EVIDENCE} notes.")
        notes = [_string(item, "evidence", 300) for item in evidence]
        # A claim, kept separate from the receipts it is checked against. Absent
        # means "do not claim anything", which is different from claiming
        # nothing changed and is treated as the safer of the two.
        claimed = arguments.get("changed")
        if isinstance(claimed, str):
            claimed = claimed.strip().lower() in ("true", "yes", "1")
        self.log.note_action(
            desktop_id, run_id, tool="task.finish", expected="finish the task",
            result={"summary": summary}, outcome="committed",
        )
        return {
            "proposed": True,
            "summary": summary,
            "changed": claimed if isinstance(claimed, bool) else None,
            "evidence": notes,
            "actions": self.log.evidence(desktop_id, run_id),
        }

    # ----------------------------------------------------------- helpers --

    def _view(self, desktop_id: str, view_id: Any, *, allow_default: bool = False):
        """The view a tool is aimed at, checked against this desktop.

        A reference to another desktop's view is a 404 here, not a redirect to
        something nearby: the whole point of view identity is that it does not
        wander.
        """
        self.desktops.agent_runtime_for(desktop_id)
        state = self.desktops.views(desktop_id)
        views = state["views"]
        open_now = [item["id"] for item in views if item["agentViewable"]]
        if view_id is None and allow_default:
            selected = state["layout"].get("selectedView")
            view = next((item for item in views if item["id"] == selected), None)
            if view is None:
                view = next((item for item in views if item["agentViewable"]), None)
            if view is None:
                raise ToolError("no_view", "Nothing is open on this desktop yet.")
        else:
            # Say which windows exist. A refusal that only says "viewId is
            # required" is one a run can repeat forever, and a real evaluation
            # showed a model doing exactly that.
            if not isinstance(view_id, str) or not view_id.strip():
                raise ToolError(
                    "no_view",
                    "This needs the id of a window. Open now: "
                    + (", ".join(open_now) if open_now else "nothing"),
                )
            view_id = _string(view_id, "viewId", 64)
            view = next((item for item in views if item["id"] == view_id), None)
            if view is None:
                raise ToolError(
                    "no_view",
                    f"{view_id} is not a window on this desktop. Open now: "
                    + (", ".join(open_now) if open_now else "nothing"),
                )
        if not view["agentViewable"]:
            raise ToolError("not_allowed", "That window is one of the owner's, not the agent's.")
        if not view["available"]:
            raise ToolError(
                "view_unavailable",
                "That app was reinstalled or removed; open it again before using it.",
            )
        return view

    async def _runtime_command(self, name: str, **fields) -> dict[str, Any]:
        try:
            return await self.desktops.runtime.command(name, **fields)
        except WorkerRefused as exc:
            code = WORKER_CODES.get(exc.code, "failed")
            raise ToolError(code, exc.detail, retryable=code in RETRYABLE) from exc
        except RuntimeUnavailable as exc:
            raise ToolError("runtime_unavailable", str(exc), retryable=True) from exc


def _view_summary(view: dict[str, Any]) -> dict[str, Any]:
    """What a run is told about a window. Not its bounds or its z-order."""
    return {
        "viewId": view["id"],
        "kind": view["kind"],
        "appId": view.get("appId"),
        "url": view.get("url"),
        "title": view.get("title") or view.get("appId") or view["kind"],
    }


def _string(value: Any, what: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolError("protocol_error", f"{what} is required.")
    if len(value) > limit:
        raise ToolError("too_large", f"{what} is longer than {limit} characters.")
    return value


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError("protocol_error", f"{what} is a number.")
    return float(value)


def _encode(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ToolError("protocol_error", "That action input is not something Vela can send.") from exc


def _refusal(exc: Any) -> ToolError:
    """A service refusal in the vocabulary a run reads.

    A conflict gets a sentence of its own. "409" means somebody — the owner, or
    another task — changed the thing between it being read and it being written,
    and the useful instruction is to look again rather than to try the same
    write harder. A run told only "conflict" repeats itself until its budget
    ends, which a real evaluation showed it doing.
    """
    code = _code_for(exc.status)
    if code == "conflict":
        return ToolError(
            code,
            exc.detail.rstrip(".")
            + ". Somebody changed this since you read it — look at it again before deciding.",
            retryable=True,
        )
    return ToolError(code, exc.detail)


def _code_for(status: int) -> str:
    return {
        401: "not_allowed",
        403: "not_allowed",
        404: "not_found",
        409: "conflict",
        413: "too_large",
        422: "protocol_error",
        503: "runtime_unavailable",
        504: "timed_out",
    }.get(status, "failed")
