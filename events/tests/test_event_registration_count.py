"""The public "N registered" count: imported provider rows plus site registrations.

The count is the distinct people registered for an event.  Imported Luma
(approved) and Eventbrite (attending) rows count, and so do the native
``community_base.events.EventRegistration`` rows the site's own register form
writes once they are confirmed (and still after the event marks them attended
or no-show) -- except where the same person
already has an eligible provider row for that event, so nobody counts twice.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from community_base.events.models import Event
from community_base.events.models import EventRegistration as NativeRegistration
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from event_registrants.models import EventOperatorConfig, EventRegistrantIdentity
from event_registrants.models import EventRegistration as ProviderRegistration
from events.queries import (
    _cached_published_records,
    _published_records_stamp,
    published_event_record,
    published_event_records,
)
from test_support.event_pictures import TEST_EVENT_PICTURE

#: The page tests freeze the event clock here so the events below are upcoming.
STABLE_EVENT_NOW = datetime(2026, 8, 12, tzinfo=ZoneInfo("Europe/Berlin"))
STARTS_AT = STABLE_EVENT_NOW + timedelta(days=10)
PUBLIC_ID_BASE = 96_000


def _event(index: int, **overrides: object) -> Event:
    values: dict[str, object] = {
        "content_id": uuid.uuid4(),
        "public_id": PUBLIC_ID_BASE + index,
        "title": f"Registration count event {index}",
        "slug": f"registration-count-event-{index}",
        "status": "upcoming",
        "start_datetime": STARTS_AT + timedelta(hours=index),
        "kind": "standard",
        "tags": ["dtc-type:webinar"],
    }
    values.update(overrides)
    event = Event.objects.create(**values)
    # Every event has a picture (events.pictures); this fixture bypasses the
    # creation path, so it writes the picture row itself.
    EventOperatorConfig.objects.create(
        event=event,
        banner_image=TEST_EVENT_PICTURE.data,
        banner_content_type=TEST_EVENT_PICTURE.content_type,
    )
    return event


def _provider(event: Event, email: str, *, provider: str = "luma", status: str = "approved"):
    identity, _ = EventRegistrantIdentity.objects.get_or_create(normalized_email=email)
    return ProviderRegistration.objects.create(
        event=event, identity=identity, provider=provider, status=status
    )


def _native(event: Event, email: str, *, status: str = "confirmed", user=None):
    return NativeRegistration.objects.create(
        event=event,
        original_email=email,
        normalized_email=email.lower(),
        status=status,
        user=user,
    )


def _count(event: Event) -> int:
    record = published_event_record(event.content_id)
    assert record is not None
    return record["registration_count"]


class RegistrationCountTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        _cached_published_records.cache_clear()
        self.addCleanup(_cached_published_records.cache_clear)
        self.event = _event(0)

    def test_one_native_confirmed_registration_raises_the_count_by_one(self) -> None:
        _provider(self.event, "luma-person@example.test")
        self.assertEqual(_count(self.event), 1)

        _native(self.event, "site-person@example.test")

        self.assertEqual(_count(self.event), 2)

    def test_an_attended_native_registration_counts(self) -> None:
        _native(self.event, "attended@example.test", status="attended")

        self.assertEqual(_count(self.event), 1)

    def test_a_no_show_native_registration_still_counts(self) -> None:
        # Marking no-shows after the event must not lower "N registered".
        _native(self.event, "no-show@example.test", status="no_show")

        self.assertEqual(_count(self.event), 1)

    def test_unconfirmed_and_withdrawn_native_registrations_do_not_count(self) -> None:
        for status in ("pending_verification", "cancelled", "expired"):
            _native(self.event, f"{status}@example.test", status=status)

        self.assertEqual(_count(self.event), 0)

    def test_the_same_email_on_luma_and_on_the_site_counts_once(self) -> None:
        _provider(self.event, "both@example.test")
        _native(self.event, "both@example.test")

        self.assertEqual(_count(self.event), 1)

    def test_a_declined_luma_row_does_not_hide_the_native_registration(self) -> None:
        _provider(self.event, "declined@example.test", status="declined")
        _native(self.event, "declined@example.test")

        self.assertEqual(_count(self.event), 1)

    def test_a_provider_row_for_another_event_does_not_hide_the_native_one(self) -> None:
        other = _event(1)
        _provider(other, "elsewhere@example.test")
        _native(self.event, "elsewhere@example.test")

        self.assertEqual(_count(self.event), 1)
        self.assertEqual(_count(other), 1)

    def test_an_account_anchored_identity_matches_by_account_email(self) -> None:
        user = get_user_model().objects.create_user(
            username="count-account", email="Account@Example.test", password="unused-password"
        )
        identity = EventRegistrantIdentity.objects.create(account=user)
        ProviderRegistration.objects.create(
            event=self.event, identity=identity, provider="luma", status="approved"
        )
        # Matched through the account's identity email, with no user link.
        _native(self.event, "account@example.test")

        self.assertEqual(_count(self.event), 1)

    def test_eventbrite_attending_and_a_different_native_email_count_two(self) -> None:
        _provider(self.event, "eventbrite@example.test", provider="eventbrite", status="attending")
        _native(self.event, "someone-else@example.test")

        self.assertEqual(_count(self.event), 2)

    def test_the_cache_stamp_moves_when_a_native_registration_is_added(self) -> None:
        before = _published_records_stamp()
        self.assertEqual(_count(self.event), 0)

        _native(self.event, "new@example.test")

        self.assertNotEqual(_published_records_stamp(), before)
        # The warmed record list is keyed on the stamp, so it rebuilds.
        self.assertEqual(_count(self.event), 1)
        record = next(
            item
            for item in published_event_records()
            if item["identity_id"] == str(self.event.content_id)
        )
        self.assertEqual(record["registration_count"], 1)


class RegistrationCountPageTests(TestCase):
    """The detail page prints the count, the hub never does, and it is never edge-cached."""

    def setUp(self) -> None:
        super().setUp()
        clock = patch("content.event_content.timezone.now", return_value=STABLE_EVENT_NOW)
        clock.start()
        self.addCleanup(clock.stop)
        _cached_published_records.cache_clear()
        self.addCleanup(_cached_published_records.cache_clear)
        self.event = _event(0)
        self.path = f"/events/{self.event.public_id}/{self.event.slug}"

    def _hub_count(self) -> str | None:
        body = self.client.get("/events").content.decode()
        start = body.index(f'href="{self.path}"')
        end = body.find("</article>", start)
        match = re.search(r"(\d+) registered", body[start:end])
        return match.group(1) if match else None

    def _detail_count(self) -> str | None:
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        match = re.search(r"(\d+)\s+registered", response.content.decode())
        return match.group(1) if match else None

    def test_only_the_detail_page_prints_the_count(self) -> None:
        _provider(self.event, "both@example.test")
        _native(self.event, "both@example.test")
        _native(self.event, "site-only@example.test")
        _native(self.event, "pending@example.test", status="pending_verification")

        # The listing never prints the count, so every row keeps one shape;
        # the event's own page carries it.
        self.assertIsNone(self._hub_count())
        self.assertEqual(self._detail_count(), "2")

    def test_an_upcoming_detail_page_is_not_shared_even_at_zero(self) -> None:
        response = self.client.get(self.path)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self._detail_count())
        self.assertIn("no-store", response.headers["Cache-Control"])

        _native(self.event, "first@example.test")

        self.assertEqual(self._detail_count(), "1")

    def test_listing_query_count_does_not_grow_with_registrations(self) -> None:
        events = [self.event, *(_event(index) for index in range(1, 6))]

        def measure() -> int:
            # Cold: the records are rebuilt, so the row fetch is measured too.
            _cached_published_records.cache_clear()
            with CaptureQueriesContext(connection) as captured:
                response = self.client.get("/events")
            self.assertEqual(response.status_code, 200)
            return len(captured.captured_queries)

        baseline = measure()
        for index, event in enumerate(events):
            for person in range(4):
                email = f"p{index}-{person}@example.test"
                _provider(event, email)
                _native(event, email)
                _native(event, f"site-{index}-{person}@example.test")
        loaded = measure()

        self.assertEqual(loaded, baseline)
        self.assertLessEqual(loaded, 20)
