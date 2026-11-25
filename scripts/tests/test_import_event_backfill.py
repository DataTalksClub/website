"""The event backfill: pictures, field edits, sponsors and retired links, from a manifest."""

from __future__ import annotations

import hashlib
import json
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

from community_base.events.models import Event
from django.test import TestCase

import scripts.prod
from core.sponsors import create_sponsor
from event_registrants.models import EventOperatorConfig, EventSponsor
from events.identity import create_event_identity
from scripts.prod import import_event_backfill as entry_point
from test_support.event_pictures import TEST_EVENT_PICTURE

#: A second valid 1x1 PNG, different bytes from the test picture.
NEW_PICTURE = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c63f8ffff3f0005fe02fea7d6a6f400"
    "00000049454e44ae426082"
)
LUMA = {"label": "Register on Luma", "url": "https://luma.com/abc"}
EVENTBRITE = {"label": "View event on Eventbrite", "url": "https://www.eventbrite.com/e/1"}
RECORDING = {"label": "Watch recording", "url": "https://www.youtube.com/watch?v=x"}
STARTS = datetime(2030, 1, 7, 16, tzinfo=UTC)


def _event(title: str, *, materials: list[dict[str, str]] | None = None) -> Event:
    event = create_event_identity(
        picture=TEST_EVENT_PICTURE,
        title=title,
        event_type="webinar",
        speakers=[("janedoe", "Jane Doe")],
        description="What it is about.",
        starts_at=STARTS,
        source_repository="example/events",
        source_revision="r1",
        source_key=f"key-{title}",
    )
    if materials is not None:
        Event.objects.filter(pk=event.pk).update(materials=materials)
        event.refresh_from_db()
    return event


class EventBackfillTests(TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        (self.root / "pictures").mkdir()
        (self.root / "pictures" / "one.png").write_bytes(NEW_PICTURE)
        self.one = _event("One", materials=[LUMA, RECORDING])
        self.two = _event("Two", materials=[EVENTBRITE])
        create_sponsor(
            payload={
                "key": "acme",
                "name": "Acme",
                "url": "https://acme.example/",
                "tagline": "",
                "lifecycle": "active",
                "assignments": [],
            },
            source="studio",
            idempotency_key=str(uuid.uuid4()),
            actor_ref="user:1",
        )

    def _manifest(self, entries: list[dict], *, drop: bool = True) -> Path:
        path = self.root / "manifest.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "drop_retired_registration_materials": drop,
                    "events": entries,
                }
            ),
            encoding="utf-8",
        )
        return path

    def _entry_one(self) -> dict:
        return {
            "event": {"content_id": str(self.one.content_id), "public_id": 1, "title": "One"},
            "picture": {
                "file": "pictures/one.png",
                "sha256": hashlib.sha256(NEW_PICTURE).hexdigest(),
            },
            "fields": {
                "description": "A better description.",
                "speakers": ["janedoe"],
                "sponsors": ["acme"],
                "start_datetime": "2030-01-14T15:30:00+00:00",
                "end_datetime": "2030-01-14T16:30:00+00:00",
            },
        }

    def test_a_dry_run_reports_and_writes_nothing(self) -> None:
        report = entry_point.run(manifest=self._manifest([self._entry_one()]), apply=False)

        self.assertFalse(report.applied)
        changed = report.events_changed[f"{self.one.public_id}: One"]
        self.assertEqual(
            changed,
            ["picture", "description", "start_datetime", "end_datetime", "sponsors", "materials"],
        )
        self.assertEqual(report.events_changed[f"{self.two.public_id}: Two"], ["materials"])
        self.one.refresh_from_db()
        self.assertEqual(self.one.description, "What it is about.")
        self.assertEqual(self.one.materials, [LUMA, RECORDING])
        self.assertFalse(EventSponsor.objects.exists())
        config = EventOperatorConfig.objects.get(event=self.one)
        self.assertNotEqual(bytes(config.banner_image or b""), NEW_PICTURE)

    def test_apply_writes_the_manifest_and_a_replay_changes_nothing(self) -> None:
        manifest = self._manifest([self._entry_one()])

        report = entry_point.run(manifest=manifest, apply=True)

        self.assertEqual(report.failed, [])
        self.assertEqual(report.pictures_replaced, 1)
        self.one.refresh_from_db()
        self.two.refresh_from_db()
        self.assertEqual(self.one.description, "A better description.")
        self.assertEqual(self.one.start_datetime, datetime(2030, 1, 14, 15, 30, tzinfo=UTC))
        self.assertEqual(self.one.materials, [RECORDING])
        self.assertEqual(self.two.materials, [])
        self.assertEqual(
            list(
                EventSponsor.objects.filter(event=self.one).values_list("sponsor__key", flat=True)
            ),
            ["acme"],
        )
        config = EventOperatorConfig.objects.get(event=self.one)
        self.assertEqual(bytes(config.banner_image or b""), NEW_PICTURE)

        again = entry_point.run(manifest=manifest, apply=True)
        self.assertEqual(again.events_changed, {})
        self.assertEqual(again.events_unchanged, again.events_total)

    def test_an_event_is_found_by_its_source_identity_and_unknown_ones_are_reported(self) -> None:
        entries: list[dict] = [
            {
                "event": {
                    "content_id": str(uuid.uuid4()),
                    "repository": "example/events",
                    "source_key": "key-Two",
                },
                "picture": None,
                "fields": {"title": "Two, renamed"},
            },
            {"event": {"content_id": str(uuid.uuid4()), "public_id": 999, "title": "Gone"}},
        ]

        report = entry_point.run(manifest=self._manifest(entries, drop=False), apply=True)

        self.two.refresh_from_db()
        self.assertEqual(self.two.title, "Two, renamed")
        self.assertEqual(report.unresolved, [{"public_id": 999, "title": "Gone"}])
        # Without the materials rule, events outside the manifest are not touched.
        self.one.refresh_from_db()
        self.assertEqual(self.one.materials, [LUMA, RECORDING])

    def test_a_failing_event_is_reported_and_the_rest_still_apply(self) -> None:
        entry = self._entry_one()
        entry["fields"] = {"sponsors": ["nobody"]}

        report = entry_point.run(manifest=self._manifest([entry]), apply=True)

        self.assertEqual(len(report.failed), 1)
        self.assertEqual(report.failed[0]["error"], "unknown_sponsor")
        self.one.refresh_from_db()
        # The failed event's picture and materials rolled back with it.
        self.assertEqual(self.one.materials, [LUMA, RECORDING])
        self.two.refresh_from_db()
        self.assertEqual(self.two.materials, [])

    def test_a_manifest_naming_a_missing_picture_is_refused_before_any_write(self) -> None:
        entry = self._entry_one()
        entry["picture"]["file"] = "pictures/missing.png"

        with self.assertRaises(entry_point.ManifestError):
            entry_point.run(manifest=self._manifest([entry]), apply=True)
        self.two.refresh_from_db()
        self.assertEqual(self.two.materials, [EVENTBRITE])

    def test_it_is_a_typed_operator_command_never_part_of_a_rebuild(self) -> None:
        self.assertEqual(entry_point.SYNC_MODEL, "one-time")
        self.assertFalse(entry_point.BOOTSTRAPS_EMPTY_DATABASE)
        self.assertIn("import_event_backfill", scripts.prod.SCRIPT_COMMAND_EXCLUSIONS)
        parser = entry_point._parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--database", ".tmp/x.sqlite3"])
        args = parser.parse_args(["--database", ".tmp/x.sqlite3", "--manifest", "m.json"])
        self.assertFalse(args.apply)
