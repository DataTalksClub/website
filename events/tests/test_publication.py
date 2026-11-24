"""A published event always carries a type and at least one speaker."""

from __future__ import annotations

from datetime import timedelta

from community_base.events.models import Event
from django.test import TestCase
from django.utils import timezone

from events.admin_services import (
    EventAdminError,
    create_event,
    create_speaker,
    publish_event_action,
    update_event,
)
from events.identity import create_event_identity
from events.publication import incomplete_published_events, publication_gaps


def _payload(**overrides: object) -> dict[str, object]:
    start = (timezone.now() + timedelta(days=7)).replace(microsecond=0)
    payload: dict[str, object] = {
        "title": "Publication Rule Webinar",
        "type": "webinar",
        "speakers": ["alexeygrigorev"],
        "start_datetime": start.isoformat(),
        "publish": True,
    }
    payload.update(overrides)
    return payload


class _WithSpeaker(TestCase):
    def setUp(self) -> None:
        super().setUp()
        create_speaker({"short": "alexeygrigorev", "name": "Alexey Grigorev"})


class AdminPublicationRuleTests(_WithSpeaker):
    def test_a_complete_event_publishes(self) -> None:
        event = create_event(_payload())["event"]
        self.assertEqual(event["status"], "upcoming")

    def test_create_and_publish_without_a_speaker_is_refused_and_writes_nothing(self) -> None:
        before = Event.objects.count()
        with self.assertRaises(EventAdminError) as caught:
            create_event(_payload(speakers=[]))
        self.assertEqual(caught.exception.code, "event_not_publishable")
        self.assertIn("speakers", str(caught.exception))
        self.assertEqual(Event.objects.count(), before)

    def test_create_and_publish_without_a_type_is_refused(self) -> None:
        with self.assertRaises(EventAdminError) as caught:
            create_event(_payload(type=None))
        self.assertEqual(caught.exception.code, "event_not_publishable")
        self.assertIn("type", str(caught.exception))

    def test_an_incomplete_draft_is_allowed_but_cannot_be_published(self) -> None:
        draft = create_event(_payload(speakers=[], publish=False))["event"]
        self.assertEqual(draft["status"], "draft")
        with self.assertRaises(EventAdminError):
            publish_event_action(event_id=draft["id"])
        self.assertEqual(Event.objects.get(content_id=draft["id"]).status, "draft")

        update_event({"speakers": ["alexeygrigorev"]}, event_id=draft["id"])
        published = publish_event_action(event_id=draft["id"])["event"]
        self.assertEqual(published["status"], "upcoming")

    def test_an_edit_cannot_strip_the_type_or_speakers_from_a_published_event(self) -> None:
        event = create_event(_payload())["event"]
        with self.assertRaises(EventAdminError):
            update_event({"speakers": []}, event_id=event["id"])
        with self.assertRaises(EventAdminError):
            update_event({"type": ""}, event_id=event["id"])
        row = Event.objects.get(content_id=event["id"])
        self.assertEqual(publication_gaps(row), ())


class IncompletePublishedEventsTests(_WithSpeaker):
    def test_reports_published_events_missing_a_type_or_a_speaker_but_not_drafts(self) -> None:
        bare = create_event_identity(
            title="Bare Published Event",
            starts_at=timezone.now() + timedelta(days=3),
            source_repository="synthetic/source",
            source_revision="r1",
            source_key="bare-published",
        )
        create_event_identity(
            title="Bare Draft Event",
            starts_at=timezone.now() + timedelta(days=3),
            source_repository="synthetic/source",
            source_revision="r1",
            source_key="bare-draft",
            status="draft",
        )
        create_event(_payload(title="Complete Published Event"))

        mine = {"Bare Published Event", "Bare Draft Event", "Complete Published Event"}
        incomplete = [item for item in incomplete_published_events() if item["title"] in mine]

        self.assertEqual([item["public_id"] for item in incomplete], [bare.public_id])
        self.assertEqual(incomplete[0]["missing"], ["type", "speakers"])
