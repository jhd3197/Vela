"""Disposable engine for the automations browser suite, with Notes installed.

Uses a temporary data directory and the pinned fixture apps, never a user's
installation. Automations execute for real against this server.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

with tempfile.TemporaryDirectory(prefix='vela-automations-browser-') as temporary:
    work = Path(temporary)
    os.environ['VELA_DATA_DIR'] = str(work / 'data')
    from scripts.fixture_apps import APPS
    from vela.api import create_app
    from vela.config import Config
    import uvicorn

    catalog = work / 'apps'
    catalog.mkdir(parents=True)
    for app in ('notes',):
        shutil.copytree(APPS / app, catalog / app)
    config = Config(work / 'data', catalog, ROOT / 'web/dist')
    config.ensure_dirs()
    # Install Notes up front so the app-action step and its blueprint exist.
    shutil.copytree(catalog / 'notes', config.installed_dir / 'notes')
    uvicorn.run(create_app(config), host='127.0.0.1', port=17716, log_level='warning')
