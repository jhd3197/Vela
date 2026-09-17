"""Watching a desktop, and taking it over.

The rule everything here is about: **one writer**. Not usually one, not one
unless two requests arrive together — one, decided by a lock. Two things typing
into the same window is a failure you do not notice until something has been
typed into the wrong field, so most of these tests are about the second request
losing.

The other rule is that giving control back is not the same as resuming. A person
may release because they are finished or because they are leaving, and an agent
that started typing again because a connection dropped would be an agent doing
something nobody asked for.

The lease, the coordinate check and the frame-age check are plain Python and are
tested directly. The end-to-end half needs a real browser, and skips with a
reason without one.
"""

import asyncio
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import test_app_contract as base
import uvicorn
from scripts.fixture_apps import APPS as FIXTURE_APPS
from vela.agent_runs.control import (
    LEASE_SECONDS,
    MAX_FRAME_AGE_SECONDS,
    Leases,
    check_frame_age,
    check_point,
)
from vela.api import create_app
from vela.app_storage import AppServiceError
from vela.config import Config
from vela.desktops.runtime import availability

ROOT = base.ROOT
STATE = availability()


class LeaseTests(unittest.TestCase):
    """One writer, and everything that tries to be a second one."""

    def setUp(self):
        self.leases = Leases()

    def test_nobody_holds_control_until_somebody_takes_it(self):
        self.assertIsNone(self.leases.holder("d1"))

    def test_the_second_request_loses(self):
        first = self.leases.take("d1", view_id="v1", epoch=1)
        with self.assertRaises(AppServiceError) as caught:
            self.leases.take("d1", view_id="v1", epoch=1)
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("Somebody else has control", caught.exception.detail)
        self.assertEqual(self.leases.holder("d1")["leaseId"], first.id)

    def test_simultaneous_requests_produce_exactly_one_writer(self):
        """The lock, under the only conditions that test it."""
        won, lost = [], []
        start = threading.Barrier(8)

        def race():
            start.wait()
            try:
                won.append(self.leases.take("d1", view_id="v1", epoch=1).id)
            except AppServiceError:
                lost.append(True)

        threads = [threading.Thread(target=race) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(won), 1, f"one writer, not {len(won)}")
        self.assertEqual(len(lost), 7)

    def test_two_desktops_have_their_own_writer(self):
        self.leases.take("d1", view_id="v1", epoch=1)
        other = self.leases.take("d2", view_id="v2", epoch=1)
        self.assertEqual(self.leases.holder("d2")["leaseId"], other.id)

    def test_input_with_a_lease_that_is_not_the_current_one_is_refused(self):
        lease = self.leases.take("d1", view_id="v1", epoch=1)
        self.leases.release("d1", lease.id)
        self.leases.take("d1", view_id="v1", epoch=2)
        with self.assertRaises(AppServiceError) as caught:
            self.leases.require("d1", lease.id)
        self.assertIn("no longer have control", caught.exception.detail)

    def test_a_lease_that_nobody_used_times_out(self):
        lease = self.leases.take("d1", view_id="v1", epoch=1)
        lease.touched_at = time.time() - LEASE_SECONDS - 1
        self.assertIsNone(self.leases.holder("d1"), "a closed laptop gives control back")
        # And somebody else may then take it.
        self.assertTrue(self.leases.take("d1", view_id="v1", epoch=2))

    def test_using_a_lease_keeps_it_alive(self):
        lease = self.leases.take("d1", view_id="v1", epoch=1)
        lease.touched_at = time.time() - LEASE_SECONDS + 5
        self.leases.require("d1", lease.id)
        self.assertGreater(self.leases.holder("d1")["expiresAt"], time.time() + LEASE_SECONDS - 5)

    def test_control_can_be_dropped_whoever_held_it(self):
        self.leases.take("d1", view_id="v1", epoch=1)
        self.assertTrue(self.leases.drop("d1"))
        self.assertIsNone(self.leases.holder("d1"))

    def test_an_observer_can_always_see_who_is_typing(self):
        lease = self.leases.take("d1", view_id="v1", epoch=3)
        holder = self.leases.holder("d1")
        self.assertEqual(holder["controlEpoch"], 3)
        self.assertEqual(holder["viewId"], "v1")
        self.assertEqual(holder["leaseId"], lease.id)


