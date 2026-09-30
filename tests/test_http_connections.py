"""The http connection provider: one declared origin, a secret the frame never sees."""
import copy
import json
import unittest
from pathlib import Path

import test_app_contract as base_tests
import httpx
from fastapi.testclient import TestClient
from vela.api import create_app
from vela.connections import Connections
from vela.manifest import ManifestError, validate_manifest

MANIFEST = {
    "schemaVersion": 2,
    "id": "remote-fixture",
    "name": "Remote fixture",
    "version": "1.0.0",
    "description": "Calls one public API through the host.",
    "category": "developer",
    "author": "Vela tests",
    "compatibility": {"bridge": 1},
    "runtime": {"static": {"entry": "index.html"}},
    "view": {"surface": "embedded", "chrome": "compact", "appearance": "auto"},
    "capabilities": {"required": ["connections"]},
    "connection": {
        "provider": "http",
        "baseUrl": "https://api.example.com",
        "operations": ["request"],
        "headers": {"Accept": "application/vnd.fixture+json"},
        "exposeHeaders": ["x-ratelimit-remaining"],
        "secret": {"label": "API token", "header": "Authorization", "prefix": "Bearer ", "required": True},
    },
}
SECRET = "fixture_token_0123456789"


class HttpConnectionTests(unittest.TestCase):
    session = base_tests.ApiBoundaryTests.session
    tearDown = base_tests.ApiBoundaryTests.tearDown

    def setUp(self):
        base_tests.ApiBoundaryTests.setUp(self)
        self.write_app(MANIFEST)
        self.calls = []
        self.client.close()
        self.transport = httpx.MockTransport(self.upstream)
        self.client = TestClient(create_app(self.config, connection_transport=self.transport))
        self.hub = {"Authorization": "Bearer " + self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]}

    def write_app(self, manifest):
        folder = self.apps / manifest["id"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
        (folder / "index.html").write_text("<!doctype html><title>fixture</title>", encoding="utf-8")

    def upstream(self, request):
        self.calls.append(request)
        if request.url.path == "/missing":
            return httpx.Response(404, json={"message": "Not Found"})
        if request.url.path == "/moved":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:7700/api/settings"})
        return httpx.Response(
            200,
            json={"path": request.url.path, "query": dict(request.url.params)},
            headers={"x-ratelimit-remaining": "59", "set-cookie": "tracker=1"},
        )

    def invoke(self, token, payload):
        return self.client.post("/api/app/connection/invoke", headers=token, json={"operation": "request", "payload": payload})

    def test_secret_is_added_by_the_engine_and_never_returned(self):
        _, token = self.session("remote-fixture")
        status = self.client.get("/api/apps/remote-fixture/connection", headers=self.hub).json()
        self.assertFalse(status["connected"])
        self.assertEqual(status["secret"]["label"], "API token")
        self.assertEqual(self.invoke(token, {"path": "/user"}).status_code, 409)
        self.assertEqual(self.calls, [])

        saved = self.client.put("/api/apps/remote-fixture/connection", headers=self.hub, json={"secret": SECRET})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["secret"]["configured"])
        self.assertNotIn(SECRET, saved.text)
        self.assertNotIn(SECRET, self.client.get("/api/app/connection", headers=token).text)

        response = self.invoke(token, {"path": "/search/issues", "query": {"q": "is:open", "per_page": 5, "draft": False}})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["body"]["query"], {"q": "is:open", "per_page": "5", "draft": "false"})
        self.assertEqual(result["headers"], {"x-ratelimit-remaining": "59"})
        sent = self.calls[-1]
        self.assertEqual(str(sent.url.copy_with(query=None)), "https://api.example.com/search/issues")
        self.assertEqual(sent.headers["authorization"], "Bearer " + SECRET)
        self.assertEqual(sent.headers["accept"], "application/vnd.fixture+json")
        self.assertNotIn("cookie", sent.headers)

        self.client.delete("/api/apps/remote-fixture/connection", headers=self.hub)
        self.assertFalse(self.client.get("/api/apps/remote-fixture/connection", headers=self.hub).json()["secret"]["configured"])
        self.assertEqual(self.invoke(token, {"path": "/user"}).status_code, 409)

    def test_requests_stay_within_the_declared_grant(self):
        _, token = self.session("remote-fixture")
        self.client.put("/api/apps/remote-fixture/connection", headers=self.hub, json={"secret": SECRET})
        refused = {
            403: [{"path": "/user", "method": "POST"}],
            422: [
                {"path": "https://evil.example/user"},
                {"path": "//evil.example/user"},
                {"path": "/repos/../admin"},
                {"path": "/repos/%2e%2e/admin"},
                {"path": "/user?token=x"},
                {"path": "/user", "url": "http://127.0.0.1:7700"},
                {"path": "/user", "query": {"q": {"nested": True}}},
                {"path": "/user", "body": {"a": 1}},
            ],
        }
        for status, payloads in refused.items():
            for payload in payloads:
                with self.subTest(payload=payload):
                    self.assertEqual(self.invoke(token, payload).status_code, status)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.client.post("/api/app/connection/invoke", headers=token, json={"operation": "models.list"}).status_code, 403)

    def test_upstream_statuses_are_answers_and_redirects_are_not_followed(self):
        _, token = self.session("remote-fixture")
        self.client.put("/api/apps/remote-fixture/connection", headers=self.hub, json={"secret": SECRET})
        missing = self.invoke(token, {"path": "/missing"}).json()
        self.assertEqual((missing["status"], missing["body"]), (404, {"message": "Not Found"}))
        moved = self.invoke(token, {"path": "/moved"}).json()
        self.assertEqual(moved["status"], 302)
        self.assertEqual([call.url.host for call in self.calls], ["api.example.com", "api.example.com"])

        def timeout(request): raise httpx.ReadTimeout("fixture deadline", request=request)
        self.transport.handler = timeout
        self.assertEqual(self.invoke(token, {"path": "/user"}).status_code, 504)
        self.transport.handler = lambda request: httpx.Response(200, content=b"x" * (2 * 1048576 + 1))
        self.assertEqual(self.invoke(token, {"path": "/user"}).status_code, 502)

    def test_secret_and_address_are_validated(self):
        self.client.post("/api/apps/remote-fixture/install", headers=self.hub)
        put = lambda body: self.client.put("/api/apps/remote-fixture/connection", headers=self.hub, json=body).status_code
        self.assertEqual(put({"endpoint": "http://127.0.0.1:11434"}), 422)
        self.assertEqual(put({"secret": "has space"}), 422)
        self.assertEqual(put({"secret": "line\nbreak"}), 422)
        self.assertEqual(put({}), 422)

    def test_a_secret_is_not_carried_to_a_new_origin(self):
        connection = MANIFEST["connection"]
        binding = {"provider": "http", "endpoint": connection["baseUrl"], "secret": SECRET}
        self.assertEqual(Connections._http_secret(None, connection, binding), SECRET)
        moved = {**connection, "baseUrl": "https://api.elsewhere.example"}
        self.assertIsNone(Connections._http_secret(None, moved, binding))

    def test_manifest_refuses_unsafe_origins_and_headers(self):
        for change in (
            {"baseUrl": "http://api.example.com"},
            {"baseUrl": "https://127.0.0.1"},
            {"baseUrl": "https://localhost"},
            {"baseUrl": "https://api.example.com/v3"},
            {"headers": {"Cookie": "session=1"}},
            {"headers": {"Host": "127.0.0.1"}},
            {"secret": {"label": "Token", "header": "Proxy-Authorization"}},
        ):
            manifest = copy.deepcopy(MANIFEST)
            manifest["connection"].update(change)
            with self.subTest(change=change), self.assertRaises(ManifestError):
                validate_manifest(manifest, folder="remote-fixture", path=Path("."))
        validate_manifest(copy.deepcopy(MANIFEST), folder="remote-fixture", path=Path("."))


if __name__ == "__main__": unittest.main()
