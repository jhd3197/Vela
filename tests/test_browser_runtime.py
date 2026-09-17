"""The browser an agent desktop renders in, as a process Vela owns.

Phase 0 proved the boundary in a test harness. These are about the thing that
ships: a real child process, started hidden, speaking the frozen protocol over
real pipes, holding a real browser, and — the part that matters most — leaving
nothing behind when it stops, crashes, or is killed while Vela is waiting on it.

Without the browser installed these skip with a reason rather than failing.
Install it with `python scripts/setup-browser-worker.py`.
"""

import asyncio
import http.server
import tempfile
import threading
import unittest
from pathlib import Path

import test_app_contract as base
from vela.desktops.runtime import (
    PROTOCOL_VERSION,
    BrowserRuntime,
    RuntimeUnavailable,
    availability,
    worker_script,
)

STATE = availability()


class _Gateway(http.server.BaseHTTPRequestHandler):
    """A stand-in for the narrow origin Vela publishes for app assets."""

    def do_GET(self):  # noqa: N802 - the base class names it
        if self.path.startswith("/agent-host/"):
            body = b"<!doctype html><title>Fixture app</title><h1 id=h>Fixture app</h1>"
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        """Quiet: a test server's access log is not evidence of anything."""


def serve():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


@unittest.skipUnless(STATE["available"], STATE["detail"] or "the browser runtime is not installed")
class BrowserRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-browser-runtime-")
        self.frames = Path(self.temp.name) / "frames"
        self.runtime = BrowserRuntime(self.frames)
        self.server, self.origin = serve()
        self.policy = {
            "gatewayOrigin": self.origin,
            "gatewayPathPrefixes": ["/agent-host/"],
            "sites": [],
        }

    async def asyncTearDown(self):
        await self.runtime.stop()
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    async def open_desktop(self, desktop_id="d1"):
        await self.runtime.start()
        await self.runtime.open_desktop(desktop_id, "rs-1", self.policy)
        return desktop_id

    # ---- starting and stopping

    async def test_it_starts_and_says_what_it_is(self):
        info = await self.runtime.start()
        self.assertEqual(info.protocol, PROTOCOL_VERSION)
        self.assertTrue(info.browser_available, info.browser_reason)
        self.assertTrue(self.runtime.running)

    async def test_starting_twice_is_the_same_process(self):
        first = await self.runtime.start()
        second = await self.runtime.start()
        self.assertIs(first, second)

    async def test_stopping_leaves_nothing_running(self):
        await self.open_desktop()
        await self.runtime.stop()
        self.assertFalse(self.runtime.running)
        self.assertEqual(self.runtime.desktops, set())

    # ---- desktops and views

    async def test_a_desktop_gets_its_own_browser_and_its_views(self):
        desktop = await self.open_desktop()
        view = await self.runtime.command(
            "view.open",
            desktopId=desktop,
            viewId="v1",
            url=f"{self.origin}/agent-host/app",
        )
        self.assertEqual(view["title"], "Fixture app")
        status = await self.runtime.command("runtime.status")
        self.assertEqual(status["views"], {desktop: ["v1"]})

        closed = await self.runtime.command("view.close", desktopId=desktop, viewId="v1")
        self.assertTrue(closed["closed"])

    async def test_reopening_a_desktop_is_the_same_browser_not_a_second_one(self):
        desktop = await self.open_desktop()
        again = await self.runtime.open_desktop(desktop, "rs-2", self.policy)
        self.assertTrue(again["reused"], "recovering must not leave a browser nobody is tracking")

    async def test_two_desktops_are_two_browsers(self):
        await self.open_desktop("d1")
        await self.runtime.open_desktop("d2", "rs-2", self.policy)
        status = await self.runtime.command("runtime.status")
        self.assertEqual(sorted(status["desktops"]), ["d1", "d2"])
        await self.runtime.close_desktop("d1")
        self.assertEqual((await self.runtime.command("runtime.status"))["desktops"], ["d2"])

    async def test_a_command_for_a_desktop_with_no_browser_is_refused(self):
        await self.runtime.start()
        with self.assertRaises(RuntimeUnavailable):
            await self.runtime.command("view.open", desktopId="nope", viewId="v1", url=self.origin)

    async def test_a_command_nobody_implemented_is_refused(self):
        await self.runtime.start()
        with self.assertRaises(RuntimeUnavailable) as raised:
            await self.runtime.command("view.dance", desktopId="d1")
        self.assertIn("view.dance", str(raised.exception))

    # ---- the boundary, applied by the packaged policy

    async def test_the_policy_the_worker_was_given_is_the_one_it_enforces(self):
        desktop = await self.open_desktop()
        # Not the gateway, and not an approved site: the rest of this computer.
        other, other_origin = serve()
        try:
            with self.assertRaises(RuntimeUnavailable) as raised:
                await self.runtime.command(
                    "view.open",
                    desktopId=desktop,
                    viewId="v-denied",
                    url=f"{other_origin}/agent-host/app",
                )
            self.assertIn("private_network_denied", str(raised.exception))
        finally:
            other.shutdown()
            other.server_close()

    async def test_the_owner_side_of_the_gateway_is_not_reachable(self):
        desktop = await self.open_desktop()
        with self.assertRaises(RuntimeUnavailable) as raised:
            await self.runtime.command(
                "view.open",
                desktopId=desktop,
                viewId="v-owner",
                url=f"{self.origin}/api/settings",
            )
        self.assertIn("gateway_path_denied", str(raised.exception))

    # ---- frames

    async def test_a_frame_is_handed_over_as_a_file_and_not_on_the_channel(self):
        desktop = await self.open_desktop()
        await self.runtime.command(
            "view.open", desktopId=desktop, viewId="v1", url=f"{self.origin}/agent-host/app"
        )
        frame = await self.runtime.command("view.capture", desktopId=desktop, viewId="v1")
        # Images never travel on the control channel: one oversized capture
        # would take the whole protocol down with it.
        self.assertNotIn("image", frame)
        path = self.frames / frame["file"]
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(path.stat().st_size, frame["bytes"])
        self.assertEqual(frame["viewId"], "v1")
        self.assertEqual(frame["runtimeSessionId"], "rs-1")

    async def test_taking_control_moves_the_generation_on(self):
        desktop = await self.open_desktop()
        first = await self.runtime.command("control.take", desktopId=desktop)
        second = await self.runtime.command("control.take", desktopId=desktop)
        self.assertGreater(second["controlEpoch"], first["controlEpoch"])

    # ---- failure

    async def test_killing_the_worker_mid_command_is_a_bounded_failure(self):
        desktop = await self.open_desktop()
        process = self.runtime._process
        waiting = asyncio.create_task(
            self.runtime.command(
                "view.open", desktopId=desktop, viewId="v1", url=f"{self.origin}/agent-host/app",
                timeout=20,
            )
        )
        await asyncio.sleep(0.05)
        process.kill()
        with self.assertRaises(RuntimeUnavailable):
            await waiting
        # And the host knows it is gone rather than waiting on a dead pipe.
        await asyncio.wait_for(process.wait(), 10)
        self.assertFalse(self.runtime.running)
        self.assertEqual(self.runtime.desktops, set())

    async def test_a_command_sent_to_a_stopped_runtime_is_refused_at_once(self):
        await self.runtime.start()
        await self.runtime.stop()
        with self.assertRaises(RuntimeUnavailable):
            await self.runtime.command("runtime.status")

    async def test_an_oversized_command_is_refused_rather_than_sent(self):
        await self.runtime.start()
        with self.assertRaises(RuntimeUnavailable) as raised:
            await self.runtime.command("view.open", desktopId="d1", viewId="v1", url="x" * 2_000_000)
        self.assertIn("too large", str(raised.exception))
        self.assertTrue(self.runtime.running, "refusing to send must not break the pipe")


