"""The background loop helper, and the registry of what a server runs.

Run with: python -m unittest discover -s tests. Uses disposable data only.
"""
import asyncio
import atexit
import logging
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

# api.py constructs its default ASGI app on import. Keep that off the user's data.
_bootstrap = tempfile.TemporaryDirectory(prefix="vela-test-loops-")
atexit.register(_bootstrap.cleanup)
os.environ["VELA_DATA_DIR"] = _bootstrap.name

from vela import loop_registry
from vela.api import create_app
from vela.config import Config
from vela.loop import BackgroundLoop, BackgroundThread
from vela.notify import (BACKUP_TICK_SECONDS, DOCTOR_INTERVAL_SECONDS,
                         DOCTOR_STARTUP_DELAY_SECONDS, SCHEDULE_INTERVAL_SECONDS,
                         UPDATE_INTERVAL_SECONDS, UPDATE_STARTUP_DELAY_SECONDS)
from vela.loop_registry import EXPECTED_LOOPS

ROOT = Path(__file__).resolve().parent.parent

#: Every registered loop must stop well inside this.
STOP_BUDGET = 2.0

#: A failure deadline, not a promise about how fast a CI runner schedules ticks.
TICK_TIMEOUT = 5.0


def run(coroutine):
    return asyncio.run(coroutine)


class BackgroundLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.built = []

    async def asyncTearDown(self):
        for background in self.built:
            await background.stop()
            loop_registry.forget(background)

    def make(self, *args, **kwargs):
        background = BackgroundLoop(*args, **kwargs)
        self.built.append(background)
        return background

    async def test_it_ticks_on_its_interval(self):
        ticks = []
        reached = asyncio.Event()

        def tick():
            ticks.append(1)
            if len(ticks) >= 3:
                reached.set()

        background = self.make("test.ticks", 0.01, tick)
        background.start()
        await asyncio.wait_for(reached.wait(), TICK_TIMEOUT)
        await background.stop()
        self.assertGreaterEqual(len(ticks), 3)
        self.assertFalse(background.running)

    async def test_two_starts_run_one_loop(self):
        ticked = asyncio.Event()
        background = self.make("test.idempotent", 0.01, ticked.set)
        background.start()
        first = background._task
        background.start()
        self.assertIs(background._task, first)
        await asyncio.wait_for(ticked.wait(), TICK_TIMEOUT)

    async def test_a_tick_that_raises_is_logged_once_and_the_loop_carries_on(self):
        ticks = []
        reached = asyncio.Event()

        def tick():
            ticks.append(1)
            if len(ticks) >= 3:
                reached.set()
            raise RuntimeError("no")

        # Three ticks deliberately take longer than the old 60 ms sleep.
        background = self.make("test.raises", 0.05, tick)
        with self.assertLogs("vela.loop", level=logging.ERROR) as logged:
            background.start()
            try:
                await asyncio.wait_for(reached.wait(), TICK_TIMEOUT)
            finally:
                await background.stop()

        self.assertGreaterEqual(len(ticks), 3, "a raising tick ended the loop")
        self.assertEqual(len(logged.output), len(ticks), "one record per failed tick")
        self.assertIn("test.raises", logged.output[0])

    async def test_stop_returns_within_the_budget_even_mid_interval(self):
        ticked = asyncio.Event()
        background = self.make("test.stops", 3600, ticked.set)
        background.start()
        await asyncio.wait_for(ticked.wait(), TICK_TIMEOUT)
        started = time.monotonic()
        await background.stop()
        self.assertLess(time.monotonic() - started, STOP_BUDGET)
        self.assertFalse(background.running)

    async def test_stopping_one_that_never_started_does_nothing(self):
        background = self.make("test.never", 1, lambda: None)
        await background.stop()
        self.assertFalse(background.running)

    async def test_run_while_refuses_to_start_and_ends_a_running_loop(self):
        allowed = {"value": False}
        ticked = asyncio.Event()

        background = self.make("test.audience", 0.01, ticked.set,
                               run_while=lambda: allowed["value"])
        background.start()
        self.assertFalse(background.running, "it started with no audience")
        allowed["value"] = True
        background.start()
        await asyncio.wait_for(ticked.wait(), TICK_TIMEOUT)
        allowed["value"] = False
        await asyncio.wait_for(asyncio.shield(background._task), TICK_TIMEOUT)
        self.assertFalse(background.running, "it kept running after the audience left")

    async def test_first_delay_holds_the_first_tick_back(self):
        ticks = []
        started = asyncio.Event()

        background = self.make("test.delayed", 0.01, lambda: ticks.append(1),
                               first_delay_s=3600, on_start=started.set)
        background.start()
        await asyncio.wait_for(started.wait(), TICK_TIMEOUT)
        await background.stop()
        self.assertEqual(ticks, [])

    async def test_on_start_runs_before_the_first_delay(self):
        order = []
        ticked = asyncio.Event()

        def tick():
            order.append("tick")
            ticked.set()

        background = self.make("test.on_start", 0.01, tick,
                               first_delay_s=0.05,
                               on_start=lambda: order.append("start"))
        background.start()
        await asyncio.wait_for(ticked.wait(), TICK_TIMEOUT)
        await background.stop()
        self.assertEqual(order[0], "start")
        self.assertIn("tick", order)

    async def test_an_async_tick_is_awaited(self):
        ticked = asyncio.Event()

        async def tick():
            await asyncio.sleep(0)
            ticked.set()

        background = self.make("test.async", 0.01, tick)
        background.start()
        await asyncio.wait_for(ticked.wait(), TICK_TIMEOUT)


