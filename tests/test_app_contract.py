"""Run with: python -m unittest discover -s tests -v. Uses disposable data only."""
import atexit
import copy
import json
import os
import shutil
import tempfile
import unittest
from scripts.fixture_apps import APPS as FIXTURE_APPS
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# api.py constructs its default ASGI app on import. Keep that off the user's data.
_bootstrap = tempfile.TemporaryDirectory(prefix="vela-test-bootstrap-")
atexit.register(_bootstrap.cleanup)
os.environ["VELA_DATA_DIR"] = _bootstrap.name

from fastapi.testclient import TestClient
from vela.api import create_app
from vela.config import Config
from vela.manifest import load_manifest, validate_manifest, ManifestError
from vela.app_storage import AppStorage, AppServiceError
from vela.backups import BackupStore

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "tests/fixtures/chat-fixture/app.json").read_text())


class ContractTests(unittest.TestCase):
    def test_legacy_normalization(self):
        legacy = [load_manifest(folder) for folder in FIXTURE_APPS.iterdir() if (folder / "app.json").exists()]
        legacy = [manifest for manifest in legacy if manifest.schema_version == 1]
        self.assertEqual({manifest.id for manifest in legacy}, {"hello-vela", "system-info", "finance"})
        for manifest in legacy:
            self.assertEqual(manifest.schema_version, 1)
            self.assertEqual(manifest.view, {"surface": "embedded", "chrome": "compact"})
            self.assertEqual(manifest.capabilities, [])

    def test_v2_defaults_and_explicit_runtime(self):
        data = copy.deepcopy(FIXTURE)
        del data["view"]["chrome"]
        data["capabilities"]["optional"] = ["future.capability"]
        manifest = validate_manifest(data, "chat-fixture", Path("chat-fixture"))
        self.assertEqual(manifest.view["chrome"], "compact")
        self.assertEqual(manifest.capabilities, ["storage"])
        self.assertEqual(manifest.unavailable_capabilities, ["future.capability"])
        data["runtime"]["process"] = {"trust": "trusted-native", "platforms": {"windows": {"run": "node server.js", "port": 9901}}}
        manifest = validate_manifest(data, "chat-fixture", Path("chat-fixture"))
        self.assertEqual(manifest.active_runtime("windows"), "process")
        self.assertEqual(manifest.runtimes("windows"), ["web", "process"])
        self.assertFalse(manifest.supports("posix"))

    def test_unsupported_fields_versions_and_paths_fail(self):
        variants = [
            {"schemaVersion": 3}, {"schemaVersion": True}, {"unknown": 1},
            {"compatibility": {"bridge": 2}}, {"capabilities": {"required": ["hub.settings"]}},
            {"runtime": {"static": {"entry": "../index.html"}}},
            {"view": {"surface": "embedded", "chrome": "magic"}},
            {"runtime": {"static": {"entry": "index.html", "extra": True}}},
            {"view": {"surface": "external", "url": "javascript:alert(1)"}},
        ]
        for patch in variants:
            with self.subTest(patch=patch), self.assertRaises(ManifestError):
                validate_manifest({**copy.deepcopy(FIXTURE), **patch}, "chat-fixture", Path("chat-fixture"))


class ApiBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="vela-contract-")
        self.root = Path(self.temp.name)
        self.apps = self.root / "catalog"
        for app_id in ("chat-fixture", "other-app", "no-storage"):
            shutil.copytree(ROOT / "tests/fixtures/chat-fixture", self.apps / app_id)
            manifest = copy.deepcopy(FIXTURE)
            manifest["id"] = app_id
            if app_id == "no-storage": manifest["capabilities"] = {}
            (self.apps / app_id / "app.json").write_text(json.dumps(manifest))
        self.config = Config(self.root / "data", self.apps, ROOT / "web/dist")
        self.config.ensure_dirs()
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def session(self, app_id="chat-fixture"):
        self.assertEqual(self.client.post(f"/api/apps/{app_id}/install", headers=self.hub).status_code, 200)
        response = self.client.post(f"/api/apps/{app_id}/session", headers=self.hub)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json(), {"Authorization": "Bearer " + response.json()["token"]}

    def test_hub_requires_auth_and_rejects_cross_origin_bootstrap(self):
        self.assertEqual(self.client.get("/api/settings").status_code, 401)
        self.assertEqual(self.client.get("/api/session").status_code, 403)
        for origin in ("null", "https://evil.example", "http://localhost:9999"):
            response = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1", "Origin": origin})
            self.assertEqual(response.status_code, 403)
            self.assertNotIn("access-control-allow-origin", response.headers)
        self.assertEqual(self.client.get("http://evil.example/api/session", headers={"X-Vela-Bootstrap": "1"}).status_code, 403)
        self.assertEqual(self.client.get("/api/settings", headers=self.hub).status_code, 200)

    def test_storage_identity_isolation_and_hub_denial(self):
        one, a = self.session()
        two, b = self.session("other-app")
        self.assertNotEqual(one["installationId"], two["installationId"])
        self.assertEqual(self.client.put("/api/app/storage", headers=a, json={"value": {"secret": "alpha"}, "revision": 0}).status_code, 200)
        self.assertIsNone(self.client.get("/api/app/storage", headers=b).json()["value"])
        self.assertEqual(self.client.get("/api/app/storage", headers=a).json()["value"], {"secret": "alpha"})
        for path in ("/api/settings", "/api/apps", "/api/backups", "/api/engine", "/api/apps/other-app/status"):
            self.assertEqual(self.client.get(path, headers=a).status_code, 401, path)
        self.assertEqual(self.client.patch("/api/settings", headers=a, json={"theme": "light"}).status_code, 401)
        self.assertEqual(self.client.post("/api/apps/other-app/session", headers=a).status_code, 401)
        self.assertEqual(self.client.put("/api/app/storage", headers=b, json={"app_id": "chat-fixture", "value": {}, "revision": 0}).status_code, 422)
        self.assertEqual(self.client.get("/api/app/storage", headers=self.hub).status_code, 401)
        self.assertEqual(self.client.get("/api/app/storage/other-app", headers=a).status_code, 404)

    def test_revision_quota_and_capability(self):
        _, headers = self.session()
        self.assertEqual(self.client.put("/api/app/storage", headers=headers, json={"value": "one", "revision": 0}).status_code, 200)
        self.assertEqual(self.client.put("/api/app/storage", headers=headers, json={"value": "two", "revision": 0}).status_code, 409)
        self.assertEqual(self.client.put("/api/app/storage", headers=headers, json={"value": "x" * 1048576, "revision": 1}).status_code, 413)
        self.assertEqual(self.client.put("/api/app/storage", headers=headers, json={"value": "bad", "revision": True}).status_code, 422)
        _, ungranted = self.session("no-storage")
        self.assertEqual(self.client.get("/api/app/storage", headers=ungranted).status_code, 403)

    def test_persistence_reinstall_and_revocation(self):
        original, headers = self.session()
        self.client.put("/api/app/storage", headers=headers, json={"value": ["retained"], "revision": 0})
        self.client.delete("/api/apps/chat-fixture", headers=self.hub)
        self.assertEqual(self.client.get("/api/app/storage", headers=headers).status_code, 401)
        fresh, fresh_headers = self.session()
        self.assertNotEqual(original["installationId"], fresh["installationId"])
        self.assertEqual(self.client.get("/api/app/storage", headers=fresh_headers).json()["value"], ["retained"])
        storage = AppStorage(self.config.data_dir / "app-data.sqlite")
        self.assertEqual(storage.read(fresh["installationId"], 1)["revision"], 1)
        self.client.delete("/api/app/session", headers=fresh_headers)
        self.assertEqual(self.client.get("/api/app/storage", headers=fresh_headers).status_code, 401)

    def test_installed_assets_sandbox_and_source_independence(self):
        self.session()
        shutil.rmtree(self.apps / "chat-fixture")
        response = self.client.get("/apps/chat-fixture/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("sandbox allow-scripts", response.headers["content-security-policy"])
        self.assertNotIn("allow-same-origin", response.headers["content-security-policy"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get("/apps/chat-fixture/_vela/sdk.js").status_code, 200)
        self.assertEqual(self.client.get("/apps/chat-fixture/sw.js").status_code, 404)
        self.assertEqual(self.client.post("/api/apps/chat-fixture/launch", headers=self.hub).status_code, 200)

    def test_transaction_serializes_competing_writers_and_schema_guard(self):
        identity, _ = self.session()
        storage = AppStorage(self.config.data_dir / "app-data.sqlite")
        def write(value):
            try:
                return storage.write(identity["installationId"], value, 0, 1, 1024)["revision"]
            except AppServiceError as exc:
                return exc.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(write, ["one", "two"])), [1, 409])
        with self.assertRaises(AppServiceError) as error:
            storage.read(identity["installationId"], 2)
        self.assertEqual(error.exception.status, 409)

    def test_backup_contains_consistent_app_document(self):
        identity, headers = self.session()
        self.client.put("/api/app/storage", headers=headers, json={"value": "snapshot", "revision": 0})
        backups = BackupStore(self.config)
        result = backups.create()
        self.client.put("/api/app/storage", headers=headers, json={"value": "new value", "revision": 1})
        backup_storage = AppStorage(self.config.data_dir / "backups" / result["name"] / "app-data.sqlite")
        self.assertEqual(backup_storage.read(identity["installationId"], 1)["value"], "snapshot")
        self.assertTrue(backups.verify(result["name"])["ok"])

    def test_invalid_installed_contract_never_falls_back_to_source(self):
        self.session()
        path = self.config.installed_dir / "chat-fixture/app.json"
        data = json.loads(path.read_text())
        data["schemaVersion"] = 99
        path.write_text(json.dumps(data))
        self.assertEqual(self.client.get("/api/apps/chat-fixture", headers=self.hub).status_code, 404)
        self.assertEqual(self.client.get("/apps/chat-fixture/").status_code, 404)


if __name__ == "__main__":
    unittest.main()