class RuntimeRouteTests(unittest.TestCase):
    """What the dashboard is told about the runtime, before anything runs."""

    setUp = base.ApiBoundaryTests.setUp
    tearDown = base.ApiBoundaryTests.tearDown

    def test_the_status_says_whether_it_can_run_and_what_is_running(self):
        status = self.client.get("/api/desktops/runtime", headers=self.hub)
        self.assertEqual(status.status_code, 200, status.text)
        body = status.json()
        self.assertIn("available", body)
        self.assertFalse(body["running"], "nothing starts a browser on its own")
        self.assertEqual(body["desktops"], [])
        if not body["available"]:
            self.assertTrue(body["detail"], "an unavailable runtime says what to do")

    def test_the_status_needs_a_hub_session(self):
        self.assertEqual(self.client.get("/api/desktops/runtime").status_code, 401)

    def test_runtime_is_not_mistaken_for_a_desktop_id(self):
        # The route is declared before "/{desktop_id}"; this is what says so.
        self.assertEqual(
            self.client.get("/api/desktops/runtime", headers=self.hub).status_code, 200
        )


class AvailabilityTests(unittest.TestCase):
    """What Vela says when the runtime cannot run, before it accepts work."""

    def test_it_reports_a_reason_somebody_can_act_on(self):
        state = availability()
        self.assertIn("available", state)
        if not state["available"]:
            self.assertTrue(state["detail"])
            self.assertRegex(state["detail"], r"(install|Reinstall|Run) ")

    def test_the_worker_ships_with_this_checkout(self):
        self.assertTrue(worker_script().is_file(), "the worker is part of the repository")


if __name__ == "__main__":
    unittest.main()