class BackgroundThreadTests(unittest.TestCase):
    """The same interface on a thread, for blocking work."""

    def setUp(self):
        self.built = []

    def tearDown(self):
        for background in self.built:
            background.stop()
            loop_registry.forget(background)

    def make(self, *args, **kwargs):
        background = BackgroundThread(*args, **kwargs)
        self.built.append(background)
        return background

    def test_it_ticks_and_stops(self):
        ticks = []
        reached = threading.Event()

        def tick():
            ticks.append(1)
            if len(ticks) >= 3:
                reached.set()

        background = self.make("test.thread", 0.01, tick)
        background.start()
        self.assertTrue(reached.wait(TICK_TIMEOUT), "the thread did not reach three ticks")
        self.assertTrue(background.running)
        background.stop()
        self.assertFalse(background.running)
        self.assertGreaterEqual(len(ticks), 3)

    def test_stop_interrupts_the_wait_rather_than_finishing_it(self):
        ticked = threading.Event()
        background = self.make("test.thread_stop", 3600, ticked.set)
        background.start()
        self.assertTrue(ticked.wait(TICK_TIMEOUT), "the thread never started")
        started = time.monotonic()
        background.stop()
        self.assertLess(time.monotonic() - started, STOP_BUDGET)

    def test_two_starts_run_one_thread(self):
        seen = set()
        ticked = threading.Event()

        def tick():
            seen.add(threading.current_thread().ident)
            ticked.set()

        background = self.make("test.thread_once", 0.01, tick)
        background.start()
        first = background._thread
        background.start()
        self.assertIs(background._thread, first)
        self.assertTrue(ticked.wait(TICK_TIMEOUT), "the thread never ticked")
        background.stop()
        self.assertEqual(len(seen), 1)

    def test_a_tick_that_raises_does_not_end_the_thread(self):
        ticks = []
        reached = threading.Event()

        def tick():
            ticks.append(1)
            if len(ticks) >= 3:
                reached.set()
            raise RuntimeError("no")

        # Three ticks deliberately take longer than the old 60 ms sleep.
        background = self.make("test.thread_raises", 0.05, tick)
        with self.assertLogs("vela.loop", level=logging.ERROR) as logged:
            background.start()
            try:
                self.assertTrue(reached.wait(TICK_TIMEOUT), "a raising tick ended the thread")
            finally:
                # Join before leaving assertLogs so the last error is included,
                # even when the event deadline or an assertion fails.
                background.stop()
        self.assertGreaterEqual(len(ticks), 3)
        self.assertEqual(len(logged.output), len(ticks), "one record per failed tick")
        self.assertIn("test.thread_raises", logged.output[0])


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory(prefix="vela-loops-")
        root = Path(self.root.name)
        config = Config(root / "data", root / "apps", ROOT / "web/dist")
        config.ensure_dirs()
        self.mark = loop_registry.snapshot()
        self.app = create_app(config)
        self.loops = loop_registry.since(self.mark)

    def tearDown(self):
        for background in self.loops:
            loop_registry.forget(background)
        self.root.cleanup()

    def test_a_built_server_registers_exactly_the_committed_names(self):
        self.assertEqual({background.name for background in self.loops}, set(EXPECTED_LOOPS))

    def test_each_name_is_registered_once(self):
        names = [background.name for background in self.loops]
        self.assertEqual(sorted(names), sorted(set(names)))

    def test_the_startup_delays_and_intervals_are_the_ones_the_constants_name(self):
        timings = {background.name: (background.first_delay_s, background.interval_s)
                   for background in self.loops}
        self.assertEqual(timings["notify.digest"],
                         (SCHEDULE_INTERVAL_SECONDS, SCHEDULE_INTERVAL_SECONDS))
        self.assertEqual(timings["notify.doctor"],
                         (DOCTOR_STARTUP_DELAY_SECONDS, DOCTOR_INTERVAL_SECONDS))
        self.assertEqual(timings["notify.backups"],
                         (BACKUP_TICK_SECONDS, BACKUP_TICK_SECONDS))
        self.assertEqual(timings["notify.updates"],
                         (UPDATE_STARTUP_DELAY_SECONDS, UPDATE_INTERVAL_SECONDS))

    def test_every_registered_loop_stops_within_the_budget(self):
        async def body():
            for background in self.loops:
                background.start()
            await asyncio.sleep(0.05)
            started = time.monotonic()
            for background in reversed(self.loops):
                stopped = background.stop()
                if stopped is not None:
                    await stopped
            return time.monotonic() - started

        elapsed = run(body())
        self.assertLess(elapsed, STOP_BUDGET)
        self.assertFalse(any(background.running for background in self.loops))

    def test_a_server_stops_its_own_loops_and_leaves_another_alone(self):
        """Two apps in one process is what the test suite does all day."""
        other = tempfile.TemporaryDirectory(prefix="vela-loops-other-")
        self.addCleanup(other.cleanup)
        root = Path(other.name)
        config = Config(root / "data", root / "apps", ROOT / "web/dist")
        config.ensure_dirs()
        mark = loop_registry.snapshot()
        create_app(config)
        second = loop_registry.since(mark)
        self.addCleanup(lambda: [loop_registry.forget(b) for b in second])
        self.assertEqual(len(second), len(self.loops))
        self.assertFalse({id(b) for b in second} & {id(b) for b in self.loops})


if __name__ == "__main__":
    unittest.main()
