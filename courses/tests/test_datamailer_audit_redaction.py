"""A send-audit row diagnoses a failure without freezing a member's identity.

The audit used to persist `str(exc)` and the Datamailer exchange verbatim, so a
row held the recipient address and, on a render, the whole `html_body` — which
carries Relay's per-recipient unsubscribe URL.  Django admin then rendered it.
These tests pin both halves of the fix: the row still says enough to work a
failure, and it no longer says who it was for.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from core.redaction import REDACTED
from course_management.datamailer.sync.audit_redaction import (
    audit_error_text,
    audit_response_payload,
    is_dry_run_exchange,
    recipient_fingerprint,
)

RECIPIENT = "learner@example.invalid"
UNSUBSCRIBE_URL = "https://relay.example.invalid/u/abc123token"


def _exchange(**overrides) -> dict:
    exchange = {
        "message": {"email": RECIPIENT, "template_key": "homework-confirmation"},
        "rendered": {
            "subject": "Saved",
            "html_body": f"<a href='{UNSUBSCRIBE_URL}'>Unsubscribe</a>",
            "text_body": f"Unsubscribe: {UNSUBSCRIBE_URL}",
        },
        "would_deliver": True,
    }
    exchange.update(overrides)
    return exchange


class AuditErrorTextTests(SimpleTestCase):
    def test_an_empty_error_stays_empty(self) -> None:
        self.assertEqual(audit_error_text(""), "")

    def test_the_diagnosable_part_of_a_failure_survives(self) -> None:
        error = (
            "HTTPSConnectionPool(host='relay.example.invalid', port=443): "
            "Read timed out. (read timeout=10)"
        )

        result = audit_error_text(error)

        self.assertIn("Read timed out", result)
        self.assertIn("read timeout=10", result)

    def test_an_address_or_url_in_an_error_is_masked(self) -> None:
        error = f"400 Client Error for url: https://relay/api/send?email={RECIPIENT}"

        result = audit_error_text(error)

        self.assertNotIn(RECIPIENT, result)
        self.assertNotIn("https://relay", result)
        self.assertIn("400 Client Error for url:", result)

    def test_the_same_failure_keeps_the_same_fingerprint(self) -> None:
        error = f"boom for {RECIPIENT}"

        first = audit_error_text(error)
        second = audit_error_text(error)

        self.assertEqual(first, second)
        self.assertNotEqual(first, audit_error_text("a different failure"))
        self.assertIn("error_fingerprint=", first)


class AuditResponsePayloadTests(SimpleTestCase):
    def test_a_delivering_send_keeps_neither_recipient_nor_body(self) -> None:
        stored = audit_response_payload({}, _exchange())

        serialized = repr(stored)
        self.assertNotIn(RECIPIENT, serialized)
        self.assertNotIn(UNSUBSCRIBE_URL, serialized)
        self.assertEqual(stored["rendered"]["html_body"], REDACTED)
        self.assertEqual(stored["message"]["email"], REDACTED)

    def test_the_template_key_and_outcome_still_survive(self) -> None:
        stored = audit_response_payload({}, _exchange())

        self.assertEqual(stored["message"]["template_key"], "homework-confirmation")
        self.assertEqual(stored["would_deliver"], True)

    def test_a_recipient_is_still_findable_by_fingerprint(self) -> None:
        stored = audit_response_payload({}, _exchange())

        self.assertEqual(
            stored["message"]["email_fingerprint"],
            recipient_fingerprint(RECIPIENT.upper()),
        )

    def test_a_dry_run_keeps_the_rendered_bodies_for_the_e2e_suite(self) -> None:
        stored = audit_response_payload({"dry_run": True}, _exchange())

        self.assertIn(UNSUBSCRIBE_URL, stored["rendered"]["html_body"])
        # The recipient is still not kept, dry run or not.
        self.assertEqual(stored["message"]["email"], REDACTED)

    def test_would_deliver_false_is_recognised_as_a_dry_run(self) -> None:
        self.assertTrue(is_dry_run_exchange({}, _exchange(would_deliver=False)))
        self.assertFalse(is_dry_run_exchange({}, _exchange()))

    def test_a_missing_response_stores_an_empty_object(self) -> None:
        self.assertEqual(audit_response_payload({}, None), {})
        self.assertEqual(audit_response_payload({}, "unexpected"), {})


class RecipientFingerprintTests(SimpleTestCase):
    def test_it_is_case_and_whitespace_insensitive(self) -> None:
        self.assertEqual(
            recipient_fingerprint(f"  {RECIPIENT.upper()} "),
            recipient_fingerprint(RECIPIENT),
        )

    def test_it_does_not_contain_the_address(self) -> None:
        fingerprint = recipient_fingerprint(RECIPIENT)

        self.assertNotIn(RECIPIENT, fingerprint)
        self.assertNotIn("example.invalid", fingerprint)

    def test_an_empty_address_has_no_fingerprint(self) -> None:
        self.assertEqual(recipient_fingerprint(""), "")
