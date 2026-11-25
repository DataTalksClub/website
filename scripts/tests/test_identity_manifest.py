"""Tests for the reviewed Event identity manifest: parsing, validation, and replay.

Moved alongside ``scripts/prod/identity_manifest.py`` (formerly part of
``events/tests/test_identity.py``, when the manifest machinery still lived in
``events/identity.py``): this is one-time ingest domain logic, so its tests
live under ``scripts/tests`` with the rest of that ingest layer's coverage.
"""

from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from pathlib import Path

from community_base.events.models import Event, EventPublicIdSequence
from django.test import TestCase

from event_qna.models import EventQnaSession
from events.identity import create_event_identity
from scripts.prod.identity_manifest import (
    EventIdentityError,
    import_identity_manifest,
    parse_identity_manifest,
)
from test_support.event_pictures import TEST_EVENT_DESCRIPTION, TEST_EVENT_PICTURE
from test_support.reference_data import EVENT_CONTENT, EVENT_IDENTITY_MANIFEST

# The synthetic reference fixture's own event count (test_support/fixtures/
# reference/event_identity_manifest.json), read from the manifest itself
# rather than pinned as a literal here -- it is not this test's job to know
# how many events the fixture happens to carry, only that the import and the
# allocator agree with whatever that number is.
EVENT_FIXTURE_TOTAL = json.loads(EVENT_IDENTITY_MANIFEST.read_text())["counts"]["events"]


def _describe_every_record(path: Path) -> object:
    """The reviewed content with a synthetic, reviewed description on every record."""

    payload = json.loads(path.read_text())
    records = payload if isinstance(payload, list) else payload["events"]
    provenance = next(r["description_provenance"] for r in records if r["description_provenance"])
    for record in records:
        if not (record.get("description_text") or "").strip():
            record["description_text"] = f"Synthetic description for {record['title']}."
            record["description_html"] = f"<p>{record['description_text']}</p>"
            record["description_provenance"] = provenance
    return payload


class _EveryEventPicture:
    """A picture source holding the test picture for every event."""

    def lookup(self, *keys: object) -> object:
        return TEST_EVENT_PICTURE


class EventIdentityManifestTests(TestCase):
    def test_checked_manifest_and_database_freeze_all_numeric_mappings(self) -> None:
        from scripts.prod.identity_manifest import load_identity_manifest

        manifest = load_identity_manifest(EVENT_IDENTITY_MANIFEST)

        self.assertEqual(manifest.schema_version, 4)
        self.assertEqual(
            {(str(item.id), item.public_id) for item in manifest.events},
            {
                (str(event_id), public_id)
                for event_id, public_id in Event.objects.values_list("content_id", "public_id")
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
        self.assertEqual(first.event_total, EVENT_FIXTURE_TOTAL)
        self.assertEqual(before, after)

    def test_importing_into_an_empty_database_leaves_the_allocator_above_the_manifest(
        self,
    ) -> None:
        """The import writes public IDs the allocator did not hand out, so it owes it."""

        EventQnaSession.objects.all().delete()
        Event.objects.all().delete()
        EventPublicIdSequence.objects.all().delete()

        # Every event is created with a description and a picture; an undescribed
        # record refuses the whole import, so describe every record first.
        refused_before = Event.objects.count()
        with self.assertRaises(EventIdentityError) as caught:
            import_identity_manifest(
                path=EVENT_IDENTITY_MANIFEST,
                content_path=EVENT_CONTENT,
                pictures=_EveryEventPicture(),
            )
        self.assertEqual(str(caught.exception), "event_description_required")
        self.assertEqual(Event.objects.count(), refused_before)

        with tempfile.TemporaryDirectory() as scratch:
            described = Path(scratch) / "events.json"
            described.write_text(json.dumps(_describe_every_record(EVENT_CONTENT)))
            report = import_identity_manifest(
                path=EVENT_IDENTITY_MANIFEST, content_path=described, pictures=_EveryEventPicture()
            )

        self.assertEqual(report.events_created, EVENT_FIXTURE_TOTAL)
        self.assertEqual(
            EventPublicIdSequence.objects.get(pk=1).next_public_id, EVENT_FIXTURE_TOTAL + 1
        )
        allocated = create_event_identity(
            picture=TEST_EVENT_PICTURE,
            description=TEST_EVENT_DESCRIPTION,
            event_type="webinar",
            speakers=[("synthetic-speaker", "Synthetic Speaker")],
            title="Allocated after a bootstrap import",
            source_repository="DataTalksClub/test",
            source_revision="a" * 40,
            source_key="allocated-after-bootstrap",
        )
        self.assertEqual(allocated.public_id, EVENT_FIXTURE_TOTAL + 1)
