"""Capture the payloads the operations normalizers are tested against.

    python scripts/capture-operations-fixtures.py

Everything here runs against a disposable Vela on a temporary data directory,
started in this process. Nothing touches an installed server or a user's apps.
The point is that `tests/operations.test.mjs` pins what the engine really
returns, not a hand-written idea of it: when an endpoint changes shape, the
fixtures are recaptured with this script and the test says what moved.

Each file records the endpoint it came from and whether the server had anything
to report, because an empty list is a real answer and the normalizers have to
survive it.
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi.testclient import TestClient  # noqa: E402

from scripts.fixture_apps import APPS as FIXTURE_APPS  # noqa: E402
from vela.api import create_app  # noqa: E402
from vela.config import Config  # noqa: E402

OUT = ROOT / "tests/fixtures/operations"
TERMINAL = ("succeeded", "failed", "cancelled", "interrupted", "timed_out")

TEXT_FLOW = {
    "version": 1,
    "meta": {},
    "nodes": [
        {"id": "start", "type": "manual-trigger", "config": {"payload": '{"name": "Vela"}'}},
        {"id": "say", "type": "template", "config": {"template": "Hello {{name}}"}},
        {"id": "note", "type": "log", "config": {"level": "info", "prefix": "greeting"}},
    ],
    "edges": [
        {"id": "e1", "source": "start", "target": "say"},
        {"id": "e2", "source": "say", "target": "note"},
    ],
}


def write(name, endpoint, payload, note):
    OUT.mkdir(parents=True, exist_ok=True)
    body = {"endpoint": endpoint, "capturedBy": Path(__file__).name, "note": note, "payload": payload}
    (OUT / name).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    print(f"  {name:<28} {endpoint}")


def run_automation(client, hub):
    """One real run, so the run fixture is a run and not an empty list."""
    workflow = client.post("/api/automations", headers=hub, json={"name": "Nightly greeting"}).json()
    client.put(
        f"/api/automations/{workflow['id']}",
        headers=hub,
        json={"revision": workflow["documentRevision"], "document": TEXT_FLOW},
    )
    client.post(f"/api/automations/{workflow['id']}/activate", headers=hub)
    started = client.post(f"/api/automations/{workflow['id']}/runs", headers=hub, json={"input": None})
    if started.status_code >= 400:
        print(f"  (no run: {started.status_code} {started.text.strip()[:120]})")
        return False
    run_id = started.json()["id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        run = client.get(f"/api/automations/runs/{run_id}", headers=hub).json()
        if run["status"] in TERMINAL or run["status"] == "waiting":
            return True
        time.sleep(0.2)
    print("  (the run did not finish in 60s; capturing it as it stands)")
    return True


def main():
    with tempfile.TemporaryDirectory(prefix="vela-operations-fixtures-") as temporary:
        root = Path(temporary)
        catalog = root / "catalog"
        catalog.mkdir()
        for app in ("notes", "meals"):
            shutil.copytree(FIXTURE_APPS / app, catalog / app)
        os.environ["VELA_DATA_DIR"] = str(root / "data")
        config = Config(root / "data", catalog, ROOT / "web/dist")
        config.ensure_dirs()
        with TestClient(create_app(config)) as client:
            token = client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
            hub = {"Authorization": "Bearer " + token}

            client.post("/api/apps/notes/install", headers=hub)

            ran = run_automation(client, hub)
            write(
                "automation-runs.json",
                "GET /api/automations/runs?limit=10",
                client.get("/api/automations/runs?limit=10", headers=hub).json(),
                "One manual run of a three-step automation." if ran else "No run: the worker refused.",
            )

            agent = client.post("/api/desktops", headers=hub, json={"name": "Agent desktop"})
            enabled = None
            if agent.status_code < 400:
                desktop_id = agent.json()["id"]
                # An agent is only allowed on a desktop that has said what it
                # may use, so the policy comes first.
                policy = client.get(f"/api/desktops/{desktop_id}/policy", headers=hub).json()
                client.put(
                    f"/api/desktops/{desktop_id}/policy",
                    headers=hub,
                    json={"revision": policy["revision"], "apps": ["notes"], "sites": []},
                )
                enabled = client.post(f"/api/desktops/{desktop_id}/enable-agent", headers=hub)
            attention = client.get("/api/desktops/attention", headers=hub).json()
            if attention["desktops"]:
                note = "One agent desktop, idle."
            else:
                refused = enabled.text.strip()[:120] if enabled is not None else agent.text.strip()[:120]
                note = (
                    "No agent desktop could be made on this machine, so the attention summary is "
                    f"empty. The engine said: {refused}"
                )
            write("desktops-attention.json", "GET /api/desktops/attention", attention, note)

            write(
                "updates.json",
                "GET /api/updates",
                client.get("/api/updates", headers=hub).json(),
                "A server that has not checked for an update.",
            )
            write(
                "updates-job.json",
                "GET /api/updates/job",
                client.get("/api/updates/job", headers=hub).json(),
                "No update has been applied on this server.",
            )

            client.post("/api/backups", headers=hub)
            write(
                "backups.json",
                "GET /api/backups",
                client.get("/api/backups", headers=hub).json(),
                "One real backup of the disposable data directory.",
            )

            client.post("/api/doctor/run", headers=hub)
            write(
                "doctor.json",
                "GET /api/doctor",
                client.get("/api/doctor", headers=hub).json(),
                "One real sweep on the disposable installation.",
            )

            write(
                "apps.json",
                "GET /api/apps",
                client.get("/api/apps", headers=hub).json(),
                "One installed fixture app and one still in the catalog.",
            )


if __name__ == "__main__":
    print(f"capturing into {OUT.relative_to(ROOT)}")
    main()
