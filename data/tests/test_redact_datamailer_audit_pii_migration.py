"""The backfill in ``data/migrations/0002_redact_datamailer_audit_pii.py``.

`e67948c` made ``DatamailerSendAudit``/``DatamailerOutboxEvent`` redact
``response_payload`` and ``error``/``last_error`` on write, but left rows
written before that landed holding whatever was frozen at the time. These
tests build synthetic, clearly-fake rows in that pre-fix shape, run the
backfill migration's forward function directly, and check the result matches
what a fresh write through ``audit_response_payload``/``audit_error_text``
would have produced -- plus that running it again changes nothing.

All addresses, tokens and URLs used here are made up for this test file:
``example.invalid`` is the RFC 2606 reserved domain for exactly this purpose,
never a real inbox.
"""

from __future__ import annotations

import importlib

from django.apps import apps as django_apps
from django.test import TestCase

from course_management.datamailer.sync.audit_redaction import (
    audit_error_text,
    audit_response_payload,
    recipient_fingerprint,
)
from data.models import DatamailerOutboxEvent, DatamailerSendAudit

_migration = importlib.import_module(
    "data.migrations.0002_redact_datamailer_audit_pii"
)
redact_datamailer_audit_rows = _migration.redact_datamailer_audit_rows

FAKE_RECIPIENT = "fake.learner@example.invalid"
FAKE_UNSUBSCRIBE_URL = "https://relay.example.invalid/u/fake-token-000111"


def _raw_exchange(**overrides) -> dict:
    exchange = {
        "message": {"email": FAKE_RECIPIENT, "template_key": "welcome"},
        "would_deliver": True,
    }
    exchange.update(overrides)
    return exchange


def _raw_dry_run_exchange() -> dict:
    return {
        "dry_run": True,
        "would_deliver": False,
        "message": {"email": FAKE_RECIPIENT, "template_key": "welcome"},
        "rendered": {
            "subject": "Welcome",
            "html_body": f"<a href='{FAKE_UNSUBSCRIBE_URL}'>Unsubscribe</a>",
        },
    }


def _raw_error() -> str:
    return (
        f"400 Client Error for url: https://relay.example.invalid/api/send"
        f"?email={FAKE_RECIPIENT}"
    )


def _run_migration() -> None:
    redact_datamailer_audit_rows(django_apps, None)


class EmptyDatabaseTests(TestCase):
    def test_running_against_an_empty_database_does_not_error(self) -> None:
        self.assertEqual(DatamailerSendAudit.objects.count(), 0)
        self.assertEqual(DatamailerOutboxEvent.objects.count(), 0)

        _run_migration()

        self.assertEqual(DatamailerSendAudit.objects.count(), 0)
        self.assertEqual(DatamailerOutboxEvent.objects.count(), 0)


class SendAuditBackfillTests(TestCase):
    def _create_pre_fix_row(self, **overrides) -> DatamailerSendAudit:
        defaults = {
            "send_type": "transactional",
            "status": "failed",
            "idempotency_key": "pre-fix-audit-1",
            "response_payload": _raw_exchange(),
            "error": _raw_error(),
        }
        defaults.update(overrides)
        return DatamailerSendAudit.objects.create(**defaults)

    def test_a_pre_fix_row_ends_up_shaped_like_a_fresh_write(self) -> None:
        raw_response = _raw_exchange()
        raw_error = _raw_error()
        row = self._create_pre_fix_row(response_payload=raw_response, error=raw_error)

        _run_migration()
        row.refresh_from_db()

        expected_response = audit_response_payload({}, raw_response)
        expected_error = audit_error_text(raw_error)

        self.assertEqual(row.response_payload, expected_response)
        self.assertEqual(row.error, expected_error)
        self.assertEqual(
            row.response_payload["message"]["email_fingerprint"],
            recipient_fingerprint(FAKE_RECIPIENT),
        )
        serialized = repr(row.response_payload) + row.error
        self.assertNotIn(FAKE_RECIPIENT, serialized)

    def test_running_it_twice_changes_nothing_the_second_time(self) -> None:
        row = self._create_pre_fix_row()

        _run_migration()
        row.refresh_from_db()
        after_first_run = (row.response_payload, row.error)

        _run_migration()
        row.refresh_from_db()
        after_second_run = (row.response_payload, row.error)

        self.assertEqual(after_first_run, after_second_run)

    def test_a_row_with_no_error_and_empty_payload_is_left_alone(self) -> None:
        row = DatamailerSendAudit.objects.create(
            send_type="transactional",
            status="succeeded",
            idempotency_key="pre-fix-audit-empty",
            response_payload={},
            error="",
        )

        _run_migration()
        row.refresh_from_db()

        self.assertEqual(row.response_payload, {})
        self.assertEqual(row.error, "")

    def test_a_benign_error_with_no_sensitive_content_is_untouched(self) -> None:
        row = self._create_pre_fix_row(
            response_payload={}, error="Datamailer is not configured"
        )

        _run_migration()
        row.refresh_from_db()

        self.assertEqual(row.error, "Datamailer is not configured")

    def test_an_already_redacted_row_is_left_exactly_as_is(self) -> None:
        raw_response = _raw_exchange()
        raw_error = _raw_error()
        already_redacted_response = audit_response_payload({}, raw_response)
        already_redacted_error = audit_error_text(raw_error)
        row = self._create_pre_fix_row(
            response_payload=already_redacted_response,
            error=already_redacted_error,
        )

        _run_migration()
        row.refresh_from_db()

        self.assertEqual(row.response_payload, already_redacted_response)
        self.assertEqual(row.error, already_redacted_error)