class PointTests(unittest.TestCase):
    """A click is in the view the picture it came from was of."""

    VIEWPORT = {"width": 1280, "height": 800}

    def test_a_point_inside_the_view_is_accepted_in_css_pixels(self):
        self.assertEqual(check_point({"x": 10, "y": 20}, self.VIEWPORT), {"x": 10.0, "y": 20.0})

    def test_a_point_outside_the_view_is_refused(self):
        for point in ({"x": 1281, "y": 10}, {"x": 10, "y": 801}, {"x": -1, "y": 0}):
            with self.assertRaises(AppServiceError) as caught:
                check_point(point, self.VIEWPORT)
            self.assertEqual(caught.exception.status, 422)

    def test_a_point_that_is_not_a_point_is_refused(self):
        for point in (None, "middle", {"x": "left", "y": 0}, {}):
            with self.assertRaises(AppServiceError):
                check_point(point, self.VIEWPORT)

    def test_a_view_with_no_known_size_takes_no_input(self):
        with self.assertRaises(AppServiceError) as caught:
            check_point({"x": 1, "y": 1}, {"width": 0, "height": 0})
        self.assertEqual(caught.exception.status, 409)

    def test_input_decided_from_an_old_picture_is_refused(self):
        check_frame_age(time.time() * 1000)
        with self.assertRaises(AppServiceError) as caught:
            check_frame_age((time.time() - MAX_FRAME_AGE_SECONDS - 1) * 1000)
        self.assertIn("seconds old", caught.exception.detail)

    def test_input_that_names_no_picture_is_refused(self):
        with self.assertRaises(AppServiceError):
            check_frame_age(None)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(STATE["available"], STATE["detail"] or "the browser runtime is not installed")
