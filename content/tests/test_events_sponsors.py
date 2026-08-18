from __future__ import annotations

import uuid
from unittest import mock

from django.db import DatabaseError
from django.test import TestCase

from core.sponsors import archive_sponsor, create_sponsor, update_sponsor


class PublicEventsSponsorTests(TestCase):
    def test_supported_by_section_is_omitted_until_active_and_escapes_text(self) -> None:
        empty = self.client.get("/events")
        self.assertEqual(empty.status_code, 200)
        self.assertNotContains(empty, "Supported by")
        created = create_sponsor(
            payload={
                "key": "acme",
                "name": 'Acme & "Friends"',
                "url": "https://acme.example/path",
                "tagline": "Plain & simple",
                "lifecycle": "draft",
                "assignments": [
                    {"placement": "events_hub", "position": 1, "enabled": True},
                ],
            },
            source="studio",
            idempotency_key=str(uuid.uuid4()),
            actor_ref="user:188",
        )
        draft = self.client.get("/events")
        self.assertNotContains(draft, "Supported by")
        update_sponsor(
            sponsor_id=created.sponsor["id"],
            payload={
                "name": 'Acme & "Friends"',
                "url": "https://acme.example/path",
                "tagline": "Plain & simple",
                "lifecycle": "active",
                "assignments": [
                    {"placement": "events_hub", "position": 1, "enabled": True},
                ],
            },
            expected_revision=1,
            source="studio",
            idempotency_key=str(uuid.uuid4()),
            actor_ref="user:188",
        )
        visible = self.client.get("/events")
        self.assertContains(visible, "Supported by")
        self.assertContains(visible, "Acme &amp; &quot;Friends&quot;")
        self.assertContains(visible, "Plain &amp; simple")
        self.assertContains(visible, 'rel="sponsored noopener noreferrer"')
        self.assertContains(visible, 'href="https://acme.example/path"')
        archive_sponsor(
            sponsor_id=created.sponsor["id"],
            confirmed=True,
            expected_revision=2,
            source="studio",
            idempotency_key=str(uuid.uuid4()),
            actor_ref="user:188",
        )
        omitted = self.client.get("/events")
        self.assertNotContains(omitted, "Supported by")

    def test_unavailable_resolution_does_not_break_the_events_hub(self) -> None:
        with mock.patch(
            "core.sponsors.resolve_public_sponsors",
            side_effect=DatabaseError("unavailable"),
        ):
            response = self.client.get("/events")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Supported by")
