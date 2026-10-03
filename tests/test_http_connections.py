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


class SelfHostedConnectionTests(unittest.TestCase):
    """An http connection whose address the owner sets, not the manifest.

    `baseUrl` stays in the manifest as a placeholder; `selfHosted: true` means
    the owner supplies the real address at setup — a public https URL or a LAN
    address — and the secret is bound to exactly that origin.
    """

    session = base_tests.ApiBoundaryTests.session
    tearDown = base_tests.ApiBoundaryTests.tearDown
    write_app = HttpConnectionTests.write_app
    upstream = HttpConnectionTests.upstream
    invoke = HttpConnectionTests.invoke

    MANIFEST = {
        **copy.deepcopy(MANIFEST),
        "id": "self-hosted-fixture",
        "name": "Self-hosted fixture",
        "description": "Reaches a panel at an address the owner sets.",
        "connection": {
            "provider": "http",
            "baseUrl": "https://panel.example.com",
            "selfHosted": True,
            "operations": ["request"],
            "secret": {"label": "API key", "header": "X-API-Key", "prefix": "", "required": True},
        },
    }
    LAN = "http://192.168.1.20:7575"

    def setUp(self):
        base_tests.ApiBoundaryTests.setUp(self)
        self.write_app(self.MANIFEST)
        self.calls = []
        self.client.close()
        self.transport = httpx.MockTransport(self.upstream)
        self.client = TestClient(create_app(self.config, connection_transport=self.transport))
        self.hub = {"Authorization": "Bearer " + self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]}
        installed = self.client.post("/api/apps/self-hosted-fixture/install", headers=self.hub)
        self.assertEqual(installed.status_code, 200, installed.text)

    def put(self, body):
        return self.client.put("/api/apps/self-hosted-fixture/connection", headers=self.hub, json=body)

    def status(self):
        return self.client.get("/api/apps/self-hosted-fixture/connection", headers=self.hub).json()

    def set_address(self, address=LAN):
        response = self.put({"endpoint": address})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_the_address_and_secret_have_to_arrive_in_order(self):
        # Nothing set: the status says plainly what is missing, and no request
        # can be made — a service with no address is a setup step, not a failure.
        status = self.status()
        self.assertEqual((status["selfHosted"], status["addressSet"]), (True, False))
        self.assertIsNone(status["endpoint"])
        self.assertFalse(status["connected"])
        _, token = self.session("self-hosted-fixture")
        self.assertEqual(self.invoke(token, {"path": "/user"}).status_code, 409)
        # The secret cannot be saved before there is an address to bind it to.
        self.assertEqual(self.put({"secret": SECRET}).status_code, 409)
        self.assertEqual(self.calls, [])

        status = self.set_address()
        self.assertEqual((status["addressSet"], status["endpoint"]), (True, self.LAN))
        self.assertFalse(status["connected"], "the key is still missing")
        saved = self.put({"secret": SECRET})
        self.assertEqual(saved.status_code, 200, saved.text)
        status = saved.json()
        self.assertTrue(status["connected"])
        self.assertTrue(status["secret"]["configured"])
        self.assertNotIn(SECRET, saved.text)

    def test_requests_go_to_the_bound_address_not_the_manifests(self):
        self.set_address()
        self.put({"secret": SECRET})
        _, token = self.session("self-hosted-fixture")
        response = self.invoke(token, {"path": "/user"})
        self.assertEqual(response.status_code, 200, response.text)
        sent = self.calls[-1]
        self.assertEqual(str(sent.url), self.LAN + "/user")
        self.assertEqual(sent.headers["x-api-key"], SECRET)
        self.assertNotIn("panel.example.com", str(sent.url))

    def test_the_address_can_be_public_https_or_a_lan_address(self):
        for address, effective in (
            ("https://status.myhouse.example", "https://status.myhouse.example"),
            ("https://status.myhouse.example/", "https://status.myhouse.example"),
            ("https://status.myhouse.example:8443", "https://status.myhouse.example:8443"),
            ("http://192.168.1.20:7575", "http://192.168.1.20:7575"),
            ("http://127.0.0.1:7575", "http://127.0.0.1:7575"),
            ("http://localhost:7575", "http://127.0.0.1:7575"),
        ):
            with self.subTest(address=address):
                self.assertEqual(self.set_address(address)["endpoint"], effective)

    def test_an_address_that_is_not_an_origin_is_refused(self):
        for address in (
            "http://status.myhouse.example",       # public DNS over plain http
            "https://user:pass@192.168.1.20:7575", # credentials in the URL
            "http://192.168.1.20:7575/panel",      # a path is not an origin
            "http://192.168.1.20:7575?token=x",
            "https://8.8.8.8",                     # a public IP is not LAN
            "panel.example.com",
            "javascript:alert(1)",
        ):
            with self.subTest(address=address):
                self.assertEqual(self.put({"endpoint": address}).status_code, 422)
        self.assertEqual(self.status()["addressSet"], False)

    def test_changing_the_address_drops_the_secret(self):
        self.set_address()
        self.put({"secret": SECRET})
        self.assertTrue(self.status()["secret"]["configured"])
        _, token = self.session("self-hosted-fixture")

        moved = self.set_address("http://192.168.1.30:7575")
        self.assertFalse(moved["secret"]["configured"], "the old key belongs to the old origin")
        self.assertFalse(moved["connected"])
        # Requests stop until a key is saved for the new address.
        self.assertEqual(self.invoke(token, {"path": "/user"}).status_code, 409)
        self.assertEqual(self.calls, [])

        self.put({"secret": "new_key_0123456789"})
        self.assertEqual(self.invoke(token, {"path": "/user"}).status_code, 200)
        self.assertEqual(self.calls[-1].headers["x-api-key"], "new_key_0123456789")
        self.assertEqual(self.calls[-1].url.host, "192.168.1.30")

    def test_owner_reads_go_to_the_bound_address(self):
        # The surface proxy pulls through the same connection; it must reach
        # the owner's address too, secret included.
        self.set_address()
        self.put({"secret": SECRET})
        desktop = self.client.get("/api/desktops", headers=self.hub).json()["defaultId"]
        view = self.client.post(
            f"/api/desktops/{desktop}/views",
            headers=self.hub,
            json={"kind": "surface", "appId": "self-hosted-fixture", "source": "/gui/srv-1"},
        )
        self.assertEqual(view.status_code, 201, view.text)
        document = {"surface": 1, "root": {"type": "text", "value": "hi"}}

        def panel(request):
            self.calls.append(request)
            return httpx.Response(200, json=document)

        self.transport.handler = panel
        response = self.client.get(
            f"/api/desktops/{desktop}/views/{view.json()['id']}/surface", headers=self.hub
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), document)
        sent = self.calls[-1]
        self.assertEqual(str(sent.url), self.LAN + "/gui/srv-1/surface")
        self.assertEqual(sent.headers["x-api-key"], SECRET)


if __name__ == "__main__": unittest.main()
