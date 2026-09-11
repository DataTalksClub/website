"""Tests for the Eventbrite attendee-level registrant reader.

Every address here is synthetic (``example.invalid``) -- no real registrant
export is read or copied in this suite. The consolidation and write behaviour
these rows feed is tested in ``events/tests/test_registrant_import.py``; what
is tested here is the file half: identity resolution via the reviewed
``eventbrite-event-identities.json`` mapping (not
``events.identity.provider_source_identity``, unlike Luma), archive discovery,
safety refusals, and column handling.
"""

from __future__ import annotations

import csv
import io
import json
import tempfile
import uuid
import zipfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from events.models import Event, EventRegistrantIdentity, EventRegistration
from events.registrant_import import RegistrantImportError, import_registrants
from scripts.prod.registration_sources.eventbrite_registrants import (
    PROVIDER,
    CanonicalEventbriteIdentity,
    discover_eventbrite_registrant_files,
    eventbrite_registrant_sources,
    load_resolved_eventbrite_identities,
    read_eventbrite_registrant_rows,
)

_COLUMNS = (
    "Order #",
    "Order Date",
    "First Name",
    "Last Name",
    "Email",
    "Attendee #",
    "Attendee Status",
)


def scratch_root() -> tempfile.TemporaryDirectory[str]:
    scratch = Path(settings.BASE_DIR) / ".tmp"
    scratch.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=scratch)


def _write_archive(
    directory: Path,
    *,
    events: dict[str, list[dict[str, str]]],
    extra_members: dict[str, bytes] | None = None,
) -> Path:
    archive_path = directory / "aggregate-v1.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for event_id, rows in events.items():
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=_COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            archive.writestr(f"{event_id}.csv", buffer.getvalue())
        for name, content in (extra_members or {}).items():
            archive.writestr(name, content)
    return archive_path


def _write_identities(directory: Path, *, entries: list[dict[str, object]]) -> Path:
    path = directory / "eventbrite-event-identities.json"
    path.write_text(json.dumps({"events": entries}), encoding="utf-8")
    return path


def _row(*, attendee: str, email: str, status: str = "Attending") -> dict[str, str]:
    return {
        "Order #": "1000",
        "Order Date": "2021-04-26 06:27:00+02:00",
        "First Name": "Jane",
        "Last Name": "Doe",
        "Email": email,
        "Attendee #": attendee,
        "Attendee Status": status,
    }


class LoadResolvedIdentitiesTests(SimpleTestCase):
    def test_only_resolved_entries_are_kept(self) -> None:
        with scratch_root() as root:
            directory = Path(root)
            path = _write_identities(
                directory,
                entries=[
                    {
                        "eventbrite_event_id": "111",
                        "status": "resolved",
                        "canonical_repository": "DataTalksClub/datatalksclub.github.io",
                        "canonical_revision": "a" * 40,
                        "canonical_source_key": "2020-11-10-example",
                    },
                    {
                        "eventbrite_event_id": "222",
                        "status": "ambiguous",
                        "canonical_repository": "",
                        "canonical_revision": "",
                        "canonical_source_key": "",
                    },
                ],
            )
            resolved = load_resolved_eventbrite_identities(path)
        self.assertEqual(set(resolved), {"111"})
        self.assertEqual(
            resolved["111"],
            CanonicalEventbriteIdentity(
                repository="DataTalksClub/datatalksclub.github.io",
                revision="a" * 40,
                source_key="2020-11-10-example",
            ),
        )

    def test_malformed_payload_is_refused(self) -> None:
        with scratch_root() as root:
            path = Path(root) / "identities.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(RegistrantImportError):
                load_resolved_eventbrite_identities(path)


