"""Tests for the event-registrant identity consolidation and fact import.

Every address here is synthetic (``example.invalid``) -- no real registrant
export is read or copied in this suite.  Nothing here touches the filesystem
either: the domain takes already-parsed rows, so these tests hand it rows
directly.  The reader that turns a Luma export into those rows is tested in
``scripts/tests/test_luma_registrant_source.py``.
"""

from __future__ import annotations

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
    PendingEventRegistrants,
    RegistrantRow,
    RunReport,
    import_registrants,
)

PROVIDER = EventRegistration.Provider.LUMA


class RegistrantImportTestCase(TestCase):
    def setUp(self) -> None:
        # One counter per event id, so a test can assert that a completed or
        # identity-less event's rows are never asked for at all.
        self.read_calls: dict[str, int] = {}
        # The order rows were actually asked for, which is the order events were
        # imported in -- an EventRegistration's primary key is a UUID, so the
        # written rows themselves carry no insertion order to read back.
        self.read_order: list[str] = []
        self.pending: list[PendingEventRegistrants] = []

    def _row(
        self,
        *,
        guest_id: str,
        email: str | None,
        status: str = "approved",
        registered_at: str = "2026-01-01T00:00:00.000Z",
    ) -> RegistrantRow:
        return RegistrantRow(
            external_registrant_identifier=guest_id,
            normalized_email=email,
            status=status,
            registered_at_raw=registered_at,
        )

    def _add_event(self, *, event_id: str, rows: list[RegistrantRow]) -> None:
        def read_rows(event_id: str = event_id) -> tuple[RegistrantRow, ...]:
            self.read_calls[event_id] = self.read_calls.get(event_id, 0) + 1
            self.read_order.append(event_id)
            return tuple(rows)

        self.pending.append(
            PendingEventRegistrants(external_event_identifier=event_id, read_rows=read_rows)
        )

    def _run(self) -> RunReport:
        return import_registrants(provider=PROVIDER, pending=tuple(self.pending))

    def _mint_event(self, *, event_id: str, title: str) -> Event:
        return create_provider_event_identity(
            provider=PROVIDER, external_event_identifier=event_id, title=title
        )


class OrderingTests(RegistrantImportTestCase):
    def test_events_are_imported_in_the_order_the_reader_supplied(self) -> None:
        for event_id in ("evt-a", "evt-b", "evt-c"):
            self._mint_event(event_id=event_id, title=f"Event {event_id}")
            self._add_event(
                event_id=event_id,
                rows=[self._row(guest_id=f"g-{event_id}", email=f"{event_id}@example.invalid")],
            )

        report = self._run()

        self.assertEqual(report.events_total, 3)
        self.assertEqual(report.events_completed, 3)
        self.assertEqual(self.read_order, ["evt-a", "evt-b", "evt-c"])
        self.assertEqual(EventRegistration.objects.count(), 3)


class ConsolidationTests(RegistrantImportTestCase):
    """The core guarantee: never a duplicate profile for the same real person."""

    def test_registrant_matching_an_existing_account_attaches_to_it_not_a_new_identity(
        self,
    ) -> None:
        account = CustomUser.objects.create(
            username="existing-learner", email="learner@example.invalid"
        )
        self._mint_event(event_id="evt-1", title="Event One")
        self._add_event(
            event_id="evt-1",
            rows=[self._row(guest_id="g1", email="learner@example.invalid")],
        )

        report = self._run()

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
        self._add_event(
            event_id="evt-1", rows=[self._row(guest_id="g1", email="repeat@example.invalid")]
        )
        self._add_event(
            event_id="evt-2", rows=[self._row(guest_id="g2", email="repeat@example.invalid")]
        )

        report = self._run()

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
        self._add_event(
            event_id="evt-1", rows=[self._row(guest_id="g1", email="fresh@example.invalid")]
        )

        report = self._run()

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
            self._add_event(
                event_id=event_id,
                rows=[self._row(guest_id=f"g{index}", email="serial-attendee@example.invalid")],
            )

        report = self._run()

        self.assertEqual(report.new_identity_total, 1)
        self.assertEqual(report.matched_prior_identity_total, event_total - 1)
        self.assertEqual(EventRegistrantIdentity.objects.count(), 1)
        identity = EventRegistrantIdentity.objects.get()
        self.assertEqual(EventRegistration.objects.filter(identity=identity).count(), event_total)

    def test_a_row_with_no_normalized_email_is_skipped_not_written(self) -> None:
        self._mint_event(event_id="evt-1", title="Event One")
        self._add_event(
            event_id="evt-1",
            rows=[
                self._row(guest_id="g1", email=None),
                self._row(guest_id="g2", email="present@example.invalid"),
            ],
        )

        report = self._run()

        self.assertEqual(report.rows_skipped, 1)
        self.assertEqual(report.rows_written, 1)
        self.assertEqual(EventRegistration.objects.count(), 1)

    def test_declined_status_is_still_recorded_as_a_fact(self) -> None:
        self._mint_event(event_id="evt-1", title="Event One")
        self._add_event(
            event_id="evt-1",
            rows=[self._row(guest_id="g1", email="declined@example.invalid", status="declined")],
        )

        self._run()

        registration = EventRegistration.objects.get()
        self.assertEqual(registration.status, "declined")

    def test_registered_at_is_parsed_onto_the_fact(self) -> None:
        self._mint_event(event_id="evt-1", title="Event One")
        self._add_event(
            event_id="evt-1",
            rows=[
                self._row(
                    guest_id="g1",
                    email="timed@example.invalid",
                    registered_at="2026-02-03T04:05:06.000Z",
                )
            ],
        )

        self._run()

        registration = EventRegistration.objects.get()
        registered_at = registration.registered_at
        if registered_at is None:
            self.fail("the row's registered_at was not parsed onto the fact")
        self.assertEqual(registered_at.year, 2026)
        self.assertEqual(registered_at.month, 2)


