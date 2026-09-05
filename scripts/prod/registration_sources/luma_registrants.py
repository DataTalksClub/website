"""Read a Luma export directory's attendee-level registrant rows.

This is the only reader in this package that touches attendee-level values, and
it is deliberately a separate module from :mod:`luma`, whose contract is "no
attendee value crosses this module boundary" and stays true.  Both know the same
file format; only this one opens the columns a person is in.

What leaves here is a provider-neutral :class:`events.registrant_import.RegistrantRow`:
a guest id, a normalized email, a status and a raw timestamp.  The email is the
consolidation key the domain needs and nothing else -- a name, phone number,
company or job title in the export is never read at all, and no value from any
row is ever logged, printed, or put in an error.  Every refusal is a bounded
:class:`~events.registrant_import.RegistrantImportError` code.

This does not reuse :func:`luma.derive_luma`'s checksum-pinned production-count
safety net, for the same reason :func:`luma.discover_luma_events` does not:
``derive_luma`` refuses any tree that disagrees with a reviewed, pinned count,
because its output *is* a public count.  Minting an identity/fact row carries no
such risk, so this applies the same structural safety checks -- safe,
non-symlink, regular-file paths; CSV/JSON pair shape; required columns; a
bounded row count -- and no pinned checksum.

Nothing on a request path imports this: ``scripts/prod/import_event_registrants.py``
is the one entry point, and it hands the rows to
:func:`events.registrant_import.import_registrants`, which writes them.  The
dependency runs adapter -> domain only.
"""

from __future__ import annotations

import csv
import json
import stat
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import NoReturn

from accounts.identity_values import normalize_account_email
from events.importers import ProtectedSourceError
from events.models import EventRegistration
from events.registrant_import import (
    PendingEventRegistrants,
    RegistrantImportError,
    RegistrantRow,
)

from .safety import safe_path

PROVIDER = EventRegistration.Provider.LUMA
# Required for identity-consolidation reads only -- a different set from the
# aggregate-only reader's REQUIRED_COLUMNS, since this reader also needs
# `email` and `registered_at`, neither of which that adapter ever touches.
REQUIRED_COLUMNS = (
    "event_id",
    "guest_id",
    "email",
    "approval_status",
    "registered_at",
)
# Generous headroom over the real export's largest single event file
# (~3,500 rows, measured) -- a bound, not a tuned expectation.
MAX_ROWS_PER_EVENT = 100_000

__all__ = [
    "PROVIDER",
    "REQUIRED_COLUMNS",
    "MAX_ROWS_PER_EVENT",
    "DiscoveredRegistrantFile",
    "discover_luma_registrant_files",
    "read_luma_registrant_rows",
    "luma_registrant_sources",
]


def _refuse(code: str) -> NoReturn:
    raise RegistrantImportError(code)


def _safe_csv_path(path: Path) -> Path:
    # The shared structural guard the aggregate-only readers use, reported in
    # this port's failure type so the entry point's one bounded-refusal handler
    # still catches it.  Both raise the same three condition codes.
    try:
        return safe_path(path, expected_kind="file")
    except ProtectedSourceError as error:
        raise RegistrantImportError(error.code) from error


@dataclass(frozen=True, slots=True)
class DiscoveredRegistrantFile:
    external_event_identifier: str
    csv_path: Path


def discover_luma_registrant_files(root: Path) -> tuple[DiscoveredRegistrantFile, ...]:
    """Pair each CSV with its JSON checkpoint's ``event_id``, sorted by file stem.

    Deliberately re-derives this pairing rather than calling
    :func:`luma.discover_luma_events`: that one reads only event-level columns
    and returns event-level facts, so reusing it would mean opening every file
    twice. Applies the same non-symlink, regular-file safety check to every path
    it opens as the aggregate-only reads do.
    """

    try:
        resolved_root = root.resolve(strict=True)
    except OSError:
        _refuse("source_unavailable")
    if not resolved_root.is_dir():
        _refuse("source_not_directory")

    csv_by_stem: dict[str, Path] = {}
    json_by_stem: dict[str, Path] = {}
    for entry in sorted(resolved_root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith("."):
            continue
        metadata = entry.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            continue
        suffix = entry.suffix.casefold()
        if suffix == ".csv":
            csv_by_stem[entry.stem] = entry
        elif suffix == ".json":
            json_by_stem[entry.stem] = entry
    if set(csv_by_stem) != set(json_by_stem):
        _refuse("mismatched_luma_pair")

    discovered: list[DiscoveredRegistrantFile] = []
    for stem in sorted(csv_by_stem):
        try:
            document = json.loads(json_by_stem[stem].read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            _refuse("malformed_json")
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            _refuse("unsupported_luma_schema")
        event_id = document.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            _refuse("malformed_json")
        discovered.append(
            DiscoveredRegistrantFile(external_event_identifier=event_id, csv_path=csv_by_stem[stem])
        )
    return tuple(discovered)


def read_luma_registrant_rows(
    csv_path: Path, *, external_event_identifier: str
) -> tuple[RegistrantRow, ...]:
    """Read one event's registrant rows.  Duplicate ``guest_id`` rows keep the first only."""

    resolved = _safe_csv_path(csv_path)
    rows: list[RegistrantRow] = []
    seen_guest_ids: set[str] = set()
    try:
        with resolved.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            headers = reader.fieldnames
            if headers is None or any(column not in headers for column in REQUIRED_COLUMNS):
                _refuse("unsupported_luma_schema")
            for index, row in enumerate(reader):
                if index >= MAX_ROWS_PER_EVENT:
                    _refuse("row_count_exceeded")
                if row.get("event_id") != external_event_identifier:
                    _refuse("mismatched_luma_pair")
                guest_id = (row.get("guest_id") or "").strip()
                status = (row.get("approval_status") or "").strip()
                if not guest_id or not status:
                    _refuse("malformed_csv")
                if guest_id in seen_guest_ids:
                    continue
                seen_guest_ids.add(guest_id)
                email = (row.get("email") or "").strip()
                rows.append(
                    RegistrantRow(
                        external_registrant_identifier=guest_id,
                        # Normalized here so the domain's consolidation key is
                        # the same value `accounts` stores, whatever a provider
                        # writes in its email column.
                        normalized_email=normalize_account_email(email),
                        status=status.casefold(),
                        registered_at_raw=(row.get("registered_at") or "").strip(),
                    )
                )
    except RegistrantImportError:
        raise
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise RegistrantImportError("malformed_csv") from error
    return tuple(rows)


def luma_registrant_sources(root: Path) -> tuple[PendingEventRegistrants, ...]:
    """Discover the export's events without reading a single registrant row.

    Each entry's ``read_rows`` opens that one event's CSV when, and only when,
    :func:`events.registrant_import.import_registrants` asks for it -- which it
    does not for an event already recorded as complete, so a resumed run never
    reopens a finished event's file.
    """

    return tuple(
        PendingEventRegistrants(
            external_event_identifier=item.external_event_identifier,
            read_rows=partial(
                read_luma_registrant_rows,
                item.csv_path,
                external_event_identifier=item.external_event_identifier,
            ),
        )
        for item in discover_luma_registrant_files(root)
    )
