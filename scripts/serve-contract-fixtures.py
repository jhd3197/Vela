"""Disposable browser-test engine; never uses ~/.vela or real installations."""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fixture_apps import APPS as FIXTURE_APPS
parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=7712)
args = parser.parse_args()

with tempfile.TemporaryDirectory(prefix="vela-browser-fixtures-") as temporary:
    root = Path(temporary)
    os.environ["VELA_DATA_DIR"] = str(root / "data")
    from vela.api import create_app
    from vela.config import Config
    import uvicorn

    config = Config(root / "data", root / "catalog", ROOT / "web/dist")
    config.ensure_dirs()
    for app_id, chrome in [("chat-fixture", "seamless"), ("compact-fixture", "compact"), ("hub-fixture", "hub"), ("failed-fixture", "seamless"), ("other-app", "seamless")]:
        target = config.installed_dir / app_id
        shutil.copytree(ROOT / "tests/fixtures/chat-fixture", target)
        data = json.loads((target / "app.json").read_text())
        data["id"], data["name"], data["view"]["chrome"] = app_id, app_id.replace("-", " ").title(), chrome
        if app_id == "failed-fixture": data["runtime"]["static"]["entry"] = "missing.html"
        (target / "app.json").write_text(json.dumps(data))
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port, log_level="warning")
