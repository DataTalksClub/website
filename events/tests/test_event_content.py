"""What a public event page says, read back off the shared event row.

Since #412 identity, schedule, description, speakers and links are one
``community_base.events.Event`` row: the DTC display vocabulary rides in the
row's ``tags``, links in its ``materials``, and speakers in ``Host`` links.
These tests pin the things that matter to the pages reading them: the record
is assembled in the order the page prints, only publicly visible statuses list,
and an undescribed row still publishes its schedule.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from community_base.events.models import Event, EventHost, Host
from django.test import TestCase

from events.queries import published_event_record, published_event_records

STARTS_AT = datetime(2026, 6, 1, 17, 0, tzinfo=UTC)


def _event(*, public_id: int, slug: str, title: str, **overrides: object) -> Event:
    values: dict[str, object] = {
        "content_id": uuid.uuid4(),
        "public_id": public_id,
        "title": title,
        "slug": slug,
        "status": "upcoming",
        "start_datetime": STARTS_AT,
        "kind": "standard",
        "tags": ["dtc-type:webinar"],
    }
    values.update(overrides)
    return Event.objects.create(**values)


def _describe(event: Event, **overrides: object) -> None:
    """Put the row into the described state the content import leaves it in."""

    values: dict[str, object] = {
        "description_html": "<p>A synthetic event.</p>",
        "description": "A synthetic event.",
    }
    values.update(overrides)
    for name, value in values.items():
        setattr(event, name, value)
    event.save()


def _speaker(event: Event, *, key: str, name: str, position: int) -> None:
    host = Host.objects.create(slug=key, name=name, kind="speaker", external_ref=key)
    EventHost.objects.create(event=event, host=host, position=position, role="speaker")


class PublishedEventRecordTests(TestCase):
    def setUp(self) -> None:
        # These assertions are about what the query returns for a controlled set
        # -- which events are listed, and in what order -- so the 421 reviewed
        # events the test database is seeded with are cleared first. Removing
        # them here rolls back with the test.
        Event.objects.all().delete()
        self.event = _event(public_id=9_001, slug="a-synthetic-event", title="A synthetic event")
        _describe(self.event)
        _speaker(self.event, key="ada", name="Ada Lovelace", position=0)
        _speaker(self.event, key="grace", name="Grace Hopper", position=1)
        self.event.materials = [
            {"label": "Watch", "url": "https://example.invalid/watch"},
            {"label": "Slides", "url": "https://example.invalid/s"},
        ]
        self.event.tags = ["dtc-type:webinar", "dtc-season:24", "dtc-episode:6"]
        self.event.save()

    def test_the_record_carries_the_page_s_own_fields(self) -> None:
        record = published_event_record(self.event.content_id)

        assert record is not None
        self.assertEqual(record["identity_id"], str(self.event.content_id))
        self.assertEqual(record["public_path"], "/events/9001/a-synthetic-event")
        self.assertEqual(record["title"], "A synthetic event")
        self.assertEqual(record["type"], "webinar")
        self.assertEqual(record["starts_at"], STARTS_AT.isoformat())
        self.assertEqual(record["season"], 24)
        self.assertEqual(record["episode"], 6)

    def test_a_missing_end_is_absent_rather_than_invented(self) -> None:
        record = published_event_record(self.event.content_id)

        assert record is not None
        self.assertEqual(record["ends_at"], "")

    def test_speakers_and_links_keep_the_order_the_page_prints(self) -> None:
        record = published_event_record(self.event.content_id)

        assert record is not None
        self.assertEqual(
            [speaker["name"] for speaker in record["speakers"]],
            ["Ada Lovelace", "Grace Hopper"],
        )
        # The profile path is resolved from the site people catalogue at read
        # time; a speaker with no profile page still appears, their link absent.
        self.assertEqual(record["speakers"][1]["public_path"], "")
        self.assertEqual([link["label"] for link in record["links"]], ["Watch", "Slides"])

    def test_an_undescribed_row_still_publishes_its_schedule(self) -> None:
        """A manifest-only row has a time but no description yet; the hub lists it.

        The shared row cannot exist without a schedule, so the former
        "identity without content is no page" gate is gone by construction:
        what the page shows is the row, and a description arrives with the
        reviewed import.
        """

        bare = _event(public_id=9_002, slug="not-yet-described", title="Not yet described")

        self.assertIsNotNone(published_event_record(bare.content_id))
        self.assertIn(
            str(bare.content_id), [record["identity_id"] for record in published_event_records()]
        )

    def test_an_unpublished_status_is_not_listed(self) -> None:
        for status in ("draft", "cancelled", "archived"):
            with self.subTest(status=status):
                Event.objects.filter(pk=self.event.pk).update(status=status)

                self.assertIsNone(published_event_record(self.event.content_id))
                self.assertEqual(published_event_records(), ())

    def test_records_are_listed_newest_first(self) -> None:
        older = _event(public_id=9_003, slug="an-older-event", title="An older event")
        _describe(older)
        older.start_datetime = STARTS_AT - timedelta(days=30)
        older.save()

        self.assertEqual(
            [record["slug"] for record in published_event_records()],
            ["a-synthetic-event", "an-older-event"],
        )

    def test_a_database_with_no_event_rows_lists_no_events(self) -> None:
        Event.objects.all().delete()

        self.assertEqual(published_event_records(), ())
