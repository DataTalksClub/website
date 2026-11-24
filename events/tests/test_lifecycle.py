"""Upcoming/in-progress/past is derived from the schedule, never from ``Event.status``.

The stored package status only carries editorial decisions (draft, cancelled,
archived); ``upcoming`` and the legacy ``completed`` both mean "published".
These tests pin the derived boundaries and prove every site reader -- the
management API and its filters, complete/cancel, public registration, the
public listing and Q&A -- reads the derived value, so a stale stored
``upcoming`` on a long-past event (production event 421, 2020-11-10) cannot
leak.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from community_base.events.models import Event
from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from content.event_content import event_groups
from events.identity import EventIdentityError, create_event_identity
from events.lifecycle import (
    DERIVED_STATUSES,
    accepts_registrations,
    derived_status,
    status_q,
)
from events.tests import test_admin_cli_api

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
MINUTE = timedelta(minutes=1)


def _row(status: str, start: datetime, end: datetime | None = None) -> SimpleNamespace:
    return SimpleNamespace(status=status, start_datetime=start, end_datetime=end)


class DerivedStatusBoundaryTests(SimpleTestCase):
    def test_before_and_at_the_start_a_published_event_is_upcoming(self) -> None:
        for stored in ("upcoming", "completed"):
            with self.subTest(stored=stored):
                self.assertEqual(derived_status(_row(stored, NOW + MINUTE), NOW), "upcoming")
                # At the exact start it is still upcoming: the public listing's split.
                self.assertEqual(derived_status(_row(stored, NOW), NOW), "upcoming")

    def test_start_passed_but_end_not_passed_is_in_progress(self) -> None:
        start = NOW - MINUTE
        self.assertEqual(derived_status(_row("upcoming", start, NOW + MINUTE), NOW), "in_progress")
        # No stored end: the effective end is one hour after the start.
        self.assertEqual(derived_status(_row("upcoming", NOW - HOUR + MINUTE), NOW), "in_progress")

    def test_end_passed_is_past_whatever_the_stored_value(self) -> None:
        for stored in ("upcoming", "completed"):
            with self.subTest(stored=stored):
                self.assertEqual(derived_status(_row(stored, NOW - HOUR, NOW), NOW), "past")
                self.assertEqual(derived_status(_row(stored, NOW - HOUR), NOW), "past")
        # The production case: a 2020 row still stored as "upcoming".
        sagemaker = _row("upcoming", datetime(2020, 11, 10, 17, 0, tzinfo=UTC))
        self.assertEqual(derived_status(sagemaker, NOW), "past")

    def test_a_legacy_completed_row_scheduled_in_the_future_is_upcoming(self) -> None:
        self.assertEqual(derived_status(_row("completed", NOW + HOUR), NOW), "upcoming")

    def test_editorial_states_stay_as_stored_on_both_sides_of_the_schedule(self) -> None:
        for stored in ("cancelled", "draft", "archived"):
            for start in (NOW + HOUR, NOW - MINUTE, NOW - 10 * HOUR):
                with self.subTest(stored=stored, start=start):
                    self.assertEqual(derived_status(_row(stored, start), NOW), stored)

    def test_registration_is_open_until_the_event_ends(self) -> None:
        self.assertTrue(accepts_registrations(_row("upcoming", NOW + HOUR), NOW))
        self.assertTrue(accepts_registrations(_row("upcoming", NOW - MINUTE), NOW))
        self.assertTrue(accepts_registrations(_row("completed", NOW + HOUR), NOW))
        self.assertFalse(accepts_registrations(_row("upcoming", NOW - HOUR), NOW))
        self.assertFalse(accepts_registrations(_row("cancelled", NOW + HOUR), NOW))
        self.assertFalse(accepts_registrations(_row("draft", NOW + HOUR), NOW))

    def test_an_unknown_filter_value_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            status_q("completed", NOW)


class DerivedStatusQueryTests(TestCase):
    def _make(self, title: str, status: str, start: datetime, end: datetime | None) -> Event:
        event = create_event_identity(
            event_type="webinar",
            speakers=[("synthetic-speaker", "Synthetic Speaker")],
            title=title,
            starts_at=start,
            ends_at=end,
            source_repository="DataTalksClub/test",
            source_revision="a" * 40,
            source_key=f"lifecycle-{title}",
            status=status,
        )
        return event

    def test_identity_creation_refuses_an_end_before_the_start(self) -> None:
        # Such a row would sit in both the upcoming and the past bucket.
        start = timezone.now() + timedelta(days=2)
        before = Event.objects.count()
        with self.assertRaisesMessage(EventIdentityError, "event_end_before_start"):
            self._make("ends-before-start", "upcoming", start, start - MINUTE)
        self.assertEqual(Event.objects.count(), before)
        # An end equal to the start is a valid zero-length schedule.
        self.assertEqual(
            derived_status(self._make("zero-length", "upcoming", start, start)), "upcoming"
        )

    def test_the_queryset_filter_agrees_with_the_row_rule_at_every_boundary(self) -> None:
        now = timezone.now().replace(microsecond=0)
        cases = [
            ("future", "upcoming", now + HOUR, None),
            ("future-completed", "completed", now + HOUR, None),
            ("live-with-end", "upcoming", now - MINUTE, now + MINUTE),
            ("live-without-end", "upcoming", now - HOUR + 5 * MINUTE, None),
            ("ended-with-end", "upcoming", now - 2 * HOUR, now - MINUTE),
            ("ended-without-end", "upcoming", now - 2 * HOUR, None),
            ("ancient", "upcoming", datetime(2020, 11, 10, 17, 0, tzinfo=UTC), None),
            ("legacy-completed", "completed", now - 3 * HOUR, None),
            ("cancelled-future", "cancelled", now + HOUR, None),
            ("cancelled-past", "cancelled", now - 3 * HOUR, None),
            ("draft", "draft", now + HOUR, None),
            ("archived", "archived", now - 3 * HOUR, None),
        ]
        ids = {self._make(*case).pk for case in cases}
        rows = Event.objects.filter(pk__in=ids)
        for status in DERIVED_STATUSES:
            with self.subTest(status=status):
                expected = {row.pk for row in rows if derived_status(row, now) == status}
                actual = set(rows.filter(status_q(status, now)).values_list("pk", flat=True))
                self.assertEqual(actual, expected)
        # The filters partition the rows: every row lands in exactly one bucket.
        buckets = [
            set(rows.filter(status_q(status, now)).values_list("pk", flat=True))
            for status in DERIVED_STATUSES
        ]
        self.assertEqual(sum(len(bucket) for bucket in buckets), len(ids))
        self.assertEqual(set().union(*buckets), ids)


class ReadersUseTheDerivedStatusTests(TestCase):
    """The management API, complete/cancel and public registration read the derived value."""

    auth_headers: dict[str, str]

    # The admin API operator and bearer token, without re-collecting those tests.
    setUp = test_admin_cli_api.EventAdminAPITests.setUp

    def _stale_past_event(self) -> Event:
        """A long-past event still stored as ``upcoming``, as production holds 466 of."""

        return create_event_identity(
            event_type="webinar",
            speakers=[("synthetic-speaker", "Synthetic Speaker")],
            title="Deploying models with AWS Sagemaker",
            starts_at=datetime(2020, 11, 10, 17, 0, tzinfo=UTC),
            source_repository="DataTalksClub/test",
            source_revision="a" * 40,
            source_key="2020-11-10-deploying-models-with-aws-sagemaker",
            status="upcoming",
        )

    def _future_event(self) -> Event:
        start = timezone.now() + timedelta(days=3)
        return create_event_identity(
            event_type="webinar",
            speakers=[("synthetic-speaker", "Synthetic Speaker")],
            title="Future lifecycle webinar",
            starts_at=start,
            ends_at=start + HOUR,
            source_repository="DataTalksClub/test",
            source_revision="a" * 40,
            source_key="future-lifecycle-webinar",
            status="upcoming",
        )

    def _get(self, path: str, **params: str):
        return self.client.get(path, params, headers=self.auth_headers)

    def _post(self, path: str, body: dict | None = None):
        return self.client.post(
            path,
            data=json.dumps(body or {}),
            content_type="application/json",
            headers=self.auth_headers,
        )

    def test_detail_and_list_report_a_stale_upcoming_row_as_past(self) -> None:
        stale = self._stale_past_event()
        future = self._future_event()

        detail = self._get(f"/api/v1/admin/events/{stale.content_id}")
        self.assertEqual(detail.status_code, 200, detail.content)
        self.assertEqual(detail.json()["event"]["status"], "past")
        # The public URL is unchanged: a published row keeps its canonical path.
        self.assertTrue(
            detail.json()["event"]["public_url"].endswith(f"/events/{stale.public_id}/{stale.slug}")
        )

        def listed(**params: str) -> set[int]:
            resp = self._get("/api/v1/admin/events", **params)
            self.assertEqual(resp.status_code, 200, resp.content)
            return {item["public_id"] for item in resp.json()["items"]}

        self.assertIn(stale.public_id, listed(status="past"))
        self.assertNotIn(stale.public_id, listed(status="upcoming"))
        self.assertNotIn(stale.public_id, listed(upcoming="true"))
        self.assertIn(stale.public_id, listed(past="true"))
        self.assertIn(future.public_id, listed(status="upcoming"))
        self.assertIn(future.public_id, listed(upcoming="true"))
        self.assertNotIn(future.public_id, listed(past="true"))

        refused = self._get("/api/v1/admin/events", status="completed")
        self.assertEqual(refused.status_code, 400, refused.content)

    def test_complete_writes_no_status_and_refuses_an_event_that_has_not_ended(self) -> None:
        future = self._future_event()
        early = self._post(f"/api/v1/admin/events/{future.content_id}/complete")
        self.assertEqual(early.status_code, 400, early.content)
        future.refresh_from_db()
        self.assertEqual(future.status, "upcoming")

        stale = self._stale_past_event()
        done = self._post(
            f"/api/v1/admin/events/{stale.content_id}/complete",
            {"recording_url": "https://youtube.com/watch?v=sagemaker"},
        )
        self.assertEqual(done.status_code, 200, done.content)
        self.assertEqual(done.json()["event"]["status"], "past")
        stale.refresh_from_db()
        self.assertEqual(stale.status, "upcoming")
        self.assertEqual(stale.recording_url, "https://youtube.com/watch?v=sagemaker")

    def test_a_past_event_cannot_be_cancelled_but_cancelled_stays_cancelled(self) -> None:
        stale = self._stale_past_event()
        refused = self._post(f"/api/v1/admin/events/{stale.content_id}/cancel")
        self.assertEqual(refused.status_code, 400, refused.content)
        stale.refresh_from_db()
        self.assertEqual(stale.status, "upcoming")

        future = self._future_event()
        cancelled = self._post(f"/api/v1/admin/events/{future.content_id}/cancel")
        self.assertEqual(cancelled.status_code, 200, cancelled.content)
        self.assertEqual(cancelled.json()["event"]["status"], "cancelled")
        # Cancelled stays cancelled once its time has passed too.
        Event.objects.filter(pk=future.pk).update(
            start_datetime=timezone.now() - timedelta(days=2),
            end_datetime=timezone.now() - timedelta(days=2) + HOUR,
        )
        later = self._get(f"/api/v1/admin/events/{future.content_id}")
        self.assertEqual(later.json()["event"]["status"], "cancelled")
        self.assertEqual(self._get("/api/v1/admin/events", status="cancelled").status_code, 200)

    def test_public_registration_closes_on_the_derived_status(self) -> None:
        stale = self._stale_past_event()
        closed = self.client.post(
            f"/events/{stale.public_id}/{stale.slug}/register",
            data={"email": "dev+past@dtcdev.click"},
        )
        self.assertEqual(closed.status_code, 302)
        self.assertIn("registered=closed", closed["Location"])

        # A legacy "completed" row that has not happened yet takes registrations.
        future = self._future_event()
        Event.objects.filter(pk=future.pk).update(status="completed")
        opened = self.client.post(
            f"/events/{future.public_id}/{future.slug}/register",
            data={"email": "dev+future@dtcdev.click"},
        )
        self.assertEqual(opened.status_code, 302)
        self.assertIn("registered=ok", opened["Location"])

    def test_the_public_listing_splits_on_the_schedule_not_the_stored_status(self) -> None:
        stale = self._stale_past_event()
        future = self._future_event()
        Event.objects.filter(pk=future.pk).update(status="completed")
        groups = event_groups(timezone.now())
        upcoming = {item["identity_id"] for item in groups.upcoming}
        recent = {item["identity_id"] for item in groups.recent}
        self.assertIn(str(stale.content_id), recent)
        self.assertNotIn(str(stale.content_id), upcoming)
        self.assertIn(str(future.content_id), upcoming)


class NoReaderComparesTheStoredTimeStatusTests(SimpleTestCase):
    """Site code may not read upcoming/past from ``Event.status`` again."""

    #: Site trees whose runtime code or templates could read events.
    ROOTS = (
        "api",
        "content",
        "core",
        "courses",
        "dtc_cli",
        "event_qna",
        "event_registrants",
        "events",
        "management_api",
        "scripts",
        "studio",
        "templates",
        "website",
    )
    SUFFIXES = frozenset({".py", ".html", ".txt"})
    _VALUE = r"""["'](?:upcoming|completed)["']"""
    FORBIDDEN = (
        # The package's status-gated time properties.
        re.compile(r"\.is_upcoming\b"),
        re.compile(r"\.is_past\b"),
        # Writing or comparing a time-meaning stored value directly, in Python
        # or in a template (``event.status == "upcoming"``).
        re.compile(rf"status\s*(?:==|!=|=)\s*{_VALUE}"),
        # Filtering on them, whatever the collection literal and however it is
        # wrapped across lines: ``status__in=("upcoming", "completed")``.
        re.compile(rf"status__in\s*=\s*[(\[{{][^)\]}}]*{_VALUE}"),
        # A bare collection of the two values (a reimplemented "published" set).
        re.compile(rf"[(\[{{]\s*{_VALUE}\s*,\s*{_VALUE}"),
    )
    #: Matches that concern a different model's own ``status`` column.
    ALLOWED = frozenset(
        {
            # core.IdempotencyRecord: its in_progress/completed replay state.
            ("core/models.py", 'status="completed",'),
        }
    )

    def _offenders(self, base: Path) -> list[str]:
        offenders: list[str] = []
        for root in self.ROOTS:
            for path in sorted((base / root).rglob("*")):
                if path.suffix not in self.SUFFIXES or not path.is_file():
                    continue
                relative = path.relative_to(base)
                if set(relative.parts) & {"tests", "migrations"} or path.name.startswith("test_"):
                    continue
                if relative.as_posix() == "events/lifecycle.py":
                    continue
                text = path.read_text(encoding="utf-8")
                lines = text.splitlines()
                for pattern in self.FORBIDDEN:
                    for match in pattern.finditer(text):
                        number = text.count("\n", 0, match.start()) + 1
                        line = lines[number - 1].strip()
                        if (relative.as_posix(), line) in self.ALLOWED:
                            continue
                        offenders.append(f"{relative}:{number}: {line}")
        return offenders

    def test_runtime_code_reads_the_derived_status(self) -> None:
        self.assertEqual(self._offenders(Path(settings.BASE_DIR)), [])

    def test_the_scan_catches_every_spelling(self) -> None:
        samples = (
            "if event.is_upcoming:",
            "if event.is_past:",
            'if event.status == "upcoming":',
            '{% if event.status == "completed" %}',
            'Event.objects.filter(status="completed")',
            'Event.objects.filter(status__in=("upcoming", "completed"))',
            'Event.objects.filter(status__in=["upcoming"])',
            "Event.objects.filter(\n    status__in=(\n"
            '        "draft",\n        "upcoming",\n    )\n)',
            'PUBLISHED = {"upcoming", "completed"}',
            'PUBLISHED = ("completed", "upcoming")',
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(any(pattern.search(sample) for pattern in self.FORBIDDEN))
        for clean in ("{% if is_past_events %}", 'status__in=("draft", "cancelled")'):
            with self.subTest(clean=clean):
                self.assertFalse(any(pattern.search(clean) for pattern in self.FORBIDDEN))
