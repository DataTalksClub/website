"""The port a historical registration source reader plugs into.

This module owns the provider-neutral half of the protected-source contract: the
bounded failure code, the aggregate-only result types, the registry of
configured source references, and the registry of *readers* that know how to
turn one configured reference into a :class:`DerivedSource`.

It owns none of the provider file formats.  Reading one provider's export
directory or archive is ingestion work and lives in
``scripts/prod/registration_sources``; those readers register themselves here
through :func:`register_source_reader` when an ingest run needs them.  A process
with no reader registered -- an ordinary web process -- can still list and
resolve configured references, and fails closed with a bounded
``source_reader_unregistered`` if asked to actually derive one.

Returned objects contain only provider event identifiers, counts, checksums,
bounded codes, and optional exact canonical mapping proposals; no attendee value
crosses this module boundary.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from .models import HistoricalRegistrationSourceRun

ADAPTER_VERSION = "historical-aggregate-v1"
STATUS_POLICY_VERSION = "historical-status-v1"
SYNTHETIC_RECONCILIATION_PROFILE = "synthetic"
_REFERENCE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
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


@dataclass(frozen=True, slots=True)
class SourceReader:
    """What a provider's export reader must supply to be usable from here.

    ``reconciliation_profile`` is the code-owned profile name a registry entry
    for this provider must declare, so a configured source cannot silently
    borrow another provider's reviewed facts.  ``read`` is called with the
    source path and the keyword arguments :func:`derive_registered_source`
    passes below; it may accept more, and a reader that needs none of the extra
    ones still has to accept them.
    """

    provider: str
    reconciliation_profile: str
    read: Callable[..., DerivedSource]


_SOURCE_READERS: dict[str, SourceReader] = {}


def register_source_reader(reader: SourceReader) -> None:
    """Register one provider's reader, replacing any reader for that provider.

    Called by the ingestion layer, never by the domain: nothing under ``events``
    knows which providers exist beyond the stored
    :class:`~events.models.HistoricalRegistrationSourceRun.Provider` values.
    """

    if reader.provider not in HistoricalRegistrationSourceRun.Provider.values:
        raise ProtectedSourceError("source_reader_provider_invalid")
    _SOURCE_READERS[reader.provider] = reader


def clear_source_readers() -> None:
    """Forget every registered reader; a test uses this to restore the empty state."""

    _SOURCE_READERS.clear()


def registered_source_reader(provider: str) -> SourceReader:
    reader = _SOURCE_READERS.get(provider)
    if reader is None:
        raise ProtectedSourceError("source_reader_unregistered")
    return reader


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
    """Return safe Studio choices without exposing registry keys.

    A configured source is offered whether or not a reader for its provider is
    registered: this lists what an operator may *select*, and the registry is
    the same in every process.  Selecting one in a process with no reader fails
    closed at staging time with ``source_reader_unregistered`` rather than
    disappearing from the picker with no explanation.  The label comes from the
    stored provider vocabulary, so this module names no provider itself.
    """

    registry = _source_registry()
    options: list[dict[str, str]] = []
    for reference in sorted(reference for reference in registry if isinstance(reference, str)):
        try:
            token = f"{_REGISTERED_SOURCE_TOKEN_PREFIX}{source_reference_digest(reference)}"
        except ProtectedSourceError:
            continue
        configuration = registry[reference]
        provider = configuration.get("provider") if isinstance(configuration, Mapping) else None
        label = "Protected historical registration source"
        if provider in HistoricalRegistrationSourceRun.Provider.values:
            stored = HistoricalRegistrationSourceRun.Provider(provider)
            label = f"{stored.label} historical registration source"
        options.append({"value": token, "label": label})
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
    reference: str,
    *,
    expected_provider: str | None = None,
    mapping_bridge: Mapping[str, object] | None = None,
    source_missing: Mapping[str, object] | None = None,
    allow_partial_mapping: bool = False,
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
    bridge = configuration.get("mapping_bridge", {}) if mapping_bridge is None else mapping_bridge
    missing = configuration.get("source_missing", {}) if source_missing is None else source_missing
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
    reader = registered_source_reader(provider)
    synthetic_profile = reconciliation_profile == SYNTHETIC_RECONCILIATION_PROFILE and getattr(
        settings, "HISTORICAL_REGISTRATION_ALLOW_SYNTHETIC_PROFILE", False
    )
    if reconciliation_profile != reader.reconciliation_profile and not synthetic_profile:
        raise ProtectedSourceError("source_registry_invalid")
    return reader.read(
        Path(raw_path),
        expected_checksum=checksum,
        mapping_bridge=bridge,
        source_missing=missing,
        enforce_pinned_reconciliation=not synthetic_profile,
        allow_partial_mapping=allow_partial_mapping,
    )
