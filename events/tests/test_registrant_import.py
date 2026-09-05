"""Tests for the event-registrant identity consolidation and fact import.

Every address here is synthetic (``example.invalid``) -- no real registrant
export is read or copied in this suite.
"""

from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError
from django.test import TestCase

from accounts.models import CustomUser
from events.identity import create_provider_event_identity
from events.models import (
    Event,
    EventRegistrantIdentity,
    EventRegistrantImportProgress,
    EventRegistration,
)
from events.registrant_import import (
    RegistrantImportError,
    discover_luma_registrant_files,
    import_luma_registrants,
    read_luma_registrant_rows,
)

_COLUMNS = (
    "guest_id",
    "user_id",
    "email",
    "first_name",
    "last_name",
    "name",
    "phone_number",
    "company",
    "job_title",
    "approval_status",
    "registered_at",
    "utm_source",
    "event_id",
    "event_name",
    "event_start_at",
)


class RegistrantImportTestCase(TestCase):
    def setUp(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def _write_event(
        self,
        *,
        stem: str,
        event_id: str,
        rows: list[dict[str, str]],
    ) -> None:
        (self.root / f"{stem}.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "event_id": event_id,
                    "event_url": f"https://luma.test/{stem}",
                }
            ),
            encoding="utf-8",
        )
        with (self.root / f"{stem}.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=_COLUMNS)
            writer.writeheader()
            for row in rows:
                full = dict.fromkeys(_COLUMNS, "")
                full.update(row)
                full["event_id"] = event_id
                writer.writerow(full)

    def _row(
        self,
        *,
        guest_id: str,
        email: str,
        status: str = "approved",
        registered_at: str = "2026-01-01T00:00:00.000Z",
    ) -> dict[str, str]:
        return {
            "guest_id": guest_id,
            "email": email,
            "approval_status": status,
            "registered_at": registered_at,
        }

    def _mint_event(self, *, event_id: str, title: str) -> Event:
        return create_provider_event_identity(
            provider="luma", external_event_identifier=event_id, title=title
        )


class DiscoveryTests(RegistrantImportTestCase):
    def test_pairs_csv_and_json_by_stem_sorted(self) -> None:
        self._write_event(stem="b-event", event_id="evt-b", rows=[])
        self._write_event(stem="a-event", event_id="evt-a", rows=[])

        discovered = discover_luma_registrant_files(self.root)

        self.assertEqual(
            [item.external_event_identifier for item in discovered], ["evt-a", "evt-b"]
        )

    def test_mismatched_pair_refuses(self) -> None:
        self._write_event(stem="solo", event_id="evt-solo", rows=[])
        (self.root / "orphan.csv").write_text("event_id\n", encoding="utf-8")

        with self.assertRaises(RegistrantImportError):
            discover_luma_registrant_files(self.root)


class RowReadingTests(RegistrantImportTestCase):
    def test_reads_normalized_email_status_and_registered_at(self) -> None:
        self._write_event(
            stem="one",
            event_id="evt-one",
            rows=[self._row(guest_id="g1", email="  Person@Example.INVALID  ")],
        )
        rows = read_luma_registrant_rows(self.root / "one.csv", external_event_identifier="evt-one")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].normalized_email, "person@example.invalid")
        self.assertEqual(rows[0].status, "approved")
        self.assertEqual(rows[0].external_registrant_identifier, "g1")

    def test_duplicate_guest_id_keeps_first_row_only(self) -> None:
        self._write_event(
            stem="dupe",
            event_id="evt-dupe",
            rows=[
                self._row(guest_id="g1", email="first@example.invalid"),
                self._row(guest_id="g1", email="second@example.invalid"),
            ],
        )
        rows = read_luma_registrant_rows(
            self.root / "dupe.csv", external_event_identifier="evt-dupe"
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].normalized_email, "first@example.invalid")

    def test_missing_guest_id_refuses(self) -> None:
        self._write_event(
            stem="bad", event_id="evt-bad", rows=[self._row(guest_id="", email="a@example.invalid")]
        )
        with self.assertRaises(RegistrantImportError):
            read_luma_registrant_rows(self.root / "bad.csv", external_event_identifier="evt-bad")

    def test_blank_email_is_read_as_no_normalized_email_not_an_error(self) -> None:
        self._write_event(
            stem="blank", event_id="evt-blank", rows=[self._row(guest_id="g1", email="")]
        )
        rows = read_luma_registrant_rows(
            self.root / "blank.csv", external_event_identifier="evt-blank"
        )
        self.assertIsNone(rows[0].normalized_email)


