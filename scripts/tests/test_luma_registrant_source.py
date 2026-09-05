"""Tests for the Luma attendee-level registrant reader.

Every address here is synthetic (``example.invalid``) -- no real registrant
export is read or copied in this suite.  The consolidation and write behaviour
these rows feed is tested in ``events/tests/test_registrant_import.py``; what is
tested here is the file half: pairing, safety refusals, and column handling.
"""

from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from accounts.models import CustomUser
from events.identity import create_provider_event_identity
from events.models import EventRegistrantIdentity, EventRegistration
from events.registrant_import import RegistrantImportError, import_registrants
from scripts.prod.registration_sources.luma_registrants import (
    PROVIDER,
    discover_luma_registrant_files,
    luma_registrant_sources,
    read_luma_registrant_rows,
)

_COLUMNS = (
    "guest_id",
    "user_id",
    "email",
    "first_name",
    "last_name",
    "name",
    "phone_number",
    "company",
    "job_title",
    "approval_status",
    "registered_at",
    "utm_source",
    "event_id",
    "event_name",
    "event_start_at",
)


def scratch_root() -> tempfile.TemporaryDirectory[str]:
    scratch = Path(settings.BASE_DIR) / ".tmp"
    scratch.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=scratch)


class LumaRegistrantExportMixin:
    root: Path

    def _write_event(
        self,
        *,
        stem: str,
        event_id: str,
        rows: list[dict[str, str]],
    ) -> None:
        (self.root / f"{stem}.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "event_id": event_id,
                    "event_url": f"https://luma.test/{stem}",
                }
            ),
            encoding="utf-8",
        )
        with (self.root / f"{stem}.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=_COLUMNS)
            writer.writeheader()
            for row in rows:
                full = dict.fromkeys(_COLUMNS, "")
                full.update(row)
                full["event_id"] = event_id
                writer.writerow(full)

    def _row(
        self,
        *,
        guest_id: str,
        email: str,
        status: str = "approved",
        registered_at: str = "2026-01-01T00:00:00.000Z",
    ) -> dict[str, str]:
        return {
            "guest_id": guest_id,
            "email": email,
            "approval_status": status,
            "registered_at": registered_at,
        }


class LumaRegistrantReaderTestCase(LumaRegistrantExportMixin, SimpleTestCase):
    """Reading an export needs no database -- these never write a row."""

    def setUp(self) -> None:
        temporary = scratch_root()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)


class DiscoveryTests(LumaRegistrantReaderTestCase):
    def test_pairs_csv_and_json_by_stem_sorted(self) -> None:
        self._write_event(stem="b-event", event_id="evt-b", rows=[])
        self._write_event(stem="a-event", event_id="evt-a", rows=[])

        discovered = discover_luma_registrant_files(self.root)

        self.assertEqual(
            [item.external_event_identifier for item in discovered], ["evt-a", "evt-b"]
        )

    def test_mismatched_pair_refuses(self) -> None:
        self._write_event(stem="solo", event_id="evt-solo", rows=[])
        (self.root / "orphan.csv").write_text("event_id\n", encoding="utf-8")

        with self.assertRaises(RegistrantImportError):
            discover_luma_registrant_files(self.root)

    def test_a_missing_directory_refuses(self) -> None:
        with self.assertRaises(RegistrantImportError):
            discover_luma_registrant_files(self.root / "absent")

    def test_a_hidden_entry_is_ignored_not_paired(self) -> None:
        self._write_event(stem="visible", event_id="evt-visible", rows=[])
        (self.root / ".hidden.csv").write_text("event_id\n", encoding="utf-8")

        discovered = discover_luma_registrant_files(self.root)

        self.assertEqual([item.external_event_identifier for item in discovered], ["evt-visible"])

    def test_an_unsupported_checkpoint_schema_refuses(self) -> None:
        self._write_event(stem="one", event_id="evt-one", rows=[])
        (self.root / "one.json").write_text(json.dumps({"schema_version": 2}), encoding="utf-8")

        with self.assertRaises(RegistrantImportError):
            discover_luma_registrant_files(self.root)


