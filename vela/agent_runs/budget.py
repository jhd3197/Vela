"""What a run may spend, and the accounting that makes it stop.

A budget is not a safety feature on its own — a task that can only be stopped by
running out of steps is a task nobody is supervising. It is the floor: whatever
else goes wrong, a run ends, and it ends with a sentence saying which limit it
reached rather than with a process somebody notices next week.

Four numbers and two timers.

`steps` is tool calls. `modelRequests` is questions asked of the model, counted
separately because a model that answers with nothing useful costs requests
without costing steps. `activeSeconds` is wall-clock time the run actually spent
working — time waiting for a person to approve something is not the run's to
spend, and charging it would mean a slow human ends a task. Output is bounded per
tool result so one enormous page cannot fill a transcript.

Nothing here can be reset by the thing it is limiting. A run has no tool that
touches its own counters, cannot start another run, and cannot ask for a larger
budget: the numbers come from the desktop's policy, which is the owner's.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..desktops.policy import DEFAULT_BUDGET

#: Longest a single tool call may take before the run stops waiting on it. A
#: browser command has its own shorter timeouts; this is the backstop for one
#: that somehow does not.
STEP_TIMEOUT_SECONDS = 90

#: Longest one model request may take. A local model on a busy machine is slow;
#: three minutes is slow, and past that something is wrong rather than loaded.
MODEL_TIMEOUT_SECONDS = 180

#: Most bytes of tool output a single step contributes to the transcript.
MAX_STEP_OUTPUT_BYTES = 24_000

#: Consecutive steps that changed nothing before the run gives up. Distinct from
#: the observation-level rule in `observations.py`: that one notices a view that
#: is not moving, this one notices a run that is not getting anywhere at all.
MAX_FRUITLESS_STEPS = 8


class BudgetExhausted(Exception):
    """A limit was reached. Carries which one, in words a person can act on."""

    def __init__(self, limit: str, detail: str):
        super().__init__(detail)
        self.limit = limit
        self.detail = detail


@dataclass
class Budget:
    """One run's allowance and what it has used.

    Time is accumulated in explicit intervals rather than measured from the
    start, because a run is not always running: it pauses for approvals, for the
    human taking over, and for a model slot. Those are not its seconds.
    """

    steps: int = DEFAULT_BUDGET["steps"]
    active_seconds: int = DEFAULT_BUDGET["activeSeconds"]
    model_requests: int = DEFAULT_BUDGET["modelRequests"]

    steps_used: int = 0
    model_requests_used: int = 0
    seconds_used: float = 0.0
    fruitless: int = 0
    _running_since: float | None = field(default=None, repr=False)

    @classmethod
    def from_policy(cls, policy: dict[str, Any]) -> "Budget":
        allowance = (policy or {}).get("budget") or DEFAULT_BUDGET
        return cls(
            steps=int(allowance.get("steps", DEFAULT_BUDGET["steps"])),
            active_seconds=int(allowance.get("activeSeconds", DEFAULT_BUDGET["activeSeconds"])),
            model_requests=int(
                allowance.get("modelRequests", DEFAULT_BUDGET["modelRequests"])
            ),
        )

    # --------------------------------------------------------- the clock --

    def resume(self) -> None:
        """Start counting. Idempotent, so a caller cannot double-start it."""
        if self._running_since is None:
            self._running_since = time.monotonic()

    def suspend(self) -> None:
        """Stop counting — waiting for a person is not the run's time to spend."""
        if self._running_since is not None:
            self.seconds_used += time.monotonic() - self._running_since
            self._running_since = None

    @property
    def elapsed(self) -> float:
        running = 0.0 if self._running_since is None else time.monotonic() - self._running_since
        return self.seconds_used + running

    # -------------------------------------------------------- the checks --

    def check(self) -> None:
        """Raise if this run has spent what it was given."""
        if self.steps_used >= self.steps:
            raise BudgetExhausted(
                "steps",
                f"This task used all {self.steps} of its steps without finishing.",
            )
        if self.model_requests_used >= self.model_requests:
            raise BudgetExhausted(
                "modelRequests",
                f"This task asked the model {self.model_requests} times without finishing.",
            )
        if self.elapsed >= self.active_seconds:
            raise BudgetExhausted(
                "activeSeconds",
                f"This task spent its {self.active_seconds} seconds of working time.",
            )
        if self.fruitless >= MAX_FRUITLESS_STEPS:
            raise BudgetExhausted(
                "noProgress",
                f"The last {self.fruitless} steps changed nothing. This task is not "
                "getting anywhere.",
            )

    def spend_step(self, *, progressed: bool) -> None:
        self.steps_used += 1
        # Reset rather than decrement: the rule is about a *run* of fruitless
        # steps, and one that got somewhere has ended that run.
        self.fruitless = 0 if progressed else self.fruitless + 1

    def spend_model_request(self) -> None:
        self.model_requests_used += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": {"used": self.steps_used, "limit": self.steps},
            "modelRequests": {"used": self.model_requests_used, "limit": self.model_requests},
            "activeSeconds": {"used": round(self.elapsed, 1), "limit": self.active_seconds},
            "fruitlessSteps": self.fruitless,
        }


def clip_output(value: Any, limit: int = MAX_STEP_OUTPUT_BYTES) -> Any:
    """Bound one tool result before it becomes part of a transcript.

    Clipped rather than refused: a page longer than the limit is still worth
    looking at, and an agent told "output too large" learns nothing about the
    page. What it must not do is quietly look complete, so the marker says so.
    """
    import json

    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {"error": "that result could not be read"}
    if len(encoded.encode("utf-8")) <= limit:
        return value
    return {
        "truncated": True,
        "note": f"This result was longer than {limit} bytes and has been cut short.",
        "preview": encoded[: limit // 2],
    }
