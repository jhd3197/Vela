"""What an agent may cause on a website, decided before anything is sent.

The decision is a pure function of a described request and a site rule, which is
why almost all of this needs no browser: a classification that is only correct
inside a running Chromium is a classification nobody can check.

The claims being made here are narrow on purpose:

- A GET is treated as a read. Not because a GET cannot change anything — it can,
  on plenty of sites — but because that is the one Vela lets an agent issue
  without describing it first.
- Anything else is described or it does not happen. A body Vela cannot read the
  field names of is not summarized incorrectly; it becomes "a person has to do
  this".
- An approval is bound to the exact request. Change the address, the method, a
  field name or a byte of the body and the previous answer does not apply.
"""

import unittest

from vela.desktops import site_policy
from vela.desktops.models import DesktopError
from vela.desktops.policy import allows_site, site_rule, validate_policy


def described(**overrides):
    request = {
        "method": "POST",
        "url": "https://example.com/orders/new?ref=1",
        "resourceType": "document",
        "navigation": True,
        "contentType": "application/x-www-form-urlencoded",
        "fields": ["item", "quantity"],
        "bodyBytes": 24,
        "bodyDigest": "a" * 64,
        "bodyAvailable": True,
    }
    request.update(overrides)
    return request


class PolicyDocumentTests(unittest.TestCase):
    def test_a_site_rule_keeps_the_effect_mode_it_was_given(self):
        policy = validate_policy({"sites": [{"origin": "https://example.com", "effects": "ask"}]})
        self.assertEqual(policy["sites"][0]["effects"], "ask")

    def test_a_site_rule_with_no_effect_mode_is_read_only(self):
        policy = validate_policy({"sites": ["https://example.com"]})
        self.assertEqual(policy["sites"][0]["effects"], "read")

    def test_an_effect_mode_nobody_defined_is_refused_rather_than_guessed(self):
        with self.assertRaises(DesktopError):
            validate_policy({"sites": [{"origin": "https://example.com", "effects": "always"}]})

    def test_a_rule_stored_before_effects_existed_reads_as_read_only(self):
        # The direction a missing setting defaults in is the whole question.
        self.assertEqual(site_policy.effects_mode({"origin": "https://example.com"}), "read")
        self.assertEqual(site_policy.effects_mode(None), "read")

    def test_remembering_sessions_is_off_unless_it_was_asked_for(self):
        self.assertFalse(validate_policy({})["rememberSessions"])
        self.assertTrue(validate_policy({"rememberSessions": True})["rememberSessions"])

    def test_the_rule_an_origin_matched_is_the_one_the_effect_is_decided_from(self):
        policy = validate_policy(
            {
                "sites": [
                    {"origin": "https://example.com", "effects": "ask"},
                    {"origin": "https://docs.test", "includeSubdomains": True},
                ]
            }
        )
        self.assertEqual(site_rule(policy, "https://example.com")["effects"], "ask")
        self.assertEqual(site_rule(policy, "https://api.docs.test")["effects"], "read")
        self.assertIsNone(site_rule(policy, "https://elsewhere.test"))
        self.assertTrue(allows_site(policy, "https://example.com"))
        self.assertFalse(allows_site(policy, "https://example.com.evil.test"))


