"""The tools an agent run perceives and acts through.

Two halves.

The first needs nothing: the no-progress rule and the evidence record are plain
Python, and a fixture browser would tell us nothing about them.

The second needs everything. A listening Vela on a disposable data directory, an
installed app, a managed browser, and an agent run that opens a window, reads
what is in it, types into the real app and asks it to save through a named
action. What is being checked there is not that a tool call returned a value —
it is that the app's stored data changed only when a grant said it could, and
that a reference from one desktop is worth nothing on another.

Tool calls are scheduled onto the server's own event loop. The browser runtime's
pipes and pending futures live there, and awaiting them from a second loop is
how a test passes for reasons nobody can explain.
"""

import asyncio
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import test_app_contract as base
import uvicorn
from scripts.fixture_apps import APPS as FIXTURE_APPS
from vela.agent_runs.observations import NO_PROGRESS_LIMIT, ObservationLog, StalledError, fingerprint
from vela.agent_runs.approvals import ApprovalPending
from vela.agent_runs.tools import TOOLS, TOOL_NAMES, ToolError
from vela.api import create_app
from vela.config import Config
from vela.desktops.runtime import availability

ROOT = base.ROOT
STATE = availability()


def observation(url="http://x/one", controls=(("button", "Save"),), text="hello"):
    """An observation shaped like the worker's, for the parts that only read it."""
    return {
        "observationId": "o1",
        "page": {
            "url": url,
            "title": "A window",
            "text": text,
            "controls": [
                {"role": role, "name": name, "value": None, "disabled": False}
                for role, name in controls
            ],
            "dialogs": [],
            "untrusted": True,
        },
    }


class ToolSurfaceTests(unittest.TestCase):
    """What the surface is, before anything runs on it."""

    def test_nothing_in_the_surface_executes_arbitrary_code(self):
        # The list is the contract. A tool that could evaluate, run a command or
        # read a path would make every other check in this feature decorative.
        for name in TOOL_NAMES:
            self.assertNotIn("eval", name)
            self.assertNotIn("script", name)
            self.assertNotIn("shell", name)
            self.assertNotIn("exec", name)
            self.assertNotIn("fetch", name)
        self.assertEqual(len(set(TOOL_NAMES)), len(TOOL_NAMES))

    def test_every_tool_says_whether_it_changes_anything(self):
        for tool in TOOLS:
            self.assertIn("summary", tool, tool["name"])
            self.assertIn("arguments", tool, tool["name"])
            self.assertIsInstance(tool["changes"], bool, tool["name"])

    def test_a_refusal_carries_a_code_a_run_can_act_on(self):
        error = ToolError("not_allowed", "no", retryable=False)
        self.assertEqual(
            error.as_dict(), {"error": "not_allowed", "detail": "no", "retryable": False}
        )


