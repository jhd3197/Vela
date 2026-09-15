"""Host-owned web registrations never become executable app packages."""
import copy
from unittest.mock import patch
import unittest

import test_app_contract as base
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.app_storage import AppStorage, AppServiceError
from vela.connected_apps import ConnectedApps, web_address
from vela.manifest import validate_manifest, ManifestError


class ConnectedAppTests(unittest.TestCase):
    setUp = base.ApiBoundaryTests.setUp
    tearDown = base.ApiBoundaryTests.tearDown
    session = base.ApiBoundaryTests.session

    def add(self):
        response = self.client.post('/api/web-apps', headers=self.hub,
                                    json={'name': 'Reading', 'url': 'https://reading.example.test'})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_crud_persistence_and_conflicts(self):
        with patch('httpx.AsyncClient.send', side_effect=AssertionError('No upstream fetch')):
            app = self.add()
        app_id = app['id']
        self.assertIn('--', app_id)
        self.assertFalse(app['running'])
        self.assertEqual(app['capabilities'], [])
        self.assertEqual(app['view']['surface'], 'connected')
        self.assertIn(app, self.client.get('/api/apps', headers=self.hub).json()['apps'])
        self.assertEqual(self.client.get(f'/api/apps/{app_id}/status', headers=self.hub).json(), app)
        self.assertEqual(self.client.get('/api/engine', headers=self.hub).json()['apps_installed'], 1)
        value = {'name': 'Books', 'url': 'https://books.example.test/inbox', 'color': '#123456', 'revision': 1}
        updated = self.client.put(f'/api/web-apps/{app_id}', headers=self.hub, json=value)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()['revision'], 2)
        self.assertEqual(self.client.put(f'/api/web-apps/{app_id}', headers=self.hub, json=value).status_code, 409)
        self.assertEqual(self.client.request('DELETE', f'/api/web-apps/{app_id}', headers=self.hub, json={'revision': 1}).status_code, 409)
        with TestClient(create_app(self.config)) as restarted:
            headers = {'Authorization': 'Bearer ' + restarted.get('/api/session', headers={'X-Vela-Bootstrap': '1'}).json()['token']}
            self.assertEqual(restarted.get(f'/api/apps/{app_id}', headers=headers).json()['name'], 'Books')
            response = restarted.request('DELETE', f'/api/web-apps/{app_id}', headers=headers, json={'revision': 2})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(restarted.get(f'/api/apps/{app_id}', headers=headers).status_code, 404)
        self.assertEqual(list(self.config.installed_dir.iterdir()), [])

    def test_auth_and_no_bridge_or_lifecycle(self):
        app_id = self.add()['id']
        _, app_headers = self.session()
        for headers in ({}, app_headers):
            self.assertEqual(self.client.post('/api/web-apps', headers=headers, json={'name': 'X', 'url': 'https://x.test'}).status_code, 401)
            self.assertEqual(self.client.put(f'/api/web-apps/{app_id}', headers=headers, json={'name': 'X', 'url': 'https://x.test', 'revision': 1}).status_code, 401)
            self.assertEqual(self.client.request('DELETE', f'/api/web-apps/{app_id}', headers=headers, json={'revision': 1}).status_code, 401)
        for action in ('session', 'launch', 'stop', 'install'):
            self.assertGreaterEqual(self.client.post(f'/api/apps/{app_id}/{action}', headers=self.hub).status_code, 400)
        self.assertGreaterEqual(self.client.delete(f'/api/apps/{app_id}', headers=self.hub).status_code, 400)
        self.assertEqual(self.client.get(f'/api/apps/{app_id}', headers=self.hub).status_code, 200)
        data = copy.deepcopy(base.FIXTURE)
        data['view'] = {'surface': 'connected', 'url': 'https://reading.test'}
        with self.assertRaises(ManifestError):
            validate_manifest(data, data['id'], self.apps / data['id'])

    def test_validation_and_namespace(self):
        for value in ('http://reading.test', 'javascript:alert(1)', 'https://user:secret@reading.test',
                      'https://reading.test\\@evil.test', 'https://reading.test\n', 'https://reading.test:0',
                      'https://reading.test:99999', 'https://reading.test:', 'https://%72eading.test',
                      'https://127.1', 'https://2130706433', 'https://0x7f000001', 'https://0.0.0.0',
                      'https://[::]', 'https://foo..test', 'https://reading.test.;frame-src',
                      'https://vela.test:8800/path', 'https://VELA.TEST/path'):
            with self.subTest(url=value), self.assertRaises(AppServiceError):
                web_address(value, 'https://vela.test:7700')
        self.assertEqual(web_address('https://READING.test:443/inbox?q=hi#today'), 'https://reading.test/inbox?q=hi#today')
        self.assertEqual(web_address('https://[::1]:8443'), 'https://[::1]:8443/')
        for patch_value in ({'name': '   '}, {'color': 'red'}, {'runtime': {'process': {}}}, {'name': 42}):
            body = {'name': 'Reading', 'url': 'https://reading.test', **patch_value}
            self.assertEqual(self.client.post('/api/web-apps', headers=self.hub, json=body).status_code, 422)
        self.assertEqual(self.client.put('/api/web-apps/chat-fixture', headers=self.hub,
                                        json={'name': 'X', 'url': 'https://x.test', 'revision': 1}).status_code, 404)
        with self.assertRaises(AppServiceError):
            ConnectedApps(AppStorage(self.config.data_dir / 'app-data.sqlite')).get('../anything')