class ClassificationTests(unittest.TestCase):
    def test_a_get_is_a_read(self):
        for method in ("GET", "HEAD", "OPTIONS"):
            self.assertEqual(site_policy.classify(described(method=method)).kind, "read")

    def test_a_form_post_is_a_submission_with_its_field_names(self):
        effect = site_policy.classify(described())
        self.assertEqual(effect.kind, "submission")
        self.assertEqual(effect.method, "POST")
        self.assertEqual(effect.origin, "https://example.com")
        self.assertEqual(effect.path, "/orders/new")
        self.assertEqual(effect.fields, ["item", "quantity"])

    def test_a_file_upload_is_described_as_one_rather_than_as_an_empty_form(self):
        # The browser holds a file body as a stream and hands the worker nothing.
        # "No fields" and "fields Vela cannot see" are different, and only one of
        # them is safe to put in a prompt.
        effect = site_policy.classify(
            described(
                contentType="multipart/form-data; boundary=x",
                fields=[],
                bodyBytes=0,
                bodyDigest="",
                bodyAvailable=False,
            )
        )
        self.assertEqual(effect.kind, "submission")
        self.assertFalse(effect.readable)
        detail = " ".join(site_policy.summarize(effect)["detail"])
        self.assertIn("sending a file", detail)
        self.assertIn("cannot read", detail)

    def test_an_unreadable_body_that_is_not_an_upload_is_not_described(self):
        effect = site_policy.classify(
            described(contentType="application/json", bodyAvailable=False)
        )
        self.assertEqual(effect.kind, "unsupported")

    def test_a_request_shape_vela_cannot_read_is_not_summarized(self):
        for overrides in (
            {"resourceType": "ping"},
            {"resourceType": "websocket"},
            {"contentType": "application/octet-stream"},
            {"bodyBytes": site_policy.MAX_DESCRIBED_BODY + 1},
        ):
            effect = site_policy.classify(described(**overrides))
            self.assertEqual(effect.kind, "unsupported", overrides)
            self.assertTrue(effect.reason)

    def test_the_prompt_describes_the_shape_and_never_the_values(self):
        summary = site_policy.summarize(site_policy.classify(described()))
        text = summary["headline"] + " " + " ".join(summary["detail"])
        self.assertIn("example.com", text)
        self.assertIn("POST /orders/new", text)
        self.assertIn("item", text)
        # Nothing here claims to know what the site will do with it.
        self.assertFalse(summary["complete"])


class DecisionTests(unittest.TestCase):
    READ = {"origin": "https://example.com", "effects": "read"}
    ASK = {"origin": "https://example.com", "effects": "ask"}

    def decide(self, rule, **overrides):
        return site_policy.decide(rule, site_policy.classify(described(**overrides)))

    def test_reading_is_allowed_under_either_rule(self):
        self.assertEqual(self.decide(self.READ, method="GET"), "allow")
        self.assertEqual(self.decide(self.ASK, method="GET"), "allow")

    def test_a_submission_to_a_read_only_site_needs_a_person(self):
        self.assertEqual(self.decide(self.READ), "person")

    def test_a_submission_to_an_ask_site_becomes_a_question(self):
        self.assertEqual(self.decide(self.ASK), "ask")

    def test_something_unclassifiable_needs_a_person_even_where_changes_are_asked_about(self):
        # This is the line that matters: "ask" is permission to be asked about
        # described requests, not permission to be asked about anything.
        self.assertEqual(self.decide(self.ASK, resourceType="ping"), "person")
        self.assertEqual(self.decide(self.ASK, contentType="application/x-protobuf"), "person")


class BindingTests(unittest.TestCase):
    def test_the_same_request_has_the_same_digest(self):
        self.assertEqual(
            site_policy.digest(described()), site_policy.digest(described())
        )

    def test_changing_anything_that_matters_changes_the_digest(self):
        base = site_policy.digest(described())
        for overrides in (
            {"method": "PUT"},
            {"url": "https://example.com/orders/new?ref=2"},
            {"url": "https://example.com/orders/other"},
            {"contentType": "application/json"},
            {"fields": ["item", "quantity", "address"]},
            {"bodyDigest": "b" * 64},
            {"bodyAvailable": False},
        ):
            self.assertNotEqual(base, site_policy.digest(described(**overrides)), overrides)

    def test_the_order_fields_arrive_in_does_not_change_the_digest(self):
        self.assertEqual(
            site_policy.digest(described(fields=["item", "quantity"])),
            site_policy.digest(described(fields=["quantity", "item"])),
        )

    def test_a_site_grant_stops_matching_when_the_policy_changes(self):
        rule = {"origin": "https://example.com", "effects": "ask"}
        first = site_policy.contract(rule, 3)
        self.assertEqual(first, site_policy.contract(dict(rule), 3))
        self.assertNotEqual(first, site_policy.contract(rule, 4))
        self.assertNotEqual(first, site_policy.contract({**rule, "effects": "read"}, 3))
        self.assertNotEqual(
            first, site_policy.contract({**rule, "includeSubdomains": True}, 3)
        )

    def test_a_site_cannot_be_mistaken_for_an_installed_app(self):
        # App ids are lower-case letters, digits and hyphens. A colon is the one
        # character that cannot appear in one.
        self.assertIn(":", site_policy.principal("https://example.com"))


if __name__ == "__main__":
    unittest.main()
