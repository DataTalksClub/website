"""Protected-source adapters that derive and return aggregate-only evidence.

Rows are streamed and deduplicated in memory.  Returned objects contain only
provider event identifiers, counts, checksums, bounded codes, and optional exact
canonical mapping proposals; no attendee value crosses this module boundary.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import stat
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from zipfile import BadZipFile, ZipFile

from django.conf import settings

from .models import HistoricalRegistrationAggregateRevision, HistoricalRegistrationSourceRun

ADAPTER_VERSION = "historical-aggregate-v1"
STATUS_POLICY_VERSION = "historical-status-v1"
PINNED_EVENTBRITE_SOURCE_CHECKSUM = (
    "5cc493c7e9a142d09f5a524d28df486f4fa33ce832210ea0d325025b939744df"
)
LUMA_RECONCILIATION_PROFILE = "luma_snapshot_v1"
EVENTBRITE_RECONCILIATION_PROFILE = "eventbrite_snapshot_v1"
SYNTHETIC_RECONCILIATION_PROFILE = "synthetic"
EVENTBRITE_SCHEMA_FINGERPRINTS = MappingProxyType(
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
EVENTBRITE_REQUIRED_COLUMNS = (
    "Order #",
    "Order Date",
    "Attendee #",
    "Attendee Status",
)
LUMA_REQUIRED_COLUMNS = ("event_id", "guest_id", "approval_status")
MAX_ARCHIVE_ENTRIES = 5_000
MAX_COMPRESSED_BYTES = 512 * 1024 * 1024
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ENTRY_BYTES = 128 * 1024 * 1024
MAX_EXPANSION_RATIO = 20
MAX_ROWS = 2_000_000
_REFERENCE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_EVENTBRITE_ENTRY = re.compile(r"^(?P<event_id>[0-9]{1,20})\.csv$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# `_REFERENCE` requires a lowercase leading character, so this leading-underscore
# namespace cannot collide with a registered raw source reference.
_REGISTERED_SOURCE_TOKEN_PREFIX = "__dtc_historical_source_token_v1__:"
_REGISTERED_SOURCE_TOKEN = re.compile(
    rf"{re.escape(_REGISTERED_SOURCE_TOKEN_PREFIX)}(?P<digest>[0-9a-f]{{64}})"
)


class ProtectedSourceError(ValueError):
    """A bounded source failure that never embeds protected source values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CanonicalProposal:
    repository: str
    revision: str
    source_key: str
    slug: str


@dataclass(frozen=True, slots=True)
class AggregateCandidate:
    external_event_identifier: str
    eligible_count: int
    excluded_count: int
    quarantined_count: int
    status_totals: Mapping[str, int]
    schema_version: str
    state: str
    reason_code: str
    aggregate_checksum: str
    proposal: CanonicalProposal | None = None


@dataclass(frozen=True, slots=True)
class DerivedSource:
    provider: str
    adapter_version: str
    schema_version: str
    whole_source_checksum: str
    manifest_entry_total: int
    manifest_event_total: int
    parsed_row_total: int
    eligible_row_total: int
    excluded_row_total: int
    quarantined_event_total: int
    status_totals: Mapping[str, int]
    state_totals: Mapping[str, int]
    reason_codes: tuple[str, ...]
    candidates: tuple[AggregateCandidate, ...]
    source_missing: tuple[tuple[str, CanonicalProposal | None], ...] = ()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _aggregate_checksum(
    *, provider: str, external_id: str, eligible: int, excluded: int, quarantined: int
) -> str:
    digest = hashlib.sha256(b"dtc-historical-aggregate-v1\0")
    for value in (provider, external_id, str(eligible), str(excluded), str(quarantined)):
        encoded = value.encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _proposal(value: object) -> CanonicalProposal | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {
        "repository",
        "revision",
        "source_key",
        "slug",
    }:
        raise ProtectedSourceError("invalid_mapping_bridge")
    fields = tuple(value[key] for key in ("repository", "revision", "source_key", "slug"))
    if any(not isinstance(item, str) or not item or len(item) > 512 for item in fields):
        raise ProtectedSourceError("invalid_mapping_bridge")
    return CanonicalProposal(*fields)


