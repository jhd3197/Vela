"""Workspace files and the plain-text editor: controller mode only.

Everything here runs a Vela in controller mode on disposable data with a
disposable directory standing in for `/workspace` (via `VELA_WORKSPACE_DIR`),
driving the workspace tools through the driven-run API the way
`test_terminals.py` drives the terminal — no browser, no network, no real
workspace. Unlike the terminal, the editor keeps no process and needs no pty,
so nothing here is platform-gated; the same suite runs on the windows-latest
leg.

The mode-boundary tests stand an ordinary Vela beside the controller-mode one
and assert that outside controller mode nothing exists: no share, no tools,
no routes.
"""

import atexit
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# api.py constructs its default ASGI app on import. Keep that off the user's data.
_bootstrap = tempfile.TemporaryDirectory(prefix="vela-editor-test-")
atexit.register(_bootstrap.cleanup)
os.environ["VELA_DATA_DIR"] = _bootstrap.name

from fastapi.testclient import TestClient

from vela.agent_runs.tools import (
    TOOL_NAMES,
    WORKSPACE_TOOL_NAMES,
    ToolError,
    tools_for,
)
from vela.api import create_app
from vela.config import CONTROLLER_PROTOCOL, load_config
from vela.editors import MAX_FILE_BYTES, MAX_READ_BYTES, MAX_READ_CHARS, revision_of

TOKEN = "controller-token-" + "x" * 40


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


