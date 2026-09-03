"""Application services for aggregate-only historical registration totals."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q

from core.audit import AuditWriteContext, record_audit_event
from core.models import AuditEvent, RevisionConflict
from core.services import ServiceContext
from jobs.dispatch import dispatch_after_commit

from .identity import (
    EventIdentityError,
    EventIdentityNotFound,
    canonical_detail_path,
    event_projection_record,
    resolve_source_identity,
    resolve_uuid,
)
from .importers import (
    STATUS_POLICY_VERSION,
    AggregateCandidate,
    DerivedSource,
    ProtectedSourceError,
    derive_registered_source,
    source_reference_digest,
)
from .models import (
    Event,
    HistoricalRegistrationAggregateRevision,
    HistoricalRegistrationAggregateSlot,
    HistoricalRegistrationPointerDisplacement,
    HistoricalRegistrationSourceRun,
    HistoricalRegistrationTotalState,
)

MAPPING_PERMISSION = "events.historical_registration_mapping_manage"
IMPORT_PERMISSION = "events.historical_registration_import_manage"
POLICY_VERSION = "historical-registration-v1"
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_COVERAGE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


class HistoricalRegistrationConflict(RuntimeError):
    pass


class HistoricalRegistrationInvalid(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PublicRegistrationTotal:
    count: int
    revision: int


def _audit_context(context: ServiceContext, *, actor: Any | None = None) -> AuditWriteContext:
    return AuditWriteContext.from_service_context(
        context,
        actor_id=getattr(actor, "pk", None),
    )


def _actor_ref(context: ServiceContext, actor: Any | None) -> str:
    if context.actor_ref:
        return context.actor_ref
    identifier = getattr(actor, "pk", None)
    return f"user:{identifier}" if identifier is not None else ""


def _validate_reason_code(reason_code: str, *, required: bool = True) -> str:
    if not isinstance(reason_code, str) or _REASON_CODE.fullmatch(reason_code) is None:
        if required:
            raise HistoricalRegistrationInvalid("reason_code_invalid")
        return ""
    return reason_code


def _validate_coverage(value: str) -> str:
    if not isinstance(value, str) or _COVERAGE.fullmatch(value) is None:
        raise HistoricalRegistrationInvalid("coverage_boundary_invalid")
    return value


def _canonical_event(event_id: uuid.UUID | str) -> dict[str, Any]:
    """Resolve a CMP aggregate target by UUID, then exact source provenance only."""

    try:
        resolved = resolve_uuid(event_id)
        record = event_projection_record(resolved)
        if record.get("identity_id") != str(resolved.id):
            raise EventIdentityError("event_projection_identity_mismatch")
        return record
    except (EventIdentityError, EventIdentityNotFound, ValueError, TypeError) as exc:
        raise HistoricalRegistrationInvalid("canonical_event_unavailable") from exc


def _canonical_identity(event: Mapping[str, Any]) -> tuple[str, str, str, str]:
    provenance = event["provenance"]
    return (
        provenance["repository"],
        provenance["revision"],
        provenance["source_key"],
        event["slug"],
    )


def _aggregate_matches_projection(aggregate: HistoricalRegistrationAggregateRevision) -> bool:
    """Whether a resolved aggregate's Event still resolves to itself in the public projection."""

    if aggregate.event_id is None:
        return False
    try:
        event = _canonical_event(aggregate.event_id)
    except HistoricalRegistrationInvalid:
        return False
    return event.get("identity_id") == str(aggregate.event_id)


def _mask_identifier(value: str) -> str:
    return f"protected:{hashlib.sha256(value.encode()).hexdigest()[:12]}"


def _transition(
    instance: Any,
    *,
    state: str,
    reason_code: str | None = None,
) -> None:
    instance.state = state
    update_fields = ["state", "revision", "updated_at"]
    if reason_code is not None:
        instance.reason_code = reason_code
        update_fields.append("reason_code")
    instance.revision += 1
    instance.save(update_fields=tuple(update_fields))


def _resolve_explicit_event(candidate: AggregateCandidate) -> Event | None:
    """Resolve one candidate's explicit current-event bridge target, or fail loudly.

    ``candidate.proposal`` is only ever present when the caller supplied an exact
    provider-event-to-canonical-Event bridge (the current-registration-input JSON
    file, or a registered source's own baked-in bridge) -- never inferred from a
    title or date.  A bridge naming a target that does not resolve to a real Event
    is a broken input, not an ambiguous one: it fails the whole staging
    transaction rather than silently leaving the row unresolved.
    """

    if candidate.proposal is None:
        return None
    try:
        return resolve_source_identity(
            repository=candidate.proposal.repository,
            revision=candidate.proposal.revision,
            source_key=candidate.proposal.source_key,
        )
    except EventIdentityNotFound as error:
        raise HistoricalRegistrationConflict("explicit_mapping_target_unavailable") from error


def _ensure_explicit_event(
    aggregate: HistoricalRegistrationAggregateRevision, *, event: Event
) -> None:
    """Resolve one aggregate revision to `event`, without retargeting an already-resolved row.

    Mirrors the one-way null -> Event discipline on the model's own ``save()``:
    a second, identical call (replay) is a no-op; a second call naming a
    *different* event is a conflict, never a silent retarget.
    """

    if aggregate.event_id is not None:
        if aggregate.event_id != event.id:
            raise HistoricalRegistrationConflict("explicit_mapping_conflict")
        return
    if aggregate.state != HistoricalRegistrationAggregateRevision.State.STAGED:
        raise HistoricalRegistrationConflict("explicit_mapping_not_activatable")
    aggregate.event = event
    aggregate.revision += 1
    aggregate.save(update_fields=("event", "revision", "updated_at"))


def stage_registered_source(
    *,
    provider: str,
    source_reference: str,
    mapping_set_revision: int,
    actor: Any | None,
    context: ServiceContext,
) -> tuple[HistoricalRegistrationSourceRun, bool]:
    if provider not in HistoricalRegistrationSourceRun.Provider.values:
        raise HistoricalRegistrationInvalid("provider_invalid")
    if not isinstance(mapping_set_revision, int) or isinstance(mapping_set_revision, bool):
        raise HistoricalRegistrationInvalid("mapping_set_revision_invalid")
    if mapping_set_revision < 1:
        raise HistoricalRegistrationInvalid("mapping_set_revision_invalid")
    # Derivation crosses only aggregate evidence into the transaction; attendee rows
    # and their temporary deduplication sets are already gone.
    derived = derive_registered_source(source_reference, expected_provider=provider)
    reference_digest = source_reference_digest(source_reference)
    return _persist_derived_source(
        provider=provider,
        derived=derived,
        reference_digest=reference_digest,
        mapping_set_revision=mapping_set_revision,
        actor=actor,
        context=context,
    )


