"""Landing staged content on identities minted from a provider export.

The 421-record corpus cannot grow: its descriptions arrive through a bridge that
matches on a legacy tuple a discovered event does not have. This is the other
door, and these tests pin that it stays a reconcile -- it refuses an identity it
does not hold, it refuses to land on an event it was not built for, and a second
run changes nothing.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings
from django.test import TestCase

from events.content_import import (
    EventContentImportError,
    import_new_event_content,
)
from events.models import Event, EventContent

PROVIDER_REPOSITORY = "dtc-historical-source/luma"
PROVIDER_REVISION = "luma-aggregate-v1"
STARTS_AT = "2026-08-10T15:00:00+00:00"


def _event(*, public_id: int, source_key: str, repository: str = PROVIDER_REPOSITORY) -> Event:
    event = Event(
        id=uuid.uuid4(),
        title="A synthetic workshop",
        slug=f"a-synthetic-workshop-{public_id}",
        source_repository=repository,
        source_revision=PROVIDER_REVISION,
        source_key=source_key,
    )
    event._allow_public_id_assignment = True
    event.public_id = public_id
    event.save()
    return event


def _record(event: Event, **overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "record_schema_version": 1,
        "identity_id": str(event.id),
        "type": "workshop",
        "starts_at": STARTS_AT,
        "ends_at": "",
        "season": None,
        "episode": None,
        "description_html": '<p class="mt-4 leading-7">We will build the thing.</p>',
        "description_text": "We will build the thing.",
        "description_provenance": {
            "source": "luma-export-v1",
            "export_file": "2026-08-10_a-synthetic-workshop_evt-synthetic01.md",
            "removed_speaker_bio": True,
        },
        "type_provenance": {
            "input": "local-event-type-input.json",
            "review_revision": 1,
            "reason": "Hands-on session run by the speaker.",
        },
        "speakers": [],
        "links": [],
        "provenance": {
            "repository": event.source_repository,
            "revision": event.source_revision,
            "source_key": event.source_key,
        },
    }
    record.update(overrides)
    return record


def _artifact(records: list[dict[str, Any]]) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "source": "luma-export-v1",
        "counts": {"events": len(records)},
        "events": records,
    }
    # Recomputed here rather than imported, so the two ends have to agree by
    # arithmetic rather than by sharing one helper.
    encoded = json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    artifact["content_sha256"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return artifact


class NewEventContentImportTests(TestCase):
    def setUp(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.path = Path(self.temporary.name) / "staged.json"
        self.event = _event(public_id=90_001, source_key="evt-Synthetic01")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _stage(self, records: list[dict[str, Any]]) -> Path:
        self.path.write_text(json.dumps(_artifact(records)), encoding="utf-8")
        return self.path

    def test_a_record_lands_on_the_identity_it_names(self) -> None:
        report = import_new_event_content(path=self._stage([_record(self.event)]))

        self.assertEqual((report.total, report.created, report.described), (1, 1, 1))
        self.assertFalse(report.replayed)
        content = EventContent.objects.get(event=self.event)
        self.assertEqual(content.type, EventContent.Type.WORKSHOP)
        self.assertEqual(content.starts_at.isoformat(), STARTS_AT)
        # Luma states no end anybody reviewed, so the row states none either.
        self.assertIsNone(content.ends_at)

    def test_replaying_changes_nothing(self) -> None:
        staged = self._stage([_record(self.event)])
        import_new_event_content(path=staged)
        before = EventContent.objects.get(event=self.event).updated_at

        report = import_new_event_content(path=staged)

        self.assertTrue(report.replayed)
        self.assertEqual((report.created, report.updated, report.unchanged), (0, 0, 1))
        self.assertEqual(EventContent.objects.get(event=self.event).updated_at, before)

    def test_a_record_naming_an_identity_we_do_not_hold_is_refused(self) -> None:
        """Creating an event is events.identity's job, and only its job."""

        record = _record(self.event, identity_id=str(uuid.uuid4()))

        with self.assertRaises(EventContentImportError) as refusal:
            import_new_event_content(path=self._stage([record]))

        self.assertEqual(str(refusal.exception), "new_event_content_identity_unknown")
        self.assertFalse(EventContent.objects.filter(event=self.event).exists())

    def test_a_record_built_for_another_source_cannot_land_here(self) -> None:
        """The 421 carry the legacy repository, so a staged record can never reach them."""

        legacy = _event(
            public_id=90_002,
            source_key="2026-08-10-a-synthetic-workshop",
            repository="DataTalksClub/datatalksclub.github.io",
        )
        record = _record(legacy)
        record["provenance"]["repository"] = PROVIDER_REPOSITORY

        with self.assertRaises(EventContentImportError) as refusal:
            import_new_event_content(path=self._stage([record]))

        self.assertEqual(str(refusal.exception), "new_event_content_provenance_conflict")
        self.assertFalse(EventContent.objects.filter(event=legacy).exists())

    def test_one_bad_record_stops_the_whole_candidate(self) -> None:
        other = _event(public_id=90_003, source_key="evt-Synthetic02")

        with self.assertRaises(EventContentImportError):
            import_new_event_content(
                path=self._stage(
                    [_record(self.event), _record(other, identity_id=str(uuid.uuid4()))]
                )
            )

        self.assertFalse(EventContent.objects.filter(event__in=(self.event, other)).exists())

    def test_a_description_arriving_without_its_type_review_is_refused(self) -> None:
        """No source states the type, so the decision has to travel with the row."""

        with self.assertRaises(EventContentImportError) as refusal:
            import_new_event_content(path=self._stage([_record(self.event, type_provenance={})]))

        self.assertEqual(str(refusal.exception), "new_event_content_type_provenance_missing")

    def test_a_record_with_no_description_is_refused(self) -> None:
        """Carrying a description in is the only reason this artifact exists."""

        record = _record(self.event, description_html="", description_text="")

        with self.assertRaises(EventContentImportError) as refusal:
            import_new_event_content(path=self._stage([record]))

        self.assertEqual(str(refusal.exception), "event_content_description_html_invalid")

    def test_a_tampered_artifact_is_refused(self) -> None:
        artifact = _artifact([_record(self.event)])
        artifact["events"][0]["description_text"] = "Something else."
        self.path.write_text(json.dumps(artifact), encoding="utf-8")

        with self.assertRaises(EventContentImportError) as refusal:
            import_new_event_content(path=self.path)

        self.assertEqual(str(refusal.exception), "new_event_content_artifact_digest_mismatch")

    def test_a_dry_run_writes_nothing(self) -> None:
        report = import_new_event_content(path=self._stage([_record(self.event)]), dry_run=True)

        self.assertEqual((report.total, report.created), (1, 1))
        self.assertTrue(report.dry_run)
        self.assertFalse(EventContent.objects.filter(event=self.event).exists())

    def test_an_empty_artifact_is_a_no_op_rather_than_a_failure(self) -> None:
        """It is written whenever the review is clean, which may be for no events."""

        report = import_new_event_content(path=self._stage([]))

        self.assertEqual((report.total, report.created), (0, 0))
        self.assertTrue(report.replayed)

    def test_speakers_and_links_are_replaced_as_a_set(self) -> None:
        record = _record(
            self.event,
            speakers=[{"key": "ada", "name": "Ada", "public_path": "/people/ada"}],
            links=[{"label": "Recording", "url": "https://www.youtube.com/watch?v=synthetic"}],
        )
        import_new_event_content(path=self._stage([record]))

        import_new_event_content(path=self._stage([_record(self.event)]))

        content = EventContent.objects.get(event=self.event)
        self.assertEqual(content.speakers.count(), 0)
        self.assertEqual(content.links.count(), 0)
