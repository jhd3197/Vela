"""Pairing the Vela app: codes, device credentials, sessions and removal.

A disposable data directory and password throughout; the Wi-Fi address is
faked on the running engine rather than started, so nothing binds a port.
"""
import json
from dataclasses import replace
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

import test_app_contract as base
from vela import devices as devices_module
from vela.access import set_password
from vela.api import create_app
from vela.config import Config

PASSWORD = 'fixture-password'
ORIGIN = 'https://192.168.1.20:7702'
FINGERPRINT = 'AB' * 32


class DevicePairingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vela-devices-')
        root = Path(self.temp.name)
        self.config = Config(root, root / 'apps', base.ROOT / 'web/dist')
        self.config.ensure_dirs()
        set_password(self.config.data_dir / 'access.json', PASSWORD)
        self.app = create_app(self.config)
        self.phone_access = self.app.state.phone_access
        self.auth = self.phone_access.auth
        self.local = TestClient(self.app)
        self.local_headers = {'Authorization': 'Bearer ' + self.auth.hub_token}
        self.device = TestClient(self.app, base_url=ORIGIN, client=('192.168.1.30', 1234))

    def tearDown(self):
        self.local.close()
        self.device.close()
        self.temp.cleanup()

    def enable_wifi(self):
        # What `PhoneAccess.start` records once its listeners are up.
        self.auth.phone_origin = ORIGIN
        self.phone_access.origin = ORIGIN
        self.phone_access.setup_url = 'http://192.168.1.20:7701/setup'
        self.phone_access.fingerprint = FINGERPRINT

    def new_code(self):
        response = self.local.post('/api/devices/pairing', headers=self.local_headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def pair(self, code, client=None, **extra):
        body = {'code': code, 'name': 'Pixel 9', 'form': 'phone', 'platform': 'android',
                'appVersion': '0.1.0', **extra}
        return (client or self.device).post('/api/devices/pair', json=body)

    def paired(self):
        self.enable_wifi()
        response = self.pair(self.new_code()['code'])
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def session(self, credential):
        return self.device.post('/api/devices/session', headers={'X-Vela-Device': credential}, json={})

    # --------------------------------------------------------------- codes

    def test_a_code_needs_an_address_the_app_can_reach(self):
        refused = self.local.post('/api/devices/pairing', headers=self.local_headers)
        self.assertEqual(refused.status_code, 409)
        self.assertFalse(self.local.get('/api/devices', headers=self.local_headers).json()['available'])

    def test_the_link_carries_the_address_the_fingerprint_and_the_code(self):
        self.enable_wifi()
        issued = self.new_code()
        link = urlsplit(issued['link'])
        self.assertEqual((link.scheme, link.netloc), ('vela', 'pair'))
        query = {key: values[0] for key, values in parse_qs(link.query).items()}
        self.assertEqual(query, {'v': '1', 'origin': ORIGIN, 'setup': 'http://192.168.1.20:7701',
                                 'fp': FINGERPRINT, 'code': issued['code']})
        self.assertRegex(issued['code'], r'^[34679ACDEFGHJKMNPQRTUVWXY]{4}-[34679ACDEFGHJKMNPQRTUVWXY]{4}$')

    def test_a_configured_https_address_is_not_pinned(self):
        hosted = replace(self.config, remote_access=True, public_origin='https://vela.example.com')
        with TestClient(create_app(hosted), base_url='https://vela.example.com') as client:
            token = client.post('/api/login', headers={'X-Vela-Bootstrap': '1'},
                                json={'password': PASSWORD}).json()['token']
            issued = client.post('/api/devices/pairing', headers={'Authorization': f'Bearer {token}'})
        self.assertEqual(issued.status_code, 200, issued.text)
        query = parse_qs(urlsplit(issued.json()['link']).query)
        self.assertEqual(query['origin'], ['https://vela.example.com'])
        self.assertNotIn('fp', query)
        self.assertNotIn('setup', query)

    def test_a_code_needs_a_signed_in_session(self):
        self.enable_wifi()
        self.assertEqual(self.device.post('/api/devices/pairing').status_code, 401)

    # ------------------------------------------------------------- pairing

    def test_a_code_pairs_one_device_once(self):
        self.enable_wifi()
        code = self.new_code()['code']
        first = self.pair(code.lower().replace('-', ' '))
        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()
        self.assertEqual(body['device']['name'], 'Pixel 9')
        self.assertTrue(body['credential'])
        self.assertEqual(self.pair(code).status_code, 401)

    def test_an_expired_code_is_refused(self):
        self.enable_wifi()
        code = self.new_code()['code']
        store = self.app.state.devices
        key = devices_module.normalize_code(code)
        store._codes[key] = time.monotonic() - 1
        self.assertEqual(self.pair(code).status_code, 401)

    def test_pairing_is_only_offered_on_the_wifi_address(self):
        self.enable_wifi()
        code = self.new_code()['code']
        self.assertEqual(self.pair(code, client=self.local).status_code, 403)
        page = self.device.post('/api/devices/pair', json={'code': code},
                                headers={'Origin': 'https://attacker.example'})
        self.assertEqual(page.status_code, 403)
        # Neither spent the code.
        self.assertEqual(self.pair(code).status_code, 200)

    def test_guessing_codes_is_throttled_with_the_password(self):
        self.enable_wifi()
        for _ in range(5):
            self.assertEqual(self.pair('XXXX-XXXX').status_code, 401)
        self.assertEqual(self.pair(self.new_code()['code']).status_code, 429)

    def test_the_credential_is_never_stored(self):
        credential = self.paired()['credential']
        for path in self.config.data_dir.rglob('*'):
            if path.is_file():
                self.assertNotIn(credential.encode(), path.read_bytes(), path)
        listed = self.local.get('/api/devices', headers=self.local_headers).json()['devices']
        self.assertEqual(len(listed), 1)
        self.assertNotIn('secret', listed[0])

    # ------------------------------------------------------------ sessions

    def test_a_credential_starts_an_ordinary_session(self):
        credential = self.paired()['credential']
        started = self.session(credential)
        self.assertEqual(started.status_code, 200, started.text)
        token = started.json()['token']
        self.assertIn('__Host-vela-session', started.headers['set-cookie'])
        listed = self.device.get('/api/devices', headers={'Authorization': f'Bearer {token}'})
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(self.session('not-a-credential').status_code, 401)
        self.assertEqual(self.session('').status_code, 401)

    def test_removing_a_device_ends_its_sessions_and_nothing_else(self):
        credential = self.paired()['credential']
        token = self.session(credential).json()['token']
        password = self.device.post('/api/login', headers={'X-Vela-Bootstrap': '1'},
                                    json={'password': PASSWORD}).json()['token']
        device_id = self.local.get('/api/devices', headers=self.local_headers).json()['devices'][0]['id']
        removed = self.local.delete(f'/api/devices/{device_id}', headers=self.local_headers)
        self.assertEqual(removed.json(), {'devices': []})
        self.assertEqual(self.device.get('/api/devices', headers={'Authorization': f'Bearer {token}'}).status_code, 401)
        self.assertEqual(self.session(credential).status_code, 401)
        self.assertEqual(self.device.get('/api/devices', headers={'Authorization': f'Bearer {password}'}).status_code, 200)

    def test_a_device_can_be_renamed(self):
        self.paired()
        device_id = self.local.get('/api/devices', headers=self.local_headers).json()['devices'][0]['id']
        renamed = self.local.patch(f'/api/devices/{device_id}', headers=self.local_headers,
                                   json={'name': '  Living room\x07 TV  '})
        self.assertEqual(renamed.json()['name'], 'Living room TV')
        self.assertEqual(self.local.patch('/api/devices/missing', headers=self.local_headers,
                                          json={'name': 'x'}).status_code, 404)

    def test_paired_devices_survive_a_restart(self):
        credential = self.paired()['credential']
        restarted = create_app(self.config)
        restarted.state.phone_access.auth.phone_origin = ORIGIN
        with TestClient(restarted, base_url=ORIGIN, client=('192.168.1.30', 1234)) as client:
            again = client.post('/api/devices/session', headers={'X-Vela-Device': credential}, json={})
        self.assertEqual(again.status_code, 200, again.text)
        stored = json.loads((self.config.data_dir / 'devices.json').read_text(encoding='utf-8'))
        self.assertEqual(stored['version'], 1)


if __name__ == '__main__':
    unittest.main()
