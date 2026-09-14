"""Smoke-test the actual portable server from a fresh directory and data home."""
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parent.parent


def main():
    with tempfile.TemporaryDirectory(prefix='vela-server-smoke-') as temporary:
        work = Path(temporary)
        shutil.copytree(ROOT / '.local/server-build/dist/Vela', work / 'Vela')
        executable = work / 'Vela' / ('Vela.exe' if os.name == 'nt' else 'Vela')
        shutil.copytree(ROOT / 'tests/fixtures/chat-fixture', work / 'chat-fixture')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('VELA_', 'PYTHON', 'VIRTUAL_ENV'))}
        env['VELA_DATA_DIR'] = str(work / 'data')
        base = f'http://127.0.0.1:{port}'
        opener = build_opener(ProxyHandler({}))

        def request(path, method='GET', payload=None, headers=None):
            data = json.dumps(payload).encode() if payload is not None else None
            with opener.open(Request(base + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json', **(headers or {})}), timeout=3) as response:
                return response.read()

        with (work / 'server.log').open('w', encoding='utf-8') as output:
            server = subprocess.Popen([str(executable), '--no-open-browser', '--port', str(port)],
                                      cwd=work, env=env, stdout=output, stderr=subprocess.STDOUT,
                                      **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
            try:
                for _ in range(200):
                    if server.poll() is not None:
                        raise AssertionError('Packaged server exited during startup')
                    try:
                        request('/api/health')
                        break
                    except (URLError, TimeoutError):
                        time.sleep(0.15)
                else:
                    raise AssertionError('Packaged server did not become ready')
                html = request('/').decode()
                assert '<!doctype html>' in html.lower(), 'Missing bundled dashboard'
                script = re.search(r'src="(/assets/[^\"]+\.js)"', html)
                assert script and len(request(script[1])) > 1000, 'Missing frontend JavaScript'
                token = json.loads(request('/api/session', headers={'X-Vela-Bootstrap': '1'}))['token']
                hub = {'Authorization': 'Bearer ' + token}
                review = json.loads(request('/api/releases/prepare', 'POST', {'folder': str(work / 'chat-fixture')}, hub))
                request('/api/releases/' + review['review'] + '/commit', 'POST',
                        {'capabilities': review['capabilities'], 'operations': review['operations']}, hub)
                assert b'Vela' in request('/apps/chat-fixture/_vela/sdk.js'), 'Missing bundled SDK'
                session = json.loads(request('/api/apps/chat-fixture/session', 'POST', headers=hub))
                app = {'Authorization': 'Bearer ' + session['token']}
                request('/api/app/storage', 'PUT', {'value': {'messages': []}, 'revision': 0}, app)
                assert json.loads(request('/api/app/storage', headers=app))['value'] == {'messages': []}
                print('PASS: relocated server starts, serves dashboard/assets, validates and installs an app, serves SDK, and stores app data')
            except Exception:
                output.flush()
                print((work / 'server.log').read_text(encoding='utf-8'))
                raise
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == '__main__':
    main()
