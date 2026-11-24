"""Terminals: a shell inside the bot's own computer, controller mode only.

The PTY tests run a Vela in controller mode on disposable data with a
disposable directory standing in for `/workspace` (via `VELA_WORKSPACE_DIR`),
driving real pseudo-terminals with the system shell — no browser, no network,
no real workspace. A takeover test stands in for the browser the way
`test_driven_runs.py` does, because taking over asks the runtime for a fresh
control generation first.

Windows has no pty, which is exactly the unsupported path under test: the
module-level rules run everywhere, and every PTY test skips there the way the
suite skips browser tests where no browser exists.
"""

import atexit
import asyncio
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# api.py constructs its default ASGI app on import. Keep that off the user's data.
_bootstrap = tempfile.TemporaryDirectory(prefix="vela-terminal-test-")
atexit.register(_bootstrap.cleanup)
os.environ["VELA_DATA_DIR"] = _bootstrap.name

from fastapi.testclient import TestClient

from vela.agent_runs.tools import TERMINAL_TOOL_NAMES, TOOL_NAMES, ToolError, tools_for
from vela.api import create_app
from vela.config import load_config
from vela.terminals import MAX_SCROLLBACK_BYTES, clip_output, shell_env, strip_ansi

POSIX = os.name == "posix"
TOKEN = "controller-token-" + "x" * 40
requires_pty = unittest.skipUnless(POSIX, "terminals need a POSIX pty")


def write_token_file(root):
    path = Path(root) / "controller-token"
    path.write_text(TOKEN + "\n", encoding="utf-8")
    return path


