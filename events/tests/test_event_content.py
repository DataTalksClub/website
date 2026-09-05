"""What a public event page says, as database rows.

``Event`` is identity and says nothing about the event; ``EventContent`` with
its speakers and links is the other half. These tests pin the two things that
matter to the pages reading them: an event is only published once it has both
halves, and the record a page reads is assembled in the order the page prints.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from events.models import Event, EventContent, EventLink, EventSpeaker
from events.queries import published_event_record, published_event_records

STARTS_AT = datetime(2026, 6, 1, 17, 0, tzinfo=UTC)


def _event(*, public_id: int, slug: str, title: str) -> Event:
    event = Event(
        id=uuid.uuid4(),
        title=title,
        slug=slug,
        source_repository="DataTalksClub/example",
        source_revision="a" * 40,
        source_key=slug,
    )
    event._allow_public_id_assignment = True
    event.public_id = public_id
    event.save()
    return event


def _content(event: Event, **overrides: object) -> EventContent:
    values: dict[str, object] = {
        "type": EventContent.Type.WEBINAR,
        "starts_at": STARTS_AT,
        "description_html": "<p>A synthetic event.</p>",
        "description_text": "A synthetic event.",
    }
    values.update(overrides)
    return EventContent.objects.create(event=event, **values)


class EventContentConstraintTests(TestCase):
    def setUp(self) -> None:
        self.event = _event(public_id=9_001, slug="a-synthetic-event", title="A synthetic event")

    def test_an_end_before_the_start_is_refused(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            _content(self.event, ends_at=STARTS_AT - timedelta(hours=1))

    def test_an_end_equal_to_the_start_is_allowed(self) -> None:
        """A zero-length slot is a real thing to record; a negative one is not."""

        content = _content(self.event, ends_at=STARTS_AT)

        self.assertEqual(content.ends_at, STARTS_AT)

    def test_a_season_without_an_episode_is_refused(self) -> None:
        """The two name one position between them, so half of it names nothing."""

        for season, episode in ((24, None), (None, 6)):
            with self.subTest(season=season, episode=episode):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    _content(self.event, season=season, episode=episode)

    def test_one_event_carries_at_most_one_content_row(self) -> None:
        _content(self.event)

        with self.assertRaises(IntegrityError), transaction.atomic():
            _content(self.event)

    def test_two_speakers_cannot_share_a_position_or_a_key(self) -> None:
        content = _content(self.event)
        EventSpeaker.objects.create(content=content, key="ada", name="Ada", position=0)

        with self.assertRaises(IntegrityError), transaction.atomic():
            EventSpeaker.objects.create(content=content, key="grace", name="Grace", position=0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            EventSpeaker.objects.create(content=content, key="ada", name="Ada again", position=1)

    def test_an_unlabelled_link_is_refused(self) -> None:
        content = _content(self.event)

        with self.assertRaises(IntegrityError), transaction.atomic():
            EventLink.objects.create(
                content=content, label="", url="https://example.invalid", position=0
            )

    def test_an_event_title_is_still_required(self) -> None:
        with self.assertRaises(ValidationError):
            Event(
                title="",
                slug="untitled",
                source_repository="DataTalksClub/example",
                source_revision="a" * 40,
                source_key="untitled",
            ).save()


class PublishedEventRecordTests(TestCase):
    def setUp(self) -> None:
        # The test database starts with the reviewed identities and no content
        # rows, so they publish nothing and these assertions see only what this
        # test describes.
        self.event = _event(public_id=9_001, slug="a-synthetic-event", title="A synthetic event")
        self.content = _content(self.event, season=24, episode=6)
        for position, (key, name, path) in enumerate(
            (
                ("ada", "Ada Lovelace", "/people/ada.html"),
                ("grace", "Grace Hopper", ""),
            )
        ):
            EventSpeaker.objects.create(
                content=self.content, key=key, name=name, public_path=path, position=position
            )
        for position, (label, url) in enumerate(
            (("Watch", "https://example.invalid/watch"), ("Slides", "https://example.invalid/s"))
        ):
            EventLink.objects.create(content=self.content, label=label, url=url, position=position)

    def test_the_record_carries_the_page_s_own_fields(self) -> None:
        record = published_event_record(self.event.id)

        assert record is not None
        self.assertEqual(record["identity_id"], str(self.event.id))
        self.assertEqual(record["public_path"], "/events/9001/a-synthetic-event")
        self.assertEqual(record["title"], "A synthetic event")
        self.assertEqual(record["type"], "webinar")
        self.assertEqual(record["starts_at"], STARTS_AT.isoformat())
        self.assertEqual(record["season"], 24)
        self.assertEqual(record["episode"], 6)

    def test_a_missing_end_is_absent_rather_than_invented(self) -> None:
        record = published_event_record(self.event.id)

        assert record is not None
        self.assertEqual(record["ends_at"], "")

    def test_speakers_and_links_keep_the_order_the_page_prints(self) -> None:
        record = published_event_record(self.event.id)

        assert record is not None
        self.assertEqual(
            [speaker["name"] for speaker in record["speakers"]],
            ["Ada Lovelace", "Grace Hopper"],
        )
        # A speaker without a person page still appears; only their link is absent.
        self.assertEqual(record["speakers"][1]["public_path"], "")
        self.assertEqual([link["label"] for link in record["links"]], ["Watch", "Slides"])

    def test_an_identity_with_no_content_row_publishes_nothing(self) -> None:
        """Identity is imported first and content follows; the gap is not a page."""

        bare = _event(public_id=9_002, slug="not-yet-described", title="Not yet described")

        self.assertIsNone(published_event_record(bare.id))
        self.assertEqual(
            [record["identity_id"] for record in published_event_records()],
            [str(self.event.id)],
        )

    def test_an_unpublished_lifecycle_is_not_listed(self) -> None:
        for lifecycle in (Event.Lifecycle.DRAFT, Event.Lifecycle.CANCELLED):
            with self.subTest(lifecycle=lifecycle):
                Event.objects.filter(pk=self.event.pk).update(lifecycle=lifecycle)

                self.assertIsNone(published_event_record(self.event.id))
                self.assertEqual(published_event_records(), ())

    def test_records_are_listed_newest_first(self) -> None:
        older = _event(public_id=9_003, slug="an-older-event", title="An older event")
        _content(older, starts_at=STARTS_AT - timedelta(days=30))

        self.assertEqual(
            [record["slug"] for record in published_event_records()],
            ["a-synthetic-event", "an-older-event"],
        )

    def test_a_database_with_no_content_rows_lists_no_events(self) -> None:
        EventContent.objects.all().delete()

        self.assertEqual(published_event_records(), ())
