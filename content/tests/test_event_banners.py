from __future__ import annotations

import struct
from pathlib import Path

from community_base.events.models import Event
from django.test import TestCase

from content.event_banners import EVENT_BANNER_FILENAMES, event_banner_url
from event_registrants.models import EventOperatorConfig
from events.identity import canonical_detail_path
from events.queries import published_event_records
from test_support.reference_data import load_reviewed_reference_data

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PNG_SIZE = (1000, 1000)


class EventBannerTests(TestCase):
    def test_known_event_id_resolves_to_a_static_url(self) -> None:
        identity_id = next(iter(EVENT_BANNER_FILENAMES))

        self.assertEqual(
            event_banner_url({"identity_id": identity_id}),
            f"/static/core/event-banners/{EVENT_BANNER_FILENAMES[identity_id]}",
        )

    def test_unknown_or_malformed_event_identity_uses_the_empty_fallback(self) -> None:
        for event in ({}, {"identity_id": 42}, {"identity_id": "not-mapped"}):
            with self.subTest(event=event):
                self.assertEqual(event_banner_url(event), "")

    def test_every_published_event_banner_is_a_1000_square_png(self) -> None:
        for filename in EVENT_BANNER_FILENAMES.values():
            with self.subTest(filename=filename):
                path = REPOSITORY_ROOT / "core" / "static" / "core" / "event-banners" / filename
                self.assertTrue(path.is_file())
                payload = path.read_bytes()
                self.assertEqual(payload[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(struct.unpack(">II", payload[16:24]), EXPECTED_PNG_SIZE)


class EventBannerPageTests(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        load_reviewed_reference_data()

    def test_event_pages_render_the_uploaded_picture_and_social_metadata(self) -> None:
        # Every event is created with its picture (events.pictures), and the
        # uploaded picture wins over the code-owned artwork.
        for identity_id in EVENT_BANNER_FILENAMES:
            with self.subTest(identity_id=identity_id):
                event = Event.objects.get(content_id=identity_id)
                response = self.client.get(canonical_detail_path(event.content_id))
                body = response.content.decode()
                image_path = f"/events/{event.public_id}/banner"
                canonical_image_url = f"https://datatalks.club{image_path}"
                projected_event = {
                    record["identity_id"]: record for record in published_event_records()
                }.get(identity_id)

                self.assertEqual(response.status_code, 200)
                assert projected_event is not None
                self.assertIn('data-testid="event-banner"', body)
                self.assertIn(f'src="{image_path}"', body)
                self.assertIn(
                    f'<meta property="og:image" content="{canonical_image_url}">',
                    body,
                )
                self.assertEqual(event.slug, projected_event["slug"])

    def test_a_pre_rule_event_without_a_picture_keeps_the_no_image_fallback(self) -> None:
        event = Event.objects.exclude(content_id__in=EVENT_BANNER_FILENAMES).first()
        self.assertIsNotNone(event)
        assert event is not None
        # A row written before the picture rule: queryset delete bypasses the guard.
        EventOperatorConfig.objects.filter(event=event).delete()

        response = self.client.get(canonical_detail_path(event.content_id))
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('data-testid="event-banner"', body)
        self.assertNotIn('<meta property="og:image"', body)
        self.assertNotIn('<meta name="twitter:image"', body)
