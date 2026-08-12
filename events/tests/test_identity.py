from __future__ import annotations

import uuid
from typing import ClassVar

from django.test import TestCase

from content.public_data import public_projection
from events.identity import (
    EventIdentityNotFound,
    canonical_detail_path,
    canonical_registration_path,
    current_slug,
    load_identity_manifest,
    resolve_legacy_path,
    resolve_source_identity,
)
from events.models import Event, EventAlias


class EventIdentityManifestTests(TestCase):
    def test_checked_manifest_and_database_have_one_identity_per_projection_row(self) -> None:
        manifest = load_identity_manifest()
        projection = public_projection()
        self.assertEqual(len(manifest.events), 421)
        self.assertEqual(len(manifest.aliases), 421)
        self.assertEqual(Event.objects.count(), 421)
        self.assertEqual(EventAlias.objects.count(), 421)
        self.assertEqual(
            {str(item.id) for item in manifest.events},
            set(projection["events_by_identity_id"]),
        )
        self.assertTrue(
            all(
                item.path == f"/events/{item.id}/{item.slug}" and "/202" not in item.path
                for item in manifest.events
            )
        )

    def test_title_change_updates_only_cosmetic_slug_and_primary_key_reassignment_fails(
        self,
    ) -> None:
        event = Event.objects.create(
            title="Identity fixture",
            source_repository="DataTalksClub/test",
            source_revision="a" * 40,
            source_key="fixture-event",
        )
        event_id = event.id
        event.title = "Identity fixture renamed"
        event.slug = ""
        event.save()
        event.refresh_from_db()
        self.assertEqual(event.id, event_id)
        self.assertEqual(event.slug, "identity-fixture-renamed")
        self.assertTrue(
            EventAlias.objects.filter(
                event=event,
                source_path=f"/events/{event.id}/identity-fixture",
                kind=EventAlias.Kind.TITLE_SLUG,
            ).exists()
        )
        self.assertEqual(
            canonical_detail_path(event.id),
            f"/events/{event.public_id}/identity-fixture-renamed",
        )
        self.assertEqual(current_slug(event.id), "identity-fixture-renamed")
        event.id = uuid.uuid4()
        with self.assertRaisesMessage(ValueError, "event identity cannot be reassigned"):
            event.save()

    def test_reassignment_to_an_existing_uuid_cannot_overwrite_that_identity(self) -> None:
        first = Event.objects.order_by("source_key").first()
        second = Event.objects.order_by("source_key").last()
        assert first is not None and second is not None and first.id != second.id
        original_title = second.title
        first.id = second.id
        first.title = "Attempted identity overwrite"
        with self.assertRaisesMessage(ValueError, "event identity cannot be reassigned"):
            first.save()
        second.refresh_from_db()
        self.assertEqual(second.title, original_title)

    def test_exact_source_attachment_and_alias_resolution_are_not_slug_inference(self) -> None:
        event = Event.objects.order_by("source_key").first()
        assert event is not None
        self.assertEqual(
            resolve_source_identity(
                repository=event.source_repository,
                revision=event.source_revision,
                source_key=event.source_key,
            ).id,
            event.id,
        )
        alias = event.aliases.get()
        self.assertEqual(resolve_legacy_path(alias.source_path).id, event.id)
        self.assertEqual(
            canonical_registration_path(event.id), canonical_detail_path(event.id) + "/register"
        )
        with self.assertRaises(EventIdentityNotFound):
            resolve_legacy_path(alias.source_path + "-guessed")

    def test_people_relationships_use_uuid_event_paths(self) -> None:
        projection = public_projection()
        canonical_paths = {event["public_path"] for event in projection["events"]}
        speaker_paths = {
            relationship["public_path"]
            for person in projection["people"]
            for relationship in person["relationships"]
            if relationship["role"] == "speaker"
        }
        self.assertTrue(speaker_paths)
        self.assertTrue(speaker_paths <= canonical_paths)
        self.assertTrue(all(path.count("/") == 3 for path in speaker_paths))


class EventIdentityRouteTests(TestCase):
    event: ClassVar[Event]
    path: ClassVar[str]

    @classmethod
    def setUpTestData(cls) -> None:
        event = Event.objects.order_by("source_key").first()
        assert event is not None
        cls.event = event
        cls.path = canonical_detail_path(event.id)

    def test_canonical_stale_and_legacy_routes_are_terminal(self) -> None:
        response = self.client.get(self.path + "?utm_source=fixture")
        self.assertEqual(response.status_code, 200)
        stale = self.client.get(
            f"/events/{self.event.id}/stale-title?utm_source=fixture",
        )
        self.assertEqual(stale.status_code, 301)
        self.assertEqual(stale["Location"], self.path + "?utm_source=fixture")
        legacy = self.client.get(self.event.aliases.get().source_path + "?utm_source=fixture")
        self.assertEqual(legacy.status_code, 301)
        self.assertEqual(legacy["Location"], self.path + "?utm_source=fixture")
        self.assertEqual(self.client.head(self.path).status_code, 200)
        self.assertEqual(self.client.post(self.path).status_code, 405)
        self.assertEqual(
            self.client.get("/events/00000000-0000-4000-8000-000000000000/nope").status_code,
            404,
        )
        self.assertEqual(self.client.get("/events/not-inventoried").status_code, 404)
