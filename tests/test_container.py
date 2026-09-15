"""Container startup, persistent credentials and HTTPS proxy boundaries."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_app_contract  # Keeps the default API app on disposable data.
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from vela.access import verify_password
from vela.api import create_app
from vela.config import load_config
from vela.container import configure


class ContainerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="vela-container-test-")
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(os.environ, {
            "VELA_DATA_DIR": self.directory.name,
            "VELA_PUBLIC_ORIGIN": "https://vela.example.com",
            "VELA_TRUSTED_PROXIES": "172.18.0.1",
            "VELA_INITIAL_PASSWORD": "disposable-test-password",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_first_start_and_restart_preserve_password_and_data(self):
        self.assertEqual(configure(), "172.18.0.1")
        password_file = Path(self.directory.name) / "access.json"
        record = password_file.read_bytes()
        self.assertTrue(verify_password(password_file, "disposable-test-password"))
        self.assertNotIn("VELA_INITIAL_PASSWORD", os.environ)
        marker = Path(self.directory.name) / "settings.json"
        marker.write_text('{"theme": "dark"}')
        os.environ["VELA_INITIAL_PASSWORD"] = "different-test-password"
        configure()
        self.assertEqual(password_file.read_bytes(), record)
        self.assertEqual(marker.read_text(), '{"theme": "dark"}')

    def test_missing_or_invalid_settings_fail_before_creating_credentials(self):
        for key, values in {
            "VELA_PUBLIC_ORIGIN": ["", "http://vela.example.com", "https://vela.example.com/",
                                   "https://user:pass@vela.example.com", "https://vela.example.com:bad"],
            "VELA_TRUSTED_PROXIES": ["", "*", "0.0.0.0/0", "::/0", "proxy.example.com"],
            "VELA_INITIAL_PASSWORD": ["", "short"],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}):
                    with self.assertRaises(ValueError):
                        configure()
                    self.assertFalse((Path(self.directory.name) / "access.json").exists())

    def test_proxy_login_and_untrusted_forwarding(self):
        configure()
        app = ProxyHeadersMiddleware(create_app(load_config()), trusted_hosts=["172.18.0.1"])
        headers = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "192.0.2.12",
                   "X-Vela-Bootstrap": "1", "Origin": "https://vela.example.com"}
        with TestClient(app, base_url="http://vela.example.com", client=("172.18.0.1", 1234)) as client:
            self.assertEqual(client.get("/api/health").status_code, 200)
            self.assertEqual(client.get("/api/session", headers=headers).status_code, 401)
            login = client.post("/api/login", headers=headers,
                                json={"password": "disposable-test-password"})
            self.assertEqual(login.status_code, 200)
            self.assertIn("Secure", login.headers["set-cookie"])
            token = login.json()["token"]
            authorized = {**headers, "Authorization": f"Bearer {token}"}
            self.assertEqual(client.get("/api/apps", headers=authorized).status_code, 200)
            self.assertEqual(client.get("/api/apps", headers={"Authorization": f"Bearer {token}"}).status_code, 403)
            self.assertEqual(client.post("/api/login", headers={**headers, "Origin": "https://evil.example"},
                                         json={"password": "disposable-test-password"}).status_code, 403)
        with TestClient(app, base_url="http://vela.example.com", client=("172.18.0.9", 1234)) as client:
            self.assertEqual(client.post("/api/login", headers=headers,
                                         json={"password": "disposable-test-password"}).status_code, 403)
            self.assertEqual(client.get("/api/apps", headers=authorized).status_code, 403)
