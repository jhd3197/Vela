"""Every process-lifetime loop this process built, by name.

Follows ServerKit's `backend/app/jobs/thread_ownership.py` (MIT, same owner).
ServerKit names each long-lived thread so that "what is this process running"
is a list rather than an archaeology exercise; the same applies here, and it
buys two more things:

* `create_app` stops what it started through this registry, so a loop added to
  a service and forgotten in the shutdown hook still stops;
* `EXPECTED_LOOPS` is committed, and `tests/test_loops.py` compares it with
  what a built app actually registers, so a new loop is a reviewed addition
  rather than something that appears.

Registration is by construction and process-wide, because a loop belongs to
the process. The test suite builds many apps in one process, so `create_app`
takes a `snapshot()` before it builds its services and asks for everything
`since()` that — it stops its own loops and never somebody else's.
"""

from __future__ import annotations

import threading
from typing import Any

_LOCK = threading.Lock()
_LOOPS: list[Any] = []

#: The loops a fully built server runs. Adding one here is the review.
EXPECTED_LOOPS: frozenset[str] = frozenset({
    "notify.digest",
    "notify.doctor",
    "notify.backups",
    "notify.updates",
    "system.metrics",
    "automations.schedule",
    "managed.reconcile",
})


def register(loop: Any) -> None:
    """Called by `vela.loop` when a loop is constructed."""
    with _LOCK:
        _LOOPS.append(loop)


def snapshot() -> int:
    """A mark to measure from. Pass it back to `since()`."""
    with _LOCK:
        return len(_LOOPS)


def since(mark: int) -> list[Any]:
    """The loops constructed after `mark`, in construction order."""
    with _LOCK:
        return list(_LOOPS[mark:])


def live() -> list[Any]:
    """Everything registered in this process, in construction order."""
    with _LOCK:
        return list(_LOOPS)


def names() -> set[str]:
    """The distinct names registered in this process."""
    return {loop.name for loop in live()}


def forget(loop: Any) -> None:
    """Drop one registration. For tests that build a loop and throw it away."""
    with _LOCK:
        try:
            _LOOPS.remove(loop)
        except ValueError:
            pass
