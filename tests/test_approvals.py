"""Asking a person, and what happens to the answer.

The behaviour these are about: an effect an agent has no grant for does not
fail — it becomes a question, nothing is written while the question is open, and
the answer is bound to the exact change that was described. Everything that can
go wrong with that is a way for a change to happen that nobody agreed to, so
most of these tests are about the answer *not* counting: the wrong digest, the
second click, the click that arrives after the window closed, the click after
the policy changed.

Disposable data and fixture apps throughout. Nothing here touches a browser:
approvals are a decision about an effect, and the effect boundary is HTTP.
"""

import hashlib
import json
import shutil
import time
import unittest

import test_app_contract as base
from scripts.fixture_apps import APPS as FIXTURE_APPS
from vela.agent_runs.approvals import (
    APPROVAL_SECONDS,
    EXTENSION_SECONDS,
    MAX_APPROVAL_SECONDS,
    MAX_EXTENSIONS,
    changes,
    redact,
    summarize,
)


class SummaryTests(unittest.TestCase):
    """The sentence somebody makes a decision from."""

    def test_a_secret_looking_field_is_described_and_never_repeated(self):
        for key in ("password", "apiKey", "access_token", "Recovery Seed", "otp"):
            self.assertEqual(redact(key, "hunter2"), "(hidden)", key)
        self.assertEqual(redact("title", "Groceries"), '"Groceries"')

    def test_a_long_value_is_clipped_rather_than_pasted_whole(self):
        described = redact("body", "x" * 500)
        self.assertLess(len(described), 120)
        self.assertTrue(described.endswith('…"'))

    def test_a_change_is_described_in_the_words_somebody_would_use(self):
        lines, complete = changes({"notes": [1]}, {"notes": [1, 2]})
        self.assertEqual(lines, ["notes goes from 1 to 2 entries"])
        self.assertTrue(complete)

    def test_an_added_and_a_removed_field_both_say_so(self):
        lines, _ = changes({"keep": 1, "gone": 2}, {"keep": 1, "new": "hello"})
        self.assertIn('new is added as "hello"', lines)
        self.assertIn("gone is removed", lines)

    def test_a_change_too_large_to_list_says_how_much_rather_than_pretending(self):
        before = {f"field{index}": index for index in range(40)}
        after = {f"field{index}": index + 1 for index in range(40)}
        lines, complete = changes(before, after)
        self.assertFalse(complete, "a partial list must not claim to be the whole one")
        self.assertLessEqual(len(lines), 10)

    def test_every_effect_class_has_a_headline_naming_the_app(self):
        for effect in ("write", "restore", "action", "connection", "publish", "something-new"):
            summary = summarize(effect, app_name="Notes", scope={"app": "notes", "action": "add"})
            self.assertIn("Notes", summary["headline"], effect)
            self.assertTrue(summary["detail"], effect)


