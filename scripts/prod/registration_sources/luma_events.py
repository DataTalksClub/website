"""Read event-level identity metadata from a Luma registration export."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from scripts.prod.registrant_import import RegistrantImportError

from .safety import MAX_ROWS, checked_files, safe_path

REQUIRED_COLUMNS = (
    "event_id",
    "guest_id",
    "approval_status",
    "event_name",
    "event_start_at",
)


@dataclass(frozen=True, slots=True)
class DiscoveredLumaEvent:
    external_event_identifier: str
    event_url: str
    title: str
    start_at: str
    eligible_count: int
    excluded_count: int
    quarantined_count: int
    row_total: int


def discover_luma_events(path: Path) -> tuple[DiscoveredLumaEvent, ...]:
    """Return event identity metadata; attendee values never leave this reader."""

    root = safe_path(path, expected_kind="directory")
    files = checked_files(root)
    csv_by_stem = {file.stem: file for file in files if file.suffix.casefold() == ".csv"}
    json_by_stem = {file.stem: file for file in files if file.suffix.casefold() == ".json"}
    if set(csv_by_stem) != set(json_by_stem):
        raise RegistrantImportError("mismatched_luma_pair")

    discovered: list[DiscoveredLumaEvent] = []
    event_ids: set[str] = set()
    for stem in sorted(csv_by_stem):
        try:
            document = json.loads(json_by_stem[stem].read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RegistrantImportError("malformed_json") from error
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise RegistrantImportError("unsupported_luma_schema")
        event_id = document.get("event_id")
        event_url = document.get("event_url")
        if (
            not isinstance(event_id, str)
            or not event_id
            or len(event_id) > 512
            or not isinstance(event_url, str)
            or not event_url.startswith("https://")
            or len(event_url) > 2_048
        ):
            raise RegistrantImportError("malformed_json")
        if event_id in event_ids:
            raise RegistrantImportError("duplicate_event_identifier")
        event_ids.add(event_id)

        title = ""
        start_at = ""
        eligible = excluded = quarantined = row_total = 0
        try:
            with csv_by_stem[stem].open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream, strict=True)
                headers = reader.fieldnames
                if (
                    headers is None
                    or len(headers) != len(set(headers))
                    or any(column not in headers for column in REQUIRED_COLUMNS)
                ):
                    raise RegistrantImportError("unsupported_luma_schema")
                for row in reader:
                    row_total += 1
                    if row_total > MAX_ROWS:
                        raise RegistrantImportError("row_count_exceeded")
                    if row.get("event_id") != event_id:
                        raise RegistrantImportError("mismatched_luma_pair")
                    if not title:
                        title = (row.get("event_name") or "").strip()
                        start_at = (row.get("event_start_at") or "").strip()
                    status = (row.get("approval_status") or "").casefold()
                    if status == "approved":
                        eligible += 1
                    elif status == "declined":
                        excluded += 1
                    elif status:
                        quarantined += 1
                    else:
                        raise RegistrantImportError("malformed_csv")
        except RegistrantImportError:
            raise
        except (OSError, UnicodeDecodeError, csv.Error) as error:
            raise RegistrantImportError("malformed_csv") from error
        discovered.append(
            DiscoveredLumaEvent(
                external_event_identifier=event_id,
                event_url=event_url,
                title=title,
                start_at=start_at,
                eligible_count=eligible,
                excluded_count=excluded,
                quarantined_count=quarantined,
                row_total=row_total,
            )
        )
    return tuple(discovered)