def _mapping_evidence(
    *,
    provider: str,
    mapping_bridge: Mapping[str, object],
    source_missing: Mapping[str, object],
) -> tuple[dict[str, CanonicalProposal], dict[str, CanonicalProposal | None]]:
    bridge: dict[str, CanonicalProposal] = {}
    missing: dict[str, CanonicalProposal | None] = {}
    for external_id, value in mapping_bridge.items():
        if (
            not isinstance(external_id, str)
            or not external_id
            or len(external_id) > 2_048
            or (
                provider == HistoricalRegistrationSourceRun.Provider.EVENTBRITE
                and _EVENTBRITE_ENTRY.fullmatch(f"{external_id}.csv") is None
            )
        ):
            raise ProtectedSourceError("invalid_mapping_bridge")
        proposal = _proposal(value)
        if proposal is None:
            raise ProtectedSourceError("invalid_mapping_bridge")
        bridge[external_id] = proposal
    for external_id, value in source_missing.items():
        if (
            not isinstance(external_id, str)
            or not external_id
            or len(external_id) > 512
            or (
                provider == HistoricalRegistrationSourceRun.Provider.EVENTBRITE
                and _EVENTBRITE_ENTRY.fullmatch(f"{external_id}.csv") is None
            )
        ):
            raise ProtectedSourceError("invalid_mapping_bridge")
        missing[external_id] = _proposal(value)
    if set(bridge) & set(missing):
        raise ProtectedSourceError("invalid_mapping_bridge")
    return bridge, missing


