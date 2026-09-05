"""Read a Luma export directory and reduce it to aggregate-only evidence.

Rows are streamed and deduplicated in memory.  What leaves this module contains
only provider event identifiers, counts, checksums, bounded codes, and optional
exact canonical mapping proposals; no attendee value crosses the boundary.

This is an *adapter*: it turns one provider's file format into the neutral
:class:`events.importers.DerivedSource` the domain already speaks.  Nothing on a
public request path imports it -- ``scripts/prod/import_events.py`` registers it
with the domain's source-reader registry when an ingest run needs it.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from events.importers import (
    ADAPTER_VERSION,
    AggregateCandidate,
    CanonicalProposal,
    DerivedSource,
    ProtectedSourceError,
    SourceReader,
)
from events.models import (
    HistoricalRegistrationAggregateRevision,
    HistoricalRegistrationSourceRun,
)

from .safety import (
    MAX_ROWS,
    aggregate_checksum,
    checked_files,
    directory_checksum,
    mapping_evidence,
    safe_path,
)

PROVIDER = HistoricalRegistrationSourceRun.Provider.LUMA
RECONCILIATION_PROFILE = "luma_snapshot_v1"
SCHEMA_VERSION = "luma_v1"
REQUIRED_COLUMNS = ("event_id", "guest_id", "approval_status")
# Event-level fields (duplicated on every row of a Luma export) used only for
# identity discovery -- never an attendee identity field.
DISCOVERY_REQUIRED_COLUMNS = (*REQUIRED_COLUMNS, "event_name", "event_start_at")


def _require_pinned_reconciliation(
    *,
    files: tuple[Path, ...],
    candidates: tuple[AggregateCandidate, ...],
    parsed_total: int,
    eligible_total: int,
    excluded_total: int,
    statuses: Counter[str],
    bridge: Mapping[str, CanonicalProposal],
    source_missing: Mapping[str, CanonicalProposal | None],
) -> None:
    proposal_total = sum(candidate.proposal is not None for candidate in candidates)
    review_total = sum(candidate.proposal is None for candidate in candidates)
    nonempty_total = sum(
        candidate.eligible_count + candidate.excluded_count + candidate.quarantined_count > 0
        for candidate in candidates
    )
    if (
        len(files) != 318
        or len(candidates) != 159
        or parsed_total != 50_505
        or eligible_total != 50_456
        or excluded_total != 49
        or statuses != Counter({"approved": 50_456, "declined": 49})
        or sum(candidate.eligible_count for candidate in candidates) != eligible_total
        or sum(candidate.excluded_count for candidate in candidates) != excluded_total
        or any(candidate.quarantined_count for candidate in candidates)
        or any(
            candidate.state != HistoricalRegistrationAggregateRevision.State.STAGED
            for candidate in candidates
        )
        or nonempty_total != 157
        or len(candidates) - nonempty_total != 2
        or len(bridge) != 64
        or proposal_total != 64
        or review_total != 95
        or source_missing
    ):
        raise ProtectedSourceError("protected_fact_mismatch")


def derive_luma(
    path: Path,
    *,
    expected_checksum: str,
    mapping_bridge: Mapping[str, object] = MappingProxyType({}),
    source_missing: Mapping[str, object] = MappingProxyType({}),
    enforce_pinned_reconciliation: bool = False,
    allow_partial_mapping: bool = False,
) -> DerivedSource:
    """Derive one complete source, optionally carrying an explicit current-event bridge.

    ``allow_partial_mapping`` skips only the legacy whole-snapshot mapping cardinality
    gate.  Checksum, structure, row, status, and aggregate validation still run.  It is
    intended for the separate current-event activation path, never for inferred mappings.
    """

    bridge, missing = mapping_evidence(
        mapping_bridge=mapping_bridge,
        source_missing=source_missing,
    )
    root = safe_path(path, expected_kind="directory")
    files = checked_files(root)
    checksum = directory_checksum(root, files)
    if checksum != expected_checksum:
        raise ProtectedSourceError("checksum_drift")
    csv_by_stem = {path.stem: path for path in files if path.suffix.casefold() == ".csv"}
    json_by_stem = {path.stem: path for path in files if path.suffix.casefold() == ".json"}
    if set(csv_by_stem) != set(json_by_stem):
        raise ProtectedSourceError("mismatched_luma_pair")

    candidates: list[AggregateCandidate] = []
    event_ids: set[str] = set()
    event_urls: set[str] = set()
    all_statuses: Counter[str] = Counter()
    parsed_total = eligible_total = excluded_total = 0
    for stem in sorted(csv_by_stem):
        try:
            document = json.loads(json_by_stem[stem].read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProtectedSourceError("malformed_json") from error
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ProtectedSourceError("unsupported_luma_schema")
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
            raise ProtectedSourceError("malformed_json")
        if event_id in event_ids or event_url in event_urls:
            raise ProtectedSourceError("duplicate_event_identifier")
        event_ids.add(event_id)
        event_urls.add(event_url)
        eligible = excluded = 0
        quarantined = False
        statuses: Counter[str] = Counter()
        dedupe: set[tuple[str, str]] = set()
        try:
            with csv_by_stem[stem].open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream, strict=True)
                headers = reader.fieldnames
                if (
                    headers is None
                    or len(headers) != len(set(headers))
                    or any(column not in headers for column in REQUIRED_COLUMNS)
                ):
                    raise ProtectedSourceError("unsupported_luma_schema")
                for row in reader:
                    parsed_total += 1
                    if parsed_total > MAX_ROWS:
                        raise ProtectedSourceError("row_count_exceeded")
                    if row.get("event_id") != event_id:
                        raise ProtectedSourceError("mismatched_luma_pair")
                    guest_id = row.get("guest_id")
                    status_value = row.get("approval_status")
                    if not guest_id or not status_value:
                        raise ProtectedSourceError("malformed_csv")
                    dedupe_key = (event_id, guest_id)
                    if dedupe_key in dedupe:
                        quarantined = True
                        statuses["duplicate"] += 1
                        continue
                    dedupe.add(dedupe_key)
                    status_key = status_value.casefold()
                    statuses[status_key] += 1
                    if status_key == "approved":
                        eligible += 1
                    elif status_key == "declined":
                        excluded += 1
                    else:
                        quarantined = True
        except ProtectedSourceError:
            raise
        except (OSError, UnicodeDecodeError, csv.Error) as error:
            raise ProtectedSourceError("malformed_csv") from error
        reason_code = (
            "unknown_status"
            if any(status not in {"approved", "declined", "duplicate"} for status in statuses)
            else "duplicate_registration"
            if statuses["duplicate"]
            else ""
        )
        state = (
            HistoricalRegistrationAggregateRevision.State.QUARANTINED
            if quarantined
            else HistoricalRegistrationAggregateRevision.State.STAGED
        )
        all_statuses.update(statuses)
        eligible_total += eligible
        excluded_total += excluded + statuses["duplicate"]
        if event_id in bridge and event_url in bridge:
            raise ProtectedSourceError("invalid_mapping_bridge")
        candidates.append(
            AggregateCandidate(
                external_event_identifier=event_id,
                eligible_count=eligible,
                excluded_count=excluded + statuses["duplicate"],
                quarantined_count=sum(
                    count
                    for status, count in statuses.items()
                    if status not in {"approved", "declined"}
                ),
                status_totals=dict(sorted(statuses.items())),
                schema_version=SCHEMA_VERSION,
                state=state,
                reason_code=reason_code,
                aggregate_checksum=aggregate_checksum(
                    provider=PROVIDER,
                    external_id=event_id,
                    eligible=eligible,
                    excluded=excluded + statuses["duplicate"],
                    quarantined=sum(
                        count
                        for status, count in statuses.items()
                        if status not in {"approved", "declined"}
                    ),
                ),
                proposal=bridge[event_id] if event_id in bridge else bridge.get(event_url),
            )
        )
    if set(bridge) - (event_ids | event_urls) or set(missing) & (event_ids | event_urls):
        raise ProtectedSourceError("invalid_mapping_bridge")
    quarantined_total = sum(
        item.state == HistoricalRegistrationAggregateRevision.State.QUARANTINED
        for item in candidates
    )
    state_totals = Counter(item.state for item in candidates)
    reason_codes = tuple(sorted({item.reason_code for item in candidates if item.reason_code}))
    candidate_tuple = tuple(candidates)
    if enforce_pinned_reconciliation and not allow_partial_mapping:
        _require_pinned_reconciliation(
            files=files,
            candidates=candidate_tuple,
            parsed_total=parsed_total,
            eligible_total=eligible_total,
            excluded_total=excluded_total,
            statuses=all_statuses,
            bridge=bridge,
            source_missing=missing,
        )
    return DerivedSource(
        provider=PROVIDER,
        adapter_version=ADAPTER_VERSION,
        schema_version=SCHEMA_VERSION,
        whole_source_checksum=checksum,
        manifest_entry_total=len(files),
        manifest_event_total=len(candidates),
        parsed_row_total=parsed_total,
        eligible_row_total=eligible_total,
        excluded_row_total=excluded_total,
        quarantined_event_total=quarantined_total,
        status_totals=dict(sorted(all_statuses.items())),
        state_totals=dict(sorted(state_totals.items())),
        reason_codes=reason_codes,
        candidates=candidate_tuple,
        source_missing=tuple(
            (external_id, proposal) for external_id, proposal in sorted(missing.items())
        ),
    )


@dataclass(frozen=True, slots=True)
class DiscoveredLumaEvent:
    """One Luma export event's public, event-level identity metadata.

    Never carries a guest id, name, email, phone number, or any other
    attendee-level value -- only fields duplicated on every row of the export
    that describe the event itself.
    """

    external_event_identifier: str
    event_url: str
    title: str
    start_at: str
    eligible_count: int
    excluded_count: int
    quarantined_count: int
    row_total: int


def discover_luma_events(path: Path) -> tuple[DiscoveredLumaEvent, ...]:
    """Read one Luma export directory for identity discovery, not registration counts.

    This is deliberately not :func:`derive_luma`.  ``derive_luma`` exists to
    produce registration *counts*, so it refuses to run unless the whole-tree
    checksum matches a previously pinned, reviewed fact
    (``_docs/migration-data/event-registration-sources.json``) -- a fresh export
    a human has not yet reconciled will always fail that gate, by design.

    Minting an event *identity* carries none of that risk: it is title and a
    canonical path, nothing that could silently corrupt a public count.  So this
    reader applies the same structural safety checks ``derive_luma`` does (safe,
    non-symlink paths; hidden-entry and archive-shape rejection; required-column
    and pair-shape validation) but never compares against a pinned checksum --
    the caller decides what a "new" event is from the result, not this function.

    Returns one :class:`DiscoveredLumaEvent` per CSV/JSON pair, sorted by file
    stem, alongside eligible/excluded/quarantined counts computed with the same
    status policy ``derive_luma`` uses (``approved`` eligible, ``declined``
    excluded, anything else quarantined) so a caller can sanity-check a count
    against the raw export without a second, divergent implementation of the
    status rule.

    A CSV with a header row and no data rows produces ``title == ""`` (there is
    nowhere else in a Luma export to read a title from) rather than raising --
    a real event can genuinely have zero registrations.  Callers must treat an
    empty title as "cannot mint an identity yet", not as a malformed export.
    """

    root = safe_path(path, expected_kind="directory")
    files = checked_files(root)
    csv_by_stem = {file.stem: file for file in files if file.suffix.casefold() == ".csv"}
    json_by_stem = {file.stem: file for file in files if file.suffix.casefold() == ".json"}
    if set(csv_by_stem) != set(json_by_stem):
        raise ProtectedSourceError("mismatched_luma_pair")

    discovered: list[DiscoveredLumaEvent] = []
    event_ids: set[str] = set()
    for stem in sorted(csv_by_stem):
        try:
            document = json.loads(json_by_stem[stem].read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProtectedSourceError("malformed_json") from error
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ProtectedSourceError("unsupported_luma_schema")
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
            raise ProtectedSourceError("malformed_json")
        if event_id in event_ids:
            raise ProtectedSourceError("duplicate_event_identifier")
        event_ids.add(event_id)

        title = ""
        start_at = ""
        eligible = excluded = quarantined = 0
        row_total = 0
        try:
            with csv_by_stem[stem].open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream, strict=True)
                headers = reader.fieldnames
                if (
                    headers is None
                    or len(headers) != len(set(headers))
                    or any(column not in headers for column in DISCOVERY_REQUIRED_COLUMNS)
                ):
                    raise ProtectedSourceError("unsupported_luma_schema")
                for row in reader:
                    row_total += 1
                    if row_total > MAX_ROWS:
                        raise ProtectedSourceError("row_count_exceeded")
                    if row.get("event_id") != event_id:
                        raise ProtectedSourceError("mismatched_luma_pair")
                    if not title:
                        title = (row.get("event_name") or "").strip()
                        start_at = (row.get("event_start_at") or "").strip()
                    status_value = row.get("approval_status")
                    if not status_value:
                        raise ProtectedSourceError("malformed_csv")
                    status_key = status_value.casefold()
                    if status_key == "approved":
                        eligible += 1
                    elif status_key == "declined":
                        excluded += 1
                    else:
                        quarantined += 1
        except ProtectedSourceError:
            raise
        except (OSError, UnicodeDecodeError, csv.Error) as error:
            raise ProtectedSourceError("malformed_csv") from error
        # A zero-registration event has no row to read a title from at all -- an
        # empty CSV body is valid (some real events genuinely have none), so this
        # is reported with an empty title rather than aborting every other event
        # in the export; the caller skips creating an identity without one.
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


def source_reader() -> SourceReader:
    return SourceReader(
        provider=PROVIDER,
        reconciliation_profile=RECONCILIATION_PROFILE,
        read=derive_luma,
    )