def wait_for(condition, timeout=15, interval=0.05):
    """Poll until a condition holds, for things settled by a background task."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("the condition never held")


class TerminalUnitTests(unittest.TestCase):
    """The pieces that need no server: the env contract, clipping, the listing."""

    def test_the_shell_environment_is_built_never_inherited(self):
        workspace = Path(tempfile.mkdtemp(prefix="vela-env-")).resolve()
        env = shell_env(workspace, "/bin/sh")
        # Everything points at the workspace; PATH starts at its .local/bin so
        # what a bot installs there is what the shell finds first.
        self.assertEqual(env["HOME"], str(workspace / ".home"))
        self.assertEqual(env["PREFIX"], str(workspace / ".local"))
        self.assertEqual(env["NPM_CONFIG_PREFIX"], str(workspace / ".local"))
        self.assertEqual(env["PYTHONUSERBASE"], str(workspace / ".local"))
        self.assertEqual(env["PIP_USER"], "1")
        self.assertTrue(env["PATH"].startswith(str(workspace / ".local" / "bin") + ":"))
        # Nothing of Vela's environment reaches the shell: no token path, no
        # session keys, nothing else this process was handed.
        for key in env:
            self.assertFalse(key.startswith("VELA_"), key)
        for value in env.values():
            self.assertNotIn(TOKEN, value)
            self.assertNotIn("controller-token", value)

    def test_clipping_keeps_head_and_tail_and_says_so(self):
        text = "x" * 100_000
        clipped, truncated = clip_output(text, limit=1000)
        self.assertTrue(truncated)
        self.assertIn("output cut", clipped)
        self.assertLess(len(clipped), 2000)
        self.assertTrue(clipped.startswith("x" * 400))
        self.assertTrue(clipped.endswith("x" * 400))
        short, truncated = clip_output("small")
        self.assertFalse(truncated)
        self.assertEqual(short, "small")

    def test_control_sequences_are_stripped_for_frames(self):
        self.assertEqual(strip_ansi("\x1b[31mred\x1b[0m\nplain"), "red\nplain")
        self.assertEqual(strip_ansi("\x1b]0;title\x07text"), "text")

    def test_the_terminal_tools_exist_only_when_the_service_does(self):
        class Off:
            supported = False

        class On:
            supported = True

        self.assertEqual(tuple(tool["name"] for tool in tools_for(None)), TOOL_NAMES)
        self.assertEqual(tuple(tool["name"] for tool in tools_for(Off())), TOOL_NAMES)
        self.assertEqual(
            tuple(tool["name"] for tool in tools_for(On())),
            TOOL_NAMES + TERMINAL_TOOL_NAMES,
        )


class ControllerTerminalTests(unittest.TestCase):
    """Terminal tools through the driven-run API, on real PTYs."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="vela-terminal-")
        self.addCleanup(self.directory.cleanup)
        # Resolved, because the terminal reports real paths and this root is
        # compared to them: the temp directory can be a symlink (on macOS
        # /var is /private/var).
        root = Path(self.directory.name).resolve()
        self.workspace = root / "workspace"
        self.token_file = write_token_file(root)
        self.environment = patch.dict(os.environ, {
            "VELA_DATA_DIR": str(root / "data"),
            "VELA_CONTROLLER_MODE": "1",
            "VELA_CONTROLLER_TOKEN_FILE": str(self.token_file),
            "VELA_WORKSPACE_DIR": str(self.workspace),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.app = create_app(load_config())
        # Terminals live on the server's one event loop; the context manager
        # gives this client one persistent portal, the way uvicorn gives the
        # real server one persistent loop. A portal per request would close
        # the terminal's loop behind it.
        self.client_stack = TestClient(self.app)
        self.client = self.client_stack.__enter__()
        self.headers = {"Authorization": f"Bearer {TOKEN}"}
        self.desktops = self.app.state.desktops
        self.runs = self.app.state.agent_runs
        self.terminals = self.app.state.terminals
        self.addCleanup(self._close_client)
        self.desktop = self.client.get("/api/desktops", headers=self.headers).json()["defaultId"]
        self.desktops.store.set_kind(self.desktop, "agent")
        self.desktops.agent_runtime_for = lambda desktop_id: None
        # The real tool surface: these tests are about what it really does.
        self.runs.supervisor.tools = self.desktops.tools

    def _close_client(self):
        # Kill every shell the test left open while the portal is alive, then
        # close the client — its shutdown runs the server's own stop_all.
        self.terminals.stop_all()
        self.client_stack.__exit__(None, None, None)

    # ---- plumbing

    def create(self, title="Work in the terminal"):
        return self.client.post(
            f"/api/desktops/{self.desktop}/driven-runs",
            headers=self.headers,
            json={"title": title},
        )

    def step(self, run_id, tool, arguments=None, client_step_id=None):
        return self.client.post(
            f"/api/desktops/{self.desktop}/driven-runs/{run_id}/steps",
            headers=self.headers,
            json={
                "tool": tool,
                "arguments": arguments or {},
                "clientStepId": client_step_id or f"{tool}-{time.time_ns()}",
            },
        )

    def open_terminal(self, run_id, **arguments):
        answered = self.step(run_id, "terminal_open", arguments)
        self.assertEqual(answered.status_code, 200, answered.text)
        body = answered.json()
        self.assertIn("result", body, body)
        return body["result"]

    def run_command(self, run_id, terminal_id, command, **arguments):
        answered = self.step(
            run_id, "terminal_run", {"terminalId": terminal_id, "command": command, **arguments}
        )
        self.assertEqual(answered.status_code, 200, answered.text)
        body = answered.json()
        self.assertIn("result", body, body)
        return body["result"]

    def finish(self, run_id):
        response = self.client.post(
            f"/api/desktops/{self.desktop}/driven-runs/{run_id}/finish",
            headers=self.headers,
            json={"outcome": "succeeded", "summary": "Done."},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def process_alive(self, terminal_id):
        terminal = self.terminals._all.get(terminal_id)
        if terminal is None:
            return False
        pid = terminal.pid
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return terminal.exit_code is None

    # ---- the round trip

    @requires_pty
    def test_open_run_read_and_close(self):
        run = self.create().json()
        opened = self.open_terminal(run["id"], title="Dev server")
        self.assertEqual(opened["desktopId"], self.desktop)
        self.assertEqual(opened["cwd"], str(self.workspace))
        self.assertTrue(opened["running"])
        self.assertIn(Path(opened["shell"]).name, ("bash", "sh"))
        # The terminal is a window on the desktop, watched like any other.
        views = self.client.get(
            f"/api/desktops/{self.desktop}/views", headers=self.headers
        ).json()["views"]
        view = next(item for item in views if item["id"] == opened["viewId"])
        self.assertEqual(view["kind"], "terminal")
        self.assertEqual(view["title"], "Dev server")
        self.assertFalse(view["agentViewable"])

        ran = self.run_command(run["id"], opened["terminalId"], "echo hello-$((20+22))")
        self.assertEqual(ran["exitCode"], 0)
        self.assertFalse(ran["running"])
        self.assertIn("hello-42", ran["output"])

        since = ran["cursor"]
        self.run_command(run["id"], opened["terminalId"], "echo second")
        read = self.step(
            run["id"], "terminal_read",
            {"terminalId": opened["terminalId"], "sinceCursor": since},
        ).json()["result"]
        self.assertIn("second", read["output"])
        self.assertGreater(read["cursor"], since)
        self.assertFalse(read["dropped"])

        closed = self.step(
            run["id"], "terminal_close", {"terminalId": opened["terminalId"]}
        ).json()["result"]
        self.assertTrue(closed["closed"])
        wait_for(lambda: not self.process_alive(opened["terminalId"]), timeout=5)
        views = self.client.get(
            f"/api/desktops/{self.desktop}/views", headers=self.headers
        ).json()["views"]
        self.assertNotIn(opened["viewId"], [item["id"] for item in views])
        self.finish(run["id"])

    @requires_pty
    def test_the_environment_is_rooted_at_the_workspace(self):
        run = self.create().json()
        opened = self.open_terminal(run["id"])
        terminal_id = opened["terminalId"]
        checks = {
            "pwd": str(self.workspace),
            "echo $HOME": str(self.workspace / ".home"),
            "echo $NPM_CONFIG_PREFIX": str(self.workspace / ".local"),
            "echo $PYTHONUSERBASE": str(self.workspace / ".local"),
            "echo $PIP_USER": "1",
            "basename $PREFIX": ".local",
        }
        for command, expected in checks.items():
            ran = self.run_command(run["id"], terminal_id, command)
            self.assertEqual(ran["exitCode"], 0, f"{command}: {ran['output']}")
            self.assertIn(expected, ran["output"], f"{command}: {ran['output']!r}")
        ran = self.run_command(run["id"], terminal_id, "echo $PATH")
        self.assertIn(str(self.workspace / ".local" / "bin"), ran["output"])
        # None of Vela's configuration leaks through: the token path is not in
        # the shell's environment, and neither is anything else Vela-named.
        ran = self.run_command(
            run["id"], terminal_id, "echo token-file:${VELA_CONTROLLER_TOKEN_FILE:-none}"
        )
        self.assertIn("token-file:none", ran["output"])
        ran = self.run_command(
            run["id"], terminal_id, "test -z \"$VELA_CONTROLLER_TOKEN\" && echo no-token-value"
        )
        self.assertEqual(ran["exitCode"], 0, ran["output"])
        self.assertIn("no-token-value", ran["output"])
        self.finish(run["id"])

    @requires_pty
    def test_a_failing_command_reports_its_exit_code(self):
        run = self.create().json()
        opened = self.open_terminal(run["id"])
        ran = self.run_command(run["id"], opened["terminalId"], "exit 3")
        self.assertEqual(ran["exitCode"], 3)
        self.assertFalse(ran["running"])
        self.finish(run["id"])

    # ---- bounds

    @requires_pty
    def test_long_output_comes_back_capped_with_a_marker(self):
        import re

        run = self.create().json()
        opened = self.open_terminal(run["id"])
        ran = self.run_command(run["id"], opened["terminalId"], "seq 1 20000")
        self.assertEqual(ran["exitCode"], 0)
        self.assertTrue(ran["truncated"])
        self.assertIn("output cut", ran["output"])
        self.assertLess(len(ran["output"].encode("utf-8")), 20_000)
        # Both ends survive, so the answer is still useful. (Carriage returns
        # are normalized: readline redraws leave a stray one at the seam.)
        normalized = ran["output"].replace("\r", "")
        self.assertTrue(re.search(r"^1$", normalized, re.M))
        self.assertIn("20000", normalized)

    @requires_pty
    def test_the_scrollback_is_bounded(self):
        run = self.create().json()
        opened = self.open_terminal(run["id"])
        terminal = self.terminals.get(self.desktop, opened["terminalId"])
        self.run_command(
            run["id"], opened["terminalId"], f"head -c {MAX_SCROLLBACK_BYTES * 3} /dev/zero | tr '\\0' 'x'"
        )
        self.assertLessEqual(terminal._bytes, MAX_SCROLLBACK_BYTES)
        # A read from before the cut says output was lost; the driver's step
        # answer clips a result this large, so the flag is checked on the
        # service result itself.
        self.assertTrue(self.terminals.read(self.desktop, opened["terminalId"], 0)["dropped"])
        recent = self.terminals.read(self.desktop, opened["terminalId"], terminal.cursor - 1)
        self.assertFalse(recent["dropped"])
        self.finish(run["id"])

    @requires_pty
    def test_a_timeout_leaves_the_command_running(self):
        run = self.create().json()
        opened = self.open_terminal(run["id"])
        started = time.monotonic()
        ran = self.run_command(
            run["id"], opened["terminalId"], "sleep 60", timeoutSeconds=1
        )
        self.assertLess(time.monotonic() - started, 20)
        self.assertTrue(ran["running"])
        self.assertIsNone(ran["exitCode"])
        # The terminal itself is alive and usable: Ctrl-C ends the sleep and
        # the next command answers.
        wrote = self.step(
            run["id"], "terminal_write", {"terminalId": opened["terminalId"], "keys": "ctrl-c"}
        ).json()["result"]
        self.assertTrue(wrote["sent"])
        ran = self.run_command(run["id"], opened["terminalId"], "echo still-here")
        self.assertEqual(ran["exitCode"], 0)
        self.assertIn("still-here", ran["output"])
        self.finish(run["id"])

    @requires_pty
    def test_the_timeout_is_capped(self):
        run = self.create().json()
        opened = self.open_terminal(run["id"])
        started = time.monotonic()
        # Asking for an hour gets the cap, not a step that outlives the driver.
        ran = self.run_command(
            run["id"], opened["terminalId"], "echo quick", timeoutSeconds=3600
        )
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(ran["exitCode"], 0)
        self.finish(run["id"])

    @requires_pty
    def test_a_dev_server_keeps_running_after_waitfor_none(self):
        run = self.create().json()
        opened = self.open_terminal(run["id"])
        sent = self.run_command(
            run["id"], opened["terminalId"], "sleep 30 && echo late-done", waitFor="none"
        )
        self.assertTrue(sent["running"])
        self.assertIsNone(sent["exitCode"])
        wait_for(lambda: self.terminals.get(self.desktop, opened["terminalId"]).cursor > 0)
        # Something is on the line; Ctrl-C clears it for the next command.
        self.step(
            run["id"], "terminal_write", {"terminalId": opened["terminalId"], "keys": "ctrl-c"}
        )
        ran = self.run_command(run["id"], opened["terminalId"], "echo next")
        self.assertEqual(ran["exitCode"], 0)
        self.finish(run["id"])

    # ---- validation, and the per-desktop cap

    @requires_pty
    def test_only_a_few_terminals_per_desktop(self):
        from vela.terminals import MAX_TERMINALS_PER_DESKTOP

        run = self.create().json()
        opened = []
        for index in range(MAX_TERMINALS_PER_DESKTOP):
            opened.append(self.open_terminal(run["id"], title=f"t{index}"))
        refused = self.step(run["id"], "terminal_open", {"title": "one more"})
        body = refused.json()
        self.assertIn("refusal", body, body)
        self.assertIn("Close one", body["refusal"]["detail"])
        # Closing one makes room.
        self.step(run["id"], "terminal_close", {"terminalId": opened[0]["terminalId"]})
        self.open_terminal(run["id"], title="again")
        self.finish(run["id"])

    @requires_pty
    def test_the_working_directory_stays_inside_the_workspace(self):
        run = self.create().json()
        escaped = self.step(run["id"], "terminal_open", {"cwd": "/etc"})
        self.assertEqual(escaped.json()["refusal"]["code"], "protocol_error")
        upward = self.step(run["id"], "terminal_open", {"cwd": ".."})
        self.assertEqual(upward.json()["refusal"]["code"], "protocol_error")
        made = self.open_terminal(run["id"], cwd="project/app")
        self.assertEqual(made["cwd"], str(self.workspace / "project" / "app"))
        self.assertTrue((self.workspace / "project" / "app").is_dir())
        self.finish(run["id"])

    @requires_pty
    def test_a_command_is_one_line(self):
        run = self.create().json()
        opened = self.open_terminal(run["id"])
        refused = self.step(
            run["id"], "terminal_run",
            {"terminalId": opened["terminalId"], "command": "echo one\necho two"},
        )
        self.assertEqual(refused.json()["refusal"]["code"], "protocol_error")
        self.finish(run["id"])

    @requires_pty
    def test_terminal_ids_do_not_wander_between_desktops(self):
        other = self.client.post("/api/desktops", headers=self.headers, json={"name": "Two"}).json()["id"]
        self.desktops.store.set_kind(other, "agent")
        run = self.create().json()
        opened = self.open_terminal(run["id"])
        # Its own desktop reads it fine.
        read = self.step(
            run["id"], "terminal_read", {"terminalId": opened["terminalId"]}
        )
        self.assertIn("result", read.json())
        # Another desktop cannot name it.
        with self.assertRaises(Exception):
            self.terminals.get(other, opened["terminalId"])
        unknown = self.step(run["id"], "terminal_read", {"terminalId": "f" * 32})
        self.assertEqual(unknown.json()["refusal"]["code"], "not_found")
        self.finish(run["id"])

    # ---- watching, and a person at the keyboard

    @requires_pty
    def test_frames_are_served_to_a_watcher(self):
        run = self.create().json()
        opened = self.open_terminal(run["id"])
        self.run_command(run["id"], opened["terminalId"], "echo watcher-marker")
        frame = self.client.get(
            f"/api/desktops/{self.desktop}/views/{opened['viewId']}/frame",
            headers=self.headers,
        )
        self.assertEqual(frame.status_code, 200, frame.text)
        body = frame.json()
        self.assertEqual(body["kind"], "terminal")
        self.assertEqual(body["viewId"], opened["viewId"])
        self.assertEqual(body["terminalId"], opened["terminalId"])
        self.assertIn("watcher-marker", body["text"])
        self.assertIn("cursor", body)
        first_revision = body["revision"]
        self.run_command(run["id"], opened["terminalId"], "echo another-line")
        frame = self.client.get(
            f"/api/desktops/{self.desktop}/views/{opened['viewId']}/frame",
            headers=self.headers,
        ).json()
        self.assertGreater(frame["revision"], first_revision)
        self.assertIn("another-line", frame["text"])
        self.finish(run["id"])

    @requires_pty
    def test_a_person_taking_over_types_into_the_terminal(self):
        async def fake_command(name, **fields):
            return {"controlEpoch": 1}

        self.desktops.runtime.command = fake_command
        run = self.create().json()
        opened = self.open_terminal(run["id"])
        taken = self.client.post(
            f"/api/desktops/{self.desktop}/takeover", headers=self.headers,
            json={"viewId": opened["viewId"]},
        )
        self.assertEqual(taken.status_code, 201, taken.text)
        lease = taken.json()
        # The driver is stopped while the person holds the desktop.
        held = self.step(
            run["id"], "terminal_write", {"terminalId": opened["terminalId"], "text": "no"}
        )
        self.assertEqual(held.json()["refusal"]["code"], "person_driving")
        # The person types into the terminal through the same lease route any
        # other view answers to: text first, then the Enter key by name.
        typed = self.client.post(
            f"/api/desktops/{self.desktop}/takeover/{lease['leaseId']}/input",
            headers=self.headers,
            json={"kind": "text", "viewId": opened["viewId"], "text": "echo from-a-person"},
        )
        self.assertEqual(typed.status_code, 200, typed.text)
        keyed = self.client.post(
            f"/api/desktops/{self.desktop}/takeover/{lease['leaseId']}/input",
            headers=self.headers,
            json={"kind": "key", "viewId": opened["viewId"], "key": "enter"},
        )
        self.assertEqual(keyed.status_code, 200, keyed.text)
        refused_click = self.client.post(
            f"/api/desktops/{self.desktop}/takeover/{lease['leaseId']}/input",
            headers=self.headers,
            json={"kind": "click", "viewId": opened["viewId"], "point": {"x": 1, "y": 1}},
        )
        self.assertEqual(refused_click.status_code, 422, refused_click.text)
        self.client.delete(
            f"/api/desktops/{self.desktop}/takeover/{lease['leaseId']}", headers=self.headers
        )
        self.client.post(
            f"/api/desktops/{self.desktop}/tasks/{run['id']}/control",
            headers=self.headers,
            json={"action": "resume"},
        )
        read = self.step(
            run["id"], "terminal_read", {"terminalId": opened["terminalId"], "sinceCursor": 0}
        ).json()["result"]
        self.assertIn("from-a-person", read["output"])
        self.finish(run["id"])

    # ---- lifecycle

    @requires_pty
    def test_a_cancelled_run_takes_its_terminals_not_other_runs(self):
        # Another desktop must exist before this one can be deleted.
        self.client.post("/api/desktops", headers=self.headers, json={"name": "Two"})
        first = self.create(title="first").json()
        kept = self.open_terminal(first["id"])
        self.finish(first["id"])
        wait_for(lambda: self.runs.store.get(first["id"])["state"] == "succeeded")
        second = self.create(title="second").json()
        taken = self.open_terminal(second["id"])
        cancelled = self.client.post(
            f"/api/desktops/{self.desktop}/driven-runs/{second['id']}/cancel",
            headers=self.headers,
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        # The cancelled run's shell is gone; the finished run's dev server
        # keeps running, and dies only when its desktop does.
        wait_for(lambda: not self.process_alive(taken["terminalId"]), timeout=5)
        self.assertTrue(self.process_alive(kept["terminalId"]))
        deleted = self.client.delete(
            f"/api/desktops/{self.desktop}", headers=self.headers
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        wait_for(lambda: not self.process_alive(kept["terminalId"]), timeout=5)

    @requires_pty
    def test_closing_the_window_kills_the_shell(self):
        run = self.create().json()
        opened = self.open_terminal(run["id"])
        closed = self.client.delete(
            f"/api/desktops/{self.desktop}/views/{opened['viewId']}", headers=self.headers
        )
        self.assertEqual(closed.status_code, 200, closed.text)
        wait_for(lambda: not self.process_alive(opened["terminalId"]), timeout=5)
        gone = self.step(
            run["id"], "terminal_read", {"terminalId": opened["terminalId"]}
        )
        self.assertEqual(gone.json()["refusal"]["code"], "not_found")
        self.finish(run["id"])

    @requires_pty
    def test_disabling_the_agent_ends_its_terminals(self):
        run = self.create().json()
        opened = self.open_terminal(run["id"])
        self.finish(run["id"])
        self.client.post(
            f"/api/desktops/{self.desktop}/disable-agent", headers=self.headers
        )
        wait_for(lambda: not self.process_alive(opened["terminalId"]), timeout=5)


class ModeBoundaryTests(unittest.TestCase):
    """Outside controller mode there is no terminal, and no way to ask for one."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="vela-terminal-mode-")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name).resolve()
        self.workspace = root / "workspace"
        self.environment = patch.dict(os.environ, {
            "VELA_DATA_DIR": str(root / "data"),
            "VELA_WORKSPACE_DIR": str(self.workspace),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.app = create_app(load_config())
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.headers = {"Authorization": f"Bearer {token}"}
        self.desktops = self.app.state.desktops
        self.desktop = self.client.get("/api/desktops", headers=self.headers).json()["defaultId"]

    def test_the_terminal_tools_are_not_listed_outside_controller_mode(self):
        self.assertEqual(
            tuple(tool["name"] for tool in tools_for(self.desktops.terminals)), TOOL_NAMES
        )
        self.assertFalse(self.desktops.terminals.supported)

    def test_opening_a_terminal_is_refused_outside_controller_mode(self):
        with self.assertRaises(Exception) as caught:
            self.desktops.terminals.open(self.desktop)
        self.assertIn("controller mode", str(caught.exception))

    def test_the_tool_surface_refuses_terminal_tools(self):
        async def call():
            return await self.desktops.tools.call(
                self.desktop, "terminal_open", {}, run_id="run-1"
            )

        with self.assertRaises(ToolError) as caught:
            asyncio.run(call())
        self.assertEqual(caught.exception.code, "unknown_tool")

    def test_opening_a_terminal_window_by_hand_is_refused(self):
        self.desktops.store.set_kind(self.desktop, "agent")
        refused = self.client.post(
            f"/api/desktops/{self.desktop}/views",
            headers=self.headers,
            json={"kind": "terminal"},
        )
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertIn("terminal tools", refused.json()["detail"])


class UnsupportedPlatformTests(unittest.TestCase):
    """A controller-mode Vela without a pty says so, cleanly."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="vela-terminal-nopty-")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name).resolve()
        self.workspace = root / "workspace"
        self.token_file = write_token_file(root)
        self.environment = patch.dict(os.environ, {
            "VELA_DATA_DIR": str(root / "data"),
            "VELA_CONTROLLER_MODE": "1",
            "VELA_CONTROLLER_TOKEN_FILE": str(self.token_file),
            "VELA_WORKSPACE_DIR": str(self.workspace),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_without_a_pty_the_terminal_reports_itself_unsupported(self):
        import vela.terminals as terminals_module

        with patch.object(terminals_module, "_HAS_PTY", False):
            app = create_app(load_config())
            client = TestClient(app)
            self.addCleanup(client.close)
            headers = {"Authorization": f"Bearer {TOKEN}"}
            desktops = app.state.desktops
            desktop = client.get("/api/desktops", headers=headers).json()["defaultId"]
            desktops.store.set_kind(desktop, "agent")
            with self.assertRaises(Exception) as caught:
                desktops.terminals.open(desktop)
            self.assertIn("pseudo-terminal", str(caught.exception))
            self.assertFalse(desktops.terminals.supported)


if __name__ == "__main__":
    unittest.main()
