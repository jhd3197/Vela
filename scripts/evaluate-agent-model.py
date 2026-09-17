"""Find out whether a real model can actually work a Vela desktop.

The automated suite uses a scripted model. That is the right tool for checking
that the loop dispatches, that budgets bite and that a cancelled run stops — and
it is worth being blunt about what it cannot tell you: whether any particular
model can look at a window and decide what to do next. Nothing but a real model
against a real browser answers that, and the answer is different for every model
and every machine.

So this is a separate, deliberate run. It starts a Vela on a disposable data
directory, installs the Notes fixture, converts a desktop, and gives a model a
small task through the ordinary API. It reports what happened, per attempt, with
the failure type — not a pass or a fail, because "this model got four out of
five" is the useful sentence and "OK" is not.

Nothing here touches your own Vela, your installed apps or your data.

    python scripts/evaluate-agent-model.py --model qwen3:8b --attempts 3

Add `--json report.json` to keep the record. Results belong in a progress note,
with the model, the machine and the failure types written down as they were.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.fixture_apps import APPS as FIXTURE_APPS  # noqa: E402
from vela.api import create_app  # noqa: E402
from vela.config import Config  # noqa: E402
from vela.desktops.runtime import availability  # noqa: E402

#: The tasks. Small on purpose: each one is a thing a person would plausibly ask
#: for, and each has an outcome that can be checked rather than believed.
TASKS = (
    {
        "id": "read",
        "instruction": (
            "Open the Notes app and tell me what it currently shows. Do not change "
            "anything."
        ),
        "needs_change": False,
        # Reading counts only if it actually looked. A run that could not open
        # the app and reported that is an honest failure, not a success.
        "check": lambda notes, result: "desktop.observe" in (result.get("tools") or []),
    },
    {
        "id": "create",
        "instruction": (
            "Open the Notes app and create a note titled 'Milk' with the body "
            "'two litres'. Use the app's create-note action."
        ),
        "needs_change": True,
        "check": lambda notes, result: any(note.get("title") == "Milk" for note in notes),
    },
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Harness:
    """A whole Vela, on a temporary directory, for the length of this script."""

    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-model-eval-")
        root = Path(self.temp.name)
        catalog = root / "catalog"
        catalog.mkdir()
        shutil.copytree(FIXTURE_APPS / "notes", catalog / "notes")
        self.port = free_port()
        os.environ["VELA_PORT"] = str(self.port)
        self.config = Config(root / "data", catalog, ROOT / "web/dist")
        self.config.ensure_dirs()
        self.app = create_app(self.config)
        self.base = f"http://127.0.0.1:{self.port}"

    def start(self):
        import httpx
        import uvicorn

        self.server = uvicorn.Server(
            uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(300):
            try:
                if httpx.get(f"{self.base}/api/health", timeout=1).status_code == 200:
                    if getattr(self.app.state, "loop", None):
                        break
            except Exception:  # noqa: BLE001 - it is simply not up yet
                pass
            time.sleep(0.05)
        else:
            raise RuntimeError("the evaluation server did not start")
        self.client = httpx.Client(base_url=self.base, timeout=120)
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}
        self.client.post("/api/apps/notes/install", headers=self.hub)
        self.desktop = self.client.get("/api/desktops", headers=self.hub).json()["defaultId"]

    def stop(self):
        try:
            self.client.post(f"/api/desktops/{self.desktop}/disable-agent", headers=self.hub)
            self.client.close()
        finally:
            self.server.should_exit = True
            self.thread.join(timeout=20)
            self.temp.cleanup()

    # ---- one attempt

    def reset(self, *, approvals: str):
        """A clean desktop for each attempt: no notes, no grants, no windows."""
        storage = self.app.state.desktops._storage
        identity = storage.installation("notes")
        document = storage.read(identity, 1)
        if document.get("value"):
            storage.write(identity, {"notes": []}, document["revision"], 1, 1048576)
        self.client.post(f"/api/desktops/{self.desktop}/disable-agent", headers=self.hub)
        for view in self.client.get(
            f"/api/desktops/{self.desktop}/views", headers=self.hub
        ).json()["views"]:
            self.client.delete(f"/api/desktops/{self.desktop}/views/{view['id']}", headers=self.hub)
        current = self.client.get(f"/api/desktops/{self.desktop}/policy", headers=self.hub).json()
        self.client.put(
            f"/api/desktops/{self.desktop}/policy",
            headers=self.hub,
            json={
                "revision": current["revision"],
                "apps": ["notes"],
                "approvals": approvals,
                # In `granted` mode the owner has said ahead of time that this
                # one action does not need asking each time. That is what makes
                # an unattended evaluation possible without anybody approving
                # anything by hand — and it is a real setting, not a test hook.
                "actionScopes": (
                    [{"app": "notes", "action": "create-note"}] if approvals == "granted" else []
                ),
                "budget": {"steps": 25, "activeSeconds": 600, "modelRequests": 25},
            },
        )
        started = self.client.post(
            f"/api/desktops/{self.desktop}/enable-agent", headers=self.hub
        )
        if started.status_code != 200:
            raise RuntimeError(f"could not convert the desktop: {started.text}")

    def notes(self):
        storage = self.app.state.desktops._storage
        document = storage.read(storage.installation("notes"), 1)
        return (document.get("value") or {}).get("notes") or []

    def attempt(self, task, model, timeout):
        self.reset(approvals="granted" if task["needs_change"] else "ask")
        started = time.monotonic()
        accepted = self.client.post(
            f"/api/desktops/{self.desktop}/tasks",
            headers=self.hub,
            json={"instruction": task["instruction"], "model": model},
        )
        if accepted.status_code != 202:
            return {"outcome": "refused", "detail": accepted.text, "seconds": 0}
        run_id = accepted.json()["id"]
        while time.monotonic() - started < timeout:
            run = self.client.get(
                f"/api/desktops/{self.desktop}/tasks/{run_id}", headers=self.hub
            ).json()
            if run["state"] in ("succeeded", "failed", "cancelled", "interrupted", "outcome_unknown"):
                break
            time.sleep(1.0)
        else:
            self.client.post(
                f"/api/desktops/{self.desktop}/tasks/{run_id}/control",
                headers=self.hub,
                json={"action": "stop"},
            )
            return {"outcome": "timed_out", "detail": "the attempt ran past its limit",
                    "seconds": round(time.monotonic() - started, 1)}

        # What the desktop actually looks like afterwards, which is the only
        # thing that decides whether this worked.
        events = [
            event
            for event in self.client.get(
                f"/api/desktops/{self.desktop}/events", headers=self.hub
            ).json()["events"]
            # Events are numbered per desktop and this desktop is reused, so an
            # attempt reads only its own. Counting a previous attempt's steps
            # would make every later one look busier than it was.
            if event.get("runId") == run_id
        ]
        steps = [event["payload"] for event in events if event["kind"] == "step.finished"]
        tools = [step.get("tool") for step in steps]
        refusals = [
            {"tool": step.get("tool"), "detail": step.get("detail")}
            for step in steps
            if not step.get("ok")
        ]
        outcome_of = {
            "steps": len(tools),
            "tools": tools,
            "refusals": refusals,
        }
        real = task["check"](self.notes(), outcome_of)
        return {
            "outcome": (
                "succeeded" if run["state"] == "succeeded" and real
                else "claimed_but_not_done" if run["state"] == "succeeded"
                else run["state"]
            ),
            "detail": run.get("detail"),
            "summary": (run.get("result") or {}).get("summary"),
            "steps": len(tools),
            "tools": tools,
            "refusals": refusals,
            "budget": run.get("budget"),
            "seconds": round(time.monotonic() - started, 1),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="the Ollama model to evaluate")
    parser.add_argument("--attempts", type=int, default=3, help="attempts per task")
    parser.add_argument("--timeout", type=int, default=420, help="seconds per attempt")
    parser.add_argument("--task", action="append", help="run only these task ids")
    parser.add_argument("--json", help="write the full record here")
    args = parser.parse_args()

    state = availability()
    if not state["available"]:
        print(f"The agent desktop runtime is not available: {state['detail']}")
        return 2

    tasks = [task for task in TASKS if not args.task or task["id"] in args.task]
    harness = Harness()
    harness.start()
    record = {
        "model": args.model,
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpus": os.cpu_count(),
        },
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "attempts": [],
    }
    try:
        from vela.agent_runs.model import OllamaAdapter

        capabilities = asyncio.run(OllamaAdapter().capabilities(args.model))
        record["capabilities"] = capabilities
        print(f"{args.model}: tools={capabilities['tools']} vision={capabilities['vision']}")
        for task in tasks:
            for attempt in range(1, args.attempts + 1):
                print(f"  {task['id']} attempt {attempt}…", end="", flush=True)
                result = harness.attempt(task, args.model, args.timeout)
                result.update(task=task["id"], attempt=attempt)
                record["attempts"].append(result)
                print(f" {result['outcome']} ({result['seconds']}s, {result.get('steps', 0)} steps)")
    finally:
        harness.stop()

    by_task: dict[str, list[str]] = {}
    for result in record["attempts"]:
        by_task.setdefault(result["task"], []).append(result["outcome"])
    print("\nResults")
    for task_id, outcomes in by_task.items():
        good = sum(1 for outcome in outcomes if outcome == "succeeded")
        print(f"  {task_id}: {good}/{len(outcomes)} — {', '.join(outcomes)}")
    record["summary"] = {task: outcomes for task, outcomes in by_task.items()}
    if args.json:
        Path(args.json).write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(f"\nWritten to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
