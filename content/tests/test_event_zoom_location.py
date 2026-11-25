"""A Zoom event says where it happens; only a confirmed seat gets the join link."""

from __future__ import annotations

from datetime import UTC, datetime

from community_base.events.models import Event, EventRegistration
from django.contrib.auth import get_user_model
from django.test import TestCase

from events.identity import create_event_identity
from test_support.event_pictures import TEST_EVENT_PICTURE

JOIN_URL = "https://example.invalid/zoom/j/123?pwd=synthetic"


class EventZoomLocationTests(TestCase):
    def setUp(self) -> None:
        self.event = create_event_identity(
            picture=TEST_EVENT_PICTURE,
            title="A Zoom session",
            event_type="webinar",
            speakers=[("janedoe", "Jane Doe")],
            description="What the session is about.",
            starts_at=datetime(2030, 1, 7, 16, tzinfo=UTC),
        )
        Event.objects.filter(pk=self.event.pk).update(platform="zoom", zoom_join_url=JOIN_URL)
        self.path = f"/events/{self.event.public_id}/{self.event.slug}"

    def test_a_visitor_reads_the_place_but_never_the_link(self) -> None:
        body = self.client.get(self.path).content.decode()

        self.assertIn('data-testid="event-location"', body)
        self.assertIn("<strong>Online on Zoom</strong>", body)
        self.assertIn("Register to get the link", body)
        self.assertNotIn("example.invalid/zoom", body)

    def test_a_confirmed_seat_gets_the_join_link(self) -> None:
        member = get_user_model().objects.create_user(
            username="zoomer", email="zoomer@example.invalid", password="pw"
        )
        EventRegistration.objects.create(
            event=self.event,
            user=member,
            original_email=member.email,
            normalized_email=member.email,
            status=EventRegistration.Status.CONFIRMED,
        )
        self.client.force_login(member)

        body = self.client.get(self.path).content.decode()

        self.assertIn(f'href="{JOIN_URL.replace("&", "&amp;")}"', body)
        self.assertIn("Join the meeting", body)
        self.assertNotIn("Register to get the link", body)

    def test_a_pending_seat_does_not_get_the_link(self) -> None:
        member = get_user_model().objects.create_user(
            username="pending", email="pending@example.invalid", password="pw"
        )
        EventRegistration.objects.create(
            event=self.event,
            user=member,
            original_email=member.email,
            normalized_email=member.email,
            status=EventRegistration.Status.PENDING_VERIFICATION,
        )
        self.client.force_login(member)

        self.assertNotIn("example.invalid/zoom", self.client.get(self.path).content.decode())

    def test_an_event_without_a_meeting_draws_no_location_row(self) -> None:
        Event.objects.filter(pk=self.event.pk).update(zoom_join_url="")

        body = self.client.get(self.path).content.decode()

        self.assertNotIn('data-testid="event-location"', body)