def _require_pinned_luma_reconciliation(
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


def _require_pinned_eventbrite_reconciliation(
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
        or schema_totals
        != Counter(
            {
                "eventbrite_csv_v1": 22,
                "eventbrite_csv_v2": 12,
                "eventbrite_csv_v3": 175,
            }
        )
        or len(bridge) != 200
        or proposal_total != 200
        or review_total != 9
        or len(source_missing) != 27
    ):
        raise ProtectedSourceError("protected_fact_mismatch")


def _safe_path(path: Path, *, expected_kind: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        metadata = path.lstat()
    except OSError as error:
        raise ProtectedSourceError("source_unavailable") from error
    if stat.S_ISLNK(metadata.st_mode):
        raise ProtectedSourceError("source_symlink")
    if expected_kind == "file" and not resolved.is_file():
        raise ProtectedSourceError("source_not_file")
    if expected_kind == "directory" and not resolved.is_dir():
        raise ProtectedSourceError("source_not_directory")
    return resolved


def _directory_checksum(root: Path, files: tuple[Path, ...]) -> str:
    digest = hashlib.sha256(b"dtc-protected-tree-v1\0")
    for path in files:
        relative = path.relative_to(root).as_posix().encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _checked_files(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.name.startswith("."):
            raise ProtectedSourceError("hidden_entry")
        try:
            metadata = path.lstat()
        except OSError as error:
            raise ProtectedSourceError("source_unavailable") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise ProtectedSourceError("source_symlink")
        if not stat.S_ISREG(metadata.st_mode):
            raise ProtectedSourceError("unsupported_entry")
        if path.suffix.casefold() not in {".csv", ".json"}:
            raise ProtectedSourceError("unsupported_entry")
        if metadata.st_size > MAX_ENTRY_BYTES:
            raise ProtectedSourceError("entry_too_large")
        files.append(path)
    if len(files) > MAX_ARCHIVE_ENTRIES:
        raise ProtectedSourceError("entry_count_exceeded")
    if len({path.name.casefold() for path in files}) != len(files):
        raise ProtectedSourceError("duplicate_entry")
    return tuple(files)


def derive_luma(
    path: Path,
    *,
    expected_checksum: str,
    mapping_bridge: Mapping[str, object] = MappingProxyType({}),
    source_missing: Mapping[str, object] = MappingProxyType({}),
    enforce_pinned_reconciliation: bool = False,
) -> DerivedSource:
    bridge, missing = _mapping_evidence(
        provider=HistoricalRegistrationSourceRun.Provider.LUMA,
        mapping_bridge=mapping_bridge,
        source_missing=source_missing,
    )
    root = _safe_path(path, expected_kind="directory")
    files = _checked_files(root)
    checksum = _directory_checksum(root, files)
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
                    or any(column not in headers for column in LUMA_REQUIRED_COLUMNS)
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
                schema_version="luma_v1",
                state=state,
                reason_code=reason_code,
                aggregate_checksum=_aggregate_checksum(
                    provider=HistoricalRegistrationSourceRun.Provider.LUMA,
                    external_id=event_id,
                    eligible=eligible,
                    excluded=excluded + statuses["duplicate"],
                    quarantined=sum(
                        count
                        for status, count in statuses.items()
                        if status not in {"approved", "declined"}
                    ),
                ),
                proposal=bridge.get(event_url),
            )
        )
    if set(bridge) - event_urls or set(missing) & event_ids:
        raise ProtectedSourceError("invalid_mapping_bridge")
    quarantined_total = sum(
        item.state == HistoricalRegistrationAggregateRevision.State.QUARANTINED
        for item in candidates
    )
    state_totals = Counter(item.state for item in candidates)
    reason_codes = tuple(sorted({item.reason_code for item in candidates if item.reason_code}))
    candidate_tuple = tuple(candidates)
    if enforce_pinned_reconciliation:
        _require_pinned_luma_reconciliation(
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
        provider=HistoricalRegistrationSourceRun.Provider.LUMA,
        adapter_version=ADAPTER_VERSION,
        schema_version="luma_v1",
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


def _validate_archive_member(name: str) -> PurePosixPath:
    if "\\" in name:
        raise ProtectedSourceError("path_traversal")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ProtectedSourceError("path_traversal")
    if any(part.startswith(".") for part in path.parts):
        raise ProtectedSourceError("hidden_entry")
    if len(path.parts) != 1:
        raise ProtectedSourceError("unsafe_archive_structure")
    return path


def derive_eventbrite(
    path: Path,
    *,
    expected_checksum: str,
    mapping_bridge: Mapping[str, object] = MappingProxyType({}),
    source_missing: Mapping[str, object] = MappingProxyType({}),
    allowed_schema_fingerprints: Mapping[str, tuple[str, int]] = EVENTBRITE_SCHEMA_FINGERPRINTS,
    enforce_pinned_reconciliation: bool = False,
) -> DerivedSource:
    bridge, missing = _mapping_evidence(
        provider=HistoricalRegistrationSourceRun.Provider.EVENTBRITE,
        mapping_bridge=mapping_bridge,
        source_missing=source_missing,
    )
    archive_path = _safe_path(path, expected_kind="file")
    try:
        payload = archive_path.read_bytes()
    except OSError as error:
        raise ProtectedSourceError("source_unavailable") from error
    if len(payload) > MAX_COMPRESSED_BYTES:
        raise ProtectedSourceError("source_too_large")
    checksum = _sha256_bytes(payload)
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
        member = _validate_archive_member(entry.filename)
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
        match = _EVENTBRITE_ENTRY.fullmatch(entry.filename)
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
                fingerprint = _sha256_bytes("\x1f".join(headers).encode())
                schema = allowed_schema_fingerprints.get(fingerprint)
                if (
                    schema is None
                    or schema[1] != len(headers)
                    or any(column not in headers for column in EVENTBRITE_REQUIRED_COLUMNS)
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
                            aggregate_checksum=_aggregate_checksum(
                                provider=HistoricalRegistrationSourceRun.Provider.EVENTBRITE,
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
                aggregate_checksum=_aggregate_checksum(
                    provider=HistoricalRegistrationSourceRun.Provider.EVENTBRITE,
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
    if checksum == PINNED_EVENTBRITE_SOURCE_CHECKSUM or enforce_pinned_reconciliation:
        _require_pinned_eventbrite_reconciliation(
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
        provider=HistoricalRegistrationSourceRun.Provider.EVENTBRITE,
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


def _source_registry() -> Mapping[str, object]:
    registry = getattr(settings, "HISTORICAL_REGISTRATION_SOURCES", {})
    if not isinstance(registry, Mapping):
        raise ProtectedSourceError("source_registry_invalid")
    return registry


def source_reference_digest(reference: str) -> str:
    if not isinstance(reference, str) or _REFERENCE.fullmatch(reference) is None:
        raise ProtectedSourceError("source_reference_invalid")
    return hashlib.sha256(f"dtc-source-reference-v1\0{reference}".encode()).hexdigest()


def registered_source_options() -> tuple[dict[str, str], ...]:
    """Return safe Studio choices without exposing registry keys."""

    registry = _source_registry()
    labels: dict[str, str] = {
        HistoricalRegistrationSourceRun.Provider.LUMA: "Luma historical registration source",
        HistoricalRegistrationSourceRun.Provider.EVENTBRITE: (
            "Eventbrite historical registration source"
        ),
    }
    options: list[dict[str, str]] = []
    for reference in sorted(reference for reference in registry if isinstance(reference, str)):
        try:
            token = f"{_REGISTERED_SOURCE_TOKEN_PREFIX}{source_reference_digest(reference)}"
        except ProtectedSourceError:
            continue
        configuration = registry[reference]
        provider = configuration.get("provider") if isinstance(configuration, Mapping) else None
        if not isinstance(provider, str):
            provider = ""
        options.append(
            {
                "value": token,
                "label": labels.get(provider, "Protected historical registration source"),
            }
        )
    return tuple(options)


def resolve_registered_source_reference(selection: str) -> str:
    """Resolve a Studio selection token while retaining direct service input support."""

    if not isinstance(selection, str):
        raise ProtectedSourceError("source_reference_invalid")
    registry = _source_registry()
    if selection.startswith(_REGISTERED_SOURCE_TOKEN_PREFIX):
        token = _REGISTERED_SOURCE_TOKEN.fullmatch(selection)
        if token is None:
            raise ProtectedSourceError("source_reference_token_invalid")
        digest = token["digest"]
        matches: list[str] = []
        for reference in registry:
            if not isinstance(reference, str):
                continue
            try:
                if source_reference_digest(reference) == digest:
                    matches.append(reference)
            except ProtectedSourceError:
                continue
        if len(matches) != 1:
            raise ProtectedSourceError("source_reference_unregistered")
        return matches[0]

    source_reference_digest(selection)
    if selection not in registry:
        raise ProtectedSourceError("source_reference_unregistered")
    return selection


def derive_registered_source(
    reference: str, *, expected_provider: str | None = None
) -> DerivedSource:
    digest = source_reference_digest(reference)
    del digest  # The digest is stored by the service; the raw reference is not.
    configuration = _source_registry().get(reference)
    if not isinstance(configuration, Mapping):
        raise ProtectedSourceError("source_reference_unregistered")
    allowed_keys = {
        "provider",
        "path",
        "sha256",
        "mapping_bridge",
        "source_missing",
        "reconciliation_profile",
    }
    if set(configuration) - allowed_keys:
        raise ProtectedSourceError("source_registry_invalid")
    provider = configuration.get("provider")
    raw_path = configuration.get("path")
    checksum = configuration.get("sha256")
    bridge = configuration.get("mapping_bridge", {})
    missing = configuration.get("source_missing", {})
    reconciliation_profile = configuration.get("reconciliation_profile")
    if (
        provider not in HistoricalRegistrationSourceRun.Provider.values
        or (expected_provider is not None and provider != expected_provider)
        or not isinstance(raw_path, str | Path)
        or not isinstance(checksum, str)
        or _SHA256.fullmatch(checksum) is None
        or not isinstance(bridge, Mapping)
        or not isinstance(missing, Mapping)
        or not isinstance(reconciliation_profile, str)
    ):
        raise ProtectedSourceError("source_registry_invalid")
    expected_profile = {
        HistoricalRegistrationSourceRun.Provider.LUMA: LUMA_RECONCILIATION_PROFILE,
        HistoricalRegistrationSourceRun.Provider.EVENTBRITE: EVENTBRITE_RECONCILIATION_PROFILE,
    }[provider]
    synthetic_profile = reconciliation_profile == SYNTHETIC_RECONCILIATION_PROFILE and getattr(
        settings, "HISTORICAL_REGISTRATION_ALLOW_SYNTHETIC_PROFILE", False
    )
    if reconciliation_profile != expected_profile and not synthetic_profile:
        raise ProtectedSourceError("source_registry_invalid")
    enforce_pinned_reconciliation = not synthetic_profile
    if provider == HistoricalRegistrationSourceRun.Provider.LUMA:
        return derive_luma(
            Path(raw_path),
            expected_checksum=checksum,
            mapping_bridge=bridge,
            source_missing=missing,
            enforce_pinned_reconciliation=enforce_pinned_reconciliation,
        )
    return derive_eventbrite(
        Path(raw_path),
        expected_checksum=checksum,
        mapping_bridge=bridge,
        source_missing=missing,
        enforce_pinned_reconciliation=enforce_pinned_reconciliation,
    )
