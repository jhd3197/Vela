"""The typed error base, its subclasses and the one handler that renders them.

Run with: python -m unittest discover -s tests. Uses disposable data only.
"""
import atexit
import os
import tempfile
import unittest
from pathlib import Path

# api.py constructs its default ASGI app on import. Keep that off the user's data.
_bootstrap = tempfile.TemporaryDirectory(prefix="vela-test-errors-http-")
atexit.register(_bootstrap.cleanup)
os.environ["VELA_DATA_DIR"] = _bootstrap.name

from fastapi import FastAPI
from fastapi.testclient import TestClient

from vela.api import create_app
from vela.app_storage import AppServiceError
from vela.config import Config
from vela.desk import DeskError
from vela.desktops.models import DesktopConflict, DesktopError
from vela.errors_http import (SUBCLASSES, AuthRequired, Conflict, Forbidden,
                              InvalidRequest, Locked, NotAllowed, NotFound,
                              Precondition, TooEarly, TooLarge, Unavailable,
                              Unprocessable, Upstream, VelaError)
from vela.files import FileError
from vela.lifecycle import LifecycleError
from vela.logs import LogError
from vela.manifest import ManifestError
from vela.notify import NotifyConfigError, NotifyError
from vela.updates import UpdateError
from vela.wallpaper import WallpaperError
from vela.weather import WeatherError

ROOT = Path(__file__).resolve().parent.parent

EXPECTED_STATUS = {
    InvalidRequest: 400,
    AuthRequired: 401,
    Forbidden: 403,
    NotFound: 404,
    NotAllowed: 405,
    Conflict: 409,
    TooLarge: 413,
    Unprocessable: 422,
    Locked: 423,
    TooEarly: 425,
    Precondition: 428,
    Upstream: 502,
    Unavailable: 503,
}


class ErrorBaseTests(unittest.TestCase):
    def test_every_subclass_carries_its_status_and_a_dotted_code(self):
        self.assertEqual(set(SUBCLASSES), set(EXPECTED_STATUS))
        for cls, status in EXPECTED_STATUS.items():
            with self.subTest(cls=cls.__name__):
                error = cls()
                self.assertEqual(error.status, status)
                self.assertEqual(error.code, cls.code)
                self.assertRegex(error.code, r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
                self.assertTrue(error.detail)

    def test_body_is_detail_code_and_status(self):
        error = NotFound("No such error", code="errors.unknown")
        self.assertEqual(
            error.to_body(),
            {"detail": "No such error", "code": "errors.unknown", "status": 404},
        )

    def test_details_ride_along_only_when_given(self):
        self.assertNotIn("details", Conflict("nope").to_body())
        body = Conflict("nope", details={"revision": 4}).to_body()
        self.assertEqual(body["details"], {"revision": 4})

    def test_str_is_the_detail_so_existing_callers_keep_working(self):
        self.assertEqual(str(Unprocessable("that name is too long")), "that name is too long")

    def test_a_raise_site_may_override_the_status(self):
        error = VelaError("held", code="thing.held", status=423)
        self.assertEqual(error.status, 423)
        self.assertEqual(error.to_body()["status"], 423)

    def test_base_answers_500_by_default(self):
        self.assertEqual(VelaError("boom").status, 500)


class CompatibilityTests(unittest.TestCase):
    """The bridges that let a migration land without touching every caller."""

    def test_app_service_error_keeps_its_positional_constructor(self):
        error = AppServiceError(409, "Installation directory already exists")
        self.assertEqual(error.status, 409)
        self.assertEqual(error.detail, "Installation directory already exists")
        self.assertEqual(str(error), "Installation directory already exists")
        self.assertIsInstance(error, VelaError)

    def test_lifecycle_error_keeps_status_code(self):
        error = LifecycleError(400, "Only a bundled static v1-to-v2 migration is supported here")
        self.assertEqual(error.status_code, 400)
        self.assertEqual(error.status, 400)
        self.assertEqual(error.detail, "Only a bundled static v1-to-v2 migration is supported here")

    def test_manifest_error_is_still_a_value_error(self):
        try:
            raise ManifestError("hello: missing app.json")
        except ValueError as exc:
            self.assertEqual(str(exc), "hello: missing app.json")
            self.assertEqual(exc.status, 422)
            self.assertEqual(exc.code, "manifest.invalid")
        else:  # pragma: no cover - the raise above always raises
            self.fail("ManifestError was not caught as a ValueError")

    def test_desk_error_is_still_a_value_error(self):
        with self.assertRaises(ValueError):
            raise DeskError("widgets must be a list")
        self.assertEqual(DeskError("widgets must be a list").status, 422)

    def test_not_found_is_still_a_lookup_error(self):
        with self.assertRaises(LookupError):
            raise LogError("unknown log")

    def test_forbidden_is_still_a_permission_error(self):
        with self.assertRaises(PermissionError):
            raise Forbidden("nope")

    def test_the_status_carrying_classes_kept_their_constructors(self):
        for cls, status in ((FileError, 404), (WallpaperError, 413),
                            (WeatherError, 502), (DesktopError, 409)):
            with self.subTest(cls=cls.__name__):
                error = cls(status, "a reason")
                self.assertEqual((error.status, error.detail), (status, "a reason"))

    def test_the_message_only_classes_carry_one_status_each(self):
        self.assertEqual(LogError("unknown log").status, 404)
        self.assertEqual(UpdateError("There is no newer version to install.").status, 409)
        self.assertEqual(NotifyConfigError("Set an ntfy server").status, 502)
        self.assertIsInstance(NotifyConfigError("x"), NotifyError)

    def test_desktop_conflict_carries_the_revision_on_its_header(self):
        error = DesktopConflict("Someone else saved first", 7)
        self.assertEqual(error.status, 409)
        self.assertEqual(error.revision, 7)
        self.assertEqual(error.headers, {"X-Vela-Desk-Revision": "7"})


class HandlerTests(unittest.TestCase):
    """One handler, on a throwaway app that raises each class from a route."""

    def setUp(self):
        app = FastAPI()

        # The same handler `create_app` registers, on a bare app, so each class
        # can be raised from a route of its own without 200 real ones nearby.
        @app.exception_handler(VelaError)
        async def render(request, exc: VelaError):
            from fastapi.responses import JSONResponse
            return JSONResponse(exc.to_body(), status_code=exc.status, headers=exc.headers)

        for cls in SUBCLASSES:
            def route(cls=cls):
                raise cls()
            app.get(f"/{cls.__name__}")(route)

        @app.get("/conflict")
        def desk_conflict():
            raise DesktopConflict("Someone else saved first", 12)

        self.client = TestClient(app)

    def test_each_subclass_renders_its_own_status_and_body(self):
        for cls, status in EXPECTED_STATUS.items():
            with self.subTest(cls=cls.__name__):
                response = self.client.get(f"/{cls.__name__}")
                self.assertEqual(response.status_code, status)
                body = response.json()
                self.assertEqual(body["status"], status)
                self.assertEqual(body["code"], cls.code)
                self.assertEqual(body["detail"], cls.default_detail)

    def test_a_header_carrying_error_keeps_its_header(self):
        response = self.client.get("/conflict")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.headers["X-Vela-Desk-Revision"], "12")


