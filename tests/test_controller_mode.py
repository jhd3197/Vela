"""Controller mode: the token-only Vela a single owning process drives.

Everything here uses a disposable data directory and a disposable controller
token file; no test reaches a real installation or the network.
"""
import atexit
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

# api.py constructs its default ASGI app on import. Keep that off the user's data.
_bootstrap = tempfile.TemporaryDirectory(prefix="vela-controller-test-")
import atexit
atexit.register(_bootstrap.cleanup)
os.environ["VELA_DATA_DIR"] = _bootstrap.name

from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from vela import __version__
from vela.api import create_app
from vela.config import CONTROLLER_PROTOCOL, load_config, load_controller_token
from vela.container import configure as configure_container

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "controller-token-" + "x" * 40


def write_token_file(directory, token=TOKEN):
    path = Path(directory) / "controller-token"
    path.write_text(token + "\n", encoding="utf-8")
    return path


def catalog_with_fixture(root):
    """The chat fixture with an icon declared, as a private catalog copy."""
    catalog = Path(root) / "catalog"
    shutil.copytree(ROOT / "tests/fixtures/chat-fixture", catalog / "chat-fixture")
    manifest_path = catalog / "chat-fixture" / "app.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["icon"] = "icon.svg"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return catalog


class ControllerEntryTests(unittest.TestCase):
    """`python -m vela.computer` refuses to start on a bad token setup."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="vela-computer-test-")
        self.addCleanup(self.directory.cleanup)
        self.token_file = write_token_file(self.directory.name)
        self.environment = patch.dict(os.environ, {
            "VELA_DATA_DIR": self.directory.name,
            "VELA_CONTROLLER_TOKEN_FILE": str(self.token_file),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_an_inline_env_var_token_is_refused(self):
        with patch.dict(os.environ, {"VELA_CONTROLLER_TOKEN": TOKEN}):
            with self.assertRaisesRegex(ValueError, "file named by VELA_CONTROLLER_TOKEN_FILE"):
                load_controller_token()

    def test_a_missing_token_file_is_refused(self):
        with patch.dict(os.environ, {"VELA_CONTROLLER_TOKEN_FILE": str(Path(self.directory.name) / "absent")}):
            with self.assertRaisesRegex(ValueError, "missing or cannot be read"):
                load_controller_token()

    def test_an_unreadable_token_file_is_refused(self):
        with patch.dict(os.environ, {"VELA_CONTROLLER_TOKEN_FILE": self.directory.name}):
            with self.assertRaisesRegex(ValueError, "missing or cannot be read"):
                load_controller_token()

    def test_a_short_token_is_refused(self):
        for token in ("", "short", "x" * 31):
            with self.subTest(token=token):
                path = write_token_file(self.directory.name, token)
                with patch.dict(os.environ, {"VELA_CONTROLLER_TOKEN_FILE": str(path)}):
                    with self.assertRaisesRegex(ValueError, "at least 32 characters"):
                        load_controller_token()

    def test_whitespace_and_control_characters_are_refused(self):
        for token in ("ab cd" + "x" * 40, "x" * 20 + "\t" + "x" * 20, "x" * 20 + "" + "x" * 20,
                      "x" * 20 + " " + "x" * 20):
            with self.subTest(token=repr(token)):
                path = write_token_file(self.directory.name, token)
                with patch.dict(os.environ, {"VELA_CONTROLLER_TOKEN_FILE": str(path)}):
                    with self.assertRaisesRegex(ValueError, "whitespace or control characters"):
                        load_controller_token()

    def test_the_trailing_line_break_a_redirect_adds_is_tolerated(self):
        self.assertEqual(load_controller_token(), TOKEN)

    def test_controller_mode_requires_a_token(self):
        from vela.config import Config
        root = Path(self.directory.name) / "no-token"
        config = Config(root / "data", root / "apps", ROOT / "web/dist", controller_mode=True)
        with self.assertRaisesRegex(ValueError, "requires a controller token"):
            create_app(config)

    def test_main_enables_controller_mode_and_binds_the_gateway_port(self):
        from vela import computer
        with patch.dict(os.environ, {"VELA_PORT": "7701"}), \
                patch.object(computer, "ensure_bundled_wallpapers"), \
                patch.object(computer.uvicorn, "run") as run:
            computer.main()
            self.assertEqual(os.environ["VELA_CONTROLLER_MODE"], "1")
        self.assertEqual(run.call_args.kwargs["host"], "0.0.0.0")
        self.assertEqual(run.call_args.kwargs["port"], 7701)
        self.assertFalse(run.call_args.kwargs["proxy_headers"])

    def test_main_refuses_with_a_clear_sentence(self):
        from vela import computer
        with patch.dict(os.environ, {"VELA_CONTROLLER_TOKEN_FILE": str(Path(self.directory.name) / "absent")}), \
                self.assertRaises(SystemExit) as caught:
            computer.main()
        self.assertIn("missing or cannot be read", str(caught.exception))


class ControllerModeTests(unittest.TestCase):
    """The auth boundary: one token, refused person routes, app sessions intact."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="vela-controller-mode-")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.catalog = catalog_with_fixture(root)
        self.token_file = write_token_file(root)
        self.environment = patch.dict(os.environ, {
            "VELA_DATA_DIR": str(root / "data"),
            "VELA_APPS_DIR": str(self.catalog),
            "VELA_CONTROLLER_MODE": "1",
            "VELA_CONTROLLER_TOKEN_FILE": str(self.token_file),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.config = load_config()
        self.app = create_app(self.config)
        self.auth = self.app.state.phone_access.auth
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": f"Bearer {TOKEN}"}

    def install_fixture(self):
        self.assertEqual(self.client.post("/api/apps/chat-fixture/install", headers=self.headers).status_code, 200)
        opened = self.client.post("/api/apps/chat-fixture/session", headers=self.headers)
        self.assertEqual(opened.status_code, 200, opened.text)
        return {"Authorization": f"Bearer {opened.json()['token']}"}

    def test_health_stays_open_and_minimal(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "version": __version__})

    def test_protected_routes_require_the_controller_token(self):
        self.assertEqual(self.client.get("/api/apps").status_code, 401)
        wrong = {"Authorization": f"Bearer {'y' * 48}"}
        self.assertEqual(self.client.get("/api/apps", headers=wrong).status_code, 401)
        self.assertEqual(self.client.get("/api/apps", headers=self.headers).status_code, 200)

    def test_repeated_bad_tokens_are_throttled_like_login(self):
        wrong = {"Authorization": f"Bearer {'y' * 48}"}
        for _ in range(5):
            self.assertEqual(self.client.get("/api/apps", headers=wrong).status_code, 401)
        self.assertEqual(self.client.get("/api/apps", headers=wrong).status_code, 429)
        # The right token still works, and a new run of bad ones starts counting fresh.
        self.assertEqual(self.client.get("/api/apps", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get("/api/apps", headers=wrong).status_code, 401)

    def test_discovery_reports_the_mode_version_protocol_and_features(self):
        self.assertEqual(self.client.get("/api/controller").status_code, 401)
        response = self.client.get("/api/controller", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["mode"], "controller")
        self.assertEqual(body["version"], __version__)
        self.assertEqual(body["protocol"], CONTROLLER_PROTOCOL)
        self.assertEqual(body["features"]["controller"], True)
        self.assertEqual(body["features"]["driver"], True)
        # The terminal needs a POSIX pty: advertised where one exists, absent
        # where it does not (the windows-latest CI leg included).
        if os.name == "posix":
            self.assertEqual(body["features"]["terminal"], True)
        else:
            self.assertNotIn("terminal", body["features"])

    def test_person_sign_in_lock_phone_and_pairing_routes_are_refused(self):
        refused = [
            ("GET", "/api/session"), ("POST", "/api/login"), ("POST", "/api/logout"),
            ("GET", "/api/security"), ("POST", "/api/security/enroll"),
            ("PATCH", "/api/security"), ("DELETE", "/api/security"),
            ("POST", "/api/security/lock"), ("POST", "/api/security/unlock"),
            ("POST", "/api/security/activity"),
            ("GET", "/api/phone-access"), ("POST", "/api/phone-access"), ("DELETE", "/api/phone-access"),
            ("GET", "/api/devices"), ("POST", "/api/devices/pairing"), ("POST", "/api/devices/pair"),
            ("POST", "/api/devices/session"), ("POST", "/api/devices/requests"),
            ("POST", "/api/devices/requests/lookup"), ("POST", "/api/devices/claim"),
        ]
        for method, path in refused:
            with self.subTest(method=method, path=path):
                response = self.client.request(method, path, headers=self.headers)
                self.assertEqual(response.status_code, 403, response.text)
                self.assertTrue(response.json()["detail"])

    def test_the_loopback_bootstrap_token_is_not_an_owner(self):
        # The container's own loopback is not the owner: the random in-memory
        # bootstrap token a local Vela hands its own dashboard must do nothing here.
        local = {"Authorization": f"Bearer {self.auth.hub_token}", "X-Vela-Bootstrap": "1"}
        self.assertEqual(self.client.get("/api/session", headers=local).status_code, 403)
        self.assertEqual(self.client.get("/api/apps", headers=local).status_code, 401)

    def test_app_sessions_keep_their_own_scoped_tokens(self):
        app_headers = self.install_fixture()
        # An app window answers with its own session token and no controller token.
        self.assertEqual(self.client.get("/api/app/storage", headers=app_headers).status_code, 200)
        # The controller token is a hub credential, not an app session.
        self.assertEqual(self.client.get("/api/app/storage", headers=self.headers).status_code, 401)
        # App icons stay public, as in every other mode.
        self.assertEqual(self.client.get("/api/apps/chat-fixture/icon").status_code, 200)

    def test_webhooks_keep_their_own_per_workflow_secret(self):
        response = self.client.post("/api/automations/hooks/unknown-workflow",
                                    headers={"X-Vela-Automation-Secret": "whatever"})
        self.assertEqual(response.status_code, 404)
        self.assertNotEqual(response.json()["detail"], "Controller authentication required")

    def test_updates_report_the_controller_kind_and_refuse_to_install(self):
        status = self.client.get("/api/updates", headers=self.headers)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["capability"], "controller")
        # A newer version cached: apply still refuses, because replacing this
        # Vela is the controller's job.
        cache = self.config.data_dir / "updates" / "latest.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({
            "checkedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "latest": "999.0.0", "notes": "", "asset": None, "checksum": None, "error": None,
        }), encoding="utf-8")
        applied = self.client.post("/api/updates/apply",
                                   headers={**self.headers, "X-Vela-Confirm": "update"})
        self.assertEqual(applied.status_code, 409)
        self.assertIn("updates a different way", applied.json()["detail"])


class ControllerDashboardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="vela-controller-page-")
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.catalog = catalog_with_fixture(root)
        self.token_file = write_token_file(root)
        self.environment = patch.dict(os.environ, {
            "VELA_DATA_DIR": str(root / "data"),
            "VELA_APPS_DIR": str(self.catalog),
            "VELA_CONTROLLER_MODE": "1",
            "VELA_CONTROLLER_TOKEN_FILE": str(self.token_file),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(create_app(load_config()))
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": f"Bearer {TOKEN}"}

    def test_pages_answer_a_plain_explanation_not_the_dashboard(self):
        for path in ("/", "/settings", "/deep/link"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/plain", response.headers["content-type"])
                self.assertIn("run by a controller", response.text)
        # The dashboard bootstrap must not leak through any page path.
        self.assertNotIn("text/html", self.client.get("/").headers["content-type"])

    def test_app_windows_still_serve_for_agent_desktops(self):
        self.client.post("/api/apps/chat-fixture/install", headers=self.headers)
        response = self.client.get("/apps/chat-fixture/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("sandbox allow-scripts", response.headers["content-security-policy"])


class ModeRegressionTests(unittest.TestCase):
    """Local and remote modes answer exactly as they did before controller mode."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="vela-mode-regression-")
        self.addCleanup(self.directory.cleanup)

    def local_client(self):
        environment = patch.dict(os.environ, {"VELA_DATA_DIR": self.directory.name})
        environment.start()
        self.addCleanup(environment.stop)
        client = TestClient(create_app(load_config()))
        self.addCleanup(client.close)
        return client

    def test_local_mode_keeps_its_bootstrap_and_session_flow(self):
        client = self.local_client()
        bootstrapped = client.get("/api/session", headers={"X-Vela-Bootstrap": "1"})
        self.assertEqual(bootstrapped.status_code, 200)
        token = bootstrapped.json()["token"]
        self.assertEqual(bootstrapped.json()["remote"], False)
        self.assertEqual(client.get("/api/apps").status_code, 401)
        self.assertEqual(client.get("/api/apps", headers={"Authorization": f"Bearer {token}"}).status_code, 200)
        # Discovery exists but says this is not a controller Vela.
        self.assertEqual(client.get("/api/controller").status_code, 401)
        refused = client.get("/api/controller", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(refused.status_code, 403)
        self.assertIn("not running in controller mode", refused.json()["detail"])
        # The dashboard is the ordinary one, not the controller explanation.
        self.assertNotIn("run by a controller", client.get("/").text)

    def test_remote_mode_keeps_password_login_and_refuses_discovery(self):
        environment = patch.dict(os.environ, {
            "VELA_DATA_DIR": self.directory.name,
            "VELA_PUBLIC_ORIGIN": "https://vela.example.com",
            "VELA_TRUSTED_PROXIES": "172.18.0.1",
            "VELA_INITIAL_PASSWORD": "disposable-test-password",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.assertEqual(configure_container(), "172.18.0.1")
        app = ProxyHeadersMiddleware(create_app(load_config()), trusted_hosts=["172.18.0.1"])
        headers = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "192.0.2.12",
                   "X-Vela-Bootstrap": "1", "Origin": "https://vela.example.com"}
        with TestClient(app, base_url="https://vela.example.com", client=("172.18.0.1", 1234)) as client:
            login = client.post("/api/login", headers=headers,
                                json={"password": "disposable-test-password"})
            self.assertEqual(login.status_code, 200)
            token = login.json()["token"]
            authorized = {**headers, "Authorization": f"Bearer {token}"}
            self.assertEqual(client.get("/api/apps", headers=authorized).status_code, 200)
            discovered = client.get("/api/controller", headers=authorized)
            self.assertEqual(discovered.status_code, 403)
            self.assertIn("not running in controller mode", discovered.json()["detail"])


if __name__ == "__main__":
    unittest.main()
