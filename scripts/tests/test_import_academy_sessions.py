"""The free Academy sessions become ordinary events, and their signups move with them."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from community_base.events.models import Event, EventRegistration
from django.core import mail
from django.test import TestCase

import scripts.prod
from academy.tests import content as fixture
from academy.tests.test_academy import _clear_academy
from events.identity import create_event_identity
from scripts.prod import import_academy_sessions as entry_point
from test_support.event_pictures import TEST_EVENT_PICTURE

AHEAD = datetime(2030, 1, 7, 16, tzinfo=UTC)
PAST = datetime(2020, 1, 7, 16, tzinfo=UTC)
ACADEMY_LINK = {
    "label": "Event link",
    "url": "https://datatalks.club/academy/sessions/?utm_source=dtc_site#register",
}
SLIDES = {"label": "Slides", "url": "https://example.invalid/slides"}


def _event(number: int, *, starts_at: datetime = AHEAD, title: str = "") -> Event:
    event = create_event_identity(
        picture=TEST_EVENT_PICTURE,
        title=title or f"Synthetic Series #{number}: Session {number}",
        event_type="webinar",
        speakers=[("janedoe", "Jane Doe")],
        description="What the session is about.",
        starts_at=starts_at,
    )
    Event.objects.filter(pk=event.pk).update(materials=[ACADEMY_LINK, SLIDES])
    event.refresh_from_db()
    return event


class AcademySessionsImportTests(TestCase):
    def setUp(self) -> None:
        _clear_academy()
        fixture.publish()
        self.one = _event(1, starts_at=PAST)
        self.two = _event(2)
        self.three = _event(3)
        self.directory = Path(tempfile.mkdtemp())

    def _signups(self, rows: list[dict]) -> Path:
        path = self.directory / "signups.json"
        path.write_text(json.dumps(rows), encoding="utf-8")
        return path

    def test_a_dry_run_writes_nothing(self) -> None:
        report = entry_point.run(signups=None, apply=False)

        self.assertFalse(report.applied)
        self.assertEqual(
            sorted(report.events_updated),
            sorted([self.one.public_id, self.two.public_id, self.three.public_id]),
        )
        self.two.refresh_from_db()
        self.assertIsNone(self.two.event_series_id)
        self.assertEqual(self.two.zoom_join_url, "")
        self.assertEqual(self.two.materials, [ACADEMY_LINK, SLIDES])

    def test_each_session_becomes_a_zoom_event_in_the_series(self) -> None:
        report = entry_point.run(signups=None, apply=True)

        self.assertTrue(report.series_created)
        self.assertEqual(report.unresolved_sessions, {})
        for number, event in ((1, self.one), (2, self.two), (3, self.three)):
            event.refresh_from_db()
            self.assertEqual(event.event_series.slug, "synthetic-series")
            self.assertEqual(event.series_position, number)
            self.assertEqual(event.platform, "zoom")
            self.assertEqual(event.zoom_join_url, f"https://example.invalid/zoom/{number}")
            # The retired signup link goes; every other material stays.
            self.assertEqual(event.materials, [SLIDES])
        # The second run finds everything in place.
        again = entry_point.run(signups=None, apply=True)
        self.assertFalse(again.series_created)
        self.assertEqual(again.events_linked, [])
        self.assertEqual(again.events_updated, [])
        self.assertEqual(len(again.events_unchanged), 3)

    def test_an_ambiguous_or_missing_event_is_reported_never_guessed(self) -> None:
        _event(2, title="Synthetic Series #2: A second candidate")
        self.three.title = "Something else entirely"
        self.three.save(update_fields=["title"])

        report = entry_point.run(signups=None, apply=True)

        self.assertEqual(
            report.unresolved_sessions,
            {
                "session-2": "several_events_with_that_title",
                "session-3": "no_event_with_that_title",
            },
        )
        self.three.refresh_from_db()
        self.assertIsNone(self.three.event_series_id)

    def test_confirmed_signups_become_confirmed_seats_for_every_session(self) -> None:
        signups = self._signups(
            [
                {
                    "email": "Ada@Example.invalid",
                    "name": "Ada",
                    "sessions": ["session-1", "session-2", "session-3", "session-9"],
                    "confirmed": True,
                    "created_at": "2026-11-14T10:00:00Z",
                    "consent_version": "2026-11-13.v1",
                    "academy_optin": "yes",
                    "track_interest": "both",
                    "free_text": "never read",
                },
                {"email": "pending@example.invalid", "sessions": ["session-2"]},
                {"email": "waitlist@example.invalid", "sessions": [], "confirmed": True},
                {"email": "not-an-address", "sessions": ["session-2"], "status": "confirmed"},
                {
                    "email": "grace@example.invalid",
                    "sessions": "session-3",
                    "confirmed_at": 1763114400,
                },
            ]
        )

        report = entry_point.run(signups=signups, apply=True)

        self.assertEqual(report.signup_rows, 5)
        self.assertEqual(report.signup_rows_unconfirmed, 1)
        self.assertEqual(report.signup_rows_without_sessions, 1)
        self.assertEqual(report.signup_rows_invalid_email, 1)
        self.assertEqual(report.selections_past_session, 1)
        self.assertEqual(report.selections_unknown_session, 1)
        self.assertEqual(
            report.registrations_created, {"session-1": 1, "session-2": 1, "session-3": 2}
        )
        self.assertEqual(EventRegistration.objects.filter(event=self.one).count(), 1)
        seat = EventRegistration.objects.get(event=self.two)
        self.assertEqual(seat.normalized_email, "ada@example.invalid")
        self.assertEqual(seat.status, EventRegistration.Status.CONFIRMED)
        self.assertEqual(seat.privacy_notice_version, "academy:2026-11-13.v1")
        self.assertIsNone(seat.newsletter_consent)
        self.assertEqual(
            seat.acquisition_metadata,
            {
                "source": "academy-sessions",
                "academy_session": "session-2",
                "academy_optin": True,
                "academy_consent_version": "2026-11-13.v1",
            },
        )
        self.assertNotIn("never read", json.dumps(report.as_dict()))
        self.assertNotIn("example.invalid", json.dumps(report.as_dict()))
        # Moving a seat sends nobody anything.
        self.assertEqual(mail.outbox, [])

        # Replaying writes nothing new, and an existing seat is left as it is.
        again = entry_point.run(signups=signups, apply=True)
        self.assertEqual(again.registrations_created, {})
        self.assertEqual(
            again.registrations_already_present, {"session-1": 1, "session-2": 1, "session-3": 2}
        )
        self.assertEqual(EventRegistration.objects.filter(event=self.three).count(), 2)

    def test_it_is_a_typed_dry_run_first_operator_command(self) -> None:
        args = entry_point._parser().parse_args(["--database", ".tmp/x.sqlite3"])
        self.assertFalse(args.apply)
        self.assertIsNone(args.signups)
        self.assertEqual(entry_point.SYNC_MODEL, "one-time")
        self.assertFalse(entry_point.BOOTSTRAPS_EMPTY_DATABASE)
        self.assertIn("import_academy_sessions", scripts.prod.SCRIPT_COMMAND_EXCLUSIONS)
