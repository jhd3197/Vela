"""Disposable app-action browser fixture; no real user installations touched."""
import os
import shutil
import sys
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fixture_apps import APPS as FIXTURE_APPS
with tempfile.TemporaryDirectory(prefix='vela-actions-browser-') as temporary:
    os.environ['VELA_DATA_DIR'] = str(Path(temporary) / 'data')
    from vela.api import create_app
    from vela.config import Config
    import uvicorn
    config = Config(Path(temporary) / 'data', FIXTURE_APPS, ROOT / 'web/dist')
    config.ensure_dirs()
    for app_id in ('notes', 'meals'):
        shutil.copytree(FIXTURE_APPS / app_id, config.installed_dir / app_id)
    uvicorn.run(create_app(config), host='127.0.0.1', port=17716, log_level='warning')