class OutboxEventBackfillTests(TestCase):
    def _create_pre_fix_row(self, **overrides) -> DatamailerOutboxEvent:
        defaults = {
            "event_id": "pre-fix-outbox-1",
            "event_type": "recipient_list.member_upsert",
            "idempotency_key": "pre-fix-outbox-1:idempotency",
            "ordering_key": "user:1",
            "payload": {"email": FAKE_RECIPIENT, "list_key": "ml-zoomcamp-2026"},
            "response_payload": _raw_exchange(),
            "last_error": _raw_error(),
        }
        defaults.update(overrides)
        return DatamailerOutboxEvent.objects.create(**defaults)

    def test_a_pre_fix_row_ends_up_shaped_like_a_fresh_write(self) -> None:
        raw_payload = {"email": FAKE_RECIPIENT, "list_key": "ml-zoomcamp-2026"}
        raw_response = _raw_exchange()
        raw_error = _raw_error()
        row = self._create_pre_fix_row(
            payload=raw_payload,
            response_payload=raw_response,
            last_error=raw_error,
        )

        _run_migration()
        row.refresh_from_db()

        expected_response = audit_response_payload(raw_payload, raw_response)
        expected_error = audit_error_text(raw_error)

        self.assertEqual(row.response_payload, expected_response)
        self.assertEqual(row.last_error, expected_error)

    def test_the_delivery_payload_field_is_never_modified(self) -> None:
        raw_payload = {"email": FAKE_RECIPIENT, "list_key": "ml-zoomcamp-2026"}
        row = self._create_pre_fix_row(payload=raw_payload)

        _run_migration()
        row.refresh_from_db()

        # `payload` is the delivery instruction: it must still hold the real
        # address after the backfill, unchanged, exactly as it was excluded
        # from redaction on write.
        self.assertEqual(row.payload, raw_payload)

    def test_a_genuine_dry_run_row_keeps_its_rendered_body(self) -> None:
        raw_payload = {"dry_run": True, "email": FAKE_RECIPIENT}
        raw_response = _raw_dry_run_exchange()
        row = self._create_pre_fix_row(
            payload=raw_payload,
            response_payload=raw_response,
        )

        _run_migration()
        row.refresh_from_db()

        self.assertIn(
            FAKE_UNSUBSCRIBE_URL, row.response_payload["rendered"]["html_body"]
        )
        # The recipient itself is still redacted, dry run or not.
        self.assertEqual(row.response_payload["message"]["email"], "[REDACTED]")

    def test_running_it_twice_changes_nothing_the_second_time(self) -> None:
        row = self._create_pre_fix_row()

        _run_migration()
        row.refresh_from_db()
        after_first_run = (
            row.payload,
            row.response_payload,
            row.last_error,
        )

        _run_migration()
        row.refresh_from_db()
        after_second_run = (
            row.payload,
            row.response_payload,
            row.last_error,
        )

        self.assertEqual(after_first_run, after_second_run)

    def test_a_dry_run_row_stays_stable_on_a_second_pass(self) -> None:
        """The trickiest idempotency case: the rendered body legitimately
        keeps content that looks sensitive (an unsubscribe URL) forever, so
        the second pass has to recognise the row is already done rather than
        looping on it."""

        row = self._create_pre_fix_row(
            payload={"dry_run": True, "email": FAKE_RECIPIENT},
            response_payload=_raw_dry_run_exchange(),
        )

        _run_migration()
        row.refresh_from_db()
        after_first_run = row.response_payload

        _run_migration()
        row.refresh_from_db()

        self.assertEqual(row.response_payload, after_first_run)