def stage_derived_source(
    *,
    provider: str,
    derived: DerivedSource,
    reference_digest: str,
    mapping_set_revision: int,
    actor: Any | None,
    context: ServiceContext,
) -> tuple[HistoricalRegistrationSourceRun, bool]:
    """Persist already-derived aggregate evidence without crossing attendee data.

    The local rehearsal uses this seam after the protected adapter has parsed the complete
    source with an explicit current-event bridge.  A candidate whose export event carries no
    bridge target (``candidate.proposal is None``) is simply staged unresolved
    (``event=None``); nothing here gates that on a caller-supplied flag -- a bridge target,
    when supplied, is always applied.
    """

    if provider not in HistoricalRegistrationSourceRun.Provider.values:
        raise HistoricalRegistrationInvalid("provider_invalid")
    if derived.provider != provider:
        raise HistoricalRegistrationInvalid("provider_mismatch")
    if not isinstance(reference_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", reference_digest):
        raise HistoricalRegistrationInvalid("source_reference_digest_invalid")
    if not isinstance(mapping_set_revision, int) or isinstance(mapping_set_revision, bool):
        raise HistoricalRegistrationInvalid("mapping_set_revision_invalid")
    if mapping_set_revision < 1:
        raise HistoricalRegistrationInvalid("mapping_set_revision_invalid")
    return _persist_derived_source(
        provider=provider,
        derived=derived,
        reference_digest=reference_digest,
        mapping_set_revision=mapping_set_revision,
        actor=actor,
        context=context,
    )


@transaction.atomic
def _persist_derived_source(
    *,
    provider: str,
    derived: DerivedSource,
    reference_digest: str,
    mapping_set_revision: int,
    actor: Any | None,
    context: ServiceContext,
) -> tuple[HistoricalRegistrationSourceRun, bool]:
    existing = HistoricalRegistrationSourceRun.objects.filter(
        provider=provider,
        whole_source_checksum=derived.whole_source_checksum,
        schema_version=derived.schema_version,
        mapping_set_revision=mapping_set_revision,
        policy_version=POLICY_VERSION,
    ).first()
    created = False
    if existing is not None:
        run = existing
    if existing is None:
        try:
            run = HistoricalRegistrationSourceRun.objects.create(
                provider=provider,
                adapter_version=derived.adapter_version,
                schema_version=derived.schema_version,
                whole_source_checksum=derived.whole_source_checksum,
                source_reference_digest=reference_digest,
                manifest_entry_total=derived.manifest_entry_total,
                manifest_event_total=derived.manifest_event_total,
                parsed_row_total=derived.parsed_row_total,
                eligible_row_total=derived.eligible_row_total,
                excluded_row_total=derived.excluded_row_total,
                quarantined_event_total=derived.quarantined_event_total,
                status_totals=dict(derived.status_totals),
                state_totals=dict(derived.state_totals),
                reason_codes=list(derived.reason_codes),
                mapping_set_revision=mapping_set_revision,
                policy_version=POLICY_VERSION,
                state=(
                    HistoricalRegistrationSourceRun.State.QUARANTINED
                    if derived.quarantined_event_total
                    else HistoricalRegistrationSourceRun.State.STAGED
                ),
                actor=actor,
                actor_ref=_actor_ref(context, actor),
                reason_code="source_contains_quarantine" if derived.quarantined_event_total else "",
            )
        except IntegrityError:
            run = HistoricalRegistrationSourceRun.objects.get(
                provider=provider,
                whole_source_checksum=derived.whole_source_checksum,
                schema_version=derived.schema_version,
                mapping_set_revision=mapping_set_revision,
                policy_version=POLICY_VERSION,
            )
        else:
            created = True
    if not created:
        # Replay against an existing run: a human may have extended the explicit
        # current-registration-input bridge without bumping mapping_set_revision.
        # Resolve any of this run's own candidates that are still unresolved and
        # now carry an explicit bridge target -- never touch an already-resolved
        # row (see _ensure_explicit_event).
        for candidate in derived.candidates:
            if candidate.proposal is None:
                continue
            event = _resolve_explicit_event(candidate)
            try:
                aggregate = run.aggregate_revisions.get(
                    external_event_identifier=candidate.external_event_identifier
                )
            except HistoricalRegistrationAggregateRevision.DoesNotExist as error:
                raise HistoricalRegistrationConflict("explicit_mapping_conflict") from error
            _ensure_explicit_event(aggregate, event=event)
        return run, False
    for candidate in derived.candidates:
        event = _resolve_explicit_event(candidate)
        aggregate = HistoricalRegistrationAggregateRevision.objects.create(
            source_run=run,
            external_event_identifier=candidate.external_event_identifier,
            eligible_count=candidate.eligible_count,
            excluded_count=candidate.excluded_count,
            quarantined_count=candidate.quarantined_count,
            coverage_boundary="historical",
            status_policy_version=STATUS_POLICY_VERSION,
            combination_policy=(
                HistoricalRegistrationAggregateRevision.CombinationPolicy.REPLACEMENT
            ),
            aggregate_checksum=candidate.aggregate_checksum,
            state=candidate.state,
            reason_code=candidate.reason_code,
        )
        if event is not None:
            _ensure_explicit_event(aggregate, event=event)
    # `derived.source_missing` -- provider ids the bridge names that the export
    # itself does not contain -- names no aggregate row at all, so there is
    # nothing here to resolve or persist; it stays a caller-reported fact only
    # (see scripts/prod/import_events.py).
    record_audit_event(
        action="events.historical_registration_import.staged",
        target_type="events.historical_registration_source_run",
        target_id=run.id,
        target_label="historical-registration-source",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=_audit_context(context, actor=actor),
        changes={"state": {"before": None, "after": run.state}},
        metadata={
            "provider": run.provider,
            "source_checksum": run.whole_source_checksum,
            "schema_version": run.schema_version,
            "policy_version": run.policy_version,
            "mapping_set_revision": run.mapping_set_revision,
            "event_total": run.manifest_event_total,
            "row_total": run.parsed_row_total,
            "eligible_total": run.eligible_row_total,
            "excluded_total": run.excluded_row_total,
            "quarantined_event_total": run.quarantined_event_total,
            "reason_codes": run.reason_codes,
        },
    )
    return run, True


def _find_registered_reference(run: HistoricalRegistrationSourceRun) -> str:
    registry = getattr(settings, "HISTORICAL_REGISTRATION_SOURCES", {})
    if not isinstance(registry, Mapping):
        raise ProtectedSourceError("source_registry_invalid")
    matches = [
        reference
        for reference in registry
        if isinstance(reference, str)
        and source_reference_digest(reference) == run.source_reference_digest
    ]
    if len(matches) != 1:
        raise ProtectedSourceError("source_reference_unavailable")
    return matches[0]


def dry_run_source(
    run_id: uuid.UUID,
    *,
    actor: Any | None,
    context: ServiceContext,
) -> dict[str, Any]:
    run = HistoricalRegistrationSourceRun.objects.get(pk=run_id)
    derived = derive_registered_source(
        _find_registered_reference(run), expected_provider=run.provider
    )
    matches = (
        derived.whole_source_checksum == run.whole_source_checksum
        and derived.schema_version == run.schema_version
        and derived.parsed_row_total == run.parsed_row_total
        and derived.eligible_row_total == run.eligible_row_total
        and derived.excluded_row_total == run.excluded_row_total
        and derived.manifest_event_total == run.manifest_event_total
    )
    record_audit_event(
        action="events.historical_registration_import.dry_run",
        target_type="events.historical_registration_source_run",
        target_id=run.id,
        target_label="historical-registration-source",
        outcome=AuditEvent.Outcome.SUCCEEDED if matches else AuditEvent.Outcome.FAILED,
        context=_audit_context(context, actor=actor),
        changes={},
        metadata={
            "source_checksum": run.whole_source_checksum,
            "provider": run.provider,
            "matches": matches,
            "event_total": derived.manifest_event_total,
            "row_total": derived.parsed_row_total,
        },
    )
    if not matches:
        raise HistoricalRegistrationConflict("source_changed")
    return {"run_id": str(run.id), "state": run.state, "matches": True, "replayed": True}


# --------------------------------------------------------------------------
# Automatic resolution -- exact-normalized-title, same-date only
# --------------------------------------------------------------------------
#
# There is no persistent "mapping under review" row or state machine.  An
# aggregate revision is either resolved (``event`` set, by an explicit
# current-registration-input bridge at staging time -- see
# ``_ensure_explicit_event`` above) or it is not.  This section is the other,
# narrower resolution path for the backlog left unresolved by staging: the
# subset where the provider export itself already proves the match beyond
# doubt -- the provider event's title, once case/whitespace-normalized, is
# byte-identical to exactly one canonical event's title, and that canonical
# event is the only one on the provider event's date.  No ranking, no partial
# match, no score: any other shape (zero or multiple canonical events on that
# date, or a title that is merely similar) is left unresolved, reported under
# its own reason so a human can go add the right entry to the
# current-registration-input JSON file.
_PROVIDER_EVENT_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
_CANONICAL_SOURCE_KEY_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")


@dataclass(frozen=True, slots=True)
class ProviderEventMetadata:
    """One provider event's public title and start date, used for matching only.

    Never carries a guest id, name, email, or any other attendee-level value.
    """

    external_event_identifier: str
    title: str
    start_at: str


def _normalized_title(title: str) -> str:
    return " ".join(title.split()).casefold()


def _canonical_event_date(source_key: str) -> str | None:
    """The event's date, read from its canonical ``YYYY-MM-DD-slug`` source key.

    Some canonical events (older podcast-style entries) have no date component
    in their source key at all. Those events simply never participate in the
    date comparison below -- they cannot be the "only canonical event on this
    date" for any provider event, so a provider event that would otherwise
    match one stays unresolved.
    """

    match = _CANONICAL_SOURCE_KEY_DATE.match(source_key)
    return match.group(1) if match else None


def resolve_unmatched_aggregates(
    *,
    provider: str,
    provider_metadata: Mapping[str, ProviderEventMetadata],
    actor: Any | None,
    context: ServiceContext,
) -> dict[str, Any]:
    """Resolve every still-unresolved aggregate revision an exact title+date proves.

    For every ``event__isnull`` aggregate revision of ``provider`` (across every
    source run, not just the latest -- an old row is harmless to resolve too):
    look up the provider event's title/date (from ``provider_metadata``, keyed by
    ``external_event_identifier`` -- for Luma this is
    ``events.importers.discover_luma_events``; Eventbrite's export carries no
    event-level title or date at all, so an empty mapping here correctly leaves
    every Eventbrite row unmatched and reported as such).  Set ``event`` directly
    only when exactly one canonical ``Event`` shares the provider event's date and
    that event's case/whitespace-normalized title equals the provider event's
    normalized title exactly.  Idempotent: an already-resolved row is not
    re-examined, and re-running with the same database state reports the same
    still-unresolved rows again rather than treating them as new.  Pure
    resolution -- never touches ``state`` (activation for public display is a
    separate, still-gated concern; see ``activate_source`` /
    ``activate_explicit_current_source``).
    """

    if provider not in HistoricalRegistrationSourceRun.Provider.values:
        raise HistoricalRegistrationInvalid("provider_invalid")

    events_by_date: dict[str, list[Event]] = defaultdict(list)
    for event in Event.objects.all():
        date = _canonical_event_date(event.source_key)
        if date is not None:
            events_by_date[date].append(event)

    pending = list(
        HistoricalRegistrationAggregateRevision.objects.filter(
            source_run__provider=provider,
            event__isnull=True,
        ).order_by("external_event_identifier", "id")
    )

    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    reason_totals: Counter[str] = Counter()

    def _leave_unresolved(
        aggregate: HistoricalRegistrationAggregateRevision, *, reason: str, **detail: Any
    ) -> None:
        reason_totals[reason] += 1
        unresolved.append(
            {
                "provider": provider,
                "external_event_identifier": aggregate.external_event_identifier,
                "reason": reason,
                **detail,
            }
        )

    for aggregate in pending:
        metadata = provider_metadata.get(aggregate.external_event_identifier)
        if metadata is None or not metadata.title or not metadata.start_at:
            _leave_unresolved(aggregate, reason="provider_event_metadata_unavailable")
            continue
        date_match = _PROVIDER_EVENT_DATE.match(metadata.start_at)
        if date_match is None:
            _leave_unresolved(aggregate, reason="provider_event_date_unparseable")
            continue
        date = date_match.group(1)
        candidates = events_by_date.get(date, [])
        if not candidates:
            _leave_unresolved(aggregate, reason="no_canonical_event_on_date", date=date)
            continue
        if len(candidates) > 1:
            _leave_unresolved(
                aggregate,
                reason="ambiguous_date",
                date=date,
                canonical_event_count_on_date=len(candidates),
            )
            continue
        canonical_event = candidates[0]
        if _normalized_title(canonical_event.title) != _normalized_title(metadata.title):
            _leave_unresolved(
                aggregate,
                reason="title_mismatch",
                date=date,
                canonical_event_title=canonical_event.title,
                provider_event_title=metadata.title,
            )
            continue
        aggregate.event = canonical_event
        aggregate.revision += 1
        aggregate.save(update_fields=("event", "revision", "updated_at"))
        record_audit_event(
            action="events.historical_registration_aggregate.resolved",
            target_type="events.historical_registration_aggregate_revision",
            target_id=aggregate.id,
            target_label="historical-registration-aggregate",
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=_audit_context(context, actor=actor),
            changes={"event_id": {"before": None, "after": str(canonical_event.id)}},
            metadata={
                "provider": provider,
                "reason_code": "auto_matched_exact_title_date",
                "matched_date": date,
            },
        )
        resolved.append(
            {
                "provider": provider,
                "external_event_identifier": aggregate.external_event_identifier,
                "canonical_event_id": str(canonical_event.id),
                "canonical_event_public_id": canonical_event.public_id,
                "canonical_event_slug": canonical_event.slug,
                "matched_date": date,
                "matched_title": canonical_event.title,
                "aggregate_revision_id": str(aggregate.id),
            }
        )

    return {
        "provider": provider,
        "mechanism": "auto_exact_title_date_match",
        "unresolved_total": len(pending),
        "resolved_total": len(resolved),
        "resolved": resolved,
        "still_unresolved_total": len(unresolved),
        "still_unresolved_reasons": dict(sorted(reason_totals.items())),
        "still_unresolved": unresolved,
    }


@transaction.atomic
def validate_source(
    run_id: uuid.UUID,
    *,
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> HistoricalRegistrationSourceRun:
    _validate_reason_code(reason_code)
    run = HistoricalRegistrationSourceRun.objects.get(pk=run_id)
    if run.state == HistoricalRegistrationSourceRun.State.VALIDATED:
        return run
    if run.state != HistoricalRegistrationSourceRun.State.STAGED:
        raise HistoricalRegistrationConflict("run_not_staged")
    aggregates = tuple(
        run.aggregate_revisions.select_related("event").filter(
            state=HistoricalRegistrationAggregateRevision.State.STAGED
        )
    )
    if not aggregates:
        raise HistoricalRegistrationConflict("run_has_no_staged_aggregates")
    for aggregate in aggregates:
        if aggregate.event_id is None:
            raise HistoricalRegistrationConflict("aggregate_not_resolved")
        if not _aggregate_matches_projection(aggregate):
            raise HistoricalRegistrationConflict("canonical_identity_changed")
        _transition(
            aggregate,
            state=HistoricalRegistrationAggregateRevision.State.VALIDATED,
            reason_code=reason_code,
        )
    if run.aggregate_revisions.filter(
        state=HistoricalRegistrationAggregateRevision.State.QUARANTINED
    ).exists():
        _transition(
            run,
            state=HistoricalRegistrationSourceRun.State.QUARANTINED,
            reason_code="aggregate_quarantined",
        )
        raise HistoricalRegistrationConflict("aggregate_quarantined")
    _transition(
        run,
        state=HistoricalRegistrationSourceRun.State.VALIDATED,
        reason_code=reason_code,
    )
    record_audit_event(
        action="events.historical_registration_import.validated",
        target_type="events.historical_registration_source_run",
        target_id=run.id,
        target_label="historical-registration-source",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=_audit_context(context, actor=actor),
        changes={"state": {"before": "staged", "after": run.state}},
        metadata={
            "source_checksum": run.whole_source_checksum,
            "event_total": run.manifest_event_total,
            "eligible_total": run.eligible_row_total,
            "mapping_set_revision": run.mapping_set_revision,
            "reason_code": reason_code,
        },
    )
    return run


def _event_key(event: Event) -> tuple[str, str, str, str]:
    return (
        event.source_repository,
        event.source_revision,
        event.source_key,
        event.slug,
    )


def _slot_policy(slot: HistoricalRegistrationAggregateSlot) -> str:
    if slot.active_revision is not None:
        return slot.active_revision.combination_policy
    return slot.replacement_combination_policy


def _clear_slot(slot: HistoricalRegistrationAggregateSlot) -> None:
    slot.active_revision = None
    slot.replacement_revision_id = None
    slot.replacement_eligible_count = None
    slot.replacement_combination_policy = ""
    slot.revision += 1
    slot.save(
        update_fields=(
            "active_revision",
            "replacement_revision_id",
            "replacement_eligible_count",
            "replacement_combination_policy",
            "revision",
            "updated_at",
        )
    )


def _record_displaced_pointer(
    *,
    replacing_revision: HistoricalRegistrationAggregateRevision,
    slot: HistoricalRegistrationAggregateSlot,
) -> None:
    if slot.active_revision_id is not None:
        HistoricalRegistrationPointerDisplacement.objects.create(
            replacing_revision=replacing_revision,
            slot=slot,
            displaced_revision_id=slot.active_revision_id,
        )
        return
    if slot.replacement_revision_id is not None:
        HistoricalRegistrationPointerDisplacement.objects.create(
            replacing_revision=replacing_revision,
            slot=slot,
            row_replacement_revision_id=slot.replacement_revision_id,
            row_replacement_eligible_count=slot.replacement_eligible_count,
            row_replacement_combination_policy=slot.replacement_combination_policy,
        )
        return
    raise HistoricalRegistrationConflict("displaced_pointer_unavailable")


def _bump_public_total(
    event_key: tuple[str, str, str, str],
    *,
    complete: bool,
) -> HistoricalRegistrationTotalState:
    repository, canonical_revision, source_key, slug = event_key
    total, created = HistoricalRegistrationTotalState.objects.get_or_create(
        canonical_repository=repository,
        canonical_revision=canonical_revision,
        canonical_source_key=source_key,
        defaults={"canonical_slug_snapshot": slug, "complete": complete},
    )
    if not created:
        expected = total.revision
        total.canonical_slug_snapshot = slug
        total.complete = complete
        total.revision += 1
        try:
            total.save(
                update_fields=(
                    "canonical_slug_snapshot",
                    "complete",
                    "revision",
                    "updated_at",
                )
            )
        except RevisionConflict as error:
            raise HistoricalRegistrationConflict("concurrent_total_change") from error
        if total.revision != expected + 1:
            raise HistoricalRegistrationConflict("concurrent_total_change")
    event_hash = hashlib.sha256("\0".join(event_key[:3]).encode()).hexdigest()
    try:
        canonical_path = canonical_detail_path(
            resolve_source_identity(
                repository=repository,
                revision=canonical_revision,
                source_key=source_key,
            ).id
        )
    except (EventIdentityError, EventIdentityNotFound) as exc:
        raise HistoricalRegistrationConflict("event_identity_unmapped") from exc
    dispatch_after_commit(
        handler="events.registration_total.invalidate",
        deduplication_key=f"event-total-{event_hash}-{total.revision}",
        payload={
            "total_state_id": str(total.id),
            "total_revision": total.revision,
            "path": canonical_path,
        },
    )
    return total


def _preflight_activation(
    run: HistoricalRegistrationSourceRun,
    aggregates: tuple[HistoricalRegistrationAggregateRevision, ...],
) -> None:
    targets: set[tuple[str, str, str, str, str]] = set()
    for aggregate in aggregates:
        if (
            aggregate.combination_policy
            == HistoricalRegistrationAggregateRevision.CombinationPolicy.EXCLUDE
        ):
            continue
        if aggregate.event_id is None or not _aggregate_matches_projection(aggregate):
            raise HistoricalRegistrationConflict("aggregate_not_activatable")
        key = _event_key(aggregate.event)
        target = (*key[:3], run.provider, aggregate.coverage_boundary)
        if target in targets:
            raise HistoricalRegistrationConflict("same_run_slot_collision")
        targets.add(target)


def _activate_aggregates(
    run: HistoricalRegistrationSourceRun,
    aggregates: tuple[HistoricalRegistrationAggregateRevision, ...],
    *,
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> HistoricalRegistrationSourceRun:
    """Activate an already-selected, fully preflighted aggregate subset."""

    _preflight_activation(run, aggregates)
    before_run_state = run.state
    affected: set[tuple[str, str, str, str]] = set()
    for aggregate in aggregates:
        if (
            aggregate.combination_policy
            == HistoricalRegistrationAggregateRevision.CombinationPolicy.EXCLUDE
        ):
            _transition(
                aggregate,
                state=HistoricalRegistrationAggregateRevision.State.SUPERSEDED,
                reason_code="coverage_excluded",
            )
            continue
        if aggregate.event_id is None or not _aggregate_matches_projection(aggregate):
            raise HistoricalRegistrationConflict("aggregate_not_activatable")
        key = _event_key(aggregate.event)
        existing_slots = (
            HistoricalRegistrationAggregateSlot.objects.select_related("active_revision")
            .filter(
                canonical_repository=key[0],
                canonical_revision=key[1],
                canonical_source_key=key[2],
                coverage_boundary=aggregate.coverage_boundary,
            )
            .filter(Q(active_revision__isnull=False) | Q(replacement_revision_id__isnull=False))
        )
        if (
            aggregate.combination_policy
            == HistoricalRegistrationAggregateRevision.CombinationPolicy.ADDITIVE_DISJOINT
        ):
            for existing_slot in existing_slots:
                if (
                    _slot_policy(existing_slot)
                    != HistoricalRegistrationAggregateRevision.CombinationPolicy.ADDITIVE_DISJOINT
                ):
                    raise HistoricalRegistrationConflict("coverage_overlap")
        elif (
            aggregate.combination_policy
            == HistoricalRegistrationAggregateRevision.CombinationPolicy.REPLACEMENT
        ):
            for existing_slot in existing_slots:
                if existing_slot.active_revision_id == aggregate.id:
                    continue
                _record_displaced_pointer(
                    replacing_revision=aggregate,
                    slot=existing_slot,
                )
                previous = existing_slot.active_revision
                if previous is not None and previous.id != aggregate.id:
                    _transition(
                        previous,
                        state=HistoricalRegistrationAggregateRevision.State.SUPERSEDED,
                        reason_code="replaced",
                    )
                if (
                    existing_slot.active_revision_id != aggregate.id
                    or existing_slot.replacement_revision_id is not None
                ):
                    _clear_slot(existing_slot)
        else:
            raise HistoricalRegistrationConflict("combination_excluded")
        slot, created = HistoricalRegistrationAggregateSlot.objects.get_or_create(
            canonical_repository=key[0],
            canonical_revision=key[1],
            canonical_source_key=key[2],
            provider=run.provider,
            coverage_boundary=aggregate.coverage_boundary,
            defaults={"canonical_slug_snapshot": key[3], "active_revision": aggregate},
        )
        if not created and slot.active_revision_id != aggregate.id:
            previous = slot.active_revision
            if previous is not None:
                _transition(
                    previous,
                    state=HistoricalRegistrationAggregateRevision.State.SUPERSEDED,
                    reason_code="replaced",
                )
            slot.active_revision = aggregate
            slot.replacement_revision_id = None
            slot.replacement_eligible_count = None
            slot.replacement_combination_policy = ""
            slot.canonical_slug_snapshot = key[3]
            slot.revision += 1
            slot.save(
                update_fields=(
                    "active_revision",
                    "replacement_revision_id",
                    "replacement_eligible_count",
                    "replacement_combination_policy",
                    "canonical_slug_snapshot",
                    "revision",
                    "updated_at",
                )
            )
        if aggregate.state != HistoricalRegistrationAggregateRevision.State.ACTIVE:
            _transition(
                aggregate,
                state=HistoricalRegistrationAggregateRevision.State.ACTIVE,
                reason_code=reason_code,
            )
        affected.add(key)
    if run.state != HistoricalRegistrationSourceRun.State.ACTIVE:
        _transition(
            run,
            state=HistoricalRegistrationSourceRun.State.ACTIVE,
            reason_code=reason_code,
        )
    for key in affected:
        _bump_public_total(key, complete=True)
    record_audit_event(
        action="events.historical_registration_import.activated",
        target_type="events.historical_registration_source_run",
        target_id=run.id,
        target_label="historical-registration-source",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=_audit_context(context, actor=actor),
        changes={"state": {"before": before_run_state, "after": run.state}},
        metadata={
            "source_checksum": run.whole_source_checksum,
            "event_total": len(affected),
            "eligible_total": sum(item.eligible_count for item in aggregates),
            "reason_code": reason_code,
        },
    )
    return run


@transaction.atomic
def activate_source(
    run_id: uuid.UUID,
    *,
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> HistoricalRegistrationSourceRun:
    _validate_reason_code(reason_code)
    run = HistoricalRegistrationSourceRun.objects.get(pk=run_id)
    if run.state == HistoricalRegistrationSourceRun.State.ACTIVE:
        return run
    if run.state != HistoricalRegistrationSourceRun.State.VALIDATED:
        raise HistoricalRegistrationConflict("run_not_validated")
    aggregates = tuple(
        run.aggregate_revisions.select_related("event").filter(
            state=HistoricalRegistrationAggregateRevision.State.VALIDATED
        )
    )
    if not aggregates:
        raise HistoricalRegistrationConflict("run_has_no_validated_aggregates")
    # Resolve the whole mapping set before changing any slot. Two candidates from
    # one run cannot replace each other in the same destination slot because that
    # would make the rollback receipt describe an intra-run state, not the exact
    # accepted state that existed before this atomic activation.
    return _activate_aggregates(
        run,
        aggregates,
        reason_code=reason_code,
        actor=actor,
        context=context,
    )


@transaction.atomic
def activate_explicit_current_source(
    run_id: uuid.UUID,
    *,
    external_event_identifiers: Collection[str],
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> HistoricalRegistrationSourceRun:
    """Activate only the aggregates named by the explicit current-event import input.

    Other candidates from the same provider snapshot intentionally remain staged and
    unresolved.  This path is separate from whole-snapshot historical activation.
    """

    _validate_reason_code(reason_code)
    try:
        requested = tuple(external_event_identifiers)
    except TypeError as error:
        raise HistoricalRegistrationInvalid("external_event_identifiers_invalid") from error
    if not requested or any(not isinstance(item, str) or not item for item in requested):
        raise HistoricalRegistrationInvalid("external_event_identifiers_invalid")
    requested_set = set(requested)
    run = HistoricalRegistrationSourceRun.objects.get(pk=run_id)
    if run.state == HistoricalRegistrationSourceRun.State.ACTIVE:
        return run
    if run.state != HistoricalRegistrationSourceRun.State.STAGED:
        raise HistoricalRegistrationConflict("run_not_staged")
    aggregates = tuple(
        run.aggregate_revisions.select_related("event").filter(
            external_event_identifier__in=requested_set,
            state__in=(
                HistoricalRegistrationAggregateRevision.State.STAGED,
                HistoricalRegistrationAggregateRevision.State.VALIDATED,
            ),
        )
    )
    if len(aggregates) != len(requested_set):
        raise HistoricalRegistrationConflict("explicit_mapping_not_in_run")
    for aggregate in aggregates:
        if aggregate.event_id is None:
            raise HistoricalRegistrationConflict("explicit_mapping_not_activatable")
        if not _aggregate_matches_projection(aggregate):
            raise HistoricalRegistrationConflict("canonical_identity_changed")
        if aggregate.state == HistoricalRegistrationAggregateRevision.State.STAGED:
            _transition(
                aggregate,
                state=HistoricalRegistrationAggregateRevision.State.VALIDATED,
                reason_code="explicit_current_event",
            )
    return _activate_aggregates(
        run,
        aggregates,
        reason_code=reason_code,
        actor=actor,
        context=context,
    )


@transaction.atomic
def cancel_source(
    run_id: uuid.UUID,
    *,
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> HistoricalRegistrationSourceRun:
    _validate_reason_code(reason_code)
    run = HistoricalRegistrationSourceRun.objects.get(pk=run_id)
    if run.state == HistoricalRegistrationSourceRun.State.CANCELLED:
        return run
    if run.state not in {
        HistoricalRegistrationSourceRun.State.STAGED,
        HistoricalRegistrationSourceRun.State.VALIDATED,
        HistoricalRegistrationSourceRun.State.QUARANTINED,
    }:
        raise HistoricalRegistrationConflict("run_not_cancellable")
    before = run.state
    for aggregate in run.aggregate_revisions.filter(
        state__in=(
            HistoricalRegistrationAggregateRevision.State.STAGED,
            HistoricalRegistrationAggregateRevision.State.VALIDATED,
        )
    ):
        _transition(
            aggregate,
            state=HistoricalRegistrationAggregateRevision.State.QUARANTINED,
            reason_code=reason_code,
        )
    _transition(
        run,
        state=HistoricalRegistrationSourceRun.State.CANCELLED,
        reason_code=reason_code,
    )
    record_audit_event(
        action="events.historical_registration_import.cancelled",
        target_type="events.historical_registration_source_run",
        target_id=run.id,
        target_label="historical-registration-source",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=_audit_context(context, actor=actor),
        changes={"state": {"before": before, "after": run.state}},
        metadata={"source_checksum": run.whole_source_checksum, "reason_code": reason_code},
    )
    return run


@transaction.atomic
def rollback_source(
    run_id: uuid.UUID,
    *,
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> HistoricalRegistrationSourceRun:
    _validate_reason_code(reason_code)
    run = HistoricalRegistrationSourceRun.objects.get(pk=run_id)
    if run.state == HistoricalRegistrationSourceRun.State.ROLLED_BACK:
        if run.aggregate_revisions.filter(
            state=HistoricalRegistrationAggregateRevision.State.ACTIVE
        ).exists():
            raise HistoricalRegistrationConflict("rollback_incomplete")
        return run
    if run.state != HistoricalRegistrationSourceRun.State.ACTIVE:
        raise HistoricalRegistrationConflict("run_not_active")
    active = tuple(
        run.aggregate_revisions.select_related("event").filter(
            state=HistoricalRegistrationAggregateRevision.State.ACTIVE
        )
    )
    affected: set[tuple[str, str, str, str]] = set()
    restored_pointer_total = 0
    for aggregate in active:
        key = _event_key(aggregate.event)
        slot = HistoricalRegistrationAggregateSlot.objects.get(active_revision=aggregate)
        slot.active_revision = None
        slot.revision += 1
        slot.save(update_fields=("active_revision", "revision", "updated_at"))
        _transition(
            aggregate,
            state=HistoricalRegistrationAggregateRevision.State.ROLLED_BACK,
            reason_code=reason_code,
        )
        displacements = tuple(
            aggregate.pointer_displacements.select_related("slot", "displaced_revision")
        )
        for displacement in displacements:
            prior_slot = HistoricalRegistrationAggregateSlot.objects.get(pk=displacement.slot_id)
            if (
                prior_slot.active_revision_id is not None
                or prior_slot.replacement_revision_id is not None
            ):
                raise HistoricalRegistrationConflict("rollback_pointer_conflict")
            prior = displacement.displaced_revision
            if prior is not None:
                if prior.state != HistoricalRegistrationAggregateRevision.State.SUPERSEDED:
                    raise HistoricalRegistrationConflict("prior_aggregate_unavailable")
                prior_slot.active_revision = prior
                _transition(
                    prior,
                    state=HistoricalRegistrationAggregateRevision.State.ACTIVE,
                    reason_code="rollback_restored",
                )
            else:
                if (
                    displacement.row_replacement_revision_id is None
                    or displacement.row_replacement_eligible_count is None
                    or not displacement.row_replacement_combination_policy
                ):
                    raise HistoricalRegistrationConflict("prior_replacement_unavailable")
                prior_slot.replacement_revision_id = displacement.row_replacement_revision_id
                prior_slot.replacement_eligible_count = displacement.row_replacement_eligible_count
                prior_slot.replacement_combination_policy = (
                    displacement.row_replacement_combination_policy
                )
            prior_slot.revision += 1
            prior_slot.save(
                update_fields=(
                    "active_revision",
                    "replacement_revision_id",
                    "replacement_eligible_count",
                    "replacement_combination_policy",
                    "revision",
                    "updated_at",
                )
            )
            restored_pointer_total += 1
        affected.add(key)
    if run.aggregate_revisions.filter(
        state=HistoricalRegistrationAggregateRevision.State.ACTIVE
    ).exists():
        raise HistoricalRegistrationConflict("rollback_incomplete")
    _transition(
        run,
        state=HistoricalRegistrationSourceRun.State.ROLLED_BACK,
        reason_code=reason_code,
    )
    for key in affected:
        complete = (
            HistoricalRegistrationAggregateSlot.objects.filter(
                canonical_repository=key[0],
                canonical_revision=key[1],
                canonical_source_key=key[2],
            )
            .filter(Q(active_revision__isnull=False) | Q(replacement_revision_id__isnull=False))
            .exists()
        )
        _bump_public_total(key, complete=complete)
    record_audit_event(
        action="events.historical_registration_import.rolled_back",
        target_type="events.historical_registration_source_run",
        target_id=run.id,
        target_label="historical-registration-source",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=_audit_context(context, actor=actor),
        changes={"state": {"before": "active", "after": run.state}},
        metadata={
            "source_checksum": run.whole_source_checksum,
            "event_total": len(affected),
            "restored_pointer_total": restored_pointer_total,
            "reason_code": reason_code,
        },
    )
    return run


@transaction.atomic
def replace_aggregate_with_row_projection(
    *,
    event_id: uuid.UUID | str | None = None,
    canonical_slug: str | None = None,
    provider: str,
    coverage_boundary: str,
    replacement_revision_id: uuid.UUID,
    eligible_count: int,
    expected_slot_revision: int,
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> HistoricalRegistrationAggregateSlot:
    """Atomically switch one aggregate slot to a reviewed future row projection."""

    _validate_reason_code(reason_code)
    _validate_coverage(coverage_boundary)
    if (
        provider not in HistoricalRegistrationSourceRun.Provider.values
        or not isinstance(replacement_revision_id, uuid.UUID)
        or not isinstance(eligible_count, int)
        or isinstance(eligible_count, bool)
        or eligible_count < 0
    ):
        raise HistoricalRegistrationInvalid("replacement_invalid")
    if event_id is None or canonical_slug is not None:
        raise HistoricalRegistrationInvalid("event_identity_required")
    event = _canonical_event(event_id)
    key = _canonical_identity(event)
    slot = HistoricalRegistrationAggregateSlot.objects.select_related("active_revision").get(
        canonical_repository=key[0],
        canonical_revision=key[1],
        canonical_source_key=key[2],
        provider=provider,
        coverage_boundary=coverage_boundary,
    )
    if slot.revision != expected_slot_revision:
        raise RevisionConflict(expected=expected_slot_revision, actual=slot.revision)
    aggregate = slot.active_revision
    if aggregate is None or aggregate.state != HistoricalRegistrationAggregateRevision.State.ACTIVE:
        raise HistoricalRegistrationConflict("active_aggregate_unavailable")
    policy = aggregate.combination_policy
    _transition(
        aggregate,
        state=HistoricalRegistrationAggregateRevision.State.SUPERSEDED,
        reason_code="row_replacement",
    )
    slot.active_revision = None
    slot.replacement_revision_id = replacement_revision_id
    slot.replacement_eligible_count = eligible_count
    slot.replacement_combination_policy = policy
    slot.revision += 1
    slot.save(
        update_fields=(
            "active_revision",
            "replacement_revision_id",
            "replacement_eligible_count",
            "replacement_combination_policy",
            "revision",
            "updated_at",
        )
    )
    total = _bump_public_total(key, complete=True)
    record_audit_event(
        action="events.historical_registration_aggregate.replaced",
        target_type="events.historical_registration_aggregate_slot",
        target_id=slot.id,
        target_label="historical-registration-slot",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=_audit_context(context, actor=actor),
        changes={
            "pointer": {"before": "aggregate", "after": "row_replacement"},
            "revision": {"before": expected_slot_revision, "after": slot.revision},
        },
        metadata={
            "eligible_count": eligible_count,
            "total_revision": total.revision,
            "reason_code": reason_code,
        },
    )
    return slot


@transaction.atomic
def restore_aggregate_from_row_projection(
    *,
    event_id: uuid.UUID | str | None = None,
    canonical_slug: str | None = None,
    provider: str,
    coverage_boundary: str,
    expected_slot_revision: int,
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> HistoricalRegistrationAggregateSlot:
    """Rollback a future row projection to its last accepted aggregate pointer."""

    _validate_reason_code(reason_code)
    if event_id is None or canonical_slug is not None:
        raise HistoricalRegistrationInvalid("event_identity_required")
    event = _canonical_event(event_id)
    key = _canonical_identity(event)
    slot = HistoricalRegistrationAggregateSlot.objects.get(
        canonical_repository=key[0],
        canonical_revision=key[1],
        canonical_source_key=key[2],
        provider=provider,
        coverage_boundary=_validate_coverage(coverage_boundary),
    )
    if slot.revision != expected_slot_revision:
        raise RevisionConflict(expected=expected_slot_revision, actual=slot.revision)
    if slot.replacement_revision_id is None or slot.active_revision_id is not None:
        raise HistoricalRegistrationConflict("row_replacement_unavailable")
    prior = (
        HistoricalRegistrationAggregateRevision.objects.select_related("event")
        .filter(
            event__source_repository=key[0],
            event__source_revision=key[1],
            event__source_key=key[2],
            source_run__provider=provider,
            coverage_boundary=coverage_boundary,
            state=HistoricalRegistrationAggregateRevision.State.SUPERSEDED,
        )
        .order_by("-updated_at", "-id")
        .first()
    )
    if prior is None:
        raise HistoricalRegistrationConflict("prior_aggregate_unavailable")
    slot.active_revision = prior
    slot.replacement_revision_id = None
    slot.replacement_eligible_count = None
    slot.replacement_combination_policy = ""
    slot.revision += 1
    slot.save(
        update_fields=(
            "active_revision",
            "replacement_revision_id",
            "replacement_eligible_count",
            "replacement_combination_policy",
            "revision",
            "updated_at",
        )
    )
    _transition(
        prior,
        state=HistoricalRegistrationAggregateRevision.State.ACTIVE,
        reason_code="row_replacement_rollback",
    )
    _bump_public_total(key, complete=True)
    record_audit_event(
        action="events.historical_registration_aggregate.replacement_rolled_back",
        target_type="events.historical_registration_aggregate_slot",
        target_id=slot.id,
        target_label="historical-registration-slot",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=_audit_context(context, actor=actor),
        changes={
            "pointer": {"before": "row_replacement", "after": "aggregate"},
            "revision": {"before": expected_slot_revision, "after": slot.revision},
        },
        metadata={"reason_code": reason_code},
    )
    return slot


def serialize_run(run: HistoricalRegistrationSourceRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "provider": run.provider,
        "adapter_version": run.adapter_version,
        "schema_version": run.schema_version,
        "source_checksum": run.whole_source_checksum,
        "manifest_entry_total": run.manifest_entry_total,
        "manifest_event_total": run.manifest_event_total,
        "parsed_row_total": run.parsed_row_total,
        "eligible_row_total": run.eligible_row_total,
        "excluded_row_total": run.excluded_row_total,
        "quarantined_event_total": run.quarantined_event_total,
        "status_totals": run.status_totals,
        "state_totals": run.state_totals,
        "reason_codes": run.reason_codes,
        "mapping_set_revision": run.mapping_set_revision,
        "policy_version": run.policy_version,
        "state": run.state,
        "revision": run.revision,
        "reason_code": run.reason_code,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def list_runs(*, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    if not 1 <= page_size <= 100 or page < 1:
        raise HistoricalRegistrationInvalid("pagination_invalid")
    queryset = HistoricalRegistrationSourceRun.objects.all()
    total = queryset.count()
    offset = (page - 1) * page_size
    return {
        "items": [serialize_run(run) for run in queryset[offset : offset + page_size]],
        "page": page,
        "page_size": page_size,
        "total_count": total,
    }


def get_run_detail(run_id: uuid.UUID) -> dict[str, Any]:
    run = HistoricalRegistrationSourceRun.objects.get(pk=run_id)
    aggregates = run.aggregate_revisions.select_related("event").all()
    return {
        **serialize_run(run),
        "aggregates": [
            {
                "id": str(item.id),
                "external_event": _mask_identifier(item.external_event_identifier),
                "event_id": str(item.event_id) if item.event_id else None,
                "resolved": item.event_id is not None,
                "canonical_slug": item.event.slug if item.event_id else "",
                "eligible_count": item.eligible_count,
                "excluded_count": item.excluded_count,
                "quarantined_count": item.quarantined_count,
                "coverage_boundary": item.coverage_boundary,
                "combination_policy": item.combination_policy,
                "state": item.state,
                "revision": item.revision,
                "reason_code": item.reason_code,
            }
            for item in aggregates
        ],
    }


def registration_total_preview(event_id: uuid.UUID | str) -> dict[str, Any]:
    event = _canonical_event(event_id)
    repository, canonical_revision, source_key, canonical_slug = _canonical_identity(event)
    total_state = HistoricalRegistrationTotalState.objects.filter(
        canonical_repository=repository,
        canonical_revision=canonical_revision,
        canonical_source_key=source_key,
    ).first()
    slots = (
        HistoricalRegistrationAggregateSlot.objects.select_related("active_revision")
        .filter(
            canonical_repository=repository,
            canonical_revision=canonical_revision,
            canonical_source_key=source_key,
        )
        .filter(Q(active_revision__isnull=False) | Q(replacement_revision_id__isnull=False))
    )
    contributions: list[dict[str, Any]] = []
    for slot in slots:
        revision = slot.active_revision
        count = revision.eligible_count if revision is not None else slot.replacement_eligible_count
        policy = (
            revision.combination_policy
            if revision is not None
            else slot.replacement_combination_policy
        )
        if count is None or not policy:
            continue
        contributions.append(
            {
                "provider": slot.provider,
                "coverage_boundary": slot.coverage_boundary,
                "count": count,
                "combination_policy": policy,
                "source_kind": "aggregate" if revision is not None else "row_replacement",
                "aggregate_revision_id": (
                    str(slot.active_revision_id) if revision is not None else None
                ),
                "replacement_revision_id": (
                    str(slot.replacement_revision_id) if revision is None else None
                ),
            }
        )
    complete = bool(total_state and total_state.complete)
    return {
        "event_id": str(event["identity_id"]),
        "canonical_slug": canonical_slug,
        "complete": complete,
        "count": sum(item["count"] for item in contributions) if complete else None,
        "total_revision": total_state.revision if total_state else None,
        "contributions": contributions,
    }


def public_registration_total(event: Mapping[str, Any]) -> PublicRegistrationTotal | None:
    repository, canonical_revision, source_key, _slug = _canonical_identity(event)
    state = HistoricalRegistrationTotalState.objects.filter(
        canonical_repository=repository,
        canonical_revision=canonical_revision,
        canonical_source_key=source_key,
        complete=True,
    ).first()
    if state is None:
        return None
    active = (
        HistoricalRegistrationAggregateSlot.objects.select_related("active_revision")
        .filter(
            canonical_repository=repository,
            canonical_revision=canonical_revision,
            canonical_source_key=source_key,
        )
        .filter(
            Q(
                active_revision__isnull=False,
                active_revision__state=HistoricalRegistrationAggregateRevision.State.ACTIVE,
                active_revision__event__isnull=False,
            )
            | Q(replacement_revision_id__isnull=False)
        )
    )
    contributions = list(active)
    if not contributions:
        return None
    count = 0
    for item in contributions:
        revision = item.active_revision
        policy = (
            revision.combination_policy
            if revision is not None
            else item.replacement_combination_policy
        )
        eligible_count = (
            revision.eligible_count if revision is not None else item.replacement_eligible_count
        )
        if eligible_count is None or not policy:
            return None
        if (
            len(contributions) > 1
            and policy
            != HistoricalRegistrationAggregateRevision.CombinationPolicy.ADDITIVE_DISJOINT
        ):
            return None
        count += eligible_count
    return PublicRegistrationTotal(count=count, revision=state.revision)


def safe_source_facts() -> dict[str, Any]:
    """Code-owned aggregate acceptance facts; no source path or event identity."""

    return {
        "luma": {
            "manifest_event_total": 159,
            "paired_json_total": 159,
            "paired_csv_total": 159,
            "parsed_row_total": 50_505,
            "unique_provider_event_guest_total": 50_505,
            "eligible_row_total": 50_456,
            "excluded_row_total": 49,
            "status_totals": {"approved": 50_456, "declined": 49},
            "nonempty_event_total": 157,
            "empty_event_total": 2,
            "exact_proposal_total": 64,
            "review_required_total": 95,
        },
        "eventbrite": {
            "whole_source_checksum": (
                "5cc493c7e9a142d09f5a524d28df486f4fa33ce832210ea0d325025b939744df"
            ),
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
                "eventbrite_csv_v1": {
                    "header_sha256": (
                        "333061583991588f9b6bc78c9873feb7ddab8711687ee999da2135a4cbef0c7e"
                    ),
                    "column_total": 23,
                    "csv_total": 22,
                },
                "eventbrite_csv_v2": {
                    "header_sha256": (
                        "6f7f37db55176240fa695289cf13c8bcbaf86970f00b0ed18c4f2a1a6ee4e9ae"
                    ),
                    "column_total": 25,
                    "csv_total": 12,
                },
                "eventbrite_csv_v3": {
                    "header_sha256": (
                        "c3a799fcbcee38d3e1733fc0cd317e84236f5d17241513c1a76b3646a19ea0b8"
                    ),
                    "column_total": 24,
                    "csv_total": 175,
                },
            },
        },
    }
