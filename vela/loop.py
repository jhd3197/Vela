"""One shape for the loops that run for as long as the server does.

Follows ServerKit's `backend/app/utils/background_loop.py` and
`app/jobs/thread_ownership.py` (MIT, same owner). ServerKit's thirteen loops
each hand-rolled the same dance — a flag, a thread, a try/except around the
tick, a sleep — and it owns that dance once. Vela's loops are asyncio rather
than threads, so the code is different and the bargain is the same:

* one stop that interrupts the wait rather than finishing it;
* `start()` that can be called twice and runs one loop;
* a tick that raises is logged with the loop's name and the loop carries on —
  a transient failure must not quietly end a daily schedule;
* `run_while` for a loop that should not run, or should stop, when the thing
  it works for is not there;
* a name, registered in `vela/loop_registry.py`, so the loops a process runs
  are a list something can read instead of a thing you find by grepping.

Every loop has the same shape:

    wait first_delay_s, then forever: tick, wait interval_s

A loop that used to sleep before its first tick says so with
`first_delay_s=interval_s`; one that ticks straight away leaves it at zero.
`on_start` runs inside the loop's own task before the first wait, for the
setup that belongs to the loop rather than to whoever started it.

`BackgroundLoop` is asyncio, for work that awaits. `BackgroundThread` is
threading, with the same interface, for blocking work that would otherwise
hold the event loop. `BackgroundThread.stop()` is synchronous and waits on an
`Event`, never on a bare sleep, so a shutdown is bounded by the join and not
by whatever the interval happens to be.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections.abc import Callable
from typing import Any

from . import loop_registry

LOG = logging.getLogger(__name__)

#: How long `stop()` waits for a thread to notice the event before giving up.
THREAD_JOIN_TIMEOUT = 5.0


class _Loop:
    """What the asyncio and threading loops agree on."""

    def __init__(
        self,
        name: str,
        interval_s: float,
        tick: Callable[[], Any],
        *,
        first_delay_s: float = 0.0,
        run_while: Callable[[], bool] | None = None,
        on_start: Callable[[], Any] | None = None,
        log: logging.Logger | None = None,
        register: bool = True,
    ) -> None:
        self.name = name
        self.interval_s = float(interval_s)
        self.first_delay_s = float(first_delay_s)
        self._tick = tick
        self._run_while = run_while
        self._on_start = on_start
        self._log = log or LOG
        if register:
            loop_registry.register(self)

    def may_run(self) -> bool:
        """Whether the thing this loop works for is still there."""
        return True if self._run_while is None else bool(self._run_while())

    @property
    def running(self) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def _failed(self) -> None:
        self._log.exception("%s: a tick failed; the loop continues", self.name)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<{type(self).__name__} {self.name} every {self.interval_s}s>"


class BackgroundLoop(_Loop):
    """A periodic task on the server's event loop."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Begin, unless this loop is already running or has no reason to."""
        if self.running or not self.may_run():
            return
        self._task = asyncio.create_task(self._run(), name=f"vela-loop:{self.name}")

    async def stop(self) -> None:
        """Cancel, wait for the task to finish, and never raise."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - a stop must not fail a shutdown
            self._log.exception("%s: stopping raised", self.name)

    async def _call(self, func) -> None:
        result = func()
        if inspect.isawaitable(result):
            await result

    async def _run(self) -> None:
        if self._on_start is not None:
            try:
                await self._call(self._on_start)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - logged, and the loop still runs
                self._failed()
        if self.first_delay_s:
            await asyncio.sleep(self.first_delay_s)
        while self.may_run():
            try:
                await self._call(self._tick)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad tick is not the end
                self._failed()
            await asyncio.sleep(self.interval_s)


class BackgroundThread(_Loop):
    """The same loop on a daemon thread, for work that blocks.

    `stop()` is synchronous because a caller on a thread has no event loop to
    await on. It sets the event the wait is sitting in, so the thread wakes
    immediately rather than finishing an interval nobody is waiting for.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._guard = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._guard:
            if self.running or not self.may_run():
                return
            self._stopping.clear()
            self._thread = threading.Thread(
                target=self._run, name=f"vela-loop:{self.name}", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float = THREAD_JOIN_TIMEOUT) -> None:
        with self._guard:
            thread, self._thread = self._thread, None
        self._stopping.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout)
            if thread.is_alive():
                self._log.warning("%s: did not stop within %ss", self.name, timeout)

    def _wait(self, seconds: float) -> bool:
        """Sleep, interruptibly. False once a stop has been asked for."""
        if not seconds:
            return not self._stopping.is_set()
        return not self._stopping.wait(seconds)

    def _run(self) -> None:
        if self._on_start is not None:
            try:
                self._on_start()
            except Exception:  # noqa: BLE001 - logged, and the loop still runs
                self._failed()
        if not self._wait(self.first_delay_s):
            return
        while not self._stopping.is_set() and self.may_run():
            try:
                self._tick()
            except Exception:  # noqa: BLE001 - one bad tick is not the end
                self._failed()
            if not self._wait(self.interval_s):
                return
