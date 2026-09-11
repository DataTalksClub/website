"""Tests for the reviewed Event identity manifest: parsing, validation, and replay.

Moved alongside ``scripts/prod/identity_manifest.py`` (formerly part of
``events/tests/test_identity.py``, when the manifest machinery still lived in
``events/identity.py``): this is one-time ingest domain logic, so its tests
live under ``scripts/tests`` with the rest of that ingest layer's coverage.
"""

from __future__ import annotations

import json
from copy import deepcopy

from django.test import TestCase

from events.models import (
    Event,
    EventPublicIdSequence,
    EventQnaSession,
    create_event_identity,
)
from scripts.prod.identity_manifest import (
    EventIdentityError,
    import_identity_manifest,
    parse_identity_manifest,
)
from test_support.reference_data import EVENT_IDENTITY_MANIFEST


class EventIdentityManifestTests(TestCase):
    def test_checked_manifest_and_database_freeze_all_numeric_mappings(self) -> None:
        from scripts.prod.identity_manifest import load_identity_manifest

        manifest = load_identity_manifest(EVENT_IDENTITY_MANIFEST)

        self.assertEqual(manifest.schema_version, 4)
        self.assertEqual(
            {(str(item.id), item.public_id) for item in manifest.events},
            {
                (str(event_id), public_id)
                for event_id, public_id in Event.objects.values_list("id", "public_id")
            },
        )
        for item in manifest.events:
            with self.subTest(event=item.id):
                self.assertEqual(
                    item.canonical_path,
                    f"/events/{item.public_id}/{item.slug}",
                )
                self.assertNotRegex(item.canonical_path, r"[0-9a-f]{8}-[0-9a-f-]{27}")

    def test_manifest_rejects_missing_duplicate_and_renumbered_public_mappings(self) -> None:
        payload = json.loads(EVENT_IDENTITY_MANIFEST.read_text(encoding="utf-8"))
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
            import_identity_manifest(path=EVENT_IDENTITY_MANIFEST, dry_run=True)

    def test_manifest_import_replay_is_byte_stable_and_a_preflight_noop(self) -> None:
        before = tuple(Event.objects.order_by("id").values_list("id", "public_id", "slug"))
        first = import_identity_manifest(path=EVENT_IDENTITY_MANIFEST, dry_run=True)
        applied = import_identity_manifest(path=EVENT_IDENTITY_MANIFEST)
        second = import_identity_manifest(path=EVENT_IDENTITY_MANIFEST, dry_run=True)
        after = tuple(Event.objects.order_by("id").values_list("id", "public_id", "slug"))

        self.assertTrue(first.replayed)
        self.assertTrue(applied.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(first.event_total, 421)
        self.assertEqual(before, after)

    def test_importing_into_an_empty_database_leaves_the_allocator_above_the_manifest(
        self,
    ) -> None:
        """The import writes public IDs the allocator did not hand out, so it owes it."""

        EventQnaSession.objects.all().delete()
        Event.objects.all().delete()
        EventPublicIdSequence.objects.all().delete()

        report = import_identity_manifest(path=EVENT_IDENTITY_MANIFEST)

        self.assertEqual(report.events_created, 421)
        self.assertEqual(EventPublicIdSequence.objects.get(pk=1).next_public_id, 422)
        allocated = create_event_identity(
            title="Allocated after a bootstrap import",
            source_repository="DataTalksClub/test",
            source_revision="a" * 40,
            source_key="allocated-after-bootstrap",
        )
        self.assertEqual(allocated.public_id, 422)