class ControllerWorkspaceTests(unittest.TestCase):
    """The workspace share, the tools and the editor, in controller mode."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="vela-editor-")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
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
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": f"Bearer {TOKEN}"}
        self.desktops = self.app.state.desktops
        self.runs = self.app.state.agent_runs
        self.editors = self.app.state.editors
        self.desktop = self.client.get("/api/desktops", headers=self.headers).json()["defaultId"]
        self.desktops.store.set_kind(self.desktop, "agent")
        self.desktops.agent_runtime_for = lambda desktop_id: None
        # The real tool surface: these tests are about what it really does.
        self.runs.supervisor.tools = self.desktops.tools

    # ---- plumbing

    def create(self, title="Work with the workspace"):
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

    def tool_result(self, run_id, tool, arguments=None):
        answered = self.step(run_id, tool, arguments)
        self.assertEqual(answered.status_code, 200, answered.text)
        body = answered.json()
        self.assertIn("result", body, body)
        return body["result"]

    def tool_refusal(self, run_id, tool, arguments=None):
        answered = self.step(run_id, tool, arguments)
        self.assertEqual(answered.status_code, 200, answered.text)
        body = answered.json()
        self.assertIn("refusal", body, body)
        return body["refusal"]

    def finish(self, run_id):
        response = self.client.post(
            f"/api/desktops/{self.desktop}/driven-runs/{run_id}/finish",
            headers=self.headers,
            json={"outcome": "succeeded", "summary": "Done."},
        )
        self.assertEqual(response.status_code, 200, response.text)

    # ---- the share

    def test_the_workspace_share_exists_and_answers_to_the_files_routes(self):
        shares = self.client.get("/api/files", headers=self.headers).json()["shares"]
        workspace = next((s for s in shares if s["id"] == "workspace"), None)
        self.assertIsNotNone(workspace, shares)
        self.assertEqual(workspace["label"], "Workspace")
        self.assertEqual(workspace["path"], str(self.workspace))
        self.assertTrue(workspace["writable"])
        self.assertTrue(workspace["reachable"])
        # It browses through the ordinary Files app routes.
        (self.workspace / "hello.txt").write_text("hi", encoding="utf-8")
        (self.workspace / "src").mkdir()
        listed = self.client.get("/api/files/workspace", headers=self.headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        entries = {e["name"]: e for e in listed.json()["entries"]}
        self.assertEqual(entries["hello.txt"]["kind"], "text")
        self.assertEqual(entries["src"]["kind"], "folder")

    def test_workspace_delete_uses_the_existing_trash(self):
        victim = self.workspace / "gone.txt"
        victim.write_text("bye", encoding="utf-8")
        deleted = self.client.delete(
            "/api/files/workspace", headers=self.headers, params={"path": "gone.txt"}
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertFalse(victim.exists())
        trash = self.client.get("/api/files-trash", headers=self.headers).json()["entries"]
        self.assertTrue(any(e["name"].endswith("gone.txt") for e in trash), trash)

    # ---- list, read, write through the tools

    def test_list_read_and_write_round_trip(self):
        run = self.create().json()
        wrote = self.tool_result(
            run["id"], "workspace_write", {"path": "todo.txt", "content": "one\n"}
        )
        self.assertEqual(wrote["path"], "todo.txt")
        self.assertEqual(wrote["revision"], revision_of(b"one\n"))
        self.assertTrue((self.workspace / "todo.txt").is_file())

        read = self.tool_result(run["id"], "workspace_read", {"path": "todo.txt"})
        self.assertEqual(read["content"], "one\n")
        self.assertEqual(read["revision"], wrote["revision"])
        self.assertFalse(read["truncated"])

        # A missing parent folder is said plainly, not created behind the back.
        refused = self.tool_refusal(
            run["id"], "workspace_write", {"path": "no/such/dir/f.txt", "content": "x"}
        )
        self.assertEqual(refused["code"], "not_found")

        # Reading a folder is not a read.
        refused = self.tool_refusal(run["id"], "workspace_read", {"path": "notes"})
        self.assertEqual(refused["code"], "not_found")

        listed = self.tool_result(run["id"], "workspace_list", {"path": ""})
        self.assertIn("todo.txt", [e["name"] for e in listed["entries"]])
        self.finish(run["id"])

    def test_reads_are_sliced_and_capped(self):
        (self.workspace / "big.txt").write_text("0123456789" * 5000, encoding="utf-8")
        run = self.create().json()
        sliced = self.tool_result(
            run["id"], "workspace_read", {"path": "big.txt", "offset": 5, "limit": 10}
        )
        self.assertEqual(sliced["content"], "56789" + "01234")
        self.assertTrue(sliced["truncated"])

        whole = self.tool_result(run["id"], "workspace_read", {"path": "big.txt"})
        self.assertTrue(whole["truncated"])
        self.assertIn("output cut", whole["content"])
        self.assertLessEqual(len(whole["content"]), MAX_READ_CHARS * 2)

        bad_offset = self.tool_refusal(
            run["id"], "workspace_read", {"path": "big.txt", "offset": -1}
        )
        self.assertEqual(bad_offset["code"], "protocol_error")
        self.finish(run["id"])

    def test_a_huge_file_is_refused_for_reading_and_editing(self):
        huge = self.workspace / "huge.bin"
        with huge.open("wb") as handle:
            handle.truncate(MAX_READ_BYTES + 1)
        run = self.create().json()
        refused = self.tool_refusal(run["id"], "workspace_read", {"path": "huge.bin"})
        self.assertEqual(refused["code"], "too_large")
        refused = self.tool_refusal(run["id"], "editor_open", {"path": "huge.bin"})
        self.assertEqual(refused["code"], "too_large")
        self.finish(run["id"])

    def test_the_editor_size_cap_is_its_own(self):
        # Bigger than the editor opens, smaller than a read refuses: the read
        # tool pages through it, the editor says why it will not show it.
        big = self.workspace / "big.txt"
        big.write_text("x" * (MAX_FILE_BYTES + 1), encoding="utf-8")
        run = self.create().json()
        read = self.tool_result(run["id"], "workspace_read", {"path": "big.txt", "limit": 10})
        self.assertEqual(read["content"], "x" * 10)
        refused = self.tool_refusal(run["id"], "editor_open", {"path": "big.txt"})
        self.assertEqual(refused["code"], "too_large")
        self.finish(run["id"])

    def test_binary_files_are_refused(self):
        (self.workspace / "data.bin").write_bytes(b"\xff\xd8\xff\xe0" + os.urandom(64))
        (self.workspace / "wide.txt").write_bytes("text".encode("utf-16-le"))
        run = self.create().json()
        for name in ("data.bin", "wide.txt"):
            with self.subTest(name=name):
                refused = self.tool_refusal(run["id"], "workspace_read", {"path": name})
                self.assertEqual(refused["code"], "unsupported")
                refused = self.tool_refusal(run["id"], "editor_open", {"path": name})
                self.assertEqual(refused["code"], "unsupported")
        self.finish(run["id"])

    # ---- the path rules

    def test_paths_cannot_leave_the_workspace(self):
        outside = Path(self.directory.name) / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        (self.workspace / "in.txt").write_text("in", encoding="utf-8")
        run = self.create().json()
        for path in ("../outside.txt", "..", "/etc/passwd" if os.name == "posix"
                     else str(Path("C:/Windows/system.ini").root) + "Windows"):
            with self.subTest(path=path):
                refusal = self.tool_refusal(run["id"], "workspace_read", {"path": path})
                self.assertEqual(refusal["code"], "not_allowed")
                self.assertIn("outside the share", refusal["detail"])
        # An absolute path that names a file inside the workspace is still an
        # absolute path: refused, the way the share rules refuse it.
        refusal = self.tool_refusal(
            run["id"], "workspace_read", {"path": str(self.workspace / "in.txt")}
        )
        self.assertEqual(refusal["code"], "not_allowed")
        self.finish(run["id"])

    def test_a_symlink_pointing_out_of_the_workspace_is_refused(self):
        outside = Path(self.directory.name) / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        link = self.workspace / "link.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("this platform will not create symlinks without extra rights")
        run = self.create().json()
        refusal = self.tool_refusal(run["id"], "workspace_read", {"path": "link.txt"})
        self.assertEqual(refusal["code"], "not_allowed")
        self.finish(run["id"])

    # ---- revisions and conflicts

    def test_a_stale_save_is_refused_with_a_clear_conflict(self):
        (self.workspace / "notes.txt").write_text("first\n", encoding="utf-8")
        run = self.create().json()
        read = self.tool_result(run["id"], "workspace_read", {"path": "notes.txt"})

        # Somebody else saves first.
        (self.workspace / "notes.txt").write_text("second\n", encoding="utf-8")

        stale = self.tool_refusal(
            run["id"], "workspace_write",
            {"path": "notes.txt", "content": "third\n", "expectedRevision": read["revision"]},
        )
        self.assertEqual(stale["code"], "conflict")
        self.assertIn("Read it again", stale["detail"])
        # The refused save changed nothing.
        self.assertEqual((self.workspace / "notes.txt").read_text(encoding="utf-8"), "second\n")

        # The fresh revision saves.
        fresh = self.tool_result(
            run["id"], "workspace_read", {"path": "notes.txt"}
        )
        saved = self.tool_result(
            run["id"], "workspace_write",
            {"path": "notes.txt", "content": "third\n",
             "expectedRevision": fresh["revision"]},
        )
        self.assertNotEqual(saved["revision"], read["revision"])
        # A save without an expected revision is taken as given.
        blind = self.tool_result(
            run["id"], "workspace_write", {"path": "notes.txt", "content": "fourth\n"}
        )
        self.assertTrue(blind["revision"])
        self.finish(run["id"])

    # ---- the editor window

    def test_editor_open_focuses_one_window_and_frames_serve_a_watcher(self):
        (self.workspace / "plan.txt").write_text("step one\n", encoding="utf-8")
        run = self.create().json()
        opened = self.tool_result(run["id"], "editor_open", {"path": "plan.txt"})
        self.assertEqual(opened["path"], "plan.txt")
        self.assertEqual(opened["title"], "plan.txt")
        self.assertTrue(opened["revision"])
        view_id = opened["viewId"]

        views = self.client.get(
            f"/api/desktops/{self.desktop}/views", headers=self.headers
        ).json()["views"]
        view = next(item for item in views if item["id"] == view_id)
        self.assertEqual(view["kind"], "editor")
        self.assertFalse(view["agentViewable"])

        frame = self.client.get(
            f"/api/desktops/{self.desktop}/views/{view_id}/frame", headers=self.headers
        )
        self.assertEqual(frame.status_code, 200, frame.text)
        body = frame.json()
        self.assertEqual(body["kind"], "editor")
        self.assertEqual(body["path"], "plan.txt")
        self.assertEqual(body["text"], "step one\n")
        first_revision = body["revision"]

        # Asking again focuses the same window rather than opening a second one.
        again = self.tool_result(run["id"], "editor_open", {"path": "plan.txt"})
        self.assertEqual(again["viewId"], view_id)
        views = self.client.get(
            f"/api/desktops/{self.desktop}/views", headers=self.headers
        ).json()["views"]
        self.assertEqual(
            [item["id"] for item in views if item["kind"] == "editor"], [view_id]
        )

        # A save somewhere else moves the frame's text and revision together.
        self.tool_result(
            run["id"], "workspace_write", {"path": "plan.txt", "content": "step two\n"}
        )
        frame = self.client.get(
            f"/api/desktops/{self.desktop}/views/{view_id}/frame", headers=self.headers
        ).json()
        self.assertGreater(frame["revision"], first_revision)
        self.assertEqual(frame["text"], "step two\n")
        self.finish(run["id"])

    def test_editor_open_refuses_what_cannot_be_shown(self):
        (self.workspace / "folder").mkdir()
        run = self.create().json()
        missing = self.tool_refusal(run["id"], "editor_open", {"path": "absent.txt"})
        self.assertEqual(missing["code"], "not_found")
        folder = self.tool_refusal(run["id"], "editor_open", {"path": "folder"})
        self.assertEqual(folder["code"], "not_found")
        # Only an agent desktop holds editor windows.
        other = self.client.post(
            "/api/desktops", headers=self.headers, json={"name": "Personal"}
        ).json()["id"]
        with self.assertRaises(Exception) as caught:
            self.editors.open(other, "absent.txt")
        self.assertIn("not running an agent", str(caught.exception))
        self.finish(run["id"])

    def test_closing_the_window_closes_the_editor(self):
        (self.workspace / "plan.txt").write_text("text\n", encoding="utf-8")
        run = self.create().json()
        opened = self.tool_result(run["id"], "editor_open", {"path": "plan.txt"})
        closed = self.client.delete(
            f"/api/desktops/{self.desktop}/views/{opened['viewId']}", headers=self.headers
        )
        self.assertEqual(closed.status_code, 200, closed.text)
        frame = self.client.get(
            f"/api/desktops/{self.desktop}/views/{opened['viewId']}/frame",
            headers=self.headers,
        )
        self.assertEqual(frame.status_code, 404, frame.text)
        self.finish(run["id"])

    # ---- a person at the keyboard

    def test_a_person_edits_under_a_lease_and_the_driver_holds(self):
        async def fake_command(name, **fields):
            return {"controlEpoch": 1}

        self.desktops.runtime.command = fake_command
        (self.workspace / "draft.txt").write_text("bot words\n", encoding="utf-8")
        run = self.create().json()
        opened = self.tool_result(run["id"], "editor_open", {"path": "draft.txt"})

        taken = self.client.post(
            f"/api/desktops/{self.desktop}/takeover", headers=self.headers,
            json={"viewId": opened["viewId"]},
        )
        self.assertEqual(taken.status_code, 201, taken.text)
        lease = taken.json()

        # The driver is refused while the person holds the desktop.
        held = self.step(
            run["id"], "workspace_write", {"path": "draft.txt", "content": "no"}
        )
        self.assertEqual(held.json()["refusal"]["code"], "person_driving")

        # The person saves through the lease, whole-text, and it lands.
        saved = self.client.post(
            f"/api/desktops/{self.desktop}/takeover/{lease['leaseId']}/input",
            headers=self.headers,
            json={"kind": "text", "viewId": opened["viewId"], "text": "person words\n"},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(
            (self.workspace / "draft.txt").read_text(encoding="utf-8"), "person words\n"
        )

        # Clicks are not something an editor takes.
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
        # The frame saw the person's save: same window, new revision.
        frame = self.client.get(
            f"/api/desktops/{self.desktop}/views/{opened['viewId']}/frame",
            headers=self.headers,
        ).json()
        self.assertEqual(frame["text"], "person words\n")
        self.assertEqual(frame["revision"], revision_of(b"person words\n"))
        self.finish(run["id"])

    # ---- the controller's own routes

    def test_the_controller_content_routes_read_and_write_with_revision(self):
        (self.workspace / "doc.txt").write_text("before\n", encoding="utf-8")
        read = self.client.get(
            f"/api/desktops/{self.desktop}/editor/content",
            headers=self.headers,
            params={"path": "doc.txt"},
        )
        self.assertEqual(read.status_code, 200, read.text)
        body = read.json()
        self.assertEqual(body["content"], "before\n")
        self.assertEqual(body["revision"], revision_of(b"before\n"))

        stale = self.client.put(
            f"/api/desktops/{self.desktop}/editor/content",
            headers=self.headers,
            json={"path": "doc.txt", "content": "after\n", "expectedRevision": "0" * 64},
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertIn("Somebody changed this file", stale.json()["detail"])

        saved = self.client.put(
            f"/api/desktops/{self.desktop}/editor/content",
            headers=self.headers,
            json={"path": "doc.txt", "content": "after\n",
                  "expectedRevision": body["revision"]},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["revision"], revision_of(b"after\n"))
        self.assertEqual((self.workspace / "doc.txt").read_text(encoding="utf-8"), "after\n")

        # The ordinary routes keep the same refusals.
        missing = self.client.get(
            f"/api/desktops/{self.desktop}/editor/content",
            headers=self.headers,
            params={"path": "../escape.txt"},
        )
        self.assertEqual(missing.status_code, 403, missing.text)

    def test_the_editor_route_opens_and_focuses(self):
        (self.workspace / "doc.txt").write_text("words\n", encoding="utf-8")
        opened = self.client.post(
            f"/api/desktops/{self.desktop}/editor",
            headers=self.headers,
            json={"path": "doc.txt"},
        )
        self.assertEqual(opened.status_code, 201, opened.text)
        first = opened.json()
        self.assertEqual(first["path"], "doc.txt")
        self.assertTrue(first["viewId"])
        again = self.client.post(
            f"/api/desktops/{self.desktop}/editor",
            headers=self.headers,
            json={"path": "doc.txt"},
        )
        self.assertEqual(again.status_code, 201, again.text)
        self.assertEqual(again.json()["viewId"], first["viewId"])

        refused = self.client.post(
            f"/api/desktops/{self.desktop}/editor",
            headers=self.headers,
            json={"path": "folder/nope.txt"},
        )
        self.assertEqual(refused.status_code, 404, refused.text)

    # ---- events and discovery

    def test_activity_is_written_to_the_event_line(self):
        (self.workspace / "plan.txt").write_text("one\n", encoding="utf-8")
        run = self.create().json()
        self.tool_result(run["id"], "editor_open", {"path": "plan.txt"})
        self.tool_result(run["id"], "workspace_read", {"path": "plan.txt"})
        self.tool_result(run["id"], "workspace_write", {"path": "plan.txt", "content": "two\n"})
        self.finish(run["id"])
        events = self.client.get(
            f"/api/desktops/{self.desktop}/events", headers=self.headers
        ).json()["events"]
        kinds = [event["kind"] for event in events]
        self.assertIn("editor.opened", kinds)
        for tool in ("workspace_read", "workspace_write"):
            self.assertEqual(
                sum(
                    1 for event in events
                    if event["kind"] == "step.finished" and event["payload"].get("tool") == tool
                ),
                1,
                kinds,
            )

    def test_discovery_reports_the_protocol_and_the_workspace_and_editor(self):
        response = self.client.get("/api/controller", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["protocol"], CONTROLLER_PROTOCOL)
        self.assertEqual(body["features"]["workspace"], True)
        self.assertEqual(body["features"]["editor"], True)

    def test_the_workspace_tools_are_listed_in_the_catalogue(self):
        self.desktops.store.set_kind(self.desktop, "agent")
        response = self.client.get(
            f"/api/desktops/{self.desktop}/driven-runs/tools", headers=self.headers
        )
        offered = {entry["function"]["name"] for entry in response.json()["tools"]}
        self.assertTrue(set(WORKSPACE_TOOL_NAMES) <= offered, offered)

    # ---- lifecycle

    def test_editor_windows_die_with_the_desktop(self):
        (self.workspace / "plan.txt").write_text("one\n", encoding="utf-8")
        run = self.create().json()
        opened = self.tool_result(run["id"], "editor_open", {"path": "plan.txt"})
        self.finish(run["id"])
        disabled = self.client.post(
            f"/api/desktops/{self.desktop}/disable-agent", headers=self.headers
        )
        self.assertEqual(disabled.status_code, 200, disabled.text)
        views = self.client.get(
            f"/api/desktops/{self.desktop}/views", headers=self.headers
        ).json()["views"]
        self.assertNotIn(opened["viewId"], [item["id"] for item in views])
        # The file it showed is untouched, of course.
        self.assertEqual((self.workspace / "plan.txt").read_text(encoding="utf-8"), "one\n")


class ModeBoundaryTests(unittest.TestCase):
    """Outside controller mode there is no workspace share, tool, route or view."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="vela-editor-mode-")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
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

    def test_the_workspace_tools_are_not_listed_nor_callable(self):
        self.assertEqual(
            tuple(tool["name"] for tool in tools_for(self.desktops.terminals, self.desktops.editors)),
            TOOL_NAMES,
        )
        self.assertFalse(self.desktops.editors.supported)
        import asyncio

        async def call():
            return await self.desktops.tools.call(
                self.desktop, "workspace_list", {}, run_id="run-1"
            )

        with self.assertRaises(ToolError) as caught:
            asyncio.run(call())
        self.assertEqual(caught.exception.code, "unknown_tool")

    def test_there_is_no_workspace_share(self):
        shares = self.client.get("/api/files", headers=self.headers).json()["shares"]
        self.assertNotIn("workspace", [s["id"] for s in shares])
        listed = self.client.get("/api/files/workspace", headers=self.headers)
        self.assertEqual(listed.status_code, 404, listed.text)

    def test_the_editor_routes_answer_404(self):
        self.desktops.store.set_kind(self.desktop, "agent")
        for method, path, kwargs in (
            ("POST", f"/api/desktops/{self.desktop}/editor", {"json": {"path": "x.txt"}}),
            ("GET", f"/api/desktops/{self.desktop}/editor/content", {"params": {"path": "x.txt"}}),
            ("PUT", f"/api/desktops/{self.desktop}/editor/content",
             {"json": {"path": "x.txt", "content": "y"}}),
        ):
            with self.subTest(method=method, path=path):
                response = self.client.request(method, path, headers=self.headers, **kwargs)
                self.assertEqual(response.status_code, 404, response.text)
                self.assertIn("controller mode", response.json()["detail"])

    def test_opening_an_editor_by_hand_is_refused(self):
        self.desktops.store.set_kind(self.desktop, "agent")
        refused = self.client.post(
            f"/api/desktops/{self.desktop}/views",
            headers=self.headers,
            json={"kind": "editor"},
        )
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertIn("editor routes", refused.json()["detail"])

    def test_the_service_itself_refuses_outside_controller_mode(self):
        with self.assertRaises(Exception) as caught:
            self.desktops.editors.open(self.desktop, "x.txt")
        self.assertIn("controller mode", str(caught.exception))
        with self.assertRaises(Exception) as caught:
            self.desktops.editors.write("x.txt", "y")
        self.assertIn("controller mode", str(caught.exception))


class EditorUnitTests(unittest.TestCase):
    """The pieces that need no server: revisions, the declared surface."""

    def test_revisions_track_content(self):
        self.assertEqual(revision_of(b"same"), revision_of(b"same"))
        self.assertNotEqual(revision_of(b"one"), revision_of(b"two"))

    def test_the_workspace_tools_exist_only_when_the_service_does(self):
        class Off:
            supported = False

        class On:
            supported = True

        base = tools_for(None)
        self.assertEqual(tuple(tool["name"] for tool in base), TOOL_NAMES)
        self.assertEqual(
            tuple(tool["name"] for tool in tools_for(None, Off())), TOOL_NAMES
        )
        self.assertEqual(
            tuple(tool["name"] for tool in tools_for(None, On())),
            TOOL_NAMES + WORKSPACE_TOOL_NAMES,
        )
        self.assertEqual(len(set(WORKSPACE_TOOL_NAMES)), len(WORKSPACE_TOOL_NAMES))
        for tool in tools_for(None, On()):
            if tool["name"] in WORKSPACE_TOOL_NAMES:
                self.assertIsInstance(tool["changes"], bool, tool["name"])


if __name__ == "__main__":
    unittest.main()