class DiscoverAndReadTests(SimpleTestCase):
    def test_discovers_only_numeric_csv_members(self) -> None:
        with scratch_root() as root:
            directory = Path(root)
            archive = _write_archive(
                directory,
                events={"123456": [_row(attendee="a1", email="one@example.invalid")]},
                extra_members={"events.xlsx": b"not really xlsx"},
            )
            discovered = discover_eventbrite_registrant_files(archive)
        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0].external_event_identifier, "123456")

    def test_reads_rows_and_dedupes_attendee_numbers(self) -> None:
        with scratch_root() as root:
            directory = Path(root)
            archive = _write_archive(
                directory,
                events={
                    "123456": [
                        _row(attendee="a1", email="one@example.invalid"),
                        _row(attendee="a1", email="one@example.invalid"),
                        _row(attendee="a2", email="two@example.invalid"),
                    ]
                },
            )
            rows = read_eventbrite_registrant_rows(archive, "123456.csv", external_event_identifier="123456")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].status, "attending")
        self.assertEqual(rows[0].normalized_email, "one@example.invalid")

    def test_missing_required_column_is_refused(self) -> None:
        with scratch_root() as root:
            directory = Path(root)
            archive_path = directory / "aggregate-v1.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("123456.csv", "Order #,Email\n1,a@example.invalid\n")
            with self.assertRaises(RegistrantImportError):
                read_eventbrite_registrant_rows(
                    archive_path, "123456.csv", external_event_identifier="123456"
                )

    def test_malformed_archive_is_refused(self) -> None:
        with scratch_root() as root:
            path = Path(root) / "not-a-zip.zip"
            path.write_bytes(b"definitely not a zip file")
            with self.assertRaises(RegistrantImportError):
                discover_eventbrite_registrant_files(path)


def _event(*, source_key: str) -> Event:
    event = Event(
        id=uuid.uuid4(),
        title="Synthetic Eventbrite event",
        slug="synthetic-eventbrite-event",
        source_repository="DataTalksClub/datatalksclub.github.io",
        source_revision="a" * 40,
        source_key=source_key,
    )
    event._allow_public_id_assignment = True
    event.public_id = 8_001
    event.save()
    return event


class EventbriteRegistrantSourcesIntegrationTests(TestCase):
    def test_resolved_event_imports_through_the_shared_consolidation_path(self) -> None:
        event = _event(source_key="2020-11-10-example")
        with scratch_root() as root:
            directory = Path(root)
            archive = _write_archive(
                directory,
                events={
                    "123456": [
                        _row(attendee="a1", email="one@example.invalid"),
                        _row(attendee="a2", email="two@example.invalid"),
                    ]
                },
            )
            identities_path = _write_identities(
                directory,
                entries=[
                    {
                        "eventbrite_event_id": "123456",
                        "status": "resolved",
                        "canonical_repository": event.source_repository,
                        "canonical_revision": event.source_revision,
                        "canonical_source_key": event.source_key,
                    }
                ],
            )
            pending = eventbrite_registrant_sources(
                archive_path=archive, identities_path=identities_path
            )
            report = import_registrants(provider=PROVIDER, pending=pending)

        self.assertEqual(report.events_completed, 1)
        self.assertEqual(report.events_awaiting_identity, 0)
        self.assertEqual(report.rows_written, 2)
        self.assertEqual(EventRegistration.objects.filter(event=event, provider="eventbrite").count(), 2)
        self.assertEqual(EventRegistrantIdentity.objects.count(), 2)

    def test_unresolved_eventbrite_id_is_reported_not_guessed(self) -> None:
        with scratch_root() as root:
            directory = Path(root)
            archive = _write_archive(
                directory,
                events={"999999": [_row(attendee="a1", email="one@example.invalid")]},
            )
            # No matching entry at all in the identities file.
            identities_path = _write_identities(directory, entries=[])
            pending = eventbrite_registrant_sources(
                archive_path=archive, identities_path=identities_path
            )
            report = import_registrants(provider=PROVIDER, pending=pending)

        self.assertEqual(report.events_awaiting_identity, 1)
        self.assertIn("999999", report.awaiting_identity_events)
        self.assertEqual(report.rows_written, 0)

    def test_email_consolidates_against_an_existing_account(self) -> None:
        from accounts.models import CustomUser

        account = CustomUser.objects.create(username="matched", email="matched@example.invalid")
        event = _event(source_key="2020-11-10-example")
        with scratch_root() as root:
            directory = Path(root)
            archive = _write_archive(
                directory,
                events={"123456": [_row(attendee="a1", email=account.email)]},
            )
            identities_path = _write_identities(
                directory,
                entries=[
                    {
                        "eventbrite_event_id": "123456",
                        "status": "resolved",
                        "canonical_repository": event.source_repository,
                        "canonical_revision": event.source_revision,
                        "canonical_source_key": event.source_key,
                    }
                ],
            )
            pending = eventbrite_registrant_sources(
                archive_path=archive, identities_path=identities_path
            )
            report = import_registrants(provider=PROVIDER, pending=pending)

        self.assertEqual(report.matched_account_total, 1)
        self.assertEqual(report.new_identity_total, 0)
        identity = EventRegistrantIdentity.objects.get(account=account)
        self.assertTrue(
            EventRegistration.objects.filter(event=event, identity=identity, provider="eventbrite").exists()
        )
