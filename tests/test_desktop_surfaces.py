"""The surface proxy routes: the owner pulling a document through an app's
http connection.

The hub draws a surface document itself, so these routes are owner
authenticated and read-only: no grant is asked for, but the connection's own
path rules, timeouts and caps apply exactly as they do to the app's calls.
"""

import json
import unittest

import test_app_contract as base_tests
import httpx
from fastapi.testclient import TestClient
from vela.api import create_app

MANIFEST = {
    "schemaVersion": 2,
    "id": "surface-fixture",
    "name": "Surface fixture",
    "version": "1.0.0",
    "description": "Serves a surface document through its connection.",
    "category": "developer",
    "author": "Vela tests",
    "compatibility": {"bridge": 1},
    "runtime": {"static": {"entry": "index.html"}},
    "view": {"surface": "embedded", "chrome": "compact", "appearance": "auto"},
    "capabilities": {"required": ["connections"]},
    "connection": {
        "provider": "http",
        "baseUrl": "https://panel.example.com",
        "operations": ["request"],
    },
}

SOURCE = "/api/v1/server-gui/srv-1"
DOCUMENT = {
    "surface": 1,
    "title": "srv-1",
    "refresh": {"every": 4},
    "root": {
        "type": "stack",
        "children": [
            {"type": "text", "value": "web server"},
            {"type": "stat", "label": "CPU", "value": 12.4, "format": "percent"},
        ],
    },
}
FRAME = {
    "image_base64": "aGVsbG8=",
    "format": "jpeg",
    "width": 1200,
    "height": 800,
    "captured_at": "2026-01-01T00:00:00Z",
}
CAPABILITIES = {"screenshot": True, "actions": False}


def nested(depth):
    node = {"type": "text", "value": "leaf"}
    for _ in range(depth):
        node = {"type": "stack", "children": [node]}
    return node


