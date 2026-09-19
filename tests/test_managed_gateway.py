"""The app's own web address: who gets in, what crosses it, and when it ends.

A managed app is served on a hostname of its own, at `/`, because that is what
an application like Memos assumes. These tests are the other half of that
bargain: that the hostname reaches only the app, that Vela's credentials never
travel with the request, that the app's own credentials do, that two browsers
stay two browsers, and that signing out of Vela really closes what was open.

Run with: python -m unittest discover -s tests. Uses disposable data only.
"""

import threading
import time
import unittest

from managed_support import APP_ID, APP_HOST, APP_ORIGIN, GATEWAY_COOKIE, ManagedTestCase


class RunningAppTestCase(ManagedTestCase):
    """One installed, running, entered app, for the tests that need one."""

    def setUp(self):
        super().setUp()
        self.install()
        self.start()
        self.enter()

    def sign_in(self, client=None):
        """Sign in to the *app*, with the app's own account."""
        response = self.app_post(
            "/api/login", client=client,
            json={"user": "tester", "password": "fixture-pw"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": "Bearer " + response.json()["token"]}


class AddressingTests(RunningAppTestCase):
    def test_the_app_is_served_at_the_root_of_its_own_host(self):
        response = self.app_get("/")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Fixture app", response.text)
        self.assertIn("text/html", response.headers["content-type"])

    def test_velas_own_api_is_not_reachable_on_an_app_host(self):
        """The gateway is in front of everything, so this never reaches a router."""
        for path in ("/api/apps", "/api/settings", "/api/session", "/api/managed"):
            with self.subTest(path=path):
                response = self.app_get(path, headers=self.hub)
                self.assertNotEqual(response.status_code, 200)
                self.assertNotIn("velaVersion", response.text)
                # What answered is the fixture, saying it has no such path.
                self.assertIn("not found", response.text.lower())

    def test_a_hub_bearer_token_does_not_authorise_an_app_host(self):
        blank = self.browser()
        response = self.app_get("/", client=blank, headers=self.hub)
        self.assertEqual(response.status_code, 401, response.text)

    def test_an_unknown_host_is_not_this_gateways_to_answer(self):
        """It falls through to Vela, which answers as it always did."""
        response = self.client.get("http://testserver/api/health")
        self.assertEqual(response.status_code, 200, response.text)
        stranger = self.client.get(
            f"http://not-installed.apps.localhost/", follow_redirects=False
        )
        self.assertEqual(stranger.status_code, 404, stranger.text)
        self.assertIn("no managed app", stranger.text)

    def test_a_name_under_the_app_domain_never_reaches_the_hub(self):
        """The gateway claims the whole domain, not only the names it can serve."""
        for host in (f"evil.{APP_ID}.apps.localhost", "apps.localhost",
                     "fixture-notes.apps.vela.invalid"):
            with self.subTest(host=host):
                response = self.client.get(f"http://{host}/", follow_redirects=False)
                self.assertNotIn(response.status_code, (200, 307))
                self.assertNotIn("<div id=\"root\">", response.text,
                                 "Vela's dashboard was served on an app domain")

    def test_the_reserved_vela_namespace_is_never_proxied(self):
        response = self.app_get("/_vela/nothing")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertIn("belongs to Vela", response.text)

    def test_the_status_endpoint_says_whether_this_browser_is_connected(self):
        connected = self.app_get("/_vela/status")
        self.assertEqual(connected.status_code, 200, connected.text)
        self.assertTrue(connected.json()["connected"])
        stranger = self.app_get("/_vela/status", client=self.browser())
        self.assertEqual(stranger.status_code, 401)
        self.assertFalse(stranger.json()["connected"])


class LaunchExchangeTests(ManagedTestCase):
    def setUp(self):
        super().setUp()
        self.install()
        self.start()

    def open_ticket(self, **payload):
        return self.launch(**payload)

    def test_a_ticket_works_once(self):
        ticket = self.open_ticket()
        headers = {"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate"}
        first = self.client.get(ticket["url"], headers=headers, follow_redirects=False)
        self.assertEqual(first.status_code, 303, first.text)
        self.assertEqual(first.headers["location"], "/")
        replayed = self.client.get(ticket["url"], headers=headers, follow_redirects=False)
        self.assertEqual(replayed.status_code, 403, replayed.text)
        self.assertIn("works once", replayed.text)

    def test_a_ticket_is_bound_to_the_apps_own_host(self):
        ticket = self.open_ticket()
        path = ticket["url"].split(APP_ORIGIN, 1)[1]
        elsewhere = self.client.get(
            f"http://other-app.apps.localhost{path}", follow_redirects=False
        )
        self.assertEqual(elsewhere.status_code, 404, elsewhere.text)

    def test_an_expired_ticket_is_refused(self):
        ticket = self.open_ticket()
        for record in self.service.sessions._tickets.values():
            record["expires"] = time.monotonic() - 1
        response = self.client.get(ticket["url"], follow_redirects=False)
        self.assertEqual(response.status_code, 403, response.text)

    def test_a_launch_link_can_only_be_spent_by_going_there(self):
        """Found in a real browser: a sibling app's `fetch` had spent a ticket.

        `<app>.apps.localhost` names are same-site, so `SameSite` does not keep
        one app's page from calling another's launch link, and a blocked CORS
        response is still a request that arrived. A ticket in an `<img src>` is
        the same shape of mistake. Only a navigation counts.
        """
        for dest, mode in (("image", "no-cors"), ("empty", "cors"),
                           ("empty", "same-origin"), ("script", "no-cors")):
            with self.subTest(dest=dest, mode=mode):
                ticket = self.open_ticket()
                refused = self.client.get(
                    ticket["url"],
                    headers={"Sec-Fetch-Dest": dest, "Sec-Fetch-Mode": mode},
                    follow_redirects=False,
                )
                self.assertEqual(refused.status_code, 400, refused.text)
                # And the ticket it refused is still good for a real visit.
                used = self.client.get(
                    ticket["url"],
                    headers={"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate"},
                    follow_redirects=False,
                )
                self.assertEqual(used.status_code, 303, used.text)

    def test_a_window_frame_may_spend_one_too(self):
        ticket = self.open_ticket()
        response = self.client.get(
            ticket["url"],
            headers={"Sec-Fetch-Dest": "iframe", "Sec-Fetch-Mode": "navigate"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303, response.text)

    def test_a_ticket_carries_a_deep_link_and_normalises_a_hostile_one(self):
        response = self.enter(path="/api/deep/one/two?x=1")
        self.assertEqual(response.headers["location"], "/api/deep/one/two?x=1")
        landed = self.app_get("/api/deep/one/two?x=1")
        self.assertEqual(landed.json()["deep"], "/api/deep/one/two")

        blank = self.browser()
        away = self.enter(client=blank, path="//evil.test/steal")
        self.assertEqual(away.headers["location"], "/")

    def test_the_session_cookie_is_host_only_and_prefixed(self):
        """`__Host-` is what stops a sibling app tossing one at the parent name."""
        response = self.enter(client=self.browser())
        raw = next(
            value for value in response.headers.get_list("set-cookie")
            if value.startswith(GATEWAY_COOKIE)
        )
        self.assertTrue(raw.startswith("__Host-"))
        self.assertNotIn("domain=", raw.lower())
        self.assertIn("HttpOnly", raw)
        self.assertIn("Secure", raw)
        self.assertIn("Path=/", raw)
        # `None`, because a Vela window frames the app cross-site and a browser
        # will not set a `Lax` cookie from one. What `Lax` would have protected
        # is done at the gateway instead, and more thoroughly.
        self.assertIn("SameSite=None", raw)

    def test_a_session_cookie_without_the_host_prefix_is_not_a_session(self):
        self.enter()
        value = self.client.cookies.get(GATEWAY_COOKIE, domain=APP_HOST)
        blank = self.browser()
        response = blank.get(
            APP_ORIGIN + "/", headers={"Cookie": f"vela-app={value}"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 401, response.text)

    def test_two_session_cookies_at_once_are_treated_as_none(self):
        self.enter()
        value = self.client.cookies.get(GATEWAY_COOKIE, domain=APP_HOST)
        blank = self.browser()
        response = blank.get(
            APP_ORIGIN + "/",
            headers={"Cookie": f"{GATEWAY_COOKIE}={value}; {GATEWAY_COOKIE}=other"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 401, response.text)


class CredentialTests(RunningAppTestCase):
    def test_the_app_never_sees_velas_credentials(self):
        """Even one sent here by mistake is dropped rather than forwarded."""
        received = self.app_get("/api/received", headers=self.hub).json()
        headers = received["headers"]
        self.assertNotIn(GATEWAY_COOKIE, received["cookies"])
        token = self.hub["Authorization"].removeprefix("Bearer ")
        self.assertNotIn(token, str(received))
        self.assertNotIn("authorization", headers)
        # The app's own host header is its loopback address, not Vela's name.
        self.assertTrue(headers["host"].startswith("127.0.0.1:"))
        self.assertEqual(headers["x-forwarded-host"], APP_HOST)

    def test_the_apps_own_bearer_and_cookies_do_reach_it(self):
        token = self.sign_in()
        self.assertEqual(self.app_get("/api/me", headers=token).json()["user"], "tester")
        # And a cookie the app set on sign-in comes back on its own. Which one
        # depends on the client: `httpx` keeps only the cookie without `Secure`,
        # so a browser is what proves the `SameSite=None; Secure` path, in
        # `web/scripts/test-managed-apps.mjs`.
        self.assertEqual(self.app_get("/api/me").json()["user"], "tester")
        received = self.app_get("/api/received", headers=token).json()
        self.assertIn("fixture_plain", received["cookies"])
        self.assertEqual(received["headers"]["authorization"], token["Authorization"])

    def test_several_cookies_survive_one_response_and_lose_their_domain(self):
        response = self.app_get("/api/set-cookies")
        cookies = response.headers.get_list("set-cookie")
        names = [value.split("=", 1)[0] for value in cookies]
        self.assertEqual(names, ["plain", "wide", "third"],
                         "a reserved name should be dropped and the rest kept")
        for value in cookies:
            self.assertNotIn("domain=", value.lower(),
                             "an app cookie must belong to the app's own host")

    def test_an_app_cannot_overwrite_the_gateways_own_cookie(self):
        before = self.client.cookies.get(GATEWAY_COOKIE, domain=APP_HOST)
        self.app_get("/api/set-cookies")
        after = self.client.cookies.get(GATEWAY_COOKIE, domain=APP_HOST)
        self.assertEqual(before, after)
        self.assertEqual(self.app_get("/").status_code, 200)

    def test_two_browsers_do_not_share_one_upstream_sign_in(self):
        """No shared cookie jar: the gateway holds no credentials of its own."""
        self.sign_in()
        other = self.browser()
        self.enter(client=other)
        self.assertEqual(self.app_get("/api/me").json()["user"], "tester")
        stranger = self.app_get("/api/me", client=other)
        self.assertEqual(stranger.status_code, 401, stranger.text)

    def test_only_the_apps_own_pages_may_use_it(self):
        """The CSRF boundary is here, not on the cookie.

        `SameSite` cannot be it. Sibling apps share a registrable domain, so it
        never separated them, and a browser refuses to set a `Lax` cookie in the
        cross-site frame a Vela window is -- which is how the window would break.
        The check is stronger than `Lax` was: a cross-site `GET` for a
        subresource is refused too, where `Lax` covered only unsafe methods.
        """
        cases = [
            ("a sibling app posting", "POST", "/api/notes",
             {"Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "cors",
              "Sec-Fetch-Dest": "empty", "Origin": "http://other.apps.localhost"}),
            ("another site posting", "POST", "/api/notes",
             {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors",
              "Sec-Fetch-Dest": "empty", "Origin": "http://evil.test"}),
            ("another site loading an image", "GET", "/api/files/x",
             {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors",
              "Sec-Fetch-Dest": "image"}),
            ("another site reading with a script", "GET", "/api/notes",
             {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors",
              "Sec-Fetch-Dest": "empty", "Origin": "http://evil.test"}),
        ]
        for label, method, path, headers in cases:
            with self.subTest(case=label):
                response = self.client.request(
                    method, APP_ORIGIN + path, headers=headers, json={"body": "no"}
                )
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual(response.json()["code"], "managed.cross_origin")

    def test_arriving_from_elsewhere_is_allowed_because_that_is_how_you_open_it(self):
        """A Vela window frames the app cross-site; a tab visits it cross-site."""
        for dest in ("document", "iframe"):
            with self.subTest(dest=dest):
                response = self.app_get("/", headers={
                    "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Dest": dest,
                })
                self.assertEqual(response.status_code, 200, response.text)

    def test_a_write_from_the_apps_own_pages_is_allowed(self):
        token = self.sign_in()
        response = self.client.post(
            APP_ORIGIN + "/api/notes",
            json={"body": "mine"},
            headers={**token, "Sec-Fetch-Site": "same-origin", "Origin": APP_ORIGIN},
        )
        self.assertEqual(response.status_code, 201, response.text)

    def test_a_browser_that_sends_no_fetch_metadata_still_gets_the_origin_check(self):
        """Older Safari sends no `Sec-Fetch-*`. `Origin` is what is left."""
        refused = self.client.post(
            APP_ORIGIN + "/api/notes", json={"body": "no"},
            headers={"Origin": "http://evil.test"},
        )
        self.assertEqual(refused.status_code, 403, refused.text)
        allowed = self.client.post(
            APP_ORIGIN + "/api/notes", json={"body": "yes"},
            headers={**self.sign_in(), "Origin": APP_ORIGIN},
        )
        self.assertEqual(allowed.status_code, 201, allowed.text)


class ProtocolTests(RunningAppTestCase):
    def test_a_redirect_is_passed_through_rather_than_followed(self):
        response = self.client.get(APP_ORIGIN + "/api/redirect", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/api/notes")

    def test_an_upload_and_a_ranged_download_both_work(self):
        payload = bytes(range(256)) * 40
        uploaded = self.client.post(
            APP_ORIGIN + "/api/files",
            content=payload,
            headers={"X-Filename": "blob.bin", "Sec-Fetch-Site": "same-origin",
                     "Origin": APP_ORIGIN},
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        self.assertEqual(uploaded.json()["size"], len(payload))

        whole = self.app_get("/api/files/blob.bin")
        self.assertEqual(whole.status_code, 200)
        self.assertEqual(whole.content, payload)

        part = self.app_get("/api/files/blob.bin", headers={"Range": "bytes=10-19"})
        self.assertEqual(part.status_code, 206, part.text)
        self.assertEqual(part.content, payload[10:20])
        self.assertEqual(part.headers["content-range"], f"bytes 10-19/{len(payload)}")

    def test_an_event_stream_arrives_as_a_stream(self):
        seen = []
        with self.client.stream("GET", APP_ORIGIN + "/api/events?count=4") as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/event-stream", response.headers["content-type"])
            for line in response.iter_lines():
                if line.startswith("data:"):
                    seen.append(line)
        self.assertEqual(len(seen), 4, seen)

    def test_a_websocket_upgrade_is_closed_with_a_reason_not_left_hanging(self):
        from starlette.testclient import WebSocketDisconnect

        with self.assertRaises(WebSocketDisconnect) as caught:
            with self.client.websocket_connect(f"ws://{APP_HOST}/api/socket"):
                pass
        self.assertEqual(caught.exception.code, 1008)
        self.assertIn("WebSocket", caught.exception.reason)

    def test_an_app_that_died_under_an_open_window_says_so(self):
        """Stopped through Vela would also revoke; this is the crash case."""
        self.service.supervisor.stop(APP_ID, self.service.manifest(APP_ID))
        response = self.app_get("/")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("is not running", response.text)


class RevocationTests(RunningAppTestCase):
    def test_signing_out_of_vela_closes_the_app(self):
        self.assertEqual(self.app_get("/").status_code, 200)
        signed_out = self.client.post(
            "/api/logout", headers={**self.hub, "X-Vela-Bootstrap": "1"}
        )
        self.assertEqual(signed_out.status_code, 200, signed_out.text)
        self.assertEqual(self.app_get("/").status_code, 401)

    def test_stopping_the_app_ends_its_sessions(self):
        self.assertEqual(self.app_get("/").status_code, 200)
        self.stop()
        self.start()
        response = self.app_get("/")
        self.assertEqual(response.status_code, 401, response.text)

    def test_replacing_the_code_ends_sessions_reviewed_against_the_old_code(self):
        self.assertEqual(self.app_get("/").status_code, 200)
        self.install(review=self.review(folder=self.build_package(version="1.1.0")))
        self.assertEqual(self.status()["managed"]["generation"], 2)
        self.assertEqual(self.app_get("/").status_code, 401)

    def test_signing_out_from_the_app_side_ends_only_that_session(self):
        other = self.browser()
        self.enter(client=other)
        response = self.client.post(
            APP_ORIGIN + "/_vela/leave",
            headers={"Sec-Fetch-Site": "same-origin", "Origin": APP_ORIGIN},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.app_get("/").status_code, 401)
        self.assertEqual(self.app_get("/", client=other).status_code, 200)

    def test_a_stream_already_open_is_closed_when_the_session_is_revoked(self):
        """A quiet event stream must not outlive the session that opened it.

        The fixture holds this one open for a minute and says nothing after the
        first line, which is what an idle event stream looks like. Waiting for
        the next chunk to notice a revocation would mean a locked Vela still
        feeding an open page, so the gateway watches the session instead.
        """
        revoker = threading.Timer(1.0, lambda: self.service.sessions.revoke_app(APP_ID))
        revoker.start()
        self.addCleanup(revoker.cancel)
        began = time.monotonic()
        with self.client.stream("GET", APP_ORIGIN + "/api/slow?seconds=60") as response:
            self.assertEqual(response.status_code, 200)
            try:
                for _ in response.iter_raw():
                    pass
            except Exception:  # noqa: BLE001 - a closed stream is the point
                pass
        elapsed = time.monotonic() - began
        self.assertLess(elapsed, 30,
                        "the stream outlived the session that opened it")


class OtherProfilesTests(ManagedTestCase):
    """Managed apps are a third profile, and the other two are unchanged."""

    def test_the_static_app_path_is_untouched_by_a_managed_installation(self):
        self.install()
        # `/apps/{id}/` belongs to packaged apps. A managed app is not one, and
        # asking for it there is a plain not-found rather than a way in.
        response = self.client.get(f"/apps/{APP_ID}/", headers=self.hub)
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["code"], "apps.unknown")

    def test_a_managed_app_gets_no_sdk_session(self):
        self.install()
        response = self.client.post(f"/api/apps/{APP_ID}/session", headers=self.hub)
        self.assertEqual(response.status_code, 404, response.text)

    def test_the_hub_still_refuses_to_be_framed(self):
        self.install()
        response = self.client.get("/api/health")
        self.assertEqual(response.headers["content-security-policy"], "frame-ancestors 'none'")


if __name__ == "__main__":
    unittest.main()
