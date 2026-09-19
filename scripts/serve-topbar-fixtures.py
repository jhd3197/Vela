"""Disposable browser-test engine for the top bar; never uses ~/.vela.

Installs three throwaway apps, each one a question the suite asks:

* ``calc-fixture`` — a fixed-size window that cannot be maximized. The
  demonstration scenario's calculator.
* ``weather-fixture`` — granted the top bar, so it publishes a status item and
  draws its declared menus.
* ``notes-fixture`` — an ordinary window with no top bar grant, which is what
  makes the refusal and the "menus follow focus" checks mean anything.

    python scripts/serve-topbar-fixtures.py [--port N]
"""
import argparse
import copy
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=17718)
args = parser.parse_args()

#: id → what to change about the fixture manifest. Everything else is shared,
#: so a difference in the suite's results is a difference in these lines.
APPS = {
    "calc-fixture": {
        "name": "Calc",
        "capabilities": {},
        "topbar": None,
        "view": {
            "surface": "embedded",
            "chrome": "compact",
            "window": {
                "resizable": False,
                "maximizable": False,
                "defaultSize": {"width": 320, "height": 460},
            },
        },
    },
    "weather-fixture": {"name": "Weather"},
    "notes-fixture": {"name": "Notes", "capabilities": {}, "topbar": None},
}

with tempfile.TemporaryDirectory(prefix="vela-topbar-fixtures-") as temporary:
    root = Path(temporary)
    os.environ["VELA_DATA_DIR"] = str(root / "data")
    from vela.api import create_app
    from vela.config import Config
    import uvicorn

    config = Config(root / "data", root / "catalog", ROOT / "web/dist")
    config.ensure_dirs()
    source = json.loads((ROOT / "tests/fixtures/topbar-fixture/app.json").read_text())
    for app_id, changes in APPS.items():
        target = config.installed_dir / app_id
        shutil.copytree(ROOT / "tests/fixtures/topbar-fixture", target)
        data = copy.deepcopy(source)
        data["id"] = app_id
        for key, value in changes.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        (target / "app.json").write_text(json.dumps(data, indent=2))
        # The page shows `document.title`, so each window is nameable on sight.
        page = (target / "index.html").read_text(encoding="utf-8")
        (target / "index.html").write_text(
            page.replace("<title>Top bar fixture</title>", f"<title>{data['name']}</title>"),
            encoding="utf-8",
        )
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port, log_level="warning")
