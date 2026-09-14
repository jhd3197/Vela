"""Disposable TLS engine with Health and a read-only mock Ollama endpoint."""
import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fixture_apps import APPS as FIXTURE_APPS
parser = argparse.ArgumentParser()
parser.add_argument('--cert', required=True)
parser.add_argument('--key', required=True)
parser.add_argument('--port', type=int, default=17713)
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix='vela-connections-') as temporary:
    os.environ['VELA_DATA_DIR'] = str(Path(temporary) / 'data')
    from vela.api import create_app
    from vela.config import Config
    from vela.access import set_password
    import httpx
    import uvicorn
    config = Config(Path(temporary) / 'data', FIXTURE_APPS, ROOT / 'web/dist', True, f'https://127.0.0.1:{args.port}')
    config.ensure_dirs()
    set_password(config.data_dir / 'access.json', 'fixture-password-123')
    for app_id in ('health', 'ollama'):
        source = FIXTURE_APPS / app_id
        shutil.copytree(source, config.installed_dir / app_id)
    def upstream(request):
        if request.url.path == '/api/version': return httpx.Response(200, json={'version': 'fixture'})
        if request.url.path == '/api/tags': return httpx.Response(200, json={'models': [{'name': 'fixture:latest', 'size': 1234}]})
        if request.url.path == '/api/show': return httpx.Response(200, json={'details': {'family': 'fixture'}})
        raise AssertionError(f'Unexpected upstream operation: {request.method} {request.url}')
    uvicorn.run(create_app(config, connection_transport=httpx.MockTransport(upstream)), host='127.0.0.1', port=args.port, ssl_certfile=args.cert, ssl_keyfile=args.key, proxy_headers=False, log_level='warning')
