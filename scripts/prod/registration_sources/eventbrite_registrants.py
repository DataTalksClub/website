"""Read attendee rows from a prepared Eventbrite export.

The adapter validates the archive, reads only the fields needed for identity
consolidation and registration status, and resolves provider event ids through
the reviewed Eventbrite identity input. It never logs attendee values and never
mints Event rows; unresolved events are reported to the ingest caller.
"""

from __future__ import annotations

import csv
import io
import json
import re
import stat
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import NoReturn
from zipfile import BadZipFile, ZipFile

from accounts.identity_values import normalize_account_email
from event_registrants.models import EventRegistration
from community_base.events.models import Event
from scripts.prod.registrant_import import (
    PendingEventRegistrants,
    RegistrantImportError,
    RegistrantRow,
)

from .safety import (
    MAX_ARCHIVE_ENTRIES,
    MAX_COMPRESSED_BYTES,
    MAX_ENTRY_BYTES,
    MAX_EXPANSION_RATIO,
    safe_path,
    validate_archive_member,
)

PROVIDER = EventRegistration.Provider.EVENTBRITE
# Only the attendee columns needed by the current registration import.
REQUIRED_COLUMNS = ("Order #", "Attendee #", "Attendee Status", "Email")
_ENTRY = re.compile(r"^(?P<event_id>[0-9]{1,20})\.csv$")
# Generous headroom over the real export's largest single event file (a few
# thousand rows, measured) -- a bound, not a tuned expectation.
MAX_ROWS_PER_EVENT = 100_000


def _refuse(code: str) -> NoReturn:
    raise RegistrantImportError(code)


# --------------------------------------------------------------------------
# Identity resolution -- via the reviewed eventbrite-event-identities.json
# mapping, not scripts.prod.registrant_import.provider_source_identity. See the module
# docstring for why.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CanonicalEventbriteIdentity:
    repository: str
    revision: str
    source_key: str


def load_resolved_eventbrite_identities(
    path: Path,
) -> dict[str, CanonicalEventbriteIdentity]:
    """Only the ``resolved`` entries, keyed by Eventbrite event id.

    The same 203-of-209 resolution :func:`scripts.build_eventbrite_descriptions
    .load_resolved_identities` reads -- kept as a separate, smaller reader here
    rather than imported from that script, since a registrant import has no
    other reason to depend on a content-staging script.
    """

    resolved_path = safe_path(path, expected_kind="file")
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _refuse("identities_source_unreadable")
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        _refuse("identities_payload_invalid")
    resolved: dict[str, CanonicalEventbriteIdentity] = {}
    for item in events:
        if not isinstance(item, dict) or item.get("status") != "resolved":
            continue
        eventbrite_id = item.get("eventbrite_event_id")
        repository = item.get("canonical_repository")
        revision = item.get("canonical_revision")
        source_key = item.get("canonical_source_key")
        if not (
            isinstance(eventbrite_id, str)
            and eventbrite_id
            and isinstance(repository, str)
            and repository
            and isinstance(revision, str)
            and revision
            and isinstance(source_key, str)
            and source_key
        ):
            _refuse("identities_payload_invalid")
        resolved[eventbrite_id] = CanonicalEventbriteIdentity(
            repository=repository, revision=revision, source_key=source_key
        )
    return resolved


def _resolve_canonical_event(identity: CanonicalEventbriteIdentity) -> Event | None:
    from events.identity import EventIdentityNotFound, resolve_source_identity

    try:
        return resolve_source_identity(
            repository=identity.repository,
            revision=identity.revision,
            source_key=identity.source_key,
        )
    except EventIdentityNotFound:
        return None


# --------------------------------------------------------------------------
# Reading the archive
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DiscoveredEventbriteRegistrantFile:
    external_event_identifier: str
    archive_path: Path
    member_name: str


