from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path
from zipfile import ZipFile

from django.conf import settings
from django.test import SimpleTestCase

from events.importers import derive_eventbrite, derive_luma
from scripts.prepare_event_registration_sources import (
    prepare_eventbrite,
    prepare_luma,
)


class PrepareEventRegistrationSourcesTests(SimpleTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path(settings.BASE_DIR) / ".tmp")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_prepares_luma_pair_without_copying_checkpoint_payload(self) -> None:
        source = self.root / "source"
        (source / "_json").mkdir(parents=True)
        (source / "_json" / "event.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "event": {
                        "id": "event-id",
                        "url": "https://example.test/event",
                        "hosts": [{"email": "private@example.test"}],
                    },
                    "guests": [{"email": "private@example.test"}],
                }
            ),
            encoding="utf-8",
        )
        with (source / "event.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream, fieldnames=("event_id", "guest_id", "approval_status", "email")
            )
            writer.writeheader()
            writer.writerow(
                {
                    "event_id": "event-id",
                    "guest_id": "guest-id",
                    "approval_status": "approved",
                    "email": "private@example.test",
                }
            )
        destination = self.root / "prepared"

        report = prepare_luma(source, destination)
        derived = derive_luma(destination, expected_checksum=str(report["tree_sha256"]))

        self.assertEqual(derived.eligible_row_total, 1)
        self.assertNotIn("private@example.test", (destination / "event.json").read_text())

    def test_flattens_eventbrite_wrapper_for_the_existing_adapter(self) -> None:
        source = self.root / "source.zip"
        csv_payload = (
            "Order #,Order Date,Attendee #,Attendee Status\norder,2026-01-01,attendee,Attending\n"
        )
        with ZipFile(source, "w") as archive:
            archive.writestr("eventbrite/csv/123.csv", csv_payload)
            archive.writestr("eventbrite/events.xlsx", b"not-opened")
        destination = self.root / "prepared.zip"

        report = prepare_eventbrite(source, destination)
        with ZipFile(destination) as archive:
            self.assertEqual(set(archive.namelist()), {"123.csv", "events.xlsx"})
        derived = derive_eventbrite(
            destination,
            expected_checksum=str(report["sha256"]),
            allowed_schema_fingerprints={
                "e98efc77fbdef00a3f0213bf7b8811bba5543263eb3bea782c84fc1e84fc24dd": (
                    "synthetic",
                    4,
                )
            },
        )
        self.assertEqual(derived.eligible_row_total, 1)
