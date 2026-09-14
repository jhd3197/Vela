import copy
import json
import shutil
import sqlite3
import unittest
from scripts.fixture_apps import APPS as FIXTURE_APPS
from pathlib import Path

import test_app_contract as base_tests
ROOT = base_tests.ROOT
import httpx
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.access import set_password
from vela.config import Config
from vela.connections import validate_endpoint
from vela.app_storage import AppServiceError


def habit(name="Read", identity="habit-one"):
    return {"id": identity, "name": name, "target": 3, "color": "#aabbcc", "days": {"2026-09-01": True}}


class ConnectionTests(unittest.TestCase):
    session = base_tests.ApiBoundaryTests.session
    tearDown = base_tests.ApiBoundaryTests.tearDown

    def setUp(self):
        base_tests.ApiBoundaryTests.setUp(self)
        for app_id in ("health", "ollama"):
            source = FIXTURE_APPS / app_id
            shutil.copytree(source, self.apps / app_id)
        self.calls = []
        self.client.close()
        self.transport = httpx.MockTransport(self.upstream)
        self.client = TestClient(create_app(self.config, connection_transport=self.transport))
        self.hub = {"Authorization": "Bearer " + self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]}

    def upstream(self, request):
        self.calls.append(request)
        if request.url.path == "/api/version": return httpx.Response(200, json={"version": "0.fixture"})
        if request.url.path == "/api/tags": return httpx.Response(200, json={"models": [{"name": "fixture:latest", "size": 1234, "details": {"family": "test"}}]})
        if request.url.path == "/api/show": return httpx.Response(200, json={"details": {"family": "test"}})
        return httpx.Response(404)

    def test_migration_retains_both_conflicts_and_is_repeat_safe(self):
        _, headers = self.session("health")
        current = {"habits": [habit("Engine version")]}
        self.client.put("/api/app/storage", headers=headers, json={"value": current, "revision": 0})
        local = [habit("Browser version"), habit("Walk", "habit-two")]
        preview = self.client.post("/api/apps/health/migration/preview", headers=self.hub, json={"value": local, "revision": 0})
        self.assertEqual(preview.json()["conflicts"], 1)
        self.assertEqual(preview.json()["added"], 2)
        stale = self.client.post("/api/apps/health/migration", headers=self.hub, json={"value": local, "revision": 0})
        self.assertEqual(stale.status_code, 409)
        response = self.client.post("/api/apps/health/migration", headers=self.hub, json={"value": local, "revision": 1})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({item["name"] for item in response.json()["value"]["habits"]}, {"Engine version", "Browser version", "Walk"})
        again = self.client.post("/api/apps/health/migration", headers=self.hub, json={"value": local, "revision": 1})
        self.assertTrue(again.json()["alreadyImported"])
        with sqlite3.connect(self.config.data_dir / "app-data.sqlite") as db:
            self.assertEqual(json.loads(db.execute("SELECT original FROM migrations WHERE app_id='health'").fetchone()[0]), local)
        db.close()
        self.assertEqual(self.client.post("/api/apps/health/migration", headers=headers, json={"value": local, "revision": 2}).status_code, 401)

    def test_health_schema_and_two_client_revision_contract(self):
        _, first = self.session("health")
        _, second = self.session("health")
        invalid = habit(); invalid["color"] = 'red" onmouseover="alert(1)'
        self.assertEqual(self.client.put("/api/app/storage", headers=first, json={"value": {"habits": [invalid]}, "revision": 0}).status_code, 422)
        valid = {"habits": [habit()]}
        self.assertEqual(self.client.put("/api/app/storage", headers=first, json={"value": valid, "revision": 0}).status_code, 200)
        self.assertEqual(self.client.get("/api/app/storage", headers=second).json()["value"], valid)
        self.assertEqual(self.client.put("/api/app/storage", headers=second, json={"value": {"habits": []}, "revision": 0}).status_code, 409)
        malformed = [habit(), habit()]
        self.assertEqual(self.client.post("/api/apps/health/migration/preview", headers=self.hub, json={"value": malformed, "revision": 0}).status_code, 422)

    def test_app_backup_restore_is_scoped_and_keeps_recovery(self):
        _, headers = self.session("health")
        self.client.put("/api/app/storage", headers=headers, json={"value": {"habits": [habit()]}, "revision": 0})
        saved = self.client.post("/api/app/storage/snapshots", headers=headers).json()
        self.client.put("/api/app/storage", headers=headers, json={"value": {"habits": []}, "revision": 1})
        path = f"/api/app/storage/snapshots/{saved['id']}/restore"
        _, other = self.session("other-app")
        self.assertEqual(self.client.post(path, headers=other, json={"revision": 0}).status_code, 404)
        self.assertEqual(self.client.post(path, headers=headers, json={"revision": 1}).status_code, 409)
        restored = self.client.post(path, headers=headers, json={"revision": 2})
        self.assertEqual(restored.json()["revision"], 3)
        self.assertEqual(restored.json()["value"]["habits"][0]["name"], "Read")
        snapshots = self.client.get("/api/app/storage/snapshots", headers=headers).json()["snapshots"]
        self.assertEqual(snapshots[0]["reason"], "Before restore")

    def test_bundled_health_upgrade_preserves_installed_original(self):
        self.session("health")
        target = self.config.installed_dir / "health"
        old = json.loads((target / "app.json").read_text())
        old = {key: old[key] for key in ("id", "name", "version", "description", "category", "author")}
        old["platforms"] = {"web": {"entry": "index.html"}}
        (target / "app.json").write_text(json.dumps(old))
        (target / "index.html").write_text("legacy package bytes")
        self.assertTrue(self.client.get("/api/apps/health", headers=self.hub).json()["upgradeAvailable"])
        response = self.client.post("/api/apps/health/upgrade", headers=self.hub)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((Path(response.json()["previousPackage"]) / "index.html").read_text(), "legacy package bytes")
        self.assertEqual(json.loads((target / "app.json").read_text())["schemaVersion"], 2)

    def test_ollama_read_grants_and_uninstall_have_no_upstream_lifecycle(self):
        self.client.post("/api/apps/ollama/install", headers=self.hub)
        bound = self.client.put("/api/apps/ollama/connection", headers=self.hub, json={"endpoint": "http://localhost:11434"})
        self.assertEqual(bound.status_code, 200, bound.text)
        _, token = self.session("ollama")
        response = self.client.post("/api/app/connection/invoke", headers=token, json={"operation": "models.list"})
        self.assertEqual(response.json()["models"][0]["name"], "fixture:latest")
        self.assertEqual(self.client.post("/api/app/connection/invoke", headers=token, json={"operation": "models.delete", "payload": {"model": "fixture:latest"}}).status_code, 403)
        self.assertEqual(self.client.post("/api/app/connection/invoke", headers=token, json={"operation": "models.list", "payload": {"url": "http://127.0.0.1:7700/api/settings"}}).status_code, 422)
        before = len(self.calls)
        self.client.delete("/api/apps/ollama", headers=self.hub)
        self.assertEqual(len(self.calls), before)
        self.assertEqual(self.client.get("/api/app/connection", headers=token).status_code, 401)
        self.client.post("/api/apps/ollama/install", headers=self.hub)
        self.assertFalse(self.client.get("/api/apps/ollama/connection", headers=self.hub).json()["connected"])
        for request in self.calls:
            self.assertNotIn("authorization", request.headers)
            self.assertNotIn("cookie", request.headers)
            self.assertIn(request.url.path, ("/api/version", "/api/tags"))

    def test_connection_validation_and_redirect_refusal(self):
        for endpoint in ("http://169.254.169.254", "http://example.com", "http://127.0.0.1/api/settings", "http://user:secret@127.0.0.1", "http://127.0.0.1?target=x", "file:///etc/passwd", "http://0.0.0.0", "http://8.8.8.8"):
            with self.subTest(endpoint=endpoint), self.assertRaises(AppServiceError): validate_endpoint(endpoint)
        self.assertEqual(validate_endpoint("http://192.168.1.40:11434"), "http://192.168.1.40:11434")
        self.client.post("/api/apps/ollama/install", headers=self.hub)
        self.transport.handler = lambda request: httpx.Response(302, headers={"location": "http://127.0.0.1:7700/api/settings"})
        response = self.client.put("/api/apps/ollama/connection", headers=self.hub, json={"endpoint": "http://localhost:11434"})
        self.assertEqual(response.status_code, 502)
        self.assertFalse(self.client.get("/api/apps/ollama/connection", headers=self.hub).json()["connected"])

    def test_https_password_sessions_logout_and_origin_boundary(self):
        password = "test-only-password-123"
        set_password(self.config.data_dir / "access.json", password)
        config = Config(self.config.data_dir, self.apps, ROOT / "web/dist", True, "https://vela.test")
        with TestClient(create_app(config), base_url="https://vela.test") as remote:
            bootstrap = {"X-Vela-Bootstrap": "1"}
            self.assertEqual(remote.get("/api/session", headers=bootstrap).status_code, 401)
            self.assertEqual(remote.post("/api/login", headers={**bootstrap, "Origin": "https://evil.test"}, json={"password": password}).status_code, 403)
            self.assertEqual(remote.post("/api/login", headers=bootstrap, json={"password": "incorrect-password"}).status_code, 401)
            login = remote.post("/api/login", headers=bootstrap, json={"password": password})
            self.assertEqual(login.status_code, 200)
            self.assertIn("Secure", login.headers["set-cookie"])
            self.assertIn("HttpOnly", login.headers["set-cookie"])
            token = {"Authorization": "Bearer " + login.json()["token"]}
            self.assertEqual(remote.get("/api/settings", headers=token).status_code, 200)
            self.assertEqual(remote.get("http://vela.test/api/session", headers=bootstrap).status_code, 403)
            remote.post("/api/apps/health/install", headers=token)
            app_token = {"Authorization": "Bearer " + remote.post("/api/apps/health/session", headers=token).json()["token"]}
            self.assertEqual(remote.get("/api/app/storage", headers=app_token).status_code, 200)
            remote.post("/api/logout", headers=bootstrap)
            self.assertEqual(remote.get("/api/settings", headers=token).status_code, 401)
            self.assertEqual(remote.get("/api/app/storage", headers=app_token).status_code, 401)

    def test_failed_rebind_keeps_original_and_bounds_upstream_responses(self):
        self.client.post("/api/apps/ollama/install", headers=self.hub)
        endpoint = "http://127.0.0.1:11434"
        self.assertEqual(self.client.put("/api/apps/ollama/connection", headers=self.hub, json={"endpoint": endpoint}).status_code, 200)
        for body in (b'not JSON', b'x' * 1048577):
            self.transport.handler = lambda request: httpx.Response(200, content=body)
            result = self.client.put("/api/apps/ollama/connection", headers=self.hub, json={"endpoint": "http://127.0.0.1:11435"})
            self.assertEqual(result.status_code, 502)
            self.assertEqual(self.client.get("/api/apps/ollama/connection", headers=self.hub).json()["endpoint"], endpoint)
        def timeout(request): raise httpx.ReadTimeout("fixture deadline", request=request)
        self.transport.handler = timeout
        _, token = self.session("ollama")
        self.assertEqual(self.client.post("/api/app/connection/invoke", headers=token, json={"operation": "models.list"}).status_code, 504)


if __name__ == "__main__": unittest.main()
