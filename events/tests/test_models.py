"""Tests for Event identity: allocation, lookup, canonical routes, and serialization.

Moved from ``events/tests/test_identity.py`` when ``events/identity.py`` was
dissolved: what is tested here is what a live route, Studio, or the admin API
actually resolves an Event by, and it now lives directly on ``events/models.py``
-- see that module's "Identity: allocation, lookup, and canonical-path building"
section. The reviewed manifest's own parsing/replay tests moved to
``scripts/tests/test_identity_manifest.py`` alongside the ingest module that
now owns that machinery; the provider-identity and duplicate-creation-guard
tests moved to ``scripts/tests/test_registrant_import.py`` alongside
``scripts/prod/registrant_import.py``.
"""

from __future__ import annotations

import re
import uuid
from typing import ClassVar

from django.test import TestCase
from django.urls import Resolver404, resolve

from content import catalogue
from events.models import (
    Event,
    EventContent,
    EventIdentityNotFound,
    EventPublicIdSequence,
    canonical_detail_path,
    canonical_registration_path,
    create_event_identity,
    current_slug,
    resolve_source_identity,
    serialize_event_identity,
)
from events.queries import published_event_records


class EventIdentityTests(TestCase):
    def test_service_allocation_is_immutable_monotonic_and_never_reused(self) -> None:
        # The next public id the service allocates is one past whatever the
        # catalogue already holds -- a fact about the sequence, not a literal
        # tied to how many events the fixture happens to carry.
        expected_first_public_id = EventPublicIdSequence.objects.get(pk=1).next_public_id
        first = create_event_identity(
            title="Identity fixture",
            source_repository="DataTalksClub/test",
            source_revision="a" * 40,
            source_key="fixture-event",
        )
        first_id = first.id
        first_public_id = first.public_id
        self.assertEqual(first_public_id, expected_first_public_id)
        first.delete()
        second = create_event_identity(
            title="Second identity fixture",
            source_repository="DataTalksClub/test",
            source_revision="a" * 40,
            source_key="fixture-event-2",
        )
        self.assertEqual(second.public_id, expected_first_public_id + 1)
        self.assertNotEqual(second.id, first_id)
        self.assertEqual(
            EventPublicIdSequence.objects.get(pk=1).next_public_id,
            expected_first_public_id + 2,
        )

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

    def test_title_change_updates_only_cosmetic_slug(self) -> None:
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
        self.assertEqual(
            canonical_detail_path(event.id),
            f"/events/{public_id}/identity-fixture-renamed",
        )
        self.assertEqual(current_slug(event.id), "identity-fixture-renamed")
        event.id = uuid.uuid4()
        with self.assertRaisesMessage(ValueError, "event identity cannot be reassigned"):
            event.save()

    def test_exact_source_identity_resolution_never_guesses(self) -> None:
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
        self.assertEqual(
            canonical_registration_path(event.id), canonical_detail_path(event.id) + "/register"
        )
        with self.assertRaises(EventIdentityNotFound):
            resolve_source_identity(
                repository=event.source_repository,
                revision=event.source_revision,
                source_key=event.source_key + "-guessed",
            )

    def test_public_projection_and_management_metadata_keep_the_identity_boundary(self) -> None:
        canonical_paths = {event["public_path"] for event in published_event_records()}
        speaker_paths = {
            relationship["public_path"]
            for person in catalogue.people()
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
        }
        self.assertEqual(len(sources), 2)
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
        event = create_event_identity(
            title=(
                "How to work with AI coding agents: spec-driven development "
                "context and loop engineering workflows"
            ),
            source_repository="DataTalksClub/test",
            source_revision="a" * 40,
            source_key="fixture-long-slug-event",
        )
        # The stored slug respects the URL budget: however long the title the
        # event was imported under, the canonical address carries the truncated
        # slug, cut at a word boundary.
        canonical = canonical_detail_path(event.id)
        self.assertEqual(
            canonical,
            f"/events/{event.public_id}/how-to-work-with-ai-coding-agents-spec-driven-development",
        )
        # The address the pre-truncation title used to publish under is a
        # stale slug like any other: exactly one redirect hop to canonical.
        stale_path = f"{canonical}-context-and-loop-engineering-workflows"

        # Identity alone publishes nothing: the page renders once the event
        # carries its content row.
        EventContent.objects.create(
            event=event,
            type=EventContent.Type.WEBINAR,
            starts_at="2026-08-10T15:00:00+00:00",
            description_html="<p>A synthetic long-slug event.</p>",
            description_text="A synthetic long-slug event.",
        )

        response = self.client.get(stale_path, follow=False)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], canonical)
        self.assertEqual(self.client.get(canonical).status_code, 200)

    def test_noncanonical_id_case_slash_and_unknown_forms_are_exact_404s(self) -> None:
        """A form that still parses as a numeric public ID routes to ``event_detail``.

        Those two forms keep the bounded event-route 404 contract (an explicit,
        query-sensitive ``Cache-Control``). Every other malformed spelling -- a
        signed/zero-padded/leading-zero ID, a trailing slash, a raw or uppercase
        UUID, or an unrecorded path -- matches no route at all now that the
        legacy UUID and date/title paths (and their catch-all) are retired, so
        it falls through to the ordinary unmatched-URLconf 404 instead.
        """

        public_id = self.event.public_id
        assert public_id is not None
        routed_but_unknown = (
            "/events/999999999/unknown",
            f"/events/{'9' * 80}/{self.event.slug}",
        )
        for path in routed_but_unknown:
            with self.subTest(path=path):
                response = self.client.get(path, follow=False)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Location", response.headers)
                self.assertEqual(response.headers["Cache-Control"], "max-age=0")
                self.assertContains(response, "Page not found", status_code=404)
                queried = self.client.get(f"{path}?utm_source=route-test", follow=False)
                self.assertEqual(queried.status_code, 404)
                self.assertEqual(queried.headers["Cache-Control"], "no-store, max-age=0")

        unmatched = (
            f"/events/0{public_id}/{self.event.slug}",
            f"/events/+{public_id}/{self.event.slug}",
            f"/events/-{public_id}/{self.event.slug}",
            f"/events/0/{self.event.slug}",
            f"/events/{public_id}/{self.event.slug}/",
            f"/events/{public_id}/",
            f"/events/{str(self.event.id).upper()}/{self.event.slug}",
            f"/events/{self.event.id}/",
            f"/events/{self.event.id}/{self.event.slug}",
            f"/events/{self.event.id}",
            "/events/not-inventoried",
            "/events/00000000-0000-4000-8000-000000000000/nope",
        )
        for path in unmatched:
            with self.subTest(path=path):
                response = self.client.get(path, follow=False)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("Location", response.headers)
                self.assertContains(response, "Page not found", status_code=404)

    def test_all_event_route_classes_reject_unsafe_methods_without_mutation(self) -> None:
        paths = (
            self.path,
            f"/events/{self.event.public_id}",
            f"/events/{self.event.public_id}/stale-title",
        )
        before = Event.objects.count()
        for path in paths:
            with self.subTest(path=path):
                response = self.client.post(path)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.headers["Allow"], "GET, HEAD")
                self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(Event.objects.count(), before)

    def test_retired_legacy_event_paths_are_plain_404s_not_redirects(self) -> None:
        """Every pre-numeric-canonical event path is gone: no lookup, no redirect route.

        DataTalks.Club events were always hosted externally; this site itself served a
        bounded checked projection at date-prefixed paths, and later a UUID/slug path,
        before the numeric public-ID canonical existed (see the reviewed identity
        manifest and ``_docs/specs/05-events-registration-email.md``'s pre-cutover
        text). The owner ruled that no redirect is owed for any of them: an event is
        referred to only by its id.
        """

        # No legacy_date_path alias rows exist any more to look up: the source key of
        # a manifest-sourced event still carries the date-prefixed spelling verbatim,
        # so it stands in for the retired alias path without depending on removed data.
        for path in (
            f"/events/{self.event.source_key}",
            f"/events/{self.event.source_key}/",
            "/events/not-inventoried",
            f"/events/{self.event.id}/{self.event.slug}",
            f"/events/{self.event.id}",
        ):
            with self.subTest(path=path):
                get_response = self.client.get(path, follow=False)
                self.assertEqual(get_response.status_code, 404)
                self.assertNotIn("Location", get_response.headers)
                post_response = self.client.post(path)
                self.assertEqual(post_response.status_code, 404)