class RowReadingTests(LumaRegistrantReaderTestCase):
    def test_reads_normalized_email_status_and_registered_at(self) -> None:
        self._write_event(
            stem="one",
            event_id="evt-one",
            rows=[self._row(guest_id="g1", email="  Person@Example.INVALID  ")],
        )
        rows = read_luma_registrant_rows(self.root / "one.csv", external_event_identifier="evt-one")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].normalized_email, "person@example.invalid")
        self.assertEqual(rows[0].status, "approved")
        self.assertEqual(rows[0].external_registrant_identifier, "g1")

    def test_duplicate_guest_id_keeps_first_row_only(self) -> None:
        self._write_event(
            stem="dupe",
            event_id="evt-dupe",
            rows=[
                self._row(guest_id="g1", email="first@example.invalid"),
                self._row(guest_id="g1", email="second@example.invalid"),
            ],
        )
        rows = read_luma_registrant_rows(
            self.root / "dupe.csv", external_event_identifier="evt-dupe"
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].normalized_email, "first@example.invalid")

    def test_missing_guest_id_refuses(self) -> None:
        self._write_event(
            stem="bad", event_id="evt-bad", rows=[self._row(guest_id="", email="a@example.invalid")]
        )
        with self.assertRaises(RegistrantImportError):
            read_luma_registrant_rows(self.root / "bad.csv", external_event_identifier="evt-bad")

    def test_blank_email_is_read_as_no_normalized_email_not_an_error(self) -> None:
        self._write_event(
            stem="blank", event_id="evt-blank", rows=[self._row(guest_id="g1", email="")]
        )
        rows = read_luma_registrant_rows(
            self.root / "blank.csv", external_event_identifier="evt-blank"
        )
        self.assertIsNone(rows[0].normalized_email)

    def test_a_row_belonging_to_another_event_refuses(self) -> None:
        self._write_event(
            stem="one",
            event_id="evt-one",
            rows=[self._row(guest_id="g1", email="a@example.invalid")],
        )
        with self.assertRaises(RegistrantImportError):
            read_luma_registrant_rows(
                self.root / "one.csv", external_event_identifier="evt-other"
            )

    def test_a_missing_required_column_refuses(self) -> None:
        (self.root / "thin.csv").write_text("event_id,guest_id\nevt-thin,g1\n", encoding="utf-8")

        with self.assertRaises(RegistrantImportError):
            read_luma_registrant_rows(
                self.root / "thin.csv", external_event_identifier="evt-thin"
            )

    def test_a_symlinked_csv_refuses(self) -> None:
        self._write_event(
            stem="real",
            event_id="evt-real",
            rows=[self._row(guest_id="g1", email="a@example.invalid")],
        )
        link = self.root / "link.csv"
        link.symlink_to(self.root / "real.csv")

        with self.assertRaises(RegistrantImportError):
            read_luma_registrant_rows(link, external_event_identifier="evt-real")

    def test_a_missing_csv_refuses(self) -> None:
        with self.assertRaises(RegistrantImportError):
            read_luma_registrant_rows(
                self.root / "absent.csv", external_event_identifier="evt-absent"
            )


class LumaRegistrantSourceTests(LumaRegistrantExportMixin, TestCase):
    """The reader and the domain writer, wired the way the entry point wires them."""

    def setUp(self) -> None:
        temporary = scratch_root()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_discovery_reads_no_registrant_row_until_the_import_asks(self) -> None:
        self._write_event(
            stem="e1",
            event_id="evt-1",
            rows=[self._row(guest_id="g1", email="lazy@example.invalid")],
        )

        pending = luma_registrant_sources(self.root)
        # Discovery has read the JSON checkpoint and nothing else, so removing
        # the registrant CSV afterwards cannot affect what it found.
        (self.root / "e1.csv").unlink()

        self.assertEqual([item.external_event_identifier for item in pending], ["evt-1"])
        self.assertEqual(EventRegistration.objects.count(), 0)

    def test_an_export_is_imported_and_consolidated_end_to_end(self) -> None:
        account = CustomUser.objects.create(
            username="existing-learner", email="learner@example.invalid"
        )
        create_provider_event_identity(
            provider=PROVIDER, external_event_identifier="evt-1", title="Event One"
        )
        self._write_event(
            stem="e1",
            event_id="evt-1",
            rows=[
                self._row(guest_id="g1", email="learner@example.invalid"),
                self._row(guest_id="g2", email="stranger@example.invalid"),
            ],
        )

        report = import_registrants(
            provider=PROVIDER, pending=luma_registrant_sources(self.root)
        )

        self.assertEqual(report.provider, PROVIDER)
        self.assertEqual(report.events_completed, 1)
        self.assertEqual(report.rows_written, 2)
        self.assertEqual(report.matched_account_total, 1)
        self.assertEqual(report.new_identity_total, 1)
        self.assertEqual(
            EventRegistrantIdentity.objects.filter(account=account).count(), 1
        )

    def test_a_completed_event_replays_without_its_file(self) -> None:
        """The resume guarantee, observed from outside: no file, no reopen."""

        create_provider_event_identity(
            provider=PROVIDER, external_event_identifier="evt-1", title="Event One"
        )
        self._write_event(
            stem="e1",
            event_id="evt-1",
            rows=[self._row(guest_id="g1", email="once@example.invalid")],
        )

        import_registrants(provider=PROVIDER, pending=luma_registrant_sources(self.root))
        pending = luma_registrant_sources(self.root)
        (self.root / "e1.csv").unlink()

        second = import_registrants(provider=PROVIDER, pending=pending)

        self.assertEqual(second.events_already_completed, 1)
        self.assertEqual(second.rows_written, 0)
        self.assertEqual(EventRegistration.objects.count(), 1)
