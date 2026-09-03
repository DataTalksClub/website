from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from pathlib import Path
from typing import ClassVar
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError
from django.test import TestCase
from django.urls import Resolver404, resolve

from content.public_data import public_projection
from events.identity import (
    EventIdentityError,
    EventIdentityNotFound,
    canonical_detail_path,
    canonical_registration_path,
    create_event_identity,
    current_slug,
    import_identity_manifest,
    load_identity_manifest,
    parse_identity_manifest,
    resolve_legacy_path,
    resolve_source_identity,
    serialize_event_identity,
)
from events.models import Event, EventAlias, EventPublicIdSequence, EventQnaSession

ROOT = Path(__file__).resolve().parents[2]


class EventIdentityManifestTests(TestCase):
    def test_checked_manifest_and_database_freeze_all_numeric_mappings_and_aliases(self) -> None:
        manifest = load_identity_manifest()
        projection = public_projection()

        self.assertEqual(manifest.schema_version, 2)
        self.assertEqual(
            {(str(item.id), item.public_id) for item in manifest.events},
            {
                (str(event_id), public_id)
                for event_id, public_id in Event.objects.values_list("id", "public_id")
            },
        )
        self.assertEqual(
            {str(item.id) for item in manifest.events},
            set(projection["events_by_identity_id"]),
        )
        for item in manifest.events:
            with self.subTest(event=item.id):
                self.assertEqual(
                    item.canonical_path,
                    f"/events/{item.public_id}/{item.slug}",
                )
                self.assertNotRegex(item.canonical_path, r"[0-9a-f]{8}-[0-9a-f-]{27}")
                self.assertEqual(
                    {alias.kind for alias in item.aliases},
                    {EventAlias.Kind.LEGACY_DATE_PATH, EventAlias.Kind.LEGACY_UUID},
                )
                self.assertIn(
                    f"/events/{item.id}/{item.slug}", {a.source_path for a in item.aliases}
                )
                self.assertIn(f"/events/{item.id}", {a.source_path for a in item.aliases})

    def test_manifest_rejects_missing_duplicate_and_renumbered_public_mappings(self) -> None:
        payload = json.loads(
            Path("events/event_identity_manifest.json").read_text(encoding="utf-8")
        )
        missing = deepcopy(payload)
        del missing["events"][0]["public_id"]
        with self.assertRaisesMessage(EventIdentityError, "manifest_event_shape_invalid"):
            parse_identity_manifest(missing)

        duplicate = deepcopy(payload)
        duplicate["events"][1]["public_id"] = duplicate["events"][0]["public_id"]
        duplicate["events"][1]["canonical_path"] = duplicate["events"][0]["canonical_path"]
        with self.assertRaises(EventIdentityError):
            parse_identity_manifest(duplicate)

        event = Event.objects.order_by("public_id").first()
        assert event is not None and event.public_id is not None
        Event.objects.filter(pk=event.pk).update(public_id=10_000)
        with self.assertRaisesMessage(EventIdentityError, "public_id_renumber_forbidden"):
            import_identity_manifest(dry_run=True)
        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "Public Event UUID/public-ID mapping is incomplete",
        ):
            public_projection()

    def test_unavailable_database_serves_the_manifest_identity_snapshot(self) -> None:
        manifest = load_identity_manifest()
        expected_paths = {item.canonical_path for item in manifest.events}

        with mock.patch.object(
            Event.objects,
            "order_by",
            side_effect=OperationalError("unable to open database file"),
        ):
            projection = public_projection()

        self.assertEqual(
            {event["public_path"] for event in projection["events"]},
            expected_paths,
        )

    def test_manifest_import_replay_is_byte_stable_and_a_preflight_noop(self) -> None:
        before = tuple(Event.objects.order_by("id").values_list("id", "public_id", "slug"))
        first = import_identity_manifest(dry_run=True)
        applied = import_identity_manifest()
        second = import_identity_manifest(dry_run=True)
        after = tuple(Event.objects.order_by("id").values_list("id", "public_id", "slug"))

        self.assertTrue(first.replayed)
        self.assertTrue(applied.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual((first.event_total, first.alias_total), (421, 1_684))
        self.assertEqual(before, after)

    def test_importing_into_an_empty_database_leaves_the_allocator_above_the_manifest(
        self,
    ) -> None:
        """The import writes public IDs the allocator did not hand out, so it owes it."""

        EventAlias.objects.all().delete()
        EventQnaSession.objects.all().delete()
        Event.objects.all().delete()
        EventPublicIdSequence.objects.all().delete()

        report = import_identity_manifest()

        self.assertEqual(report.events_created, 421)
        self.assertEqual(EventPublicIdSequence.objects.get(pk=1).next_public_id, 422)
        allocated = create_event_identity(
            title="Allocated after a bootstrap import",
            source_repository="DataTalksClub/test",
            source_revision="a" * 40,
            source_key="allocated-after-bootstrap",
        )
        self.assertEqual(allocated.public_id, 422)

    def test_service_allocation_is_immutable_monotonic_and_never_reused(self) -> None:
        first = create_event_identity(
            title="Identity fixture",
            source_repository="DataTalksClub/test",
            source_revision="a" * 40,
            source_key="fixture-event",
        )
        first_id = first.id
        first_public_id = first.public_id
        self.assertEqual(first_public_id, 422)
        first.delete()
        second = create_event_identity(
            title="Second identity fixture",
            source_repository="DataTalksClub/test",
            source_revision="a" * 40,
            source_key="fixture-event-2",
        )
        self.assertEqual(second.public_id, 423)
        self.assertNotEqual(second.id, first_id)
        self.assertEqual(EventPublicIdSequence.objects.get(pk=1).next_public_id, 424)

        second.public_id = 500
        with self.assertRaisesMessage(ValueError, "event public ID is immutable"):
            second.save()
        with self.assertRaisesMessage(
            ValueError,
            "event public ID must be allocated by the identity service",
        ):
            Event.objects.create(
                title="Unallocated",
                source_repository="DataTalksClub/test",
                source_revision="a" * 40,
                source_key="unallocated",
            )

    def test_title_change_updates_only_cosmetic_slug_and_keeps_numeric_alias(self) -> None:
        event = create_event_identity(
            title="Identity fixture",
            source_repository="DataTalksClub/test",
            source_revision="a" * 40,
            source_key="fixture-event",
        )
        event_id = event.id
        public_id = event.public_id
        event.title = "Identity fixture renamed"
        event.slug = ""
        event.save()
        event.refresh_from_db()

        self.assertEqual((event.id, event.public_id), (event_id, public_id))
        self.assertEqual(event.slug, "identity-fixture-renamed")
        self.assertTrue(
            EventAlias.objects.filter(
                event=event,
                source_path=f"/events/{public_id}/identity-fixture",
                kind=EventAlias.Kind.TITLE_SLUG,
            ).exists()
        )
        self.assertEqual(
            canonical_detail_path(event.id),
            f"/events/{public_id}/identity-fixture-renamed",
        )
        self.assertEqual(current_slug(event.id), "identity-fixture-renamed")
        event.id = uuid.uuid4()
        with self.assertRaisesMessage(ValueError, "event identity cannot be reassigned"):
            event.save()

    def test_exact_source_and_alias_resolution_never_guess(self) -> None:
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
        alias = event.aliases.filter(kind=EventAlias.Kind.LEGACY_DATE_PATH).first()
        assert alias is not None
        self.assertEqual(resolve_legacy_path(alias.source_path).id, event.id)
        self.assertEqual(
            canonical_registration_path(event.id), canonical_detail_path(event.id) + "/register"
        )
        with self.assertRaises(EventIdentityNotFound):
            resolve_legacy_path(alias.source_path + "-guessed")

    def test_public_projection_and_management_metadata_keep_the_identity_boundary(self) -> None:
        projection = public_projection()
        canonical_paths = {event["public_path"] for event in projection["events"]}
        speaker_paths = {
            relationship["public_path"]
            for person in projection["people"]
            for relationship in person["relationships"]
            if relationship["role"] == "speaker"
        }
        self.assertTrue(speaker_paths <= canonical_paths)
        self.assertTrue(
            all(re.fullmatch(r"/events/[1-9][0-9]*/[-a-z0-9]+", path) for path in canonical_paths)
        )

        event = Event.objects.order_by("public_id").first()
        assert event is not None
        serialized = serialize_event_identity(event)
        self.assertEqual(serialized["id"], str(event.id))
        self.assertEqual(serialized["public_id"], event.public_id)
        self.assertEqual(serialized["canonical_path"], canonical_detail_path(event.id))
        self.assertEqual(
            serialized["public_url"], f"https://datatalks.club{canonical_detail_path(event.id)}"
        )
        self.assertNotIn(str(event.id), serialized["canonical_path"])
        with self.assertRaises(Resolver404):
            resolve(f"/api/v1/admin/events/identities/{event.public_id}")
        self.assertEqual(
            resolve(f"/api/v1/admin/events/identities/{event.id}").kwargs["event_id"],
            event.id,
        )


class EventIdentityRouteTests(TestCase):
    event: ClassVar[Event]
    path: ClassVar[str]

    @classmethod
    def setUpTestData(cls) -> None:
        event = Event.objects.order_by("public_id").first()
        assert event is not None
        cls.event = event
        cls.path = canonical_detail_path(event.id)

    def test_canonical_get_head_and_metadata_are_numeric_only(self) -> None:
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Location", response.headers)
        self.assertContains(
            response,
            f'<link rel="canonical" href="https://datatalks.club{self.path}">',
            count=1,
        )
        self.assertContains(
            response,
            f'<meta property="og:url" content="https://datatalks.club{self.path}">',
            count=1,
        )
        body = response.content.decode()
        self.assertIn(f'"url": "https://datatalks.club{self.path}"', body)
        self.assertNotIn(str(self.event.id), body)
        head = self.client.head(self.path)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.content, b"")

        queried = self.client.get(f"{self.path}?utm_source=route-test")
        self.assertEqual(queried.status_code, 200)
        self.assertEqual(queried.headers["Cache-Control"], "no-store, max-age=0")

    def test_all_approved_alias_classes_redirect_one_hop_with_raw_query(self) -> None:
        query = "utm_source=route-test&x=%2F&x=&q=A+B&q=A%20B"
        sources = {
            f"/events/{self.event.public_id}",
            f"/events/{self.event.public_id}/stale-title",
            f"/events/{self.event.id}/{self.event.slug}",
            f"/events/{self.event.id}",
            *self.event.aliases.filter(kind=EventAlias.Kind.LEGACY_DATE_PATH).values_list(
                "source_path", flat=True
            ),
        }
        self.assertEqual(len(sources), 6)
        for source in sources:
            with self.subTest(source=source):
                response = self.client.get(f"{source}?{query}", follow=False)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response.headers["Location"], f"{self.path}?{query}")
                self.assertEqual(response.headers["Cache-Control"], "public, max-age=300")
                terminal = self.client.get(response.headers["Location"], follow=False)
                self.assertEqual(terminal.status_code, 200)
                self.assertNotIn("Location", terminal.headers)
                head = self.client.head(f"{source}?{query}", follow=False)
                self.assertEqual(head.status_code, 301)
                self.assertEqual(head.headers["Location"], f"{self.path}?{query}")

    def test_long_event_slug_uses_short_canonical_and_stale_slug_redirects(self) -> None:
        event = Event.objects.get(public_id=356)
        canonical = "/events/356/how-to-work-with-ai-coding-agents-spec-driven-development"

        self.assertEqual(canonical_detail_path(event.id), canonical)
        response = self.client.get(
            "/events/356/how-to-work-with-ai-coding-agents-spec-driven-development-"
            "context-and-loop-engineering-workflows",
            follow=False,
        )

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], canonical)
        self.assertEqual(self.client.get(canonical).status_code, 200)

    def test_noncanonical_id_case_slash_and_unknown_forms_are_exact_404s(self) -> None:
        public_id = self.event.public_id
        assert public_id is not None
        malformed = (
            f"/events/0{public_id}/{self.event.slug}",
            f"/events/+{public_id}/{self.event.slug}",
            f"/events/-{public_id}/{self.event.slug}",
            f"/events/0/{self.event.slug}",
            f"/events/{public_id}/{self.event.slug}/",
            f"/events/{public_id}/",
            f"/events/{str(self.event.id).upper()}/{self.event.slug}",
            f"/events/{self.event.id}/",
            "/events/999999999/unknown",
            f"/events/{'9' * 80}/{self.event.slug}",
            "/events/not-inventoried",
            "/events/00000000-0000-4000-8000-000000000000/nope",
        )
        for path in malformed:
            with self.subTest(path=path):
                response = self.client.get(path, follow=False)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Location", response.headers)
                self.assertEqual(response.headers["Cache-Control"], "max-age=0")
                self.assertContains(response, "Page not found", status_code=404)
                queried = self.client.get(f"{path}?utm_source=route-test", follow=False)
                self.assertEqual(queried.status_code, 404)
                self.assertEqual(queried.headers["Cache-Control"], "no-store, max-age=0")

    def test_all_event_route_classes_reject_unsafe_methods_without_mutation(self) -> None:
        date_alias = self.event.aliases.filter(
            kind=EventAlias.Kind.LEGACY_DATE_PATH,
            source_path__endswith="/",
        ).get()
        paths = (
            self.path,
            f"/events/{self.event.public_id}",
            f"/events/{self.event.public_id}/stale-title",
            f"/events/{self.event.id}/{self.event.slug}",
            f"/events/{self.event.id}",
            date_alias.source_path,
            "/events/not-inventoried",
        )
        before = Event.objects.count(), EventAlias.objects.count()
        for path in paths:
            with self.subTest(path=path):
                response = self.client.post(path)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.headers["Allow"], "GET, HEAD")
                self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
        self.assertEqual((Event.objects.count(), EventAlias.objects.count()), before)