class ApprovalFlowTests(unittest.TestCase):
    """An agent, an effect, and the owner in between."""

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
        current = self.client.get(f"/api/desktops/{self.desktop}/policy", headers=self.hub).json()
        body = {
            "revision": current["revision"],
            "apps": changes.get("apps", ["notes"]),
            "sites": changes.get("sites", []),
            "approvals": changes.get("approvals", "ask"),
            "actionScopes": changes.get("actionScopes", []),
        }
        response = self.client.put(
            f"/api/desktops/{self.desktop}/policy", headers=self.hub, json=body
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def open_view(self, app_id="notes"):
        response = self.client.post(
            f"/api/desktops/{self.desktop}/views",
            headers=self.hub,
            json={"kind": "app", "appId": app_id},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def ready(self, app_id="notes", run_id="run-1"):
        self.policy(apps=[app_id])
        self.view = self.open_view(app_id)
        response = self.client.post(
            f"/api/desktops/{self.desktop}/agent-sessions",
            headers=self.hub,
            json={"viewId": self.view["id"], "runId": run_id},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return {"Authorization": "Bearer " + response.json()["token"]}

    def agent_for(self, app_id, run_id):
        """A window on this desktop and an agent session for it."""
        view = self.open_view(app_id)
        response = self.client.post(
            f"/api/desktops/{self.desktop}/agent-sessions",
            headers=self.hub,
            json={"viewId": view["id"], "runId": run_id},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return {"Authorization": "Bearer " + response.json()["token"]}

    def write(self, headers, value, revision=0):
        return self.client.put(
            "/api/app/storage", headers=headers, json={"value": value, "revision": revision}
        )

    def ask(self, agent, value, revision=0):
        """One write that has to be asked about, and the question it produced."""
        response = self.write(agent, value, revision)
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["pending"]

    def resolve(self, request_id, decision="approve", **extra):
        return self.client.post(
            f"/api/desktops/{self.desktop}/approvals/{request_id}",
            headers=self.hub,
            json={"decision": decision, **extra},
        )

    def stored(self):
        return self.client.get("/api/app/storage", headers=self.notes).json()

    NOTE = {"notes": [{"id": "a", "title": "Milk", "body": "two litres", "updated": 1}]}
    OTHER = {"notes": [{"id": "a", "title": "Transfer", "body": "everything", "updated": 1}]}

    # ---- the question

    def test_an_effect_with_no_grant_becomes_a_question_and_writes_nothing(self):
        agent = self.ready()
        asked = self.ask(agent, self.NOTE)
        self.assertEqual(asked["state"], "pending")
        self.assertEqual(asked["effect"], "write")
        self.assertEqual(asked["appId"], "notes")
        self.assertEqual(asked["runId"], "run-1")
        self.assertEqual(asked["viewId"], self.view["id"])
        self.assertIn("Notes", asked["summary"]["headline"])
        self.assertTrue(asked["summary"]["detail"])
        self.assertEqual(self.stored()["revision"], 0, "a question is not a write")

    def test_the_same_change_asked_twice_is_one_question(self):
        agent = self.ready()
        first = self.ask(agent, self.NOTE)
        second = self.ask(agent, self.NOTE)
        self.assertEqual(first["requestId"], second["requestId"])
        waiting = self.client.get(
            f"/api/desktops/{self.desktop}/approvals", headers=self.hub
        ).json()["approvals"]
        self.assertEqual(len(waiting), 1)

    def test_a_different_change_is_a_different_question(self):
        agent = self.ready()
        first = self.ask(agent, self.NOTE)
        second = self.ask(agent, self.OTHER)
        self.assertNotEqual(first["requestId"], second["requestId"])
        self.assertNotEqual(first["requestDigest"], second["requestDigest"])

    # ---- the answer

    def test_approving_lets_that_exact_change_through_and_no_other(self):
        agent = self.ready()
        asked = self.ask(agent, self.NOTE)
        answered = self.resolve(asked["requestId"], requestDigest=asked["requestDigest"])
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertEqual(answered.json()["resolution"], "approved_once")

        saved = self.write(agent, self.NOTE)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.stored()["value"]["notes"][0]["title"], "Milk")

        # The approval was for that change. A different one asks again.
        again = self.write(agent, self.OTHER, revision=1)
        self.assertEqual(again.status_code, 202, "approving one change approves one change")

    def test_denying_leaves_the_data_alone_and_says_why(self):
        agent = self.ready()
        asked = self.ask(agent, self.NOTE)
        answered = self.resolve(asked["requestId"], decision="deny")
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertEqual(answered.json()["state"], "denied")
        self.assertEqual(self.stored()["revision"], 0)
        # And the app is told what happened rather than finding nothing.
        state = self.client.get(f"/api/app/approvals/{asked['requestId']}", headers=agent)
        self.assertEqual(state.status_code, 200)
        self.assertEqual(state.json()["state"], "denied")

    def test_answering_twice_is_refused(self):
        agent = self.ready()
        asked = self.ask(agent, self.NOTE)
        self.assertEqual(self.resolve(asked["requestId"]).status_code, 200)
        second = self.resolve(asked["requestId"], decision="deny")
        self.assertEqual(second.status_code, 409)
        self.assertIn("already approved", second.json()["detail"])

    def test_a_prompt_that_was_replaced_resolves_nothing(self):
        agent = self.ready()
        asked = self.ask(agent, self.NOTE)
        stale = self.resolve(asked["requestId"], requestDigest="0" * 64)
        self.assertEqual(stale.status_code, 409)
        self.assertIn("has changed", stale.json()["detail"])
        self.assertEqual(self.write(agent, self.NOTE).status_code, 202, "still waiting")

    def test_approving_for_a_while_covers_the_next_one_too(self):
        agent = self.ready()
        asked = self.ask(agent, self.NOTE)
        answered = self.resolve(
            asked["requestId"], requestDigest=asked["requestDigest"], scopeFuture=True
        )
        self.assertEqual(answered.json()["resolution"], "approved_scope")
        self.assertEqual(self.write(agent, self.NOTE).status_code, 200)
        # A deliberately broader decision, so a second write of this class goes
        # through without asking. That is what the person chose.
        self.assertEqual(self.write(agent, self.OTHER, revision=1).status_code, 200)

    # ---- cancellation

    def test_closing_the_window_cancels_what_it_was_waiting_on(self):
        agent = self.ready()
        asked = self.ask(agent, self.NOTE)
        self.assertEqual(
            self.client.delete(
                f"/api/desktops/{self.desktop}/views/{self.view['id']}", headers=self.hub
            ).status_code,
            200,
        )
        late = self.resolve(asked["requestId"])
        self.assertEqual(late.status_code, 409, late.text)
        self.assertIn("cancelled", late.json()["detail"])
        self.assertEqual(self.stored()["revision"], 0)

    def test_changing_the_policy_cancels_what_it_was_waiting_on(self):
        agent = self.ready()
        asked = self.ask(agent, self.NOTE)
        self.policy(apps=["notes", "meals"])
        late = self.resolve(asked["requestId"])
        self.assertEqual(late.status_code, 409)
        self.assertEqual(self.stored()["revision"], 0)

    def test_stopping_the_run_cancels_what_it_was_waiting_on(self):
        agent = self.ready()
        asked = self.ask(agent, self.NOTE)
        self.assertEqual(
            self.client.delete(
                f"/api/desktops/{self.desktop}/grants?runId=run-1", headers=self.hub
            ).status_code,
            200,
        )
        self.assertEqual(self.resolve(asked["requestId"]).status_code, 409)

    def test_an_app_that_gives_up_withdraws_its_own_question(self):
        agent = self.ready()
        asked = self.ask(agent, self.NOTE)
        gone = self.client.post(
            f"/api/app/approvals/{asked['requestId']}/abandon", headers=agent
        )
        self.assertEqual(gone.status_code, 200, gone.text)
        self.assertTrue(gone.json()["cancelled"])
        # And the owner clicking afterwards changes nothing at all.
        self.assertEqual(self.resolve(asked["requestId"]).status_code, 409)
        self.assertEqual(self.stored()["revision"], 0)

    def test_a_cancelled_question_cannot_be_revived_by_asking_again(self):
        agent = self.ready()
        asked = self.ask(agent, self.NOTE)
        self.client.post(f"/api/app/approvals/{asked['requestId']}/abandon", headers=agent)
        # Asking again opens a *new* question rather than reopening the dead one.
        reopened = self.ask(agent, self.NOTE)
        self.assertNotEqual(reopened["requestId"], asked["requestId"])
        self.assertEqual(self.resolve(asked["requestId"]).status_code, 409)

    # ---- time

    def test_more_time_is_bounded_and_the_absolute_deadline_does_not_move(self):
        agent = self.ready()
        asked = self.ask(agent, self.NOTE)
        ceiling = asked["absoluteExpiry"]
        last = asked
        for _ in range(MAX_EXTENSIONS + 3):
            extended = self.client.post(
                f"/api/app/approvals/{asked['requestId']}/extend", headers=agent
            )
            self.assertEqual(extended.status_code, 200, extended.text)
            last = extended.json()
            self.assertLessEqual(last["expiresAt"], ceiling + 0.001)
        self.assertEqual(last["absoluteExpiry"], ceiling, "the wall does not move")
        self.assertLessEqual(last["extensions"], MAX_EXTENSIONS)

    def test_a_question_nobody_answers_expires_and_stays_expired(self):
        agent = self.ready()
        asked = self.ask(agent, self.NOTE)
        approvals = self.client.app.state.desktops.approvals
        # Wind its clock forward rather than waiting five minutes. The record is
        # the thing under test; the sweep is what reads it.
        with approvals._lock:
            record = approvals._records[asked["requestId"]]
            record["expiresAt"] = time.time() - 1
        late = self.resolve(asked["requestId"])
        self.assertEqual(late.status_code, 409)
        self.assertIn("expired", late.json()["detail"])
        self.assertEqual(self.stored()["revision"], 0)

    def test_the_default_life_is_far_longer_than_the_bridge_reply_timeout(self):
        # The whole reason this exists: ten seconds is a host answering, not a
        # person deciding.
        self.assertGreaterEqual(APPROVAL_SECONDS, 60)
        self.assertGreaterEqual(MAX_APPROVAL_SECONDS, APPROVAL_SECONDS + EXTENSION_SECONDS)

    # ---- who may answer, and who may look

    def test_an_app_session_cannot_resolve_anything(self):
        agent = self.ready()
        asked = self.ask(agent, self.NOTE)
        refused = self.client.post(
            f"/api/desktops/{self.desktop}/approvals/{asked['requestId']}",
            headers=agent,
            json={"decision": "approve"},
        )
        self.assertIn(refused.status_code, (401, 403), refused.text)
        self.assertEqual(self.write(agent, self.NOTE).status_code, 202, "still waiting")

    def test_an_app_sees_only_its_own_question(self):
        # Both apps allowed from the start: changing the policy would revoke the
        # sessions this is about, which is a different test.
        self.policy(apps=["notes", "meals"])
        agent = self.agent_for("notes", "run-1")
        meals = self.agent_for("meals", "run-2")
        asked = self.ask(agent, self.NOTE)
        looked = self.client.get(f"/api/app/approvals/{asked['requestId']}", headers=meals)
        self.assertEqual(looked.status_code, 404, looked.text)

    def test_a_persons_own_change_never_becomes_a_question(self):
        self.ready()
        saved = self.write(self.notes, self.NOTE)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(
            self.client.get(f"/api/desktops/{self.desktop}/approvals", headers=self.hub).json()[
                "approvals"
            ],
            [],
        )

    # ---- what the summary is built from

    def test_the_summary_describes_the_request_and_not_the_secret_in_it(self):
        """A fixture app with no schema of its own, so the value can be anything.

        The prompt is read by a person and may be screenshotted or read aloud.
        What changed has to be describable; the secret that changed does not.
        """
        self.session("chat-fixture")
        self.policy(apps=["chat-fixture"])
        agent = self.agent_for("chat-fixture", "run-secret")
        asked = self.ask(agent, {"apiKey": "s3cret-value", "title": "Ordinary"})
        rendered = json.dumps(asked["summary"])
        self.assertNotIn("s3cret-value", rendered)
        self.assertIn("(hidden)", rendered)
        self.assertIn("Ordinary", rendered, "and what is safe to show is still shown")

    def test_the_digest_binds_the_request_that_was_described(self):
        agent = self.ready()
        asked = self.ask(agent, self.NOTE)
        expected = hashlib.sha256(
            json.dumps(self.NOTE, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(asked["requestDigest"], expected)


if __name__ == "__main__":
    unittest.main()