class ConsolidationTests(RegistrantImportTestCase):
    """The core guarantee: never a duplicate profile for the same real person."""

    def test_registrant_matching_an_existing_account_attaches_to_it_not_a_new_identity(
        self,
    ) -> None:
        account = CustomUser.objects.create(
            username="existing-learner", email="learner@example.invalid"
        )
        self._mint_event(event_id="evt-1", title="Event One")
        self._write_event(
            stem="e1",
            event_id="evt-1",
            rows=[self._row(guest_id="g1", email="learner@example.invalid")],
        )

        report = import_luma_registrants(self.root)

        self.assertEqual(report.matched_account_total, 1)
        self.assertEqual(report.new_identity_total, 0)
        self.assertEqual(report.matched_prior_identity_total, 0)
        identity = EventRegistrantIdentity.objects.get()
        self.assertEqual(identity.account_id, account.pk)
        self.assertIsNone(identity.normalized_email)
        self.assertEqual(EventRegistrantIdentity.objects.count(), 1)

    def test_registrant_matching_a_prior_registrant_only_identity_reuses_it_across_events(
        self,
    ) -> None:
        self._mint_event(event_id="evt-1", title="Event One")
        self._mint_event(event_id="evt-2", title="Event Two")
        self._write_event(
            stem="e1",
            event_id="evt-1",
            rows=[self._row(guest_id="g1", email="repeat@example.invalid")],
        )
        self._write_event(
            stem="e2",
            event_id="evt-2",
            rows=[self._row(guest_id="g2", email="repeat@example.invalid")],
        )

        report = import_luma_registrants(self.root)

        self.assertEqual(report.new_identity_total, 1)
        self.assertEqual(report.matched_prior_identity_total, 1)
        self.assertEqual(report.matched_account_total, 0)
        # One real person, one identity row, two registration facts.
        self.assertEqual(EventRegistrantIdentity.objects.count(), 1)
        identity = EventRegistrantIdentity.objects.get()
        self.assertIsNone(identity.account_id)
        self.assertEqual(identity.normalized_email, "repeat@example.invalid")
        self.assertEqual(EventRegistration.objects.filter(identity=identity).count(), 2)

    def test_registrant_matching_nothing_creates_exactly_one_new_identity(self) -> None:
        self._mint_event(event_id="evt-1", title="Event One")
        self._write_event(
            stem="e1",
            event_id="evt-1",
            rows=[self._row(guest_id="g1", email="fresh@example.invalid")],
        )

        report = import_luma_registrants(self.root)

        self.assertEqual(report.new_identity_total, 1)
        identity = EventRegistrantIdentity.objects.get()
        self.assertIsNone(identity.account_id)
        self.assertEqual(identity.normalized_email, "fresh@example.invalid")

    def test_never_creates_two_identities_for_one_email_across_many_events(self) -> None:
        """The scale property, not just the two-event case above."""

        event_total = 12
        for index in range(event_total):
            event_id = f"evt-{index}"
            self._mint_event(event_id=event_id, title=f"Event {index}")
            self._write_event(
                stem=f"e{index}",
                event_id=event_id,
                rows=[self._row(guest_id=f"g{index}", email="serial-attendee@example.invalid")],
            )

        report = import_luma_registrants(self.root)

        self.assertEqual(report.new_identity_total, 1)
        self.assertEqual(report.matched_prior_identity_total, event_total - 1)
        self.assertEqual(EventRegistrantIdentity.objects.count(), 1)
        identity = EventRegistrantIdentity.objects.get()
        self.assertEqual(EventRegistration.objects.filter(identity=identity).count(), event_total)

    def test_declined_status_is_still_recorded_as_a_fact(self) -> None:
        self._mint_event(event_id="evt-1", title="Event One")
        self._write_event(
            stem="e1",
            event_id="evt-1",
            rows=[self._row(guest_id="g1", email="declined@example.invalid", status="declined")],
        )

        import_luma_registrants(self.root)

        registration = EventRegistration.objects.get()
        self.assertEqual(registration.status, "declined")

    def test_registered_at_is_parsed_onto_the_fact(self) -> None:
        self._mint_event(event_id="evt-1", title="Event One")
        self._write_event(
            stem="e1",
            event_id="evt-1",
            rows=[
                self._row(
                    guest_id="g1",
                    email="timed@example.invalid",
                    registered_at="2026-02-03T04:05:06.000Z",
                )
            ],
        )

        import_luma_registrants(self.root)

        registration = EventRegistration.objects.get()
        registered_at = registration.registered_at
        if registered_at is None:
            self.fail("the row's registered_at was not parsed onto the fact")
        self.assertEqual(registered_at.year, 2026)
        self.assertEqual(registered_at.month, 2)


