"""Disposable engine that installs Health exclusively through its pinned release."""
import os
import sys
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
with tempfile.TemporaryDirectory(prefix='vela-release-browser-') as temporary:
    os.environ['VELA_DATA_DIR'] = str(Path(temporary) / 'data')
    from vela.api import create_app
    from vela.config import Config
    import uvicorn
    config = Config(Path(temporary) / 'data', Path(temporary) / 'empty', ROOT / 'web/dist', catalog_source=str(ROOT / 'tests/fixtures/catalog/index.json'))
    config.ensure_dirs()
    uvicorn.run(create_app(config), host='127.0.0.1', port=17715, log_level='warning')
