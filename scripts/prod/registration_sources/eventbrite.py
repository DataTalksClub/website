"""Read a prepared Eventbrite export archive and reduce it to aggregate-only evidence.

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
import io
import re
import stat
from collections import Counter
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from zipfile import BadZipFile, ZipFile

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
    MAX_ARCHIVE_ENTRIES,
    MAX_COMPRESSED_BYTES,
    MAX_ENTRY_BYTES,
    MAX_EXPANDED_BYTES,
    MAX_EXPANSION_RATIO,
    MAX_ROWS,
    aggregate_checksum,
    mapping_evidence,
    safe_path,
    sha256_bytes,
    validate_archive_member,
)

PROVIDER = HistoricalRegistrationSourceRun.Provider.EVENTBRITE
RECONCILIATION_PROFILE = "eventbrite_snapshot_v1"
PINNED_SOURCE_CHECKSUM = "5cc493c7e9a142d09f5a524d28df486f4fa33ce832210ea0d325025b939744df"
SCHEMA_FINGERPRINTS = MappingProxyType(
    {
        "333061583991588f9b6bc78c9873feb7ddab8711687ee999da2135a4cbef0c7e": (
            "eventbrite_csv_v1",
            23,
        ),
        "6f7f37db55176240fa695289cf13c8bcbaf86970f00b0ed18c4f2a1a6ee4e9ae": (
            "eventbrite_csv_v2",
            25,
        ),
        "c3a799fcbcee38d3e1733fc0cd317e84236f5d17241513c1a76b3646a19ea0b8": (
            "eventbrite_csv_v3",
            24,
        ),
    }
)
REQUIRED_COLUMNS = (
    "Order #",
    "Order Date",
    "Attendee #",
    "Attendee Status",
)
_ENTRY = re.compile(r"^(?P<event_id>[0-9]{1,20})\.csv$")
# How many of the real archive's CSVs carry each reviewed header.
_SCHEMA_CSV_TOTALS = {
    "eventbrite_csv_v1": 22,
    "eventbrite_csv_v2": 12,
    "eventbrite_csv_v3": 175,
}

# The reviewed acceptance facts for the one real export -- counts, status totals
# and header fingerprints only, never a source path or an event identity.  The
# pinned reconciliation guard below refuses any derivation that disagrees.
SAFE_SOURCE_FACTS = {
    "whole_source_checksum": PINNED_SOURCE_CHECKSUM,
    "manifest_entry_total": 210,
    "csv_total": 209,
    "unsupported_xlsx_total": 1,
    "expansion_ratio": "3.80",
    "parsed_row_total": 24_001,
    "provider_event_total": 209,
    "eligible_row_total": 24_001,
    "status_totals": {"Attending": 24_001},
    "duplicate_protected_key_total": 0,
    "exact_bridge_total": 200,
    "review_required_total": 9,
    "source_missing_total": 27,
    "csv_schemas": {
        name: {
            "header_sha256": fingerprint,
            "column_total": columns,
            "csv_total": _SCHEMA_CSV_TOTALS[name],
        }
        for fingerprint, (name, columns) in SCHEMA_FINGERPRINTS.items()
    },
}


def _is_event_identifier(external_id: str) -> bool:
    """One archive member per event, so an identifier must name a valid member."""

    return _ENTRY.fullmatch(f"{external_id}.csv") is not None


def _require_pinned_reconciliation(
    *,
    entries_total: int,
    candidates: tuple[AggregateCandidate, ...],
    xlsx_total: int,
    parsed_total: int,
    eligible_total: int,
    excluded_total: int,
    event_ids: set[str],
    statuses: Counter[str],
    schema_totals: Counter[str],
    bridge: Mapping[str, CanonicalProposal],
    source_missing: Mapping[str, CanonicalProposal | None],
) -> None:
    proposal_total = sum(candidate.proposal is not None for candidate in candidates)
    review_total = sum(candidate.proposal is None for candidate in candidates)
    if (
        entries_total != 210
        or len(candidates) != 209
        or xlsx_total != 1
        or parsed_total != 24_001
        or eligible_total != 24_001
        or excluded_total != 0
        or len(event_ids) != 209
        or statuses != Counter({"attending": 24_001})
        or sum(candidate.eligible_count for candidate in candidates) != eligible_total
        or any(candidate.excluded_count for candidate in candidates)
        or any(candidate.quarantined_count for candidate in candidates)
        or any(
            candidate.state != HistoricalRegistrationAggregateRevision.State.STAGED
            for candidate in candidates
        )
        or schema_totals != Counter(_SCHEMA_CSV_TOTALS)
        or len(bridge) != 200
        or proposal_total != 200
        or review_total != 9
        or len(source_missing) != 27
    ):
        raise ProtectedSourceError("protected_fact_mismatch")


def derive_eventbrite(
    path: Path,
    *,
    expected_checksum: str,
    mapping_bridge: Mapping[str, object] = MappingProxyType({}),
    source_missing: Mapping[str, object] = MappingProxyType({}),
    allowed_schema_fingerprints: Mapping[str, tuple[str, int]] = SCHEMA_FINGERPRINTS,
    enforce_pinned_reconciliation: bool = False,
    allow_partial_mapping: bool = False,
) -> DerivedSource:
    """Derive one complete archive with an optional explicit current-event bridge.

    The partial mode bypasses only the pinned legacy bridge-cardinality assertion; the
    archive checksum, member safety, schema, row, status, and aggregate checks remain
    mandatory.
    """

    bridge, missing = mapping_evidence(
        mapping_bridge=mapping_bridge,
        source_missing=source_missing,
        external_id_valid=_is_event_identifier,
    )
    archive_path = safe_path(path, expected_kind="file")
    try:
        payload = archive_path.read_bytes()
    except OSError as error:
        raise ProtectedSourceError("source_unavailable") from error
    if len(payload) > MAX_COMPRESSED_BYTES:
        raise ProtectedSourceError("source_too_large")
    checksum = sha256_bytes(payload)
    if checksum != expected_checksum:
        raise ProtectedSourceError("checksum_drift")
    try:
        archive = ZipFile(io.BytesIO(payload))
        entries = archive.infolist()
    except BadZipFile as error:
        raise ProtectedSourceError("malformed_archive") from error
    if not entries or len(entries) > MAX_ARCHIVE_ENTRIES:
        raise ProtectedSourceError("entry_count_exceeded")
    names: set[str] = set()
    event_ids: set[str] = set()
    expanded = 0
    for entry in entries:
        member = validate_archive_member(entry.filename)
        normalized = member.as_posix().casefold()
        if normalized in names:
            raise ProtectedSourceError("duplicate_entry")
        names.add(normalized)
        unix_mode = (entry.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(unix_mode):
            raise ProtectedSourceError("source_symlink")
        if entry.is_dir() or entry.file_size > MAX_ENTRY_BYTES:
            raise ProtectedSourceError("unsupported_entry")
        expanded += entry.file_size
        if expanded > MAX_EXPANDED_BYTES:
            raise ProtectedSourceError("expanded_size_exceeded")
        if entry.compress_size == 0 and entry.file_size:
            raise ProtectedSourceError("expansion_ratio_exceeded")
        if entry.compress_size and entry.file_size / entry.compress_size > MAX_EXPANSION_RATIO:
            raise ProtectedSourceError("expansion_ratio_exceeded")

    candidates: list[AggregateCandidate] = []
    all_statuses: Counter[str] = Counter()
    schema_totals: Counter[str] = Counter()
    reason_codes: set[str] = set()
    parsed_total = eligible_total = excluded_total = 0
    xlsx_total = 0
    for entry in entries:
        suffix = PurePosixPath(entry.filename).suffix.casefold()
        if suffix == ".xlsx":
            xlsx_total += 1
            reason_codes.add("unsupported_xlsx")
            continue
        match = _ENTRY.fullmatch(entry.filename)
        if suffix != ".csv" or match is None:
            raise ProtectedSourceError("invalid_eventbrite_entry_contract")
        event_id = match.group("event_id")
        if event_id in event_ids:
            raise ProtectedSourceError("duplicate_event_identifier")
        event_ids.add(event_id)
        eligible = excluded = quarantined = 0
        statuses: Counter[str] = Counter()
        state = HistoricalRegistrationAggregateRevision.State.STAGED
        reason_code = ""
        try:
            stream = io.TextIOWrapper(archive.open(entry), encoding="utf-8-sig", newline="")
            with stream:
                reader = csv.DictReader(stream, strict=True)
                headers = reader.fieldnames
                if headers is None or len(headers) != len(set(headers)):
                    raise ProtectedSourceError("unsupported_schema")
                fingerprint = sha256_bytes("\x1f".join(headers).encode())
                schema = allowed_schema_fingerprints.get(fingerprint)
                if (
                    schema is None
                    or schema[1] != len(headers)
                    or any(column not in headers for column in REQUIRED_COLUMNS)
                ):
                    state = HistoricalRegistrationAggregateRevision.State.QUARANTINED
                    reason_code = "unsupported_schema"
                    schema_version = "unsupported"
                    reason_codes.add(reason_code)
                    # Do not parse rows under an unsupported schema.
                    candidates.append(
                        AggregateCandidate(
                            external_event_identifier=event_id,
                            eligible_count=0,
                            excluded_count=0,
                            quarantined_count=0,
                            status_totals={},
                            schema_version=schema_version,
                            state=state,
                            reason_code=reason_code,
                            aggregate_checksum=aggregate_checksum(
                                provider=PROVIDER,
                                external_id=event_id,
                                eligible=0,
                                excluded=0,
                                quarantined=0,
                            ),
                            proposal=bridge.get(event_id),
                        )
                    )
                    continue
                schema_version = schema[0]
                schema_totals[schema_version] += 1
                dedupe: set[tuple[str, str]] = set()
                for row in reader:
                    parsed_total += 1
                    if parsed_total > MAX_ROWS:
                        raise ProtectedSourceError("row_count_exceeded")
                    order_id = row.get("Order #")
                    attendee_id = row.get("Attendee #")
                    status_value = row.get("Attendee Status")
                    if not order_id or not attendee_id or not status_value:
                        raise ProtectedSourceError("malformed_csv")
                    dedupe_key = (order_id, attendee_id)
                    if dedupe_key in dedupe:
                        quarantined += 1
                        statuses["duplicate"] += 1
                        state = HistoricalRegistrationAggregateRevision.State.QUARANTINED
                        reason_code = "duplicate_registration"
                        continue
                    dedupe.add(dedupe_key)
                    normalized_status = status_value.casefold()
                    statuses[normalized_status] += 1
                    if normalized_status == "attending":
                        eligible += 1
                    elif normalized_status in {"declined", "cancelled", "rejected"}:
                        excluded += 1
                    else:
                        quarantined += 1
                        state = HistoricalRegistrationAggregateRevision.State.QUARANTINED
                        reason_code = "unknown_status"
        except ProtectedSourceError:
            raise
        except (OSError, UnicodeDecodeError, csv.Error, RuntimeError) as error:
            raise ProtectedSourceError("malformed_csv") from error
        if reason_code:
            reason_codes.add(reason_code)
        all_statuses.update(statuses)
        eligible_total += eligible
        excluded_total += excluded + statuses["duplicate"]
        candidates.append(
            AggregateCandidate(
                external_event_identifier=event_id,
                eligible_count=eligible,
                excluded_count=excluded + statuses["duplicate"],
                quarantined_count=quarantined,
                status_totals=dict(sorted(statuses.items())),
                schema_version=schema_version,
                state=state,
                reason_code=reason_code,
                aggregate_checksum=aggregate_checksum(
                    provider=PROVIDER,
                    external_id=event_id,
                    eligible=eligible,
                    excluded=excluded + statuses["duplicate"],
                    quarantined=quarantined,
                ),
                proposal=bridge.get(event_id),
            )
        )
    if set(bridge) - event_ids or set(missing) & event_ids:
        raise ProtectedSourceError("invalid_mapping_bridge")
    state_totals = Counter(item.state for item in candidates)
    schema_summary = (
        "+".join(f"{name}:{count}" for name, count in sorted(schema_totals.items()))
        or "unsupported"
    )
    candidate_tuple = tuple(candidates)
    if (
        checksum == PINNED_SOURCE_CHECKSUM or enforce_pinned_reconciliation
    ) and not allow_partial_mapping:
        _require_pinned_reconciliation(
            entries_total=len(entries),
            candidates=candidate_tuple,
            xlsx_total=xlsx_total,
            parsed_total=parsed_total,
            eligible_total=eligible_total,
            excluded_total=excluded_total,
            event_ids=event_ids,
            statuses=all_statuses,
            schema_totals=schema_totals,
            bridge=bridge,
            source_missing=missing,
        )
    return DerivedSource(
        provider=PROVIDER,
        adapter_version=ADAPTER_VERSION,
        schema_version=schema_summary,
        whole_source_checksum=checksum,
        manifest_entry_total=len(entries),
        manifest_event_total=len(candidates),
        parsed_row_total=parsed_total,
        eligible_row_total=eligible_total,
        excluded_row_total=excluded_total,
        quarantined_event_total=sum(
            item.state == HistoricalRegistrationAggregateRevision.State.QUARANTINED
            for item in candidates
        ),
        status_totals=dict(sorted(all_statuses.items())),
        state_totals=dict(sorted(state_totals.items())),
        reason_codes=tuple(sorted(reason_codes)),
        candidates=candidate_tuple,
        source_missing=tuple(
            (external_id, proposal) for external_id, proposal in sorted(missing.items())
        ),
    )


def source_reader() -> SourceReader:
    return SourceReader(
        provider=PROVIDER,
        reconciliation_profile=RECONCILIATION_PROFILE,
        read=derive_eventbrite,
    )