class UnknownEventIdentityTests(RegistrantImportTestCase):
    def test_an_event_with_no_identity_yet_is_reported_not_created(self) -> None:
        """5.2 must have run first -- this module never mints an Event identity."""

        self._write_event(
            stem="undiscovered",
            event_id="evt-undiscovered",
            rows=[self._row(guest_id="g1", email="someone@example.invalid")],
        )

        report = import_luma_registrants(self.root)

        self.assertEqual(report.events_awaiting_identity, 1)
        self.assertIn("evt-undiscovered", report.awaiting_identity_events)
        self.assertEqual(EventRegistration.objects.count(), 0)
        self.assertEqual(EventRegistrantIdentity.objects.count(), 0)
        # This module never mints an identity itself -- confirmed narrowly
        # (not via a blanket "no Event exists" assertion, since the test
        # database's own reference-data fixture may legitimately seed
        # unrelated Event rows).
        self.assertFalse(Event.objects.filter(source_key="evt-undiscovered").exists())


class IdempotencyTests(RegistrantImportTestCase):
    def test_a_second_run_writes_nothing_new(self) -> None:
        self._mint_event(event_id="evt-1", title="Event One")
        self._write_event(
            stem="e1",
            event_id="evt-1",
            rows=[
                self._row(guest_id="g1", email="repeat-run@example.invalid"),
                self._row(guest_id="g2", email="second@example.invalid"),
            ],
        )

        first = import_luma_registrants(self.root)
        self.assertEqual(first.rows_written, 2)
        identity_count_after_first = EventRegistrantIdentity.objects.count()
        registration_count_after_first = EventRegistration.objects.count()

        second = import_luma_registrants(self.root)

        self.assertEqual(second.events_already_completed, 1)
        self.assertEqual(second.events_completed, 0)
        self.assertEqual(second.new_identity_total, 0)
        self.assertEqual(second.matched_account_total, 0)
        self.assertEqual(second.matched_prior_identity_total, 0)
        self.assertEqual(EventRegistrantIdentity.objects.count(), identity_count_after_first)
        self.assertEqual(EventRegistration.objects.count(), registration_count_after_first)

    def test_progress_row_records_completion_per_event(self) -> None:
        self._mint_event(event_id="evt-1", title="Event One")
        self._write_event(
            stem="e1", event_id="evt-1", rows=[self._row(guest_id="g1", email="p@example.invalid")]
        )

        import_luma_registrants(self.root)

        progress = EventRegistrantImportProgress.objects.get(
            provider="luma", external_event_identifier="evt-1"
        )
        self.assertTrue(progress.completed)
        self.assertEqual(progress.rows_written, 1)


class ModelConstraintTests(TestCase):
    def test_an_identity_cannot_have_both_an_account_and_a_normalized_email(self) -> None:
        account = CustomUser.objects.create(username="both", email="both@example.invalid")
        with self.assertRaises(IntegrityError):
            EventRegistrantIdentity.objects.create(
                account=account, normalized_email="both@example.invalid"
            )

    def test_an_identity_must_have_at_least_one_anchor(self) -> None:
        with self.assertRaises(IntegrityError):
            EventRegistrantIdentity.objects.create()

    def test_two_registrant_only_identities_cannot_share_an_email(self) -> None:
        EventRegistrantIdentity.objects.create(normalized_email="shared@example.invalid")
        with self.assertRaises(IntegrityError):
            EventRegistrantIdentity.objects.create(normalized_email="shared@example.invalid")
