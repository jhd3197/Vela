"""Wi-Fi access uses disposable certificates/data, never the user's server."""
import ipaddress
import ssl
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_app_contract as base
import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from fastapi import FastAPI
from fastapi.testclient import TestClient

from vela.access import set_password
from vela.api import create_app
from vela.app_storage import AppServiceError
from vela.auth import Auth
from vela.config import Config
from vela.phone_access import PhoneAccess, PRIVATE_NETWORKS, private_ipv4


class PhoneAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vela-phone-auth-')
        root = Path(self.temp.name)
        self.config = Config(root, root / 'apps', base.ROOT / 'web/dist')
        self.config.ensure_dirs()
        self.app = create_app(self.config)
        self.client = TestClient(self.app)
        self.hub = {'Authorization': 'Bearer ' + self.client.get('/api/session', headers={'X-Vela-Bootstrap': '1'}).json()['token']}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_admin_routes_require_local_hub_and_never_enable_on_read(self):
        self.assertEqual(self.client.get('/api/phone-access').status_code, 401)
        with patch('vela.phone_access.local_addresses', return_value=['192.168.1.20']):
            info = self.client.get('/api/phone-access', headers=self.hub).json()
        self.assertFalse(info['enabled'])
        self.assertTrue(info['needs_password'])
        self.assertFalse((self.config.data_dir / 'phone-access').exists())
        self.assertEqual(self.client.post('/api/phone-access', headers=self.hub, json={'address': '127.0.0.1', 'password': 'fixture-password'}).status_code, 422)
        self.assertEqual(self.client.post('/api/phone-access', headers=self.hub, json={'address': '192.168.1.20', 'password': 'short'}).status_code, 422)
        self.assertEqual(self.client.get('/api/session', headers={'X-Vela-Bootstrap': '1', 'host': '192.168.1.20'}).status_code, 403)

    def test_wifi_login_cannot_use_local_tokens_and_logout_revokes_app_sessions(self):
        set_password(self.config.data_dir / 'access.json', 'fixture-password')
        auth = Auth(self.config)
        auth.phone_origin = 'https://192.168.1.20:7702'
        app = FastAPI()
        app.middleware('http')(auth.middleware)
        from fastapi import Request
        @app.get('/api/session')
        def session(request: Request):
            return {'token': auth.bootstrap(request)}
        @app.post('/api/login')
        def login(request: Request, password: str):
            return {'token': auth.login(request, password)}
        @app.get('/api/private')
        def private(): return {'ok': True}
        @app.get('/api/app/value')
        def app_value(): return {'ok': True}
        @app.post('/api/logout')
        def logout(request: Request):
            auth.logout(request)
            return {'ok': True}
        headers = {'X-Vela-Bootstrap': '1'}
        phone = TestClient(app, base_url=auth.phone_origin, client=('192.168.1.30', 1234))
        # Auth errors use the same handler as the real engine.
        from fastapi.responses import JSONResponse
        @app.exception_handler(AppServiceError)
        async def error(request, exc): return JSONResponse({'detail': exc.detail}, status_code=exc.status)
        self.assertEqual(phone.get('/api/session', headers=headers).status_code, 401)
        local_header = {'Authorization': 'Bearer ' + auth.hub_token}
        self.assertEqual(phone.get('/api/private', headers=local_header).status_code, 401)
        phone.cookies.set('__Host-vela-session', auth.hub_token)
        self.assertEqual(phone.get('/api/session', headers=headers).status_code, 401)
        self.assertEqual(phone.post('/api/login?password=wrong-password', headers=headers).status_code, 401)
        token = phone.post('/api/login?password=fixture-password', headers=headers).json()['token']
        phone.cookies.set('__Host-vela-session', token)
        self.assertEqual(phone.get('/api/session', headers=headers).json()['token'], token)
        self.assertEqual(phone.get('/api/private', headers={'Authorization': 'Bearer ' + token}).status_code, 200)
        auth.sessions['fixture-app'] = {'owner': token, 'expires': __import__('time').monotonic() + 60}
        self.assertEqual(phone.get('/api/app/value', headers={'Authorization': 'Bearer fixture-app'}).status_code, 200)
        phone.post('/api/logout', headers=headers)
        self.assertEqual(phone.get('/api/app/value', headers={'Authorization': 'Bearer fixture-app'}).status_code, 401)
        self.assertEqual(phone.get('/api/private', headers={'Authorization': 'Bearer ' + token}).status_code, 401)
        phone.close()

    def test_hosted_server_uses_configured_origin(self):
        set_password(self.config.data_dir / 'access.json', 'fixture-password')
        config = replace(self.config, remote_access=True, public_origin='https://vela.example.test')
        info = PhoneAccess(FastAPI(), config, Auth(config)).status()
        self.assertEqual(info, {'enabled': True, 'managed': False, 'setup_url': 'https://vela.example.test/setup'})


class PhoneListenerTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_boundary_certificate_and_real_tls_listener_restart(self):
        with tempfile.TemporaryDirectory(prefix='vela-phone-listener-') as tmp:
            root = Path(tmp)
            dist = root / 'dist'
            dist.mkdir()
            (dist / 'index.html').write_text('public phone instructions')
            (dist / 'secret.key').write_text('not public')
            config = Config(root, root / 'apps', dist)
            config.ensure_dirs()
            app = create_app(config)
            service = app.state.phone_access
            auth = service.auth
            # Bind only to loopback for testing. Production accepts assigned
            # RFC1918 addresses and never opens a test listener on real Wi-Fi.
            with patch('vela.phone_access.local_addresses', return_value=['127.0.0.1']), patch('vela.phone_access.private_ipv4', return_value=True), patch('vela.phone_access.PRIVATE_NETWORKS', (*PRIVATE_NETWORKS, ipaddress.ip_network('127.0.0.0/8'))):
                try:
                    info = await service.start('127.0.0.1', 'fixture-password', ports=(0, 0))
                    ca_path = service.directory / 'ca.pem'
                    ca = x509.load_pem_x509_certificate(ca_path.read_bytes())
                    constraints = ca.extensions.get_extension_for_class(x509.NameConstraints).value
                    self.assertIn(x509.DNSName('.invalid'), constraints.permitted_subtrees)
                    self.assertEqual(ca.fingerprint(hashes.SHA256()).hex().upper(), info['fingerprint'])
                    ctx = ssl.create_default_context(cafile=str(ca_path))
                    async with httpx.AsyncClient(trust_env=False) as http:
                        response = await http.get(info['setup_url'])
                        self.assertEqual(response.text, 'public phone instructions')
                        origin = info['setup_url'].removesuffix('/setup')
                        for endpoint in ('/api/session', '/api/private', '/apps/private/index.html', '/secret.key', '/ca.key', '/server.key'):
                            self.assertEqual((await http.get(origin + endpoint)).status_code, 404)
                        self.assertEqual((await http.get(origin + '/phone-bootstrap')).json()['secure_url'], info['secure_url'])
                        self.assertEqual((await http.get(origin + '/vela-phone.cer')).content, (service.directory / 'vela-phone.cer').read_bytes())
                        self.assertEqual((await http.get(info['setup_url'], headers={'host': 'evil.test'})).status_code, 403)
                    # Verify the generated chain and hostname using only this
                    # disposable CA; do not disable TLS verification or change OS trust.
                    async with httpx.AsyncClient(verify=ctx, trust_env=False) as https:
                        self.assertEqual((await https.get(service.origin + '/api/apps')).status_code, 401)
                        self.assertEqual((await https.get(service.origin + '/api/apps', headers={'Authorization': 'Bearer ' + auth.hub_token})).status_code, 401)
                        login = await https.post(service.origin + '/api/login', json={'password': 'fixture-password'}, headers={'X-Vela-Bootstrap': '1'})
                        self.assertEqual(login.status_code, 200, login.text)
                        hub = {'Authorization': 'Bearer ' + login.json()['token']}
                        self.assertEqual((await https.get(service.origin + '/api/apps', headers=hub)).status_code, 200)
                        session = await https.get(service.origin + '/api/session', headers={'X-Vela-Bootstrap': '1'})
                        self.assertTrue(session.json()['remote'])
                        self.assertEqual((await https.delete(service.origin + '/api/phone-access', headers=hub)).status_code, 403)
                    fingerprint = info['fingerprint']
                    setup_url = info['setup_url']
                    await service.stop()
                    self.assertFalse(service.status()['enabled'])
                    await service.restore()
                    self.assertEqual(service.status()['setup_url'], setup_url)
                    self.assertEqual(service.status()['fingerprint'], fingerprint)
                    await service.stop(disable=True)
                    self.assertFalse((service.directory / 'enabled.json').exists())
                finally:
                    await service.stop()

    async def test_private_address_filter(self):
        for ip in ('127.0.0.1', '0.0.0.0', '8.8.8.8', '169.254.1.1', '::1', 'invalid'):
            self.assertFalse(private_ipv4(ip))
        for ip in ('10.0.0.2', '172.16.0.1', '192.168.1.20'):
            self.assertTrue(private_ipv4(ip))
