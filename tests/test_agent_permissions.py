"""What an agent may change, and where that is decided.

The rule these are all about: an agent's authority is checked where the effect
commits, not where the request arrives. A frontend button, a tool wrapper and a
raw HTTP request are three ways to reach the same write, and a check that lives
in any one of them is a check the other two walk past. So the tests here mostly
try to get the same change to happen through a different door.

Everything uses disposable data and fixture apps.
"""

import shutil
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

import test_app_contract as base
from scripts.fixture_apps import APPS as FIXTURE_APPS
from vela.desktops.effects import OPERATIONS, classify


class AgentPermissionTests(unittest.TestCase):
    tearDown = base.ApiBoundaryTests.tearDown
    session = base.ApiBoundaryTests.session

    def setUp(self):
        base.ApiBoundaryTests.setUp(self)
        for app in ("notes", "meals"):
            shutil.copytree(FIXTURE_APPS / app, self.apps / app)
        self.notes_session, self.notes = self.session("notes")
        self.meals_session, self.meals = self.session("meals")
        self.desktop = self.client.get("/api/desktops", headers=self.hub).json()["defaultId"]

    # ---- helpers

    def policy(self, **changes):
        current = self.client.get(
            f"/api/desktops/{self.desktop}/policy", headers=self.hub
        ).json()
        body = {
            "revision": current["revision"],
            "apps": changes.get("apps", ["notes"]),
            "sites": changes.get("sites", []),
            "approvals": changes.get("approvals", "ask"),
            "actionScopes": changes.get("actionScopes", []),
        }
        if "budget" in changes:
            body["budget"] = changes["budget"]
        return self.client.put(f"/api/desktops/{self.desktop}/policy", headers=self.hub, json=body)

    def open_view(self, app_id="notes"):
        response = self.client.post(
            f"/api/desktops/{self.desktop}/views",
            headers=self.hub,
            json={"kind": "app", "appId": app_id},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def agent_session(self, view, run_id="run-1"):
        response = self.client.post(
            f"/api/desktops/{self.desktop}/agent-sessions",
            headers=self.hub,
            json={"viewId": view["id"], "runId": run_id},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return {"Authorization": "Bearer " + response.json()["token"]}

    def grant(self, effect="write", app_id="notes", **extra):
        response = self.client.post(
            f"/api/desktops/{self.desktop}/grants",
            headers=self.hub,
            json={"effect": effect, "appId": app_id, **extra},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def ready(self, app_id="notes", run_id="run-1"):
        """A desktop that allows one app, with a window open and a session for it."""
        self.assertEqual(self.policy(apps=[app_id]).status_code, 200)
        return self.agent_session(self.open_view(app_id), run_id)

    def write(self, headers, value, revision=0):
        return self.client.put(
            "/api/app/storage", headers=headers, json={"value": value, "revision": revision}
        )

    def assertAsked(self, response, why=""):
        """An effect with no grant behind it becomes a question, not a write.

        202 with a pending request, and — the part that matters — nothing
        changed. Since delayed approvals arrived this is what "refused" looks
        like from the agent's side: still waiting, still not done.
        """
        self.assertEqual(response.status_code, 202, response.text or why)
        body = response.json()
        self.assertIn("pending", body, why)
        self.assertEqual(body["pending"]["state"], "pending", why)
        self.assertTrue(body["pending"]["summary"]["headline"], why)
        return body["pending"]

    def stored(self, headers=None):
        """What the app actually has saved, read as the person."""
        return self.client.get("/api/app/storage", headers=headers or self.notes).json()

    # ---- the classification itself

    def test_an_operation_nobody_classified_is_unavailable(self):
        # The default is the point. A route added without a thought about agents
        # has no effect class, so there is nothing to grant, so the answer is no.
        self.assertIsNone(classify("GET", "/storage/something-new"))
        self.assertIsNone(classify("POST", "/storage"))
        self.assertIsNone(classify("DELETE", "/storage"))
        self.assertEqual(classify("PUT", "/storage"), ("storage.write", "write"))
        self.assertEqual(classify("PUT", "/widgets/anything"), ("widget.publish", "publish"))

    def test_every_classified_operation_names_a_real_effect_class(self):
        from vela.desktops.effects import EFFECT_CLASSES

        for (method, path), (operation, effect) in OPERATIONS.items():
            with self.subTest(operation=operation):
                self.assertIn(effect, EFFECT_CLASSES)
                self.assertEqual(classify(method, path.replace("{}", "x")), (operation, effect))

    def test_an_effect_can_end_in_a_way_that_is_not_success_or_failure(self):
        """The vocabulary the supervisor will record against.

        `unknown` is the one that matters and the reason this is a list rather
        than a boolean: a request that went out and whose answer never came back
        has not failed, and treating it as a failure is how something gets sent
        twice. Frozen here so the phase that records outcomes cannot quietly
        invent a fourth meaning for "it did not work".
        """
        from vela.desktops.effects import OUTCOMES

        self.assertEqual(
            list(OUTCOMES),
            ["not_dispatched", "denied", "committed", "failed_before_commit", "unknown"],
        )

    # ---- what a session is for

    def test_an_agent_session_is_short_and_says_what_it_belongs_to(self):
        view = self.open_view()
        self.assertEqual(self.policy(apps=["notes"]).status_code, 200)
        view = self.open_view()
        response = self.client.post(
            f"/api/desktops/{self.desktop}/agent-sessions",
            headers=self.hub,
            json={"viewId": view["id"], "runId": "run-7"},
        )
        body = response.json()
        self.assertEqual(response.status_code, 201, response.text)
        self.assertLessEqual(body["expiresIn"], 600, "shorter than a person's hour")
        self.assertEqual(body["agent"]["runId"], "run-7")
        self.assertEqual(body["agent"]["desktopId"], self.desktop)
        self.assertEqual(body["agent"]["viewId"], view["id"])

    def test_a_desktop_that_allows_nothing_gets_no_session(self):
        view = self.open_view()
        refused = self.client.post(
            f"/api/desktops/{self.desktop}/agent-sessions",
            headers=self.hub,
            json={"viewId": view["id"], "runId": "run-1"},
        )
        self.assertEqual(refused.status_code, 403)

    def test_an_agent_session_cannot_reach_the_dashboard(self):
        agent = self.ready()
        # Everything outside the app routes wants a hub token, and this is not
        # one. The point of checking is that it stays true.
        for path in (
            "/api/settings",
            "/api/desktops",
            f"/api/desktops/{self.desktop}/policy",
            f"/api/desktops/{self.desktop}/grants",
            "/api/apps",
            "/api/session",
        ):
            with self.subTest(path=path):
                self.assertIn(self.client.get(path, headers=agent).status_code, (401, 403))

    def test_an_agent_session_cannot_reach_an_unclassified_app_route(self):
        agent = self.ready()
        refused = self.client.post("/api/apps/notes/session", headers=agent)
        self.assertIn(refused.status_code, (401, 403))

    # ---- reading versus changing

    def test_reading_needs_the_app_to_be_allowed_and_nothing_more(self):
        agent = self.ready()
        read = self.client.get("/api/app/storage", headers=agent)
        self.assertEqual(read.status_code, 200, read.text)

    def test_an_app_the_desktop_does_not_allow_is_refused(self):
        agent = self.ready("notes")
        # The window is for notes; meals is not on this desktop's list at all.
        self.assertEqual(self.policy(apps=["meals"]).status_code, 200)
        refused = self.client.get("/api/app/storage", headers=agent)
        self.assertIn(refused.status_code, (401, 403))

    def test_a_write_without_a_grant_is_refused_however_it_arrives(self):
        agent = self.ready()
        before = self.stored()["revision"]
        # The same change, through the storage route an app's Save button uses.
        asked = self.assertAsked(self.write(agent, {"notes": []}))
        self.assertEqual(asked["effect"], "write")
        self.assertEqual(self.stored()["revision"], before, "asking is not writing")
        # And through the snapshot route, which is also a change.
        self.assertAsked(self.client.post("/api/app/storage/snapshots", headers=agent))
        # A person doing the same thing is unaffected.
        self.assertEqual(self.write(self.notes, {"notes": []}).status_code, 200)

    def test_a_grant_lets_the_write_through(self):
        agent = self.ready()
        self.grant("write")
        saved = self.write(agent, {"notes": []})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["revision"], 1)

    def test_a_grant_for_one_change_does_not_cover_a_different_one(self):
        agent = self.ready()
        import hashlib
        import json

        wanted = {"notes": [{"id": "a", "title": "Groceries", "body": "milk", "updated": 1}]}
        digest = hashlib.sha256(
            json.dumps(wanted, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.grant("write", requestDigest=digest)

        other = {"notes": [{"id": "a", "title": "Groceries", "body": "keys too", "updated": 1}]}
        self.assertAsked(
            self.write(agent, other),
            "approving one change is not approving a different one that arrives after",
        )
        self.assertEqual(self.stored()["revision"], 0, "and the other one did not land")
        self.assertEqual(self.write(agent, wanted).status_code, 200)

    def test_restoring_needs_its_own_grant(self):
        agent = self.ready()
        self.write(self.notes, {"notes": []})
        snapshot = self.client.post("/api/app/storage/snapshots", headers=self.notes)
        self.assertEqual(snapshot.status_code, 200, snapshot.text)
        snapshot_id = snapshot.json()["id"]
        after = self.write(
            self.notes, {"notes": [{"id": "b", "title": "After", "body": "x", "updated": 2}]}, 1
        )
        self.assertEqual(after.status_code, 200, after.text)

        # Permission to save is not permission to replace everything with an
        # older copy of it.
        self.grant("write")
        asked = self.assertAsked(
            self.client.post(
                f"/api/app/storage/snapshots/{snapshot_id}/restore",
                headers=agent,
                json={"revision": 2},
            )
        )
        self.assertEqual(asked["effect"], "restore")
        self.assertEqual(self.stored()["revision"], 2, "asking about a restore restores nothing")

        self.grant("restore", scope={"snapshot": snapshot_id})
        allowed = self.client.post(
            f"/api/app/storage/snapshots/{snapshot_id}/restore",
            headers=agent,
            json={"revision": 2},
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_publishing_to_the_desk_needs_a_grant(self):
        shutil.copytree(base.ROOT / "tests/fixtures/widget-fixture", self.apps / "widget-fixture")
        self.session("widget-fixture")
        self.assertEqual(self.policy(apps=["widget-fixture"]).status_code, 200)
        view = self.open_view("widget-fixture")
        agent = self.agent_session(view)
        widget_id = "sync"
        summary = {"value": "3", "caption": "notes"}
        asked = self.assertAsked(
            self.client.put(
                f"/api/app/widgets/{widget_id}", headers=agent, json={"summary": summary}
            )
        )
        self.assertEqual(asked["effect"], "publish")
        self.grant("publish", app_id="widget-fixture", scope={"widget": widget_id})
        allowed = self.client.put(
            f"/api/app/widgets/{widget_id}", headers=agent, json={"summary": summary}
        )
        self.assertEqual(allowed.status_code, 200, allowed.text)

    # ---- revocation

    def test_changing_the_policy_takes_every_grant_and_session_with_it(self):
        agent = self.ready()
        self.grant("write")
        self.assertEqual(self.write(agent, {"notes": []}).status_code, 200)

        # The owner changes what this desktop may touch. Narrowing has to take
        # effect now, not when something is next re-checked.
        self.assertEqual(self.policy(apps=["notes", "meals"]).status_code, 200)
        self.assertEqual(
            self.client.get(f"/api/desktops/{self.desktop}/grants", headers=self.hub).json()[
                "grants"
            ],
            [],
        )
        after = self.write(agent, {"notes": []}, revision=1)
        self.assertIn(after.status_code, (401, 403), "the old session is gone too")

    def test_revoking_one_run_leaves_another_alone(self):
        self.assertEqual(self.policy(apps=["notes"]).status_code, 200)
        view = self.open_view("notes")
        first = self.agent_session(view, "run-a")
        second = self.agent_session(view, "run-b")
        self.grant("write", runId="run-a")
        self.grant("write", runId="run-b")

        self.client.delete(
            f"/api/desktops/{self.desktop}/grants?runId=run-a", headers=self.hub
        )
        self.assertIn(
            self.write(first, {"notes": []}).status_code,
            (401, 403),
            "revoking a run takes its sessions as well as its grants",
        )
        allowed = self.write(second, {"notes": []})
        self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_a_grant_for_one_run_is_not_a_grant_for_another(self):
        self.assertEqual(self.policy(apps=["notes"]).status_code, 200)
        view = self.open_view("notes")
        other = self.agent_session(view, "run-b")
        self.grant("write", runId="run-a")
        asked = self.assertAsked(self.write(other, {"notes": []}))
        self.assertEqual(asked["runId"], "run-b", "and it is asked on behalf of the run that asked")
        self.assertEqual(self.stored()["revision"], 0)

    def test_a_grant_does_not_survive_the_app_being_reinstalled(self):
        agent = self.ready()
        self.grant("write")
        self.assertEqual(self.write(agent, {"notes": []}).status_code, 200)

        self.assertEqual(self.client.delete("/api/apps/notes", headers=self.hub).status_code, 200)
        self.assertEqual(
            self.client.post("/api/apps/notes/install", headers=self.hub).status_code, 200
        )
        # A new installation with the same id inherits nothing: neither the
        # window nor the permission somebody gave the one before it.
        refused = self.write(agent, {"notes": []}, revision=1)
        self.assertIn(refused.status_code, (401, 403, 404))

    def test_a_grant_expires(self):
        agent = self.ready()
        self.grant("write", seconds=1)
        self.assertEqual(self.write(agent, {"notes": []}).status_code, 200)
        time.sleep(1.1)
        # An expired grant is no grant. The change goes back to being a
        # question, and revision 1 is still what is stored.
        self.assertAsked(self.write(agent, {"notes": []}, revision=1))
        self.assertEqual(self.stored()["revision"], 1)

    def test_deleting_a_desktop_takes_its_authority_with_it(self):
        self.assertEqual(self.policy(apps=["notes"]).status_code, 200)
        view = self.open_view("notes")
        agent = self.agent_session(view)
        self.grant("write")
        # Another desktop to delete this one from under.
        self.client.post("/api/desktops", headers=self.hub, json={})
        self.assertEqual(
            self.client.delete(f"/api/desktops/{self.desktop}", headers=self.hub).status_code, 200
        )
        self.assertIn(self.write(agent, {"notes": []}).status_code, (401, 403, 404))

    def test_revoking_while_a_write_is_in_flight_has_one_answer(self):
        """The race the whole design exists for.

        A grant being taken away and the effect it authorized being written
        contend for the same SQLite write lock, because they are rows in the
        same database. So one of them wins outright, and the invariant is what
        the data says afterwards: **a refusal never changed anything, and a
        success always did.**

        Three endings, not two, and the third one is the easy one to forget. If
        the revocation lands first the write finds no grant — and finding no
        grant is not the same as being refused. It becomes a question for the
        owner, nothing is written while that is open, and the reply is `202`. A
        test that only allowed 200 and 403 would fail on a correct outcome
        roughly one run in ten, which is exactly how it was found.

        Run a dozen times because the interleaving is what is being checked, and
        one pass proves nothing about the other order.
        """
        self.assertEqual(self.policy(apps=["notes"]).status_code, 200)
        view = self.open_view("notes")
        outcomes = {"committed": 0, "denied": 0, "asked": 0}
        for attempt in range(12):
            agent = self.agent_session(view, f"run-{attempt}")
            before = self.client.get("/api/app/storage", headers=self.notes).json()
            note = {"id": f"race{attempt}", "title": "Race", "body": "x", "updated": attempt + 1}
            self.grant("write")

            with ThreadPoolExecutor(max_workers=2) as pool:
                writing = pool.submit(self.write, agent, {"notes": [note]}, before["revision"])
                revoking = pool.submit(
                    self.client.delete,
                    f"/api/desktops/{self.desktop}/grants",
                    headers=self.hub,
                )
                written, revoked = writing.result(), revoking.result()

            self.assertEqual(revoked.status_code, 200, revoked.text)
            after = self.client.get("/api/app/storage", headers=self.notes).json()
            with self.subTest(attempt=attempt, wrote=written.status_code):
                if written.status_code == 200:
                    outcomes["committed"] += 1
                    self.assertEqual(
                        after["value"]["notes"],
                        [note],
                        "a write that said it succeeded has to have happened",
                    )
                    self.assertEqual(after["revision"], before["revision"] + 1)
                elif written.status_code == 202:
                    # The revocation got there first, so there was no grant to
                    # check and the write became a question instead. Nothing is
                    # written while one is open, which is the only thing that
                    # matters here.
                    outcomes["asked"] += 1
                    self.assertEqual(
                        after["revision"],
                        before["revision"],
                        "a write waiting for an answer must not have changed anything",
                    )
                    self.assertTrue(written.json()["pending"]["requestId"])
                else:
                    outcomes["denied"] += 1
                    self.assertIn(written.status_code, (401, 403))
                    self.assertEqual(
                        after["revision"],
                        before["revision"],
                        "a write that was refused must not have changed anything",
                    )
        # Not an assertion about which side wins — that is a race, and a race
        # with a guaranteed winner is not a race. It is here so a run that
        # somehow never exercised one of the two is visible.
        self.assertEqual(sum(outcomes.values()), 12, outcomes)

    # ---- actions

    def test_an_action_needs_the_app_grant_and_the_agent_grant(self):
        self.assertEqual(self.policy(apps=["meals", "notes"]).status_code, 200)
        view = self.open_view("meals")
        agent = self.agent_session(view)
        value = {"title": "Dinner plan", "body": "Mon: Pasta"}

        def invoke(headers, key="agent-key-1"):
            return self.client.post(
                "/api/app/actions/invoke",
                headers=headers,
                json={"app": "notes", "action": "create-note", "input": value, "key": key},
            )

        # Neither permission yet. The app's own missing permission is a refusal
        # — it is not something the owner can approve on the app's behalf — so
        # this one is 403 rather than a question.
        self.assertEqual(invoke(agent).status_code, 403)

        # The app is allowed to ask, which is a different question from this
        # agent being allowed to make it happen.
        status = self.client.get("/api/apps/meals/actions", headers=self.hub).json()["requests"][0]
        self.assertEqual(
            self.client.put(
                "/api/apps/meals/actions/grant",
                headers=self.hub,
                json={
                    "app": "notes",
                    "action": "create-note",
                    "allow": True,
                    "sourceContract": status["sourceContract"],
                    "targetContract": status["targetContract"],
                },
            ).status_code,
            200,
        )
        # Now the app may ask, and the run may not — so this becomes the
        # owner's question rather than a refusal, and nothing is written.
        self.assertAsked(invoke(agent, "agent-key-2"), "the app may ask; this run may not")
        self.assertEqual(
            self.client.get("/api/app/storage", headers=self.notes).json()["value"], None
        )

        self.grant("action", app_id="meals", scope={"app": "notes", "action": "create-note"})
        allowed = invoke(agent, "agent-key-3")
        self.assertEqual(allowed.status_code, 200, allowed.text)

        # And the same key replays its receipt rather than writing twice.
        again = invoke(agent, "agent-key-3")
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()["replayed"])
        saved = self.client.get("/api/app/storage", headers=self.notes).json()
        self.assertEqual(len(saved["value"]["notes"]), 1)

    # ---- what never leaks

    def test_a_session_never_carries_the_owners_token(self):
        self.assertEqual(self.policy(apps=["notes"]).status_code, 200)
        view = self.open_view("notes")
        issued = self.client.post(
            f"/api/desktops/{self.desktop}/agent-sessions",
            headers=self.hub,
            json={"viewId": view["id"], "runId": "run-1"},
        ).json()
        body = str(issued)
        self.assertNotIn(self.hub["Authorization"].removeprefix("Bearer "), body)
        self.assertNotIn("owner", issued)

    def test_the_policy_refuses_what_it_cannot_mean(self):
        for bad in (
            {"apps": ["Notes"]},
            {"apps": ["notes"], "approvals": "always"},
            {"apps": ["notes"], "sites": ["example.com"]},
            {"apps": ["notes"], "sites": ["https://example.com/page"]},
            {"apps": ["notes"], "actionScopes": [{"app": "meals", "action": "create-note"}]},
            {"apps": ["notes"], "budget": {"steps": 0}},
            {"apps": ["notes"], "budget": {"steps": 10**9}},
        ):
            with self.subTest(policy=bad):
                self.assertEqual(self.policy(**bad).status_code, 422)

    def test_the_policy_is_saved_against_its_revision(self):
        self.assertEqual(self.policy(apps=["notes"]).status_code, 200)
        stale = self.client.put(
            f"/api/desktops/{self.desktop}/policy",
            headers=self.hub,
            json={"revision": 0, "apps": ["meals"]},
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(
            self.client.get(f"/api/desktops/{self.desktop}/policy", headers=self.hub).json()[
                "apps"
            ],
            ["notes"],
        )

    def test_grants_and_policy_need_a_hub_session(self):
        self.assertEqual(self.client.get(f"/api/desktops/{self.desktop}/policy").status_code, 401)
        self.assertEqual(
            self.client.post(
                f"/api/desktops/{self.desktop}/grants", json={"effect": "write", "appId": "notes"}
            ).status_code,
            401,
        )


if __name__ == "__main__":
    unittest.main()