class UnknownEventIdentityTests(RegistrantImportTestCase):
    def test_an_event_with_no_identity_yet_is_reported_not_created(self) -> None:
        """5.2 must have run first -- this module never mints an Event identity."""

        self._add_event(
            event_id="evt-undiscovered",
            rows=[self._row(guest_id="g1", email="someone@example.invalid")],
        )

        report = self._run()

        self.assertEqual(report.events_awaiting_identity, 1)
        self.assertIn("evt-undiscovered", report.awaiting_identity_events)
        self.assertEqual(EventRegistration.objects.count(), 0)
        self.assertEqual(EventRegistrantIdentity.objects.count(), 0)
        # This module never mints an identity itself -- confirmed narrowly
        # (not via a blanket "no Event exists" assertion, since the test
        # database's own reference-data fixture may legitimately seed
        # unrelated Event rows).
        self.assertFalse(Event.objects.filter(source_key="evt-undiscovered").exists())

    def test_an_event_with_no_identity_yet_never_has_its_rows_read(self) -> None:
        """No identity means no reason to open the export at all."""

        self._add_event(
            event_id="evt-undiscovered",
            rows=[self._row(guest_id="g1", email="someone@example.invalid")],
        )

        self._run()

        self.assertNotIn("evt-undiscovered", self.read_calls)


class IdempotencyTests(RegistrantImportTestCase):
    def test_a_second_run_writes_nothing_new(self) -> None:
        self._mint_event(event_id="evt-1", title="Event One")
        self._add_event(
            event_id="evt-1",
            rows=[
                self._row(guest_id="g1", email="repeat-run@example.invalid"),
                self._row(guest_id="g2", email="second@example.invalid"),
            ],
        )

        first = self._run()
        self.assertEqual(first.rows_written, 2)
        identity_count_after_first = EventRegistrantIdentity.objects.count()
        registration_count_after_first = EventRegistration.objects.count()

        second = self._run()

        self.assertEqual(second.events_already_completed, 1)
        self.assertEqual(second.events_completed, 0)
        self.assertEqual(second.new_identity_total, 0)
        self.assertEqual(second.matched_account_total, 0)
        self.assertEqual(second.matched_prior_identity_total, 0)
        self.assertEqual(EventRegistrantIdentity.objects.count(), identity_count_after_first)
        self.assertEqual(EventRegistration.objects.count(), registration_count_after_first)

    def test_a_completed_event_is_skipped_without_reading_its_rows_again(self) -> None:
        """The resume guarantee: a finished event's file is never reopened."""

        self._mint_event(event_id="evt-1", title="Event One")
        self._add_event(
            event_id="evt-1", rows=[self._row(guest_id="g1", email="once@example.invalid")]
        )

        self._run()
        self.assertEqual(self.read_calls["evt-1"], 1)

        self._run()

        self.assertEqual(self.read_calls["evt-1"], 1)

    def test_an_interrupted_event_is_retried_whole_on_the_next_run(self) -> None:
        """Progress is only recorded inside the event's own transaction."""

        self._mint_event(event_id="evt-1", title="Event One")
        rows = [
            self._row(guest_id="g1", email="a@example.invalid"),
            self._row(guest_id="g2", email="b@example.invalid"),
        ]
        # An interrupted run leaves the progress row created-but-unfinished,
        # exactly as get_or_create leaves it before the transaction commits.
        EventRegistrantImportProgress.objects.create(
            provider=PROVIDER, external_event_identifier="evt-1"
        )
        self._add_event(event_id="evt-1", rows=rows)

        report = self._run()

        self.assertEqual(self.read_calls["evt-1"], 1)
        self.assertEqual(report.events_completed, 1)
        self.assertEqual(report.rows_written, 2)
        self.assertEqual(EventRegistration.objects.count(), 2)

    def test_progress_row_records_completion_per_event(self) -> None:
        self._mint_event(event_id="evt-1", title="Event One")
        self._add_event(
            event_id="evt-1", rows=[self._row(guest_id="g1", email="p@example.invalid")]
        )

        self._run()

        progress = EventRegistrantImportProgress.objects.get(
            provider=PROVIDER, external_event_identifier="evt-1"
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
