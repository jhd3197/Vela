"""Serve screenshot demos from pinned fixtures with a caller-owned temporary data dir."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fixture_apps import APPS
from vela.config import load_config, Config
from vela.api import create_app
import uvicorn

config = load_config()
uvicorn.run(create_app(Config(config.data_dir, APPS, config.web_dist)), host='127.0.0.1', port=7700)
