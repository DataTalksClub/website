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
from test_support.event_pictures import (
    TEST_EVENT_DESCRIPTION,
    TEST_EVENT_PICTURE,
    TEST_PICTURE_PAYLOAD,
)


def _payload(**overrides: object) -> dict[str, object]:
    start = (timezone.now() + timedelta(days=7)).replace(microsecond=0)
    payload: dict[str, object] = {
        "title": "Publication Rule Webinar",
        "type": "webinar",
        "speakers": ["alexeygrigorev"],
        "start_datetime": start.isoformat(),
        "publish": True,
        "description": "What the webinar is about.",
        "picture": TEST_PICTURE_PAYLOAD,
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
        self.assertEqual(caught.exception.code, "event_speakers_required")
        self.assertIn("speakers", str(caught.exception))
        self.assertEqual(Event.objects.count(), before)

    def test_create_and_publish_without_a_type_is_refused(self) -> None:
        with self.assertRaises(EventAdminError) as caught:
            create_event(_payload(type=None))
        self.assertEqual(caught.exception.code, "event_type_required")
        self.assertIn("type", str(caught.exception))

    def test_not_even_a_draft_can_be_created_without_a_type_or_a_speaker(self) -> None:
        before = Event.objects.count()
        with self.assertRaises(EventAdminError) as no_speaker:
            create_event(_payload(speakers=[], publish=False))
        with self.assertRaises(EventAdminError) as no_type:
            create_event(_payload(type=None, publish=False))
        self.assertEqual(no_speaker.exception.code, "event_speakers_required")
        self.assertEqual(no_type.exception.code, "event_type_required")
        self.assertEqual(Event.objects.count(), before)

    def test_a_complete_draft_publishes_later(self) -> None:
        draft = create_event(_payload(publish=False))["event"]
        self.assertEqual(draft["status"], "draft")
        published = publish_event_action(event_id=draft["id"])["event"]
        self.assertEqual(published["status"], "upcoming")

    def test_an_edit_cannot_strip_the_type_or_speakers_from_a_draft(self) -> None:
        draft = create_event(_payload(publish=False))["event"]
        with self.assertRaises(EventAdminError):
            update_event({"speakers": []}, event_id=draft["id"])
        with self.assertRaises(EventAdminError):
            update_event({"type": ""}, event_id=draft["id"])

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
            picture=TEST_EVENT_PICTURE,
            description=TEST_EVENT_DESCRIPTION,
            event_type="webinar",
            speakers=[("synthetic-speaker", "Synthetic Speaker")],
            title="Bare Published Event",
            starts_at=timezone.now() + timedelta(days=3),
            source_repository="synthetic/source",
            source_revision="r1",
            source_key="bare-published",
        )
        create_event_identity(
            picture=TEST_EVENT_PICTURE,
            description=TEST_EVENT_DESCRIPTION,
            event_type="webinar",
            speakers=[("synthetic-speaker", "Synthetic Speaker")],
            title="Bare Draft Event",
            starts_at=timezone.now() + timedelta(days=3),
            source_repository="synthetic/source",
            source_revision="r1",
            source_key="bare-draft",
            status="draft",
        )
        create_event(_payload(title="Complete Published Event"))
        # Rows written before every event had to carry both -- the shape the
        # report exists to find.
        for title in ("Bare Published Event", "Bare Draft Event"):
            row = Event.objects.get(title=title)
            row.tags = []
            row.save(update_fields=["tags"])
            row.event_host_links.all().delete()

        mine = {"Bare Published Event", "Bare Draft Event", "Complete Published Event"}
        incomplete = [item for item in incomplete_published_events() if item["title"] in mine]

        self.assertEqual([item["public_id"] for item in incomplete], [bare.public_id])
        self.assertEqual(incomplete[0]["missing"], ["type", "speakers"])


class PublishAvoidsSeriesEnrolmentLockTests(_WithSpeaker):
    """PostgreSQL rejects the package's series-enrolment lock; publishing must not use it."""

    def test_the_package_publish_step_uses_the_lock_free_enrolment(self) -> None:
        from community_base.events import registration, services

        import events.admin_services as admin_services

        self.assertIs(
            services.enroll_series_registrants_in_event,
            admin_services._enroll_series_registrants_without_outer_join_lock,
        )
        self.assertIsNot(
            services.enroll_series_registrants_in_event,
            registration.enroll_series_registrants_in_event,
        )

    def test_publish_does_not_run_the_package_enrolment_for_a_series_less_event(self) -> None:
        from unittest import mock

        created = create_event(_payload(publish=False))["event"]
        with mock.patch(
            "community_base.events.registration.enroll_series_registrants_in_event",
            side_effect=AssertionError("series enrolment must not run on publish"),
        ):
            published = publish_event_action(event_id=created["id"])["event"]
        self.assertEqual(published["status"], "upcoming")
        self.assertIsNotNone(published["public_id"])


class CreationServiceTests(TestCase):
    """The one creation path refuses an event without a type or a speaker."""

    def _create(self, **overrides: object) -> Event:
        values: dict[str, object] = {
            "title": "Creation Rule Event",
            "event_type": "podcast",
            "speakers": [("synthetic-speaker", "Synthetic Speaker")],
            "source_repository": "synthetic/source",
            "source_revision": "r1",
            "source_key": "creation-rule",
            "picture": TEST_EVENT_PICTURE,
            "description": TEST_EVENT_DESCRIPTION,
        }
        values.update(overrides)
        return create_event_identity(**values)  # type: ignore[arg-type]

    def test_an_event_exists_with_its_type_and_speakers_from_the_start(self) -> None:
        event = self._create()
        self.assertEqual(publication_gaps(event), ())

    def test_no_type_or_no_speaker_writes_nothing(self) -> None:
        from events.identity import EventIdentityError

        before = Event.objects.count()
        for overrides, code in (
            ({"event_type": None}, "event_type_required"),
            ({"event_type": "  "}, "event_type_required"),
            ({"speakers": []}, "event_speakers_required"),
            ({"speakers": [("", "Nobody")]}, "event_speaker_key_blank"),
            ({"picture": None}, "event_picture_required"),
            ({"description": "  "}, "event_description_required"),
        ):
            with self.subTest(code=code), self.assertRaises(EventIdentityError) as caught:
                self._create(**overrides)
            self.assertEqual(str(caught.exception), code)
        self.assertEqual(Event.objects.count(), before)