class SurfaceProxyTests(unittest.TestCase):
    tearDown = base_tests.ApiBoundaryTests.tearDown

    def setUp(self):
        base_tests.ApiBoundaryTests.setUp(self)
        folder = self.apps / "surface-fixture"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "app.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
        (folder / "index.html").write_text("<!doctype html><title>fixture</title>", encoding="utf-8")
        self.calls = []
        self.client.close()
        self.transport = httpx.MockTransport(self.upstream)
        self.client = TestClient(create_app(self.config, connection_transport=self.transport))
        self.hub = {
            "Authorization": "Bearer "
            + self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        }
        self.desktop = self.client.get("/api/desktops", headers=self.hub).json()["defaultId"]
        installed = self.client.post("/api/apps/surface-fixture/install", headers=self.hub)
        self.assertEqual(installed.status_code, 200, installed.text)

    def upstream(self, request):
        self.calls.append(request)
        path = request.url.path
        if path == f"{SOURCE}/surface":
            return httpx.Response(200, json=DOCUMENT)
        if path == f"{SOURCE}/frame":
            return httpx.Response(200, json=FRAME)
        if path == f"{SOURCE}/capabilities":
            return httpx.Response(200, json=CAPABILITIES)
        if path == "/big/surface":
            return httpx.Response(
                200,
                json={
                    "surface": 1,
                    "root": {
                        "type": "stack",
                        "children": [{"type": "text", "value": str(i)} for i in range(1001)],
                    },
                },
            )
        if path == "/deep/surface":
            return httpx.Response(200, json={"surface": 1, "root": nested(9)})
        if path == "/huge/surface":
            return httpx.Response(
                200, json={"surface": 1, "root": {"type": "text", "value": "x" * 1100000}}
            )
        if path == "/v2/surface":
            return httpx.Response(200, json={"surface": 2, "root": {"type": "stack"}})
        return httpx.Response(404, json={"message": "Not Found"})

    def open_surface(self, source=SOURCE, app="surface-fixture"):
        response = self.client.post(
            f"/api/desktops/{self.desktop}/views",
            headers=self.hub,
            json={"kind": "surface", "appId": app, "source": source},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def surface(self, view_id, suffix="", desktop=None):
        return self.client.get(
            f"/api/desktops/{desktop or self.desktop}/views/{view_id}/surface{suffix}",
            headers=self.hub,
        )

    # ---- the document

    def test_the_document_is_fetched_through_the_apps_connection(self):
        view = self.open_surface()
        response = self.surface(view["id"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), DOCUMENT)
        sent = self.calls[-1]
        self.assertEqual(str(sent.url), "https://panel.example.com/api/v1/server-gui/srv-1/surface")
        self.assertEqual(sent.method, "GET")

    def test_the_frame_is_proxied_with_the_documented_clamping(self):
        view = self.open_surface()
        response = self.surface(view["id"], "/frame?scale=5&quality=1&format=gif")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), FRAME)
        sent = dict(self.calls[-1].url.params)
        self.assertEqual(sent, {"scale": "1.0", "quality": "10", "format": "jpeg"})
        # Defaults match the producer's documented ones.
        self.surface(view["id"], "/frame")
        sent = dict(self.calls[-1].url.params)
        self.assertEqual(sent, {"scale": "0.75", "quality": "70", "format": "jpeg"})

    def test_capabilities_are_proxied(self):
        view = self.open_surface()
        response = self.surface(view["id"], "/capabilities")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), CAPABILITIES)

    # ---- refusals

    def test_the_routes_need_a_hub_session(self):
        view = self.open_surface()
        for suffix in ("", "/frame", "/capabilities"):
            with self.subTest(suffix=suffix):
                response = self.client.get(
                    f"/api/desktops/{self.desktop}/views/{view['id']}/surface{suffix}"
                )
                self.assertEqual(response.status_code, 401)
        self.assertEqual(len(self.calls), 0, "an unauthenticated call never reaches the app")

    def test_a_view_that_is_not_a_surface_is_a_404(self):
        app_view = self.client.post(
            f"/api/desktops/{self.desktop}/views",
            headers=self.hub,
            json={"kind": "app", "appId": "surface-fixture"},
        ).json()
        self.assertEqual(self.surface(app_view["id"]).status_code, 404)
        self.assertEqual(self.surface("0" * 32).status_code, 404)

    def test_a_view_on_another_desktop_is_a_404(self):
        other = self.client.post("/api/desktops", headers=self.hub, json={}).json()["id"]
        view = self.open_surface()
        self.assertEqual(self.surface(view["id"], desktop=other).status_code, 404)

    def test_an_app_without_an_http_connection_is_a_clear_error(self):
        # chat-fixture is one of the shared fixture apps and has no connection.
        installed = self.client.post("/api/apps/chat-fixture/install", headers=self.hub)
        self.assertEqual(installed.status_code, 200, installed.text)
        view = self.open_surface(app="chat-fixture")
        response = self.surface(view["id"])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("http connection", response.json()["detail"])

    def test_an_uninstalled_app_is_a_clear_error(self):
        view = self.open_surface()
        self.client.delete("/api/apps/surface-fixture", headers=self.hub)
        response = self.surface(view["id"])
        self.assertEqual(response.status_code, 404, response.text)

    def test_a_document_over_the_limits_is_refused_whole(self):
        for source, detail in (
            ("/big", "more parts"),
            ("/deep", "nested deeper"),
            ("/huge", "too large"),
            ("/v2", "format"),
        ):
            with self.subTest(source=source):
                view = self.open_surface(source)
                response = self.surface(view["id"])
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn(detail, response.json()["detail"])

    def test_an_upstream_answer_that_is_not_a_document_is_a_502(self):
        view = self.open_surface("/missing")
        response = self.surface(view["id"])
        self.assertEqual(response.status_code, 502, response.text)

    def test_a_connection_failure_is_a_clear_error(self):
        def timeout(request):
            raise httpx.ReadTimeout("fixture deadline", request=request)

        self.transport.handler = timeout
        view = self.open_surface()
        response = self.surface(view["id"])
        self.assertEqual(response.status_code, 504, response.text)


if __name__ == "__main__":
    unittest.main()
