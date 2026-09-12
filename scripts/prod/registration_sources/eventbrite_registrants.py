"""Read Eventbrite export attendee-level registrant rows.

Eventbrite's real attendee-level export sat unread for a while -- not because
of any deliberate policy, but because only Luma's export had ever been built
into a reader (see the note this replaces in
``scripts/prod/registrant_import.py``'s module docstring history). The data was
there the whole time: ``.local/migration-data/events/eventbrite/aggregate-v1.zip``
(the same archive :mod:`scripts.prod.registration_sources.eventbrite` already
reads for registration *counts*, byte-identical to its ``export.zip`` twin,
just with the entries flattened to the top level rather than nested under
``eventbrite/csv/``) carries one CSV per event, ``{eventbrite_id}.csv``, with
real per-attendee rows -- name, email, order, and an ``Attendee Status`` that
is uniformly ``Attending`` (24,001 rows across 209 real events, verified).

This is the attendee-level twin of that aggregate-only reader, the same way
:mod:`scripts.prod.registration_sources.luma_registrants` is Luma's:
``events.importers``'s aggregate-only contract ("no attendee value crosses
this module boundary") stays true for the counts-only reader, and this one,
deliberately separate, is where an attendee value -- an email, read only to
normalize into the domain's consolidation key -- is allowed to cross at all.

The one real difference from Luma's reader is *identity resolution*, and it is
why :class:`scripts.prod.registrant_import.PendingEventRegistrants` gained an
optional ``resolve_event``. A discovered Luma event gets its own
provider-minted source identity (``scripts.prod.registrant_import.provider_source_identity``,
repository ``dtc-historical-source/luma``), so Luma's reader resolves through
the same lookup ``scripts.prod.registrant_import`` already used. Every Eventbrite
event in this export, by contrast, is one of the 421 events the *reviewed
legacy manifest* already describes -- Eventbrite was retired well before this
migration and nothing here has ever run event discovery against it -- so its
Event rows carry the manifest's ``DataTalksClub/datatalksclub.github.io``
source identity, not a provider-minted one. Resolution instead goes through
``~/prod/dtc-data/eventbrite-event-identities.json``: the same reviewed
mapping (209 numeric Eventbrite ids resolved to a canonical Event's
``source_repository``/``source_revision``/``source_key`` via ``events.xlsx``
cross-referenced against the identity manifest, 203 ``resolved``) that
:mod:`events.eventbrite_content` already uses to land cleaned descriptions.
Reusing it here, rather than re-deriving a second mapping, is deliberate: it
is the one place this repository has already reviewed which Eventbrite id is
which real event.

Nothing here mints an Event, resolves an aggregate count, or activates a
public registration count -- exactly as for Luma. See
``_docs/runbooks/event-registration-pull.md`` for when to run this, and
``_docs/runbooks/ingest-script-inventory.md`` for where it sits in the wider
inventory.
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
from events.models import Event, EventRegistration
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
# A different set from the aggregate-only reader's REQUIRED_COLUMNS
# (scripts.prod.registration_sources.eventbrite): this reader also needs
# "Email", which that one never touches.
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
    from events.models import EventIdentityNotFound, resolve_source_identity

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
    package uses. No pinned whole-archive checksum: unlike the aggregate-only
    reader this reads no public count, so there is nothing a silent drift
    here could corrupt (see ``scripts.prod.registrant_import``'s and
    ``luma_registrants``'s own docstrings for why that pin is aggregate-only).
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