class ObservationLogTests(unittest.TestCase):
    """Noticing that nothing is happening, and keeping the evidence that it did."""

    def setUp(self):
        self.log = ObservationLog()

    def test_a_view_that_never_changes_stops_being_looked_at(self):
        for expected in range(1, NO_PROGRESS_LIMIT):
            state = self.log.record("d1", "v1", observation())
            self.assertEqual(state["repeats"], expected)
        with self.assertRaises(StalledError) as caught:
            self.log.record("d1", "v1", observation())
        self.assertIn("Nothing is changing", caught.exception.detail)
        # And the count is cleared, so a deliberate change of approach is not
        # refused on its first look.
        self.assertEqual(self.log.record("d1", "v1", observation())["repeats"], 1)

    def test_a_page_that_moved_starts_the_count_again(self):
        self.log.record("d1", "v1", observation())
        self.log.record("d1", "v1", observation())
        state = self.log.record("d1", "v1", observation(controls=(("button", "Saved"),)))
        self.assertEqual(state["repeats"], 1)

    def test_text_that_only_ticks_does_not_count_as_progress(self):
        # A clock in the corner changes the page on every look. Counting that as
        # progress would make the no-progress rule useless.
        first = fingerprint(observation(text="12:00:01"))
        second = fingerprint(observation(text="12:00:02"))
        self.assertEqual(first, second)
        moved = fingerprint(observation(text="a much longer page than before"))
        self.assertNotEqual(first, moved)

    def test_acting_on_a_view_ends_the_run_of_identical_looks(self):
        self.log.record("d1", "v1", observation())
        self.log.record("d1", "v1", observation())
        self.log.progressed("d1", "v1")
        self.assertEqual(self.log.record("d1", "v1", observation())["repeats"], 1)

    def test_two_views_are_counted_apart(self):
        for _ in range(NO_PROGRESS_LIMIT - 1):
            self.log.record("d1", "v1", observation())
        self.assertEqual(self.log.record("d1", "v2", observation())["repeats"], 1)

    def test_evidence_is_bounded_and_keeps_the_recent_end(self):
        log = ObservationLog(keep=3)
        for index in range(10):
            log.note_action("d1", "r1", tool="desktop.click", target=f"ref{index}")
        records = log.evidence("d1", "r1")
        self.assertEqual(len(records), 3)
        self.assertEqual(records[-1]["target"], "ref9")

    def test_evidence_records_the_shape_of_a_result_not_a_page(self):
        self.log.note_action(
            "d1",
            "r1",
            tool="desktop.observe",
            result={"observationId": "o1", "text": "x" * 5000, "controls": [1, 2, 3]},
        )
        stored = self.log.evidence("d1", "r1")[0]["result"]
        self.assertEqual(stored, {"observationId": "o1"})

    def test_a_desktop_can_be_forgotten_entirely(self):
        self.log.record("d1", "v1", observation())
        self.log.note_action("d1", "r1", tool="desktop.click")
        self.log.forget_desktop("d1")
        self.assertIsNone(self.log.latest("d1", "v1"))
        self.assertEqual(self.log.evidence("d1", "r1"), [])


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(STATE["available"], STATE["detail"] or "the browser runtime is not installed")
class AgentToolTests(unittest.TestCase):
    """A real server, a real browser, a real app, a real run."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="vela-agent-tools-")
        root = Path(cls.temp.name)
        cls.apps = root / "catalog"
        cls.apps.mkdir()
        shutil.copytree(FIXTURE_APPS / "notes", cls.apps / "notes")
        cls.port = free_port()
        import os

        os.environ["VELA_PORT"] = str(cls.port)
        cls.config = Config(root / "data", cls.apps, ROOT / "web/dist")
        cls.config.ensure_dirs()
        cls.app = create_app(cls.config)
        cls.server = uvicorn.Server(
            uvicorn.Config(cls.app, host="127.0.0.1", port=cls.port, log_level="warning")
        )
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"
        for _ in range(300):
            try:
                import httpx

                if httpx.get(f"{cls.base}/api/health", timeout=1).status_code == 200:
                    if getattr(cls.app.state, "loop", None):
                        break
            except Exception:  # noqa: BLE001 - it is simply not up yet
                pass
            time.sleep(0.05)
        else:
            raise RuntimeError("the fixture server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=20)
        cls.temp.cleanup()

    def setUp(self):
        import httpx

        self.client = httpx.Client(base_url=self.base, timeout=60)
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}
        self.desktop = self.client.get("/api/desktops", headers=self.hub).json()["defaultId"]
        self.client.post("/api/apps/notes/install", headers=self.hub)
        self.run_id = "run-" + str(time.time_ns())
        self.clear_notes()

    def tearDown(self):
        self.client.post(f"/api/desktops/{self.desktop}/disable-agent", headers=self.hub)
        for view in self.client.get(
            f"/api/desktops/{self.desktop}/views", headers=self.hub
        ).json()["views"]:
            self.client.delete(
                f"/api/desktops/{self.desktop}/views/{view['id']}", headers=self.hub
            )
        self.client.close()

    # ---- plumbing

    def call(self, name, arguments=None, *, desktop=None, run_id=None):
        """One tool call, on the loop the runtime actually lives on."""
        tools = self.app.state.desktops.tools
        future = asyncio.run_coroutine_threadsafe(
            tools.call(
                desktop or self.desktop,
                name,
                arguments or {},
                run_id=run_id or self.run_id,
            ),
            self.app.state.loop,
        )
        return future.result(timeout=90)

    def enable(self, apps=("notes",), sites=()):
        current = self.client.get(f"/api/desktops/{self.desktop}/policy", headers=self.hub).json()
        saved = self.client.put(
            f"/api/desktops/{self.desktop}/policy",
            headers=self.hub,
            json={"revision": current["revision"], "apps": list(apps), "sites": list(sites)},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        started = self.client.post(
            f"/api/desktops/{self.desktop}/enable-agent", headers=self.hub
        )
        self.assertEqual(started.status_code, 200, started.text)

    def observe_app(self):
        """Open Notes and get as far as its editor, the way a run would.

        Open the window, wait for the app's own text rather than for a timer,
        look, click New, look again. Every step goes through the tools — this is
        the shortest real task there is, and doing it any other way here would
        test a shortcut instead of the feature.
        """
        self.allow_writes()
        view_id, empty = self.open_notes()
        new = next(
            control
            for control in empty["page"]["controls"]
            if control["name"] in ("+ New", "Create a note")
        )
        self.call(
            "desktop.click",
            {"viewId": view_id, "observationId": empty["observationId"], "ref": new["ref"]},
        )
        # The app disables its own controls while it saves. Waiting for it to
        # settle is what a person does too, and acting into a locked form is the
        # kind of thing the target check refuses.
        self.call(
            "desktop.wait",
            {"viewId": view_id, "condition": {"type": "idle"}, "timeoutMs": 5000},
        )
        return view_id, self.call("desktop.observe", {"viewId": view_id})

    def open_notes(self):
        """Open the window and look at it once, without touching anything."""
        opened = self.call("desktop.open_app", {"appId": "notes"})
        view_id = opened["view"]["viewId"]
        # The app loads inside the host page's sandboxed frame; waiting for its
        # own text is the honest way to know it is there.
        self.call(
            "desktop.wait",
            {"viewId": view_id, "condition": {"type": "text", "text": "Notes"}, "timeoutMs": 10000},
        )
        return view_id, self.call("desktop.observe", {"viewId": view_id})

    # ---- refusing before there is anything to work with

    def test_a_personal_desktop_has_no_tools(self):
        with self.assertRaises(ToolError) as caught:
            self.call("desktop.open_app", {"appId": "notes"})
        self.assertEqual(caught.exception.code, "conflict")

    def test_a_tool_nobody_declared_is_not_a_tool(self):
        with self.assertRaises(ToolError) as caught:
            self.call("desktop.evaluate", {"script": "1"})
        self.assertEqual(caught.exception.code, "unknown_tool")

    def test_an_app_the_desktop_does_not_allow_is_refused(self):
        self.enable(apps=["notes"])
        with self.assertRaises(ToolError) as caught:
            self.call("desktop.open_app", {"appId": "meals"})
        self.assertEqual(caught.exception.code, "not_allowed")

    def test_a_site_nobody_approved_is_refused_before_a_window_exists(self):
        self.enable(apps=["notes"])
        with self.assertRaises(ToolError) as caught:
            self.call("desktop.open_site", {"url": "https://example.com/page"})
        self.assertEqual(caught.exception.code, "not_allowed")
        self.assertNotIn(
            "web",
            [
                view["kind"]
                for view in self.client.get(
                    f"/api/desktops/{self.desktop}/views", headers=self.hub
                ).json()["views"]
            ],
            "a refused site should not leave a window behind",
        )

    # ---- perceiving the real app

    def test_an_agent_sees_the_real_app_through_the_restricted_host_page(self):
        self.enable()
        view_id, seen = self.observe_app()
        self.assertEqual(seen["viewId"], view_id)
        self.assertTrue(seen["page"]["untrusted"])
        names = [control["name"] for control in seen["page"]["controls"]]
        self.assertIn("Note title", names, names)
        self.assertIn("Note body", names, names)
        # The host page contributes the frame; the app contributes the controls.
        self.assertGreaterEqual(len(seen["page"]["frames"]), 2)
        # And nothing of the owner's dashboard is anywhere in it.
        self.assertNotIn("Settings", seen["page"]["text"])
        self.assertNotIn("Approve", seen["page"]["text"])

    def test_typing_into_the_app_changes_what_the_next_observation_shows(self):
        self.enable()
        view_id, seen = self.observe_app()
        title = next(c for c in seen["page"]["controls"] if c["name"] == "Note title")
        self.call(
            "desktop.type",
            {
                "viewId": view_id,
                "observationId": seen["observationId"],
                "ref": title["ref"],
                "text": "groceries",
            },
        )
        again = self.call("desktop.observe", {"viewId": view_id})
        typed = next(c for c in again["page"]["controls"] if c["name"] == "Note title")
        self.assertEqual(typed["value"], "groceries")

    def test_an_observation_is_spent_by_the_action_it_authorised(self):
        self.enable()
        view_id, seen = self.observe_app()
        title = next(c for c in seen["page"]["controls"] if c["name"] == "Note title")
        result = self.call(
            "desktop.type",
            {
                "viewId": view_id,
                "observationId": seen["observationId"],
                "ref": title["ref"],
                "text": "one",
            },
        )
        self.assertTrue(result["observationInvalidated"])
        with self.assertRaises(ToolError) as caught:
            self.call(
                "desktop.type",
                {
                    "viewId": view_id,
                    "observationId": seen["observationId"],
                    "ref": title["ref"],
                    "text": "two",
                },
            )
        self.assertEqual(caught.exception.code, "stale_observation")
        self.assertTrue(caught.exception.retryable, "looking again is the way out of this one")
        self.assertIn("observe", caught.exception.detail)

    def test_a_view_on_another_desktop_is_not_addressable_from_this_one(self):
        self.enable()
        view_id, _ = self.observe_app()
        created = self.client.post(
            "/api/desktops", headers=self.hub, json={"name": "Somewhere else"}
        )
        self.assertIn(created.status_code, (200, 201), created.text)
        other = created.json()["id"]
        with self.assertRaises(ToolError) as caught:
            self.call("desktop.observe", {"viewId": view_id}, desktop=other)
        # It refuses because that desktop is not running an agent at all, which
        # is the first wall; the view check is the second.
        self.assertIn(caught.exception.code, ("conflict", "no_view"))
        self.client.delete(f"/api/desktops/{other}", headers=self.hub)

    def test_looking_at_a_view_that_never_changes_stops(self):
        self.enable()
        view_id, _ = self.observe_app()
        with self.assertRaises(ToolError) as caught:
            for _ in range(NO_PROGRESS_LIMIT + 2):
                self.call("desktop.observe", {"viewId": view_id})
        self.assertEqual(caught.exception.code, "no_progress")

    # ---- changing the app's data

    def test_a_click_that_would_save_is_refused_when_nothing_granted_it(self):
        """The plan's rule, exercised through the interface rather than asserted.

        The agent clicks the app's own New button. The app then tries to save,
        through the same bridge route a person's click uses, and the effect
        boundary refuses it because this desktop has been allowed to *use* Notes
        and never allowed to change anything in it. The click is real, the
        refusal is real, and the stored data is untouched.
        """
        self.enable()
        view_id, empty = self.open_notes()
        new = next(
            control
            for control in empty["page"]["controls"]
            if control["name"] in ("+ New", "Create a note")
        )
        self.call(
            "desktop.click",
            {"viewId": view_id, "observationId": empty["observationId"], "ref": new["ref"]},
        )
        self.call(
            "desktop.wait", {"viewId": view_id, "condition": {"type": "idle"}, "timeoutMs": 5000}
        )
        self.assertEqual(self.notes(), [], "clicking through the app is not a way around a grant")

    def test_a_named_action_without_a_grant_becomes_a_question(self):
        """Not a refusal — the owner's question, with nothing written meanwhile.

        The same rule an app's own click follows, reached through the tool. The
        supervisor is what waits on it; here what matters is that the data is
        untouched while it does.
        """
        self.enable()
        with self.assertRaises(ApprovalPending) as caught:
            self.call(
                "app.invoke_action",
                {
                    "appId": "notes",
                    "actionId": "create-note",
                    "value": {"title": "Milk", "body": "two litres"},
                    "requestKey": "k1",
                },
            )
        asked = caught.exception.record
        self.assertEqual(asked["effect"], "action")
        self.assertEqual(asked["scope"], {"app": "notes", "action": "create-note"})
        self.assertIn("create-note", asked["summary"]["headline"])
        self.assertEqual(self.notes(), [], "an unanswered question leaves the data alone")

    def test_a_named_action_with_a_grant_is_written_once(self):
        self.enable()
        value = {"title": "Milk", "body": "two litres"}
        self.grant_action(value)
        first = self.call(
            "app.invoke_action",
            {
                "appId": "notes",
                "actionId": "create-note",
                "value": value,
                "requestKey": "k1",
            },
        )
        self.assertEqual(first["result"]["status"], "succeeded")
        self.assertEqual([note["title"] for note in self.notes()], ["Milk"])

        # The same request key again is the same answer, not a second note.
        again = self.call(
            "app.invoke_action",
            {
                "appId": "notes",
                "actionId": "create-note",
                "value": value,
                "requestKey": "k1",
            },
        )
        self.assertTrue(again["result"]["replayed"])
        self.assertEqual(len(self.notes()), 1)

    def test_a_grant_for_one_change_does_not_authorise_a_different_one(self):
        self.enable()
        self.grant_action({"title": "Milk", "body": "two litres"})
        with self.assertRaises(ApprovalPending):
            self.call(
                "app.invoke_action",
                {
                    "appId": "notes",
                    "actionId": "create-note",
                    "value": {"title": "Transfer", "body": "everything"},
                    "requestKey": "k2",
                },
            )
        self.assertEqual(self.notes(), [], "a grant for one change asks about a different one")

    def test_revoking_before_the_call_stops_it(self):
        self.enable()
        value = {"title": "Milk", "body": "two litres"}
        grant = self.grant_action(value)
        self.client.delete(
            f"/api/desktops/{self.desktop}/grants/{grant['id']}", headers=self.hub
        )
        with self.assertRaises(ApprovalPending):
            self.call(
                "app.invoke_action",
                {
                    "appId": "notes",
                    "actionId": "create-note",
                    "value": value,
                    "requestKey": "k3",
                },
            )
        self.assertEqual(self.notes(), [], "a revoked grant is no grant")

    # ---- finishing

    def test_finishing_is_a_proposal_carrying_what_was_actually_done(self):
        self.enable()
        view_id, seen = self.observe_app()
        title = next(c for c in seen["page"]["controls"] if c["name"] == "Note title")
        self.call(
            "desktop.type",
            {
                "viewId": view_id,
                "observationId": seen["observationId"],
                "ref": title["ref"],
                "text": "done",
            },
        )
        finished = self.call(
            "task.finish", {"summary": "Typed the title", "evidence": ["the field now reads done"]}
        )
        self.assertTrue(finished["proposed"])
        tools_used = [record["tool"] for record in finished["actions"]]
        self.assertIn("desktop.open_app", tools_used)
        self.assertIn("desktop.type", tools_used)

    # ---- helpers

    def grant_action(self, value):
        import hashlib
        import json

        digest = hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        response = self.client.post(
            f"/api/desktops/{self.desktop}/grants",
            headers=self.hub,
            json={
                "effect": "action",
                "appId": "notes",
                "runId": self.run_id,
                "requestDigest": digest,
                "scope": {"app": "notes", "action": "create-note"},
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def notes(self):
        storage = self.app.state.desktops._storage
        identity = storage.installation("notes")
        document = storage.read(identity, 1)
        return (document.get("value") or {}).get("notes") or []

    def clear_notes(self):
        """Disposable data between tests. One server, so this is the owner
        resetting their own fixture app, not a tool doing it."""
        storage = self.app.state.desktops._storage
        identity = storage.installation("notes")
        document = storage.read(identity, 1)
        if document.get("value"):
            storage.write(identity, {"notes": []}, document["revision"], 1, 1048576)

    def allow_writes(self):
        """Let the app save, as the owner would have when setting the desktop up.

        Bound to the desktop and the installation but to no particular run,
        because the window's own bridge session belongs to the conversion rather
        than to the run driving the tools.
        """
        response = self.client.post(
            f"/api/desktops/{self.desktop}/grants",
            headers=self.hub,
            json={"effect": "write", "appId": "notes"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()


if __name__ == "__main__":
    unittest.main()
