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
from .store import RunStore
from .supervisor import Supervisor

MAX_CLIENT_REQUEST_ID = 100


class AgentRuns:
    """The task surface for one Vela."""

    def __init__(self, desktops, data_dir, *, settings=None, log=None):
        self.desktops = desktops
        self.store = RunStore(
            data_dir / "agent-runs.sqlite",
            history=(lambda: bool(settings.get("chat_history"))) if settings else None,
        )
        self.model = OllamaAdapter(settings)
        self.supervisor = Supervisor(desktops, self.store, model=self.model, log=log)
        self._log = log or (lambda message: None)

    def prepare(self) -> dict[str, Any]:
        """On the way up: tell the truth about what was in flight."""
        interrupted = self.store.reconcile()
        if interrupted:
            self._log(f"{interrupted} agent task(s) were interrupted by a restart")
        return {"interrupted": interrupted}

    async def stop(self) -> None:
        await self.supervisor.stop_all()

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

    def purge(self) -> int:
        """Called when history is turned off. A deletion, not a preference."""
        return self.store.purge()

    def _describe(self, run: dict[str, Any]) -> dict[str, Any]:
        live = self.supervisor.budget_of(run["id"])
        return {**run, "budget": live or run.get("budget") or {}}
