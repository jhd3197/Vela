"""Tasks as the API uses them.

A thin layer on purpose. The store knows how to write a run down, the supervisor
knows how to carry one out, and this knows the rules that sit between them: that
a task belongs to an agent desktop and not a personal one, that what comes back
to a viewer is bounded, and that a viewer reading events is doing exactly that —
reading. Nothing an HTTP handler does owns a run's lifetime.
"""

from __future__ import annotations

from typing import Any

from ..app_storage import AppServiceError
from ..desktops.models import DesktopError, validate_id
from .model import OllamaAdapter
from .retention import Retention
from .store import RunStore
from .supervisor import Supervisor
from .viewer import Viewer

MAX_CLIENT_REQUEST_ID = 100


class AgentRuns:
    """The task surface for one Vela."""

    def __init__(self, desktops, data_dir, *, settings=None, log=None, notifier=None):
        self.desktops = desktops
        self.store = RunStore(
            data_dir / "agent-runs.sqlite",
            history=(lambda: bool(settings.get("chat_history"))) if settings else None,
        )
        self.model = OllamaAdapter(settings)
        self.supervisor = Supervisor(
            desktops, self.store, model=self.model, log=log, notifier=notifier
        )
        # Watching and taking over. Built here because both need the runs and
        # the desktops, and a second object holding half of each would be two
        # answers to "who has control".
        self.viewer = Viewer(desktops, self)
        self.supervisor.leases = self.viewer.leases
        # Deciding whether a website request may be sent has to name the run it
        # belongs to, and that decision is made inside the desktop service where
        # the policy and the grants are.
        desktops.runs = self
        self._log = log or (lambda message: None)
        # What this feature keeps, and the one place that removes it.
        self.retention = Retention(desktops, self, log=self._log)

    def prepare(self) -> dict[str, Any]:
        """On the way up: tell the truth about what was in flight, and tidy up.

        A restart is the one moment when nothing is running, which makes it the
        right time to remove what a crash may have left behind — a staged file
        whose row was never written, a frame of a window that no longer exists.
        """
        interrupted = self.store.reconcile()
        if interrupted:
            self._log(f"{interrupted} agent task(s) were interrupted by a restart")
        swept = self.retention.sweep()
        return {"interrupted": interrupted, "cleaned": swept["removed"]}

    async def stop(self) -> None:
        await self.supervisor.stop_all()

    async def quiesce(self, *, reason: str = "Vela is restoring a backup") -> dict[str, Any]:
        """Stop everything this feature is doing, and take its authority with it.

        Called before a restore replaces the files underneath it. The order is
        the point: stop dispatch first, then close the browsers, then drop the
        grants and the sessions — so nothing is still running when what it was
        running against is replaced, and nothing that was authorized against the
        old data can be used against the new.

        A task that was in flight is marked interrupted by the reconcile on the
        way back up. It is never resumed: the effects it already had cannot be
        undone by starting it again, and the data it was working in is about to
        be a different version of itself.
        """
        await self.supervisor.stop_all()
        closed = []
        for desktop in self.desktops.store.list():
            if desktop["kind"] != "agent":
                continue
            self.desktops.revoke_desktop(desktop["id"], reason=reason)
            self.viewer.forget_desktop(desktop["id"])
            closed.append(desktop["id"])
        await self.desktops.stop_runtime()
        # Every browser lifetime is over, so every site grant bound to one is
        # unmatchable, and every uncertain submission belonged to a run that is
        # no longer going anywhere.
        self.desktops._runtime_sessions.clear()
        self.desktops._uncertain.clear()
        self._log(f"agent desktops quiesced: {len(closed)} browser(s) closed")
        return {"desktops": closed}

    # -------------------------------------------------------- what works --

    async def models(self) -> dict[str, Any]:
        """The models on this computer, and which of them could run a task.

        A real check rather than a list of names. The setup screen shows which
        choices actually work, because discovering that a model cannot call a
        tool *after* setting a desktop up is the thing this avoids.
        """
        from .model import ModelUnavailable

        try:
            import httpx

            async with httpx.AsyncClient(timeout=6.0) as client:
                response = await client.get(f"{self.model.url}/api/tags")
                response.raise_for_status()
                names = [
                    entry["name"]
                    for entry in (response.json().get("models") or [])
                    if isinstance(entry, dict) and entry.get("name")
                ]
        except Exception:  # noqa: BLE001 - unreachable is an answer, not a crash
            return {
                "reachable": False,
                "url": self.model.url,
                "models": [],
                "default": self.model.default_model(),
                "detail": (
                    "Vela could not reach the model server. Start it with: ollama serve"
                ),
            }
        models = []
        for name in sorted(names)[:40]:
            try:
                capabilities = await self.model.capabilities(name)
            except ModelUnavailable:
                continue
            models.append(
                {"name": name, "tools": capabilities["tools"], "vision": capabilities["vision"]}
            )
        return {
            "reachable": True,
            "url": self.model.url,
            "models": models,
            "default": self.model.default_model(),
            "usable": [model["name"] for model in models if model["tools"]],
        }

    def attention(self) -> dict[str, Any]:
        """One compact line per desktop, for the switcher and the rail.

        Deliberately small: whether something is working, and how many things
        need the person. A badge that carried the whole task state would be a
        badge nobody could read at 16 pixels.
        """
        summary = {}
        for desktop in self.desktops.store.list():
            if desktop["kind"] != "agent":
                continue
            active = self.store.active(desktop["id"])
            waiting = self.desktops.approvals.pending(desktop["id"]) if self.desktops.grants else []
            queued = [
                run for run in self.store.list(desktop["id"], limit=50)
                if run["state"] == "queued"
            ]
            summary[desktop["id"]] = {
                "state": active["state"] if active else "idle",
                "runId": active["id"] if active else None,
                "working": bool(active and active["state"] in ("starting", "running")),
                "needsYou": len(waiting),
                "queued": len(queued),
                # A queue that is holding is something the person has to clear,
                # so it belongs in the badge beside the questions.
                "blocked": self.supervisor.blocked(desktop["id"]),
            }
        # Polled often enough to be where a lease that nobody ended is noticed.
        # A person who closed their laptop holding control of a desktop should
        # not leave it held until somebody thinks to look.
        for desktop_id in self.viewer.leases.sweep():
            self.supervisor.emit(
                desktop_id,
                "control.expired",
                {
                    "detail": (
                        "Control of this desktop went back to nobody after being idle. "
                        "The task is still paused."
                    )
                },
            )
        return {"desktops": summary}

    # ------------------------------------------------------------- tasks --

    def _agent_desktop(self, desktop_id: str) -> str:
        desktop_id = validate_id(desktop_id)
        desktop = self.desktops.store.get(desktop_id)
        if desktop["kind"] != "agent":
            raise DesktopError(409, "This desktop is not running an agent.")
        return desktop_id

    async def submit(self, desktop_id: str, request: Any) -> dict[str, Any]:
        desktop_id = self._agent_desktop(desktop_id)
        request = request if isinstance(request, dict) else {}
        client_request_id = request.get("clientRequestId")
        if client_request_id is not None:
            if not isinstance(client_request_id, str) or len(client_request_id) > MAX_CLIENT_REQUEST_ID:
                raise DesktopError(422, "That is not a valid request id.")
        return await self.supervisor.submit(
            desktop_id,
            request.get("instruction") or "",
            client_request_id=client_request_id,
            model=request.get("model") if isinstance(request.get("model"), str) else None,
        )

    def list(self, desktop_id: str, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        desktop_id = validate_id(desktop_id)
        self.desktops.store.get(desktop_id)
        runs = self.store.list(desktop_id, limit=limit, offset=offset)
        active = self.store.active(desktop_id)
        return {
            "runs": [self._describe(run) for run in runs],
            "active": self._describe(active) if active else None,
            "keepingHistory": self.store.keeping_history,
            # Why nothing is starting, when something is queued and nothing is
            # running. Without this the screen would show a queue that simply
            # never moves, which reads as a bug rather than as a decision.
            "blocked": self.supervisor.blocked(desktop_id),
        }

    def get(self, desktop_id: str, run_id: str) -> dict[str, Any]:
        desktop_id = validate_id(desktop_id)
        run = self.store.get(run_id)
        if run["desktopId"] != desktop_id:
            raise AppServiceError(404, "That task is not on this desktop.")
        return self._describe(run)

    async def control(self, desktop_id: str, run_id: str, action: Any) -> dict[str, Any]:
        desktop_id = validate_id(desktop_id)
        if action not in ("pause", "resume", "stop"):
            raise DesktopError(422, "A task is paused, resumed or stopped.")
        return self._describe(await self.supervisor.control(desktop_id, run_id, action))

    async def retry(self, desktop_id: str, run_id: str) -> dict[str, Any]:
        """Do this again — as a new task, never as the old one carrying on.

        Deliberately not "resume". A run that ended has already had whatever
        effects it had, and there is no state to continue from: the browser it
        was looking at may be gone, the page has moved, and anything it sent has
        been sent. What a person means by "try again" is a fresh attempt at the
        same instruction, and that is what this queues — with a line in the
        record saying which one it came from, so a pair of half-finished
        attempts is legible afterwards.

        A task whose outcome nobody could establish is refused. Repeating that
        is exactly the thing the whole uncertain-outcome path exists to prevent;
        say what happened first, then ask again.
        """
        desktop_id = self._agent_desktop(desktop_id)
        original = self.get(desktop_id, run_id)
        if original["state"] == "outcome_unknown" or self.desktops.uncertain(desktop_id):
            raise DesktopError(
                409,
                "Something this desktop sent has not been accounted for. Check what "
                "happened to it before asking for this again.",
            )
        if original["state"] not in ("failed", "cancelled", "interrupted", "succeeded"):
            raise DesktopError(409, "That task has not finished yet.")
        fresh = await self.supervisor.submit(desktop_id, original["instruction"])
        self.supervisor.emit(
            desktop_id,
            "task.retried",
            {"runId": fresh["id"], "of": run_id},
            run_id=fresh["id"],
        )
        return self._describe(fresh)

    async def resume_queue(self, desktop_id: str) -> dict[str, Any]:
        """Carry on with what is queued, after seeing why it stopped."""
        desktop_id = self._agent_desktop(desktop_id)
        return await self.supervisor.resume_queue(desktop_id)

    def events(self, desktop_id: str, *, after: int = 0, limit: int = 200) -> dict[str, Any]:
        """Everything that happened after a cursor the viewer already has.

        The cursor is what makes a dropped connection cost nothing: reconnect,
        ask for everything after the last number you saw, and nothing is missed
        or repeated. A viewer that has fallen further behind than the stream
        keeps is told so rather than silently handed a gap.
        """
        desktop_id = validate_id(desktop_id)
        self.desktops.store.get(desktop_id)
        after = max(0, int(after or 0))
        events = self.store.events(desktop_id, after=after, limit=limit)
        latest = self.store.latest_sequence(desktop_id)
        gap = bool(events and after and events[0]["sequence"] > after + 1)
        return {
            "events": events,
            "cursor": events[-1]["sequence"] if events else after,
            "latest": latest,
            "gap": gap,
        }

    def forget_desktop(self, desktop_id: str) -> None:
        self.store.forget_desktop(desktop_id)
        self.viewer.forget_desktop(desktop_id)

    def files(self, desktop_id: str, *, run_id: str | None = None) -> dict[str, Any]:
        """The files this desktop holds, and how much room is left.

        The limits go out with the list rather than being discovered by a
        transfer failing: an interface that can say "up to 25 MB" before
        somebody picks a file is one that does not waste their time.
        """
        desktop_id = validate_id(desktop_id)
        self.desktops.store.get(desktop_id)
        return {
            "files": self.desktops.artifacts.list(desktop_id, run_id=run_id),
            "limits": self.desktops.artifacts.limits(desktop_id, run_id),
            "unresolved": self.desktops.uncertain(desktop_id),
        }

    def purge(self) -> int:
        """Called when history is turned off. A deletion, not a preference.

        The task records and their events go here. What else has to go with
        them — the pictures, the staged files — is the retention sweep's job,
        and `Retention.sweep` calls this so both halves happen together.
        """
        return self.store.purge()

    def _describe(self, run: dict[str, Any]) -> dict[str, Any]:
        live = self.supervisor.budget_of(run["id"])
        return {**run, "budget": live or run.get("budget") or {}}
