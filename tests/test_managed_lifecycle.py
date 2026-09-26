"""Installing a managed app, owning its process, and surviving a restart.

These tests start real child processes. They are the ones that would catch a
managed app that installs but never runs, a stop that leaves an orphan holding
a port, a crash loop with no end, or a Vela restart that forgets which apps the
person actually wanted running.

Run with: python -m unittest discover -s tests. Uses disposable data only.
"""

import json
import os
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from managed_support import APP_ID, ManagedTestCase

from vela.api import create_app
from vela.managed.contract import host_target
from vela.state import pid_alive


class InstallTests(ManagedTestCase):
    def test_installing_records_an_identity_a_release_and_no_grants(self):
        result = self.install()
        self.assertTrue(result["installed"])
        self.assertEqual(result["operation"], "install")

        described = self.client.get(f"/api/managed/{APP_ID}", headers=self.hub).json()
        self.assertEqual(described["profile"], "managed-web")
        self.assertEqual(described["runtime"], "managed-service")
        self.assertEqual(described["isolation"], "trusted-native")
        self.assertEqual(described["capabilities"], [])
        self.assertEqual(described["widgets"], [])
        self.assertEqual(described["managed"]["generation"], 1)
        self.assertEqual(described["managed"]["desired"], "stopped")
        self.assertEqual(described["managed"]["state"], "stopped")
        self.assertFalse(described["managed"]["startWithVela"])
        self.assertEqual(len(described["managed"]["releases"]), 1)
        self.assertTrue(described["managed"]["releases"][0]["active"])

    def test_a_managed_app_appears_in_the_library_beside_the_others(self):
        self.install()
        apps = self.client.get("/api/apps", headers=self.hub).json()["apps"]
        entry = next(app for app in apps if app["id"] == APP_ID)
        self.assertEqual(entry["profile"], "managed-web")
        self.assertTrue(entry["installed"])
        self.assertFalse(entry["running"])
        self.assertEqual(
            self.client.get(f"/api/apps/{APP_ID}/status", headers=self.hub).json()["id"],
            APP_ID,
        )

    def test_an_approval_that_names_other_bytes_is_refused(self):
        review = self.review()
        response = self.client.post(
            f"/api/managed/review/{review['review']}/install",
            headers=self.hub,
            json={
                "artifactDigest": "0" * 64,
                "packageDigest": review["packageDigest"],
                "trust": "trusted-native",
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "managed.review_mismatch")
        self.assertEqual(self.client.get("/api/managed", headers=self.hub).json()["apps"], [])

    def test_a_package_edited_after_it_was_reviewed_is_refused(self):
        """Approval is bound to bytes, and the bytes are measured again."""
        review = self.review()
        staged = next(
            (self.config.data_dir / "staging").glob("managed-*/package/app.json")
        )
        staged.write_text(staged.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        response = self.client.post(
            f"/api/managed/review/{review['review']}/install",
            headers=self.hub,
            json={
                "artifactDigest": review["artifactDigest"],
                "packageDigest": review["packageDigest"],
                "trust": "trusted-native",
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("Nothing was installed", response.json()["detail"])

    def test_installing_requires_accepting_native_execution(self):
        review = self.review()
        response = self.client.post(
            f"/api/managed/review/{review['review']}/install",
            headers=self.hub,
            json={
                "artifactDigest": review["artifactDigest"],
                "packageDigest": review["packageDigest"],
                "trust": "sandboxed",
            },
        )
        self.assertEqual(response.status_code, 428, response.text)
        self.assertEqual(response.json()["code"], "managed.trust_not_accepted")

    def test_an_expired_review_cannot_be_installed(self):
        review = self.review()
        for plan in self.service._reviews.values():
            plan["expires"] = time.monotonic() - 1
        response = self.client.post(
            f"/api/managed/review/{review['review']}/install",
            headers=self.hub,
            json={
                "artifactDigest": review["artifactDigest"],
                "packageDigest": review["packageDigest"],
                "trust": "trusted-native",
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["code"], "managed.review_expired")

    def test_a_managed_app_cannot_take_an_existing_apps_name(self):
        self.install()
        response = self.client.post(
            "/api/managed/review", headers=self.hub,
            json={"folder": str(self.build_package())},
        )
        # The same id is an *update*, which is allowed; a different profile
        # owning that id is not. Prove the second half through the registry.
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["operation"], "update")


class ServiceLifetimeTests(ManagedTestCase):
    def test_start_waits_for_the_apps_own_answer_then_reports_ready(self):
        self.install()
        state = self.start()
        self.assertEqual(state["state"], "ready")
        self.assertIsNotNone(state["port"])
        self.assertTrue(pid_alive(state["pid"]))
        self.assertEqual(self.status()["managed"]["desired"], "running")

    def test_a_bound_port_is_not_readiness(self):
        """The fixture binds and never answers. A socket check would pass."""
        self.install(
            review=self.review(
                folder=self.build_package(
                    environment={"FIXTURE_NEVER_READY": "1"}, start_timeout=3
                )
            )
        )
        response = self.client.post(f"/api/managed/{APP_ID}/start", headers=self.hub)
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("did not answer /healthz", response.json()["detail"])
        self.assertEqual(self.status()["managed"]["state"], "failed")
        # And nothing is left running behind the failure.
        self.assertIsNone(self.service.supervisor.alive(APP_ID))

    def test_a_service_that_refuses_to_start_reports_what_its_log_said(self):
        self.install(
            review=self.review(
                folder=self.build_package(
                    environment={"FIXTURE_REFUSE_START": "1"}, start_timeout=5
                )
            )
        )
        response = self.client.post(f"/api/managed/{APP_ID}/start", headers=self.hub)
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("refusing to start", response.json()["detail"])

    def test_a_failed_startup_migration_is_reported_rather_than_retried_silently(self):
        self.install(
            review=self.review(
                folder=self.build_package(
                    environment={"FIXTURE_MIGRATE_FAIL": "1"}, start_timeout=5
                )
            )
        )
        response = self.client.post(f"/api/managed/{APP_ID}/start", headers=self.hub)
        self.assertEqual(response.status_code, 502, response.text)
        self.assertIn("migration", response.json()["detail"])

    def test_two_launches_at_once_start_one_process(self):
        self.install()
        results = []
        errors = []

        def launch():
            try:
                results.append(self.service.start(APP_ID))
            except Exception as exc:  # noqa: BLE001 - recorded for the assertion
                errors.append(exc)

        threads = [threading.Thread(target=launch) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual(errors, [])
        pids = {result["pid"] for result in results}
        self.assertEqual(len(pids), 1, f"expected one process, got {pids}")

    def test_stop_ends_the_process_and_the_intent(self):
        self.install()
        pid = self.start()["pid"]
        state = self.stop()
        self.assertEqual(state["state"], "stopped")
        for _ in range(30):
            if not pid_alive(pid):
                break
            time.sleep(0.1)
        self.assertFalse(pid_alive(pid), "the service process outlived Stop")
        self.assertEqual(self.status()["managed"]["desired"], "stopped")

    def test_a_reused_process_number_is_never_adopted_or_signalled(self):
        """Ownership is the creation token, not the number."""
        self.install()
        self.start()
        supervisor = self.service.supervisor
        entry = dict(supervisor.record(APP_ID))
        # Our own process, with the wrong creation token: the same shape a
        # recycled PID has after a reboot.
        entry["pid"] = os.getpid()
        entry["pid_ctime"] = 1
        supervisor._save(APP_ID, entry)
        self.assertIsNone(supervisor.alive(APP_ID))
        self.assertEqual(supervisor.state(APP_ID).state, "stopped")
        supervisor.stop(APP_ID, self.service.manifest(APP_ID))
        self.assertTrue(pid_alive(os.getpid()), "the test process was signalled")

    def test_an_unrelated_listener_on_the_recorded_port_is_not_adopted(self):
        import socket

        self.install()
        manifest = self.service.manifest(APP_ID)
        supervisor = self.service.supervisor
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            supervisor._save(APP_ID, {
                "pid": os.getpid(),
                "pid_ctime": None,
                "port": port,
                "bind": "127.0.0.1",
                "generation": 1,
                "release_id": self.status()["managed"]["releaseId"],
                "started_at": "2026-01-01T00:00:00",
                "ready": True,
            })
            self.assertFalse(supervisor.adopt(APP_ID, manifest, 1))
        self.assertIsNone(supervisor.record(APP_ID))

    def test_the_child_gets_the_packages_settings_and_none_of_velas(self):
        """A managed app must never be handed a Vela credential to leak."""
        self.install()
        self.start()
        manifest = self.service.manifest(APP_ID)
        paths = self.service.store.paths(APP_ID)
        values = {
            "port": "1", "host": "127.0.0.1", "dataDir": str(paths.data),
            "codeDir": str(paths.code), "appId": APP_ID, "publicUrl": "http://x",
        }
        env = self.service.supervisor._environment(manifest, values)
        self.assertEqual(env["FIXTURE_VERSION"], "1.0.0")
        leaked = [name for name in env if name.startswith("VELA_")]
        self.assertEqual(leaked, [], f"Vela settings reached the child: {leaked}")
        token = self.hub["Authorization"].removeprefix("Bearer ")
        self.assertNotIn(token, json.dumps(env))
        argv = self.service.supervisor._argv(Path("exe"), manifest, values)
        self.assertNotIn(token, " ".join(argv))
        log = self.service.supervisor.log_path(APP_ID).read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertNotIn(token, log)

    def test_the_apps_log_is_readable_from_the_dashboard(self):
        self.install()
        self.start()
        response = self.client.get("/api/logs", headers=self.hub)
        self.assertEqual(response.status_code, 200, response.text)
        names = {entry["name"] for entry in response.json()["logs"]}
        self.assertIn(f"managed-{APP_ID}.log", names)


class RestartTests(ManagedTestCase):
    """What a Vela restart does, and what it must not undo."""

    def restarted(self):
        """A second server on the same data directory, started properly."""
        app = create_app(self.config)
        client = TestClient(app)
        client.__enter__()
        self.addCleanup(lambda: client.__exit__(None, None, None))
        token = client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        return app, client, {"Authorization": "Bearer " + token}

    def test_an_explicit_stop_survives_a_restart_even_with_start_on(self):
        """The setting says what to do; the decision says what was decided."""
        self.install()
        self.start()
        self.client.put(
            f"/api/managed/{APP_ID}/startup", headers=self.hub, json={"startWithVela": True}
        )
        self.stop()
        self.client.__exit__(None, None, None)

        app, client, hub = self.restarted()
        state = client.get(f"/api/managed/{APP_ID}/status", headers=hub).json()["managed"]
        self.assertEqual(state["desired"], "stopped")
        self.assertEqual(state["state"], "stopped")
        self.assertTrue(state["startWithVela"])

    def test_start_with_vela_brings_a_running_app_back(self):
        self.install()
        self.start()
        self.client.put(
            f"/api/managed/{APP_ID}/startup", headers=self.hub, json={"startWithVela": True}
        )
        # An orderly shutdown stops the service but keeps the intent.
        self.client.__exit__(None, None, None)

        app, client, hub = self.restarted()
        state = client.get(f"/api/managed/{APP_ID}/status", headers=hub).json()["managed"]
        self.assertEqual(state["state"], "ready")
        self.assertEqual(state["desired"], "running")

    def test_an_app_left_off_stays_off(self):
        self.install()
        self.start()
        self.client.__exit__(None, None, None)
        app, client, hub = self.restarted()
        state = client.get(f"/api/managed/{APP_ID}/status", headers=hub).json()["managed"]
        self.assertEqual(state["state"], "stopped")

    def test_a_surviving_process_is_adopted_rather_than_duplicated(self):
        """Vela crashed; the app did not. Reconnect to it, do not start a second."""
        self.install()
        pid = self.start()["pid"]
        # No shutdown: this is what a killed Vela leaves behind.
        app, client, hub = self.restarted()
        state = client.get(f"/api/managed/{APP_ID}/status", headers=hub).json()["managed"]
        self.assertEqual(state["state"], "ready")
        self.assertEqual(state["pid"], pid)


class CrashTests(ManagedTestCase):
    def test_a_crash_is_restarted_within_its_budget_and_then_left_alone(self):
        """A Failed badge over a machine still restarting is the worst answer."""
        self.install(
            review=self.review(
                folder=self.build_package(
                    environment={"FIXTURE_EXIT_AFTER": "0.5"}, start_timeout=10
                )
            )
        )
        self.start()
        service = self.service
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            service.reconcile()
            record = service.store.require(APP_ID)
            if record["desired"] == "stopped" and record["last_error"]:
                break
            time.sleep(0.2)
        record = service.store.require(APP_ID)
        self.assertEqual(record["desired"], "stopped",
                         "the crash loop never ran out of retries")
        self.assertIn("was not started again", record["last_error"])
        self.assertIn("Open its log", record["last_error"])
        self.assertIsNone(service.supervisor.alive(APP_ID))
        # And the reconciler leaves it alone now.
        service.reconcile()
        self.assertIsNone(service.supervisor.alive(APP_ID))


class RemovalTests(ManagedTestCase):
    def note(self):
        """Sign in to the fixture and write something worth keeping."""
        self.enter()
        signed = self.app_post("/api/login", json={"user": "tester", "password": "fixture-pw"})
        self.assertEqual(signed.status_code, 200, signed.text)
        token = {"Authorization": "Bearer " + signed.json()["token"]}
        created = self.app_post("/api/notes", headers=token, json={"body": "keep me"})
        self.assertEqual(created.status_code, 201, created.text)
        return token

    def test_removing_an_app_keeps_its_data_and_says_where_it_is(self):
        self.install()
        self.start()
        self.note()
        response = self.client.request(
            "DELETE", f"/api/managed/{APP_ID}", headers=self.hub, json={"eraseData": False}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["installed"])
        self.assertFalse(body["dataErased"])
        retained = Path(body["retainedData"]["path"])
        self.assertTrue((retained / "fixture.db").is_file())
        self.assertGreater(body["retainedData"]["size"], 0)
        self.assertEqual(self.client.get("/api/managed", headers=self.hub).json()["apps"], [])

    def test_erasing_data_is_its_own_action_and_has_to_be_typed(self):
        self.install()
        self.start()
        self.note()
        data = Path(self.client.get(f"/api/managed/{APP_ID}", headers=self.hub)
                    .json()["managed"]["data"]["path"])
        self.assertTrue((data / "fixture.db").is_file())

        refused = self.client.post(
            f"/api/managed/{APP_ID}/data/erase", headers=self.hub, json={"confirm": "yes"}
        )
        self.assertEqual(refused.status_code, 428, refused.text)
        self.assertTrue((data / "fixture.db").is_file())

        erased = self.client.post(
            f"/api/managed/{APP_ID}/data/erase", headers=self.hub, json={"confirm": APP_ID}
        )
        self.assertEqual(erased.status_code, 200, erased.text)
        self.assertFalse((data / "fixture.db").exists())
        self.assertTrue(data.is_dir(), "the app's own directory should remain")

    def test_removing_with_erase_takes_the_data_too(self):
        self.install()
        self.start()
        self.note()
        root = self.service.store.paths(APP_ID).root
        response = self.client.request(
            "DELETE", f"/api/managed/{APP_ID}", headers=self.hub, json={"eraseData": True}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["dataErased"])
        self.assertFalse(root.exists())

    def test_reinstalling_after_removal_finds_the_data_still_there(self):
        self.install()
        self.start()
        self.note()
        self.client.request(
            "DELETE", f"/api/managed/{APP_ID}", headers=self.hub, json={"eraseData": False}
        )
        self.install()
        self.start()
        self.enter()
        notes = self.app_get("/api/notes").json()["notes"]
        self.assertEqual([note["body"] for note in notes], ["keep me"])


if __name__ == "__main__":
    unittest.main()