class ServedErrorTests(unittest.TestCase):
    """The real app: the bodies a caller actually sees."""

    def setUp(self):
        self.root = tempfile.TemporaryDirectory(prefix="vela-errors-http-")
        root = Path(self.root.name)
        self.config = Config(root / "data", root / "apps", ROOT / "web/dist")
        self.config.ensure_dirs()
        self.client = TestClient(create_app(self.config))
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}

    def tearDown(self):
        self.client.close()
        self.root.cleanup()

    def test_an_unknown_app_answers_the_same_detail_with_a_code(self):
        response = self.client.get("/api/apps/nope", headers=self.hub)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "unknown app: nope", "code": "apps.unknown", "status": 404},
        )

    def test_an_unknown_endpoint_answers_a_typed_body(self):
        response = self.client.get("/api/nothing-here", headers=self.hub)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"detail": "Unknown API endpoint", "code": "api.unknown_endpoint", "status": 404},
        )

    def test_a_missing_confirmation_header_answers_428_with_its_code(self):
        response = self.client.delete("/api/logs/server.log", headers=self.hub)
        self.assertEqual(response.status_code, 428)
        body = response.json()
        self.assertEqual(body["detail"], "Confirm clearing this log")
        self.assertEqual(body["code"], "logs.confirm_required")
        self.assertEqual(body["status"], 428)

    def test_an_app_service_error_still_renders_its_own_status(self):
        response = self.client.post("/api/security/unlock", headers=self.hub, json={})
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["detail"], "Send either the unlock code or the Vela password")
        self.assertEqual(body["status"], 422)
        self.assertEqual(body["code"], "app.error")

    def test_fastapi_validation_still_answers_its_own_shape(self):
        """Not a raise site. The dashboard type-checks `detail` and falls back."""
        response = self.client.post("/api/bots", headers=self.hub, json={"name": ""})
        self.assertEqual(response.status_code, 422)
        self.assertIsInstance(response.json()["detail"], list)


if __name__ == "__main__":
    unittest.main()