class TakeoverTests(unittest.TestCase):
    """A real server, a real browser, and control changing hands."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="vela-viewer-")
        root = Path(cls.temp.name)
        cls.apps = root / "catalog"
        cls.apps.mkdir()
        shutil.copytree(FIXTURE_APPS / "notes", cls.apps / "notes")
        cls.port = free_port()
        import os

        os.environ["VELA_PORT"] = str(cls.port)
        cls.config = Config(root / "data", cls.apps, ROOT / "web/dist")
        cls.config.ensure_dirs()
        cls.app = create_app(cls.config)
        cls.server = uvicorn.Server(
            uvicorn.Config(cls.app, host="127.0.0.1", port=cls.port, log_level="warning")
        )
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"
        for _ in range(300):
            try:
                import httpx

                if httpx.get(f"{cls.base}/api/health", timeout=1).status_code == 200:
                    if getattr(cls.app.state, "loop", None):
                        break
            except Exception:  # noqa: BLE001 - it is simply not up yet
                pass
            time.sleep(0.05)
        else:
            raise RuntimeError("the fixture server did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=20)
        cls.temp.cleanup()

    def setUp(self):
        import httpx

        self.client = httpx.Client(base_url=self.base, timeout=60)
        token = self.client.get("/api/session", headers={"X-Vela-Bootstrap": "1"}).json()["token"]
        self.hub = {"Authorization": "Bearer " + token}
        self.desktop = self.client.get("/api/desktops", headers=self.hub).json()["defaultId"]
        self.client.post("/api/apps/notes/install", headers=self.hub)
        current = self.client.get(f"/api/desktops/{self.desktop}/policy", headers=self.hub).json()
        self.client.put(
            f"/api/desktops/{self.desktop}/policy",
            headers=self.hub,
            json={"revision": current["revision"], "apps": ["notes"]},
        )
        started = self.client.post(
            f"/api/desktops/{self.desktop}/enable-agent", headers=self.hub
        )
        self.assertEqual(started.status_code, 200, started.text)
        self.view = self.client.post(
            f"/api/desktops/{self.desktop}/views",
            headers=self.hub,
            json={"kind": "app", "appId": "notes"},
        ).json()
        asyncio.run(self.open_in_browser())

    def tearDown(self):
        self.client.post(f"/api/desktops/{self.desktop}/disable-agent", headers=self.hub)
        self.client.delete(
            f"/api/desktops/{self.desktop}/views/{self.view['id']}", headers=self.hub
        )
        self.client.close()

    async def open_in_browser(self):
        desktops = self.app.state.desktops
        future = asyncio.run_coroutine_threadsafe(
            desktops.open_in_browser(self.desktop, desktops.store.view(self.view["id"])),
            self.app.state.loop,
        )
        future.result(timeout=60)

    # ---- frames

    def test_a_frame_is_of_one_view_and_is_never_cached(self):
        frame = self.client.get(
            f"/api/desktops/{self.desktop}/views/{self.view['id']}/frame", headers=self.hub
        )
        self.assertEqual(frame.status_code, 200, frame.text)
        body = frame.json()
        self.assertEqual(body["viewId"], self.view["id"])
        self.assertEqual(body["width"], 1280)
        self.assertNotIn("file", body, "a frame never hands out a path on this computer")

        picture = self.client.get(
            f"/api/desktops/{self.desktop}/views/{self.view['id']}/frame/{body['digest']}",
            headers=self.hub,
        )
        self.assertEqual(picture.status_code, 200)
        self.assertEqual(picture.headers["content-type"], "image/png")
        self.assertIn("no-store", picture.headers["cache-control"])
        self.assertEqual(picture.content[:8], b"\x89PNG\r\n\x1a\n")

    def test_a_frame_needs_the_owner(self):
        frame = self.client.get(
            f"/api/desktops/{self.desktop}/views/{self.view['id']}/frame"
        )
        self.assertEqual(frame.status_code, 401)

    def test_a_picture_nobody_captured_is_not_served(self):
        refused = self.client.get(
            f"/api/desktops/{self.desktop}/views/{self.view['id']}/frame/{'a' * 64}",
            headers=self.hub,
        )
        self.assertEqual(refused.status_code, 404)

    def test_a_view_on_another_desktop_is_not_a_frame_here(self):
        other = self.client.post("/api/desktops", headers=self.hub, json={}).json()["id"]
        refused = self.client.get(
            f"/api/desktops/{other}/views/{self.view['id']}/frame", headers=self.hub
        )
        self.assertIn(refused.status_code, (404, 409, 503))
        self.client.delete(f"/api/desktops/{other}", headers=self.hub)

    # ---- control

    def test_watching_changes_nothing(self):
        before = self.client.get(f"/api/desktops/{self.desktop}/viewer", headers=self.hub).json()
        self.assertIsNone(before["control"], "looking is not controlling")
        self.client.get(
            f"/api/desktops/{self.desktop}/views/{self.view['id']}/frame", headers=self.hub
        )
        after = self.client.get(f"/api/desktops/{self.desktop}/viewer", headers=self.hub).json()
        self.assertIsNone(after["control"], "and neither is looking again")

    def test_taking_over_is_exclusive_and_bumps_the_generation(self):
        first = self.client.post(f"/api/desktops/{self.desktop}/takeover", headers=self.hub)
        self.assertEqual(first.status_code, 201, first.text)
        lease = first.json()
        self.assertGreaterEqual(lease["controlEpoch"], 1)

        second = self.client.post(f"/api/desktops/{self.desktop}/takeover", headers=self.hub)
        self.assertEqual(second.status_code, 409)
        self.assertIn("Somebody else has control", second.json()["detail"])

        released = self.client.delete(
            f"/api/desktops/{self.desktop}/takeover/{lease['leaseId']}", headers=self.hub
        )
        self.assertEqual(released.status_code, 200)
        self.assertTrue(released.json()["released"])
        self.assertIn("still paused", released.json()["note"])

    def test_a_person_can_type_into_the_view_they_hold(self):
        self.client.get(
            f"/api/desktops/{self.desktop}/views/{self.view['id']}/frame", headers=self.hub
        )
        lease = self.client.post(
            f"/api/desktops/{self.desktop}/takeover",
            headers=self.hub,
            json={"viewId": self.view["id"]},
        ).json()
        typed = self.client.post(
            f"/api/desktops/{self.desktop}/takeover/{lease['leaseId']}/input",
            headers=self.hub,
            json={"kind": "key", "viewId": self.view["id"], "key": "Tab"},
        )
        self.assertEqual(typed.status_code, 200, typed.text)
        self.assertTrue(typed.json()["ok"])
        self.client.delete(
            f"/api/desktops/{self.desktop}/takeover/{lease['leaseId']}", headers=self.hub
        )

    def test_input_without_the_lease_reaches_nothing(self):
        self.client.get(
            f"/api/desktops/{self.desktop}/views/{self.view['id']}/frame", headers=self.hub
        )
        refused = self.client.post(
            f"/api/desktops/{self.desktop}/takeover/not-a-lease/input",
            headers=self.hub,
            json={"kind": "key", "viewId": self.view["id"], "key": "Tab"},
        )
        self.assertEqual(refused.status_code, 409)

    def test_a_key_a_person_may_not_press_is_still_refused(self):
        self.client.get(
            f"/api/desktops/{self.desktop}/views/{self.view['id']}/frame", headers=self.hub
        )
        lease = self.client.post(
            f"/api/desktops/{self.desktop}/takeover",
            headers=self.hub,
            json={"viewId": self.view["id"]},
        ).json()
        # Being a person does not make a browser shortcut into a page
        # interaction. The same allowlist applies.
        refused = self.client.post(
            f"/api/desktops/{self.desktop}/takeover/{lease['leaseId']}/input",
            headers=self.hub,
            json={"kind": "key", "viewId": self.view["id"], "key": "Control+w"},
        )
        self.assertGreaterEqual(refused.status_code, 400)
        self.client.delete(
            f"/api/desktops/{self.desktop}/takeover/{lease['leaseId']}", headers=self.hub
        )

    def test_typing_into_a_view_nobody_has_looked_at_is_refused(self):
        lease = self.client.post(f"/api/desktops/{self.desktop}/takeover", headers=self.hub).json()
        refused = self.client.post(
            f"/api/desktops/{self.desktop}/takeover/{lease['leaseId']}/input",
            headers=self.hub,
            json={"kind": "key", "viewId": self.view["id"], "key": "Tab"},
        )
        self.assertEqual(refused.status_code, 409)
        self.assertIn("Look at this window", refused.json()["detail"])
        self.client.delete(
            f"/api/desktops/{self.desktop}/takeover/{lease['leaseId']}", headers=self.hub
        )

    def test_a_click_outside_the_view_is_refused(self):
        self.client.get(
            f"/api/desktops/{self.desktop}/views/{self.view['id']}/frame", headers=self.hub
        )
        lease = self.client.post(
            f"/api/desktops/{self.desktop}/takeover",
            headers=self.hub,
            json={"viewId": self.view["id"]},
        ).json()
        refused = self.client.post(
            f"/api/desktops/{self.desktop}/takeover/{lease['leaseId']}/input",
            headers=self.hub,
            json={"kind": "click", "viewId": self.view["id"], "point": {"x": 5000, "y": 5}},
        )
        self.assertEqual(refused.status_code, 422)
        self.client.delete(
            f"/api/desktops/{self.desktop}/takeover/{lease['leaseId']}", headers=self.hub
        )

    def test_the_agents_held_observation_does_not_survive_a_takeover(self):
        """The reason the epoch changes rather than the lease merely existing."""
        desktops = self.app.state.desktops

        async def observe():
            return await desktops.runtime.command(
                "view.observe", desktopId=self.desktop, viewId=self.view["id"], timeout=30.0
            )

        seen = asyncio.run_coroutine_threadsafe(observe(), self.app.state.loop).result(timeout=60)
        lease = self.client.post(f"/api/desktops/{self.desktop}/takeover", headers=self.hub).json()

        async def act():
            return await desktops.runtime.command(
                "view.act",
                desktopId=self.desktop,
                viewId=self.view["id"],
                action={
                    "action": "click",
                    "observationId": seen["observationId"],
                    "ref": (seen["page"]["controls"] or [{}])[0].get("ref", "f0:e0"),
                },
                timeout=30.0,
            )

        with self.assertRaises(Exception) as caught:
            asyncio.run_coroutine_threadsafe(act(), self.app.state.loop).result(timeout=60)
        self.assertIn("observe", str(caught.exception).lower())
        self.client.delete(
            f"/api/desktops/{self.desktop}/takeover/{lease['leaseId']}", headers=self.hub
        )


if __name__ == "__main__":
    unittest.main()