def discover_eventbrite_registrant_files(
    archive_path: Path,
) -> tuple[DiscoveredEventbriteRegistrantFile, ...]:
    """List the archive's per-event CSV members without reading any of them.

    Structural checks only -- bounded size, no symlink, no path escape, no
    hidden entry -- the same shared guards every provider reader in this
    package uses.
    """

    resolved = safe_path(archive_path, expected_kind="file")
    try:
        size = resolved.stat().st_size
    except OSError:
        _refuse("source_unavailable")
    if size > MAX_COMPRESSED_BYTES:
        _refuse("source_too_large")
    try:
        archive = ZipFile(resolved)
        entries = archive.infolist()
    except (BadZipFile, OSError):
        _refuse("malformed_archive")
    if not entries or len(entries) > MAX_ARCHIVE_ENTRIES:
        _refuse("entry_count_exceeded")

    discovered: list[DiscoveredEventbriteRegistrantFile] = []
    for entry in entries:
        member = validate_archive_member(entry.filename)
        match = _ENTRY.fullmatch(member.as_posix())
        if match is None:
            # events.xlsx and anything else that is not a per-event CSV.
            continue
        unix_mode = (entry.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(unix_mode):
            _refuse("source_symlink")
        if entry.is_dir() or entry.file_size > MAX_ENTRY_BYTES:
            _refuse("unsupported_entry")
        if entry.compress_size and entry.file_size / entry.compress_size > MAX_EXPANSION_RATIO:
            _refuse("expansion_ratio_exceeded")
        discovered.append(
            DiscoveredEventbriteRegistrantFile(
                external_event_identifier=match.group("event_id"),
                archive_path=resolved,
                member_name=entry.filename,
            )
        )
    return tuple(sorted(discovered, key=lambda item: item.external_event_identifier))


def read_eventbrite_registrant_rows(
    archive_path: Path, member_name: str, *, external_event_identifier: str
) -> tuple[RegistrantRow, ...]:
    """Read one event's registrant rows. Duplicate ``Attendee #`` rows keep the first only.

    Opens the archive itself, freshly, only when called -- the same laziness
    :func:`scripts.prod.registration_sources.luma_registrants
    .read_luma_registrant_rows` gives a plain directory, so a completed event's
    member is never even opened.
    """

    rows: list[RegistrantRow] = []
    seen_attendee_ids: set[str] = set()
    try:
        with ZipFile(archive_path) as archive, archive.open(member_name) as raw:
            stream = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
            reader = csv.DictReader(stream, strict=True)
            headers = reader.fieldnames
            if headers is None or any(column not in headers for column in REQUIRED_COLUMNS):
                _refuse("unsupported_eventbrite_schema")
            for index, row in enumerate(reader):
                if index >= MAX_ROWS_PER_EVENT:
                    _refuse("row_count_exceeded")
                attendee_id = (row.get("Attendee #") or "").strip()
                status = (row.get("Attendee Status") or "").strip()
                if not attendee_id or not status:
                    _refuse("malformed_csv")
                if attendee_id in seen_attendee_ids:
                    continue
                seen_attendee_ids.add(attendee_id)
                email = (row.get("Email") or "").strip()
                rows.append(
                    RegistrantRow(
                        external_registrant_identifier=attendee_id,
                        normalized_email=normalize_account_email(email),
                        status=status.casefold(),
                        registered_at_raw=(row.get("Order Date") or "").strip(),
                    )
                )
    except RegistrantImportError:
        raise
    except (BadZipFile, OSError, UnicodeDecodeError, csv.Error) as error:
        raise RegistrantImportError("malformed_csv") from error
    return tuple(rows)


def eventbrite_registrant_sources(
    *, archive_path: Path, identities_path: Path
) -> tuple[PendingEventRegistrants, ...]:
    """Discover the export's events, resolved against the reviewed identities file.

    An id the identities file does not carry as ``resolved`` (6 of 209 -- 3
    ambiguous, 3 unresolved) or does not carry at all still gets a pending
    entry, so it is reported under ``awaiting_identity_events`` like any other
    unresolved event, rather than silently dropped.
    """

    identities = load_resolved_eventbrite_identities(identities_path)
    discovered = discover_eventbrite_registrant_files(archive_path)
    pending: list[PendingEventRegistrants] = []
    for item in discovered:
        identity = identities.get(item.external_event_identifier)
        resolve_event = (
            partial(_resolve_canonical_event, identity) if identity is not None else (lambda: None)
        )
        pending.append(
            PendingEventRegistrants(
                external_event_identifier=item.external_event_identifier,
                read_rows=partial(
                    read_eventbrite_registrant_rows,
                    item.archive_path,
                    item.member_name,
                    external_event_identifier=item.external_event_identifier,
                ),
                resolve_event=resolve_event,
            )
        )
    return tuple(pending)
