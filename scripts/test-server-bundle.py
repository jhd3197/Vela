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


def check_automations(request, hub):
    """Run a real workflow on the runtime that shipped inside this download.

    The point is that nothing else is installed on this machine: no sibling
    checkout, no Node on PATH. If the runtime was not bundled, say so plainly
    instead of passing quietly.
    """
    status = json.loads(request('/api/automations/status', headers=hub))
    if not status['available']:
        raise AssertionError('Packaged server cannot run automations: ' + str(status['detail']))
    assert 'node-runtime' in Path(status['node']).as_posix(), (
        f"Packaged server used {status['node']} instead of its bundled runtime")
    created = json.loads(request('/api/automations', 'POST', {'name': 'Bundle check'}, hub))
    document = {
        'version': 1,
        'meta': {},
        'nodes': [
            {'id': 'start', 'type': 'manual-trigger', 'config': {'payload': '{"name": "Vela"}'}},
            {'id': 'say', 'type': 'template', 'config': {'template': 'Hello {{name}}'}},
            {'id': 'note', 'type': 'log', 'config': {'level': 'info', 'prefix': 'bundle'}},
        ],
        'edges': [{'id': 'e1', 'source': 'start', 'target': 'say'},
                  {'id': 'e2', 'source': 'say', 'target': 'note'}],
    }
    saved = json.loads(request('/api/automations/' + created['id'], 'PUT',
                               {'revision': created['documentRevision'], 'document': document}, hub))
    assert saved['draftProblems'] == [], saved['draftProblems']
    run = json.loads(request('/api/automations/' + created['id'] + '/runs', 'POST', {}, hub))
    for _ in range(300):
        detail = json.loads(request('/api/automations/runs/' + run['id'], headers=hub))
        if detail['status'] not in ('queued', 'running'):
            break
        time.sleep(0.2)
    else:
        raise AssertionError('Automation run did not finish inside the packaged server')
    assert detail['status'] == 'succeeded', f"Automation run {detail['status']}: {detail['error']}"
    logged = [event for event in detail['events']
              if event['type'] == 'node-log' and event['nodeId'] == 'note']
    assert logged and logged[0]['data'] == 'Hello Vela', logged
    worker = detail['runtime']['worker']
    print(f"       automations ran on bundled Node {worker['node']} with tramo {worker['tramo']}")


def main():
    # The automation runtime can still hold its executable for a moment after the
    # server stops, so a failed cleanup must not be read as a failed smoke test.
    with tempfile.TemporaryDirectory(prefix='vela-server-smoke-',
                                     ignore_cleanup_errors=True) as temporary:
        work = Path(temporary)
        shutil.copytree(ROOT / '.local/server-build/dist/Vela', work / 'Vela')
        executable = work / 'Vela' / ('Vela.exe' if os.name == 'nt' else 'Vela')
        shutil.copytree(ROOT / 'tests/fixtures/chat-fixture', work / 'chat-fixture')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(('VELA_', 'PYTHON', 'VIRTUAL_ENV'))}
        # Prove the download does not depend on a developer's Node installation.
        env['PATH'] = os.pathsep.join(
            entry for entry in env.get('PATH', '').split(os.pathsep)
            if entry and 'node' not in entry.lower())
        env['VELA_DATA_DIR'] = str(work / 'data')
        base = f'http://127.0.0.1:{port}'
        opener = build_opener(ProxyHandler({}))

        def request(path, method='GET', payload=None, headers=None):
            data = json.dumps(payload).encode() if payload is not None else None
            with opener.open(Request(base + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json', **(headers or {})}), timeout=3) as response:
                return response.read()

        with (work / 'server.log').open('w', encoding='utf-8') as output:
            server = subprocess.Popen([str(executable), '--no-open-browser', '--no-tray', '--port', str(port)],
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
                check_automations(request, hub)
                # psutil is imported lazily, so a bundle that failed to collect
                # it starts fine and only reports available: false here.
                metrics = json.loads(request('/api/system/metrics', headers=hub))
                assert metrics['available'], 'Packaged server has no psutil: the desk loses System and Volume'
                assert metrics['memory']['total'] > 0 and metrics['uptime']['seconds'] >= 0, metrics
                print('PASS: relocated server starts, serves dashboard/assets, validates and installs an app, '
                      'serves SDK, stores app data, reports system metrics, and runs an automation on its '
                      'bundled runtime')
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
