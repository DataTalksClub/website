"""Application services for privacy-safe course registration totals."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Min

from core.audit import AuditWriteContext, record_audit_event
from core.idempotency import hash_idempotency_key
from core.models import AuditEvent, RevisionConflict
from core.services import ServiceContext
from courses.models import (
    CourseRegistration,
    CourseRegistrationCountRevision,
    CourseRegistrationCountSlot,
    CourseRegistrationCountSourceRun,
    RegistrationCampaign,
)
from courses.registration_count_importer import (
    CourseCountSourceError,
    DerivedCourseCounts,
    aggregate_checksum,
    derive_registered_source,
    registered_source_matches,
    source_reference_digest,
)

MANAGE_PERMISSION = "courses.registration_count_baseline_manage"
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class CourseRegistrationCountConflict(RuntimeError):
    pass


class CourseRegistrationCountInvalid(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PublicCourseRegistrationCount:
    count: int
    revision: int


def _audit_context(
    context: ServiceContext,
    *,
    actor: Any | None,
    idempotency_scope: str,
) -> AuditWriteContext:
    key_hash = (
        hash_idempotency_key(idempotency_scope, context.idempotency_key)
        if context.idempotency_key is not None
        else ""
    )
    return AuditWriteContext.from_service_context(
        context,
        actor_id=getattr(actor, "pk", None),
        idempotency_key_hash=key_hash,
    )


def _actor_ref(context: ServiceContext, actor: Any | None) -> str:
    if context.actor_ref:
        return context.actor_ref
    identifier = getattr(actor, "pk", None)
    return f"user:{identifier}" if identifier is not None else ""


def _source_run_audit_metadata(
    run: CourseRegistrationCountSourceRun,
    *,
    reason_code: str,
) -> dict[str, Any]:
    return {
        "source_checksum": run.whole_source_checksum,
        "schema_checksum": run.schema_contract_checksum,
        "manifest_checksum": run.aggregate_manifest_checksum,
        "campaign_total": run.campaign_total,
        "row_total": run.row_total,
        "reason_code": reason_code,
    }


def _validate_reason_code(value: str) -> str:
    if not isinstance(value, str) or _REASON_CODE.fullmatch(value) is None:
        raise CourseRegistrationCountInvalid("reason_code_invalid")
    return value


def _require_revision(instance: Any, expected_revision: int) -> None:
    if (
        not isinstance(expected_revision, int)
        or isinstance(expected_revision, bool)
        or expected_revision < 1
    ):
        raise CourseRegistrationCountInvalid("expected_revision_invalid")
    if instance.revision != expected_revision:
        raise RevisionConflict(expected=expected_revision, actual=instance.revision)


def _transition(instance: Any, *, state: str, reason_code: str) -> None:
    instance.state = state
    instance.reason_code = reason_code
    instance.revision += 1
    instance.save(update_fields=("state", "reason_code", "revision", "updated_at"))


def _mapped_candidates(derived: DerivedCourseCounts) -> tuple[tuple[Any, Any, Any], ...]:
    mapped: list[tuple[Any, Any, Any]] = []
    seen: set[tuple[int, int]] = set()
    for candidate in derived.candidates:
        campaign = (
            RegistrationCampaign.objects.select_related("current_course")
            .filter(slug=candidate.campaign_slug)
            .first()
        )
        if campaign is None or campaign.current_course is None:
            raise CourseRegistrationCountConflict("target_campaign_missing")
        cohort = campaign.current_course
        if cohort.slug != candidate.cohort_slug:
            raise CourseRegistrationCountConflict("target_cohort_changed")
        key = (campaign.pk, cohort.pk)
        if key in seen:
            raise CourseRegistrationCountConflict("target_mapping_duplicate")
        seen.add(key)
        mapped.append((candidate, campaign, cohort))
    if len(mapped) != derived.campaign_total:
        raise CourseRegistrationCountConflict("target_manifest_incomplete")
    return tuple(mapped)


def _run_matches(
    run: CourseRegistrationCountSourceRun,
    derived: DerivedCourseCounts,
    mapped: tuple[tuple[Any, Any, Any], ...],
) -> bool:
    if (
        run.adapter_version != derived.adapter_version
        or run.schema_version != derived.schema_version
        or run.count_policy_version != derived.count_policy_version
        or run.whole_source_checksum != derived.whole_source_checksum
        or run.source_byte_size != derived.source_byte_size
        or run.schema_contract_checksum != derived.schema_contract_checksum
        or run.aggregate_manifest_checksum != derived.aggregate_manifest_checksum
        or run.captured_at != derived.captured_at
        or run.source_frozen_at != derived.source_frozen_at
        or run.campaign_total != derived.campaign_total
        or run.row_total != derived.row_total
    ):
        return False
    revisions = tuple(run.count_revisions.all())
    if len(revisions) != len(mapped):
        return False
    expected = {(campaign.pk, cohort.pk): candidate for candidate, campaign, cohort in mapped}
    for revision in revisions:
        candidate = expected.get((revision.campaign_id, revision.cohort_id))
        if candidate is None or any(
            (
                revision.campaign_slug_snapshot != candidate.campaign_slug,
                revision.cohort_slug_snapshot != candidate.cohort_slug,
                revision.baseline_count != candidate.baseline_count,
                revision.source_min_created_at != candidate.source_min_created_at,
                revision.source_max_created_at != candidate.source_max_created_at,
                revision.coverage_cutoff_at != candidate.coverage_cutoff_at,
                revision.proposed_native_start_at != candidate.proposed_native_start_at,
                revision.aggregate_checksum != candidate.aggregate_checksum,
            )
        ):
            return False
    return True


def stage_registered_source(
    *,
    source_reference: str,
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> tuple[CourseRegistrationCountSourceRun, bool]:
    # Protected rows and their transient values are gone before the target
    # transaction starts; only aggregate evidence crosses this boundary.
    reason_code = _validate_reason_code(reason_code)
    derived = derive_registered_source(source_reference)
    reference_digest = source_reference_digest(source_reference)
    mapped = _mapped_candidates(derived)
    return _persist_derived_source(
        derived=derived,
        reference_digest=reference_digest,
        mapped=mapped,
        reason_code=reason_code,
        actor=actor,
        context=context,
    )


@transaction.atomic
def _persist_derived_source(
    *,
    derived: DerivedCourseCounts,
    reference_digest: str,
    mapped: tuple[tuple[Any, Any, Any], ...],
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> tuple[CourseRegistrationCountSourceRun, bool]:
    existing = CourseRegistrationCountSourceRun.objects.filter(
        source_reference_digest=reference_digest
    ).first()
    if existing is not None:
        if not _run_matches(existing, derived, mapped):
            raise CourseRegistrationCountConflict("source_identity_conflict")
        return existing, True
    if CourseRegistrationCountSourceRun.objects.filter(
        whole_source_checksum=derived.whole_source_checksum
    ).exists():
        raise CourseRegistrationCountConflict("source_checksum_already_staged")
    try:
        run = CourseRegistrationCountSourceRun.objects.create(
            adapter_version=derived.adapter_version,
            schema_version=derived.schema_version,
            count_policy_version=derived.count_policy_version,
            whole_source_checksum=derived.whole_source_checksum,
            source_byte_size=derived.source_byte_size,
            schema_contract_checksum=derived.schema_contract_checksum,
            aggregate_manifest_checksum=derived.aggregate_manifest_checksum,
            source_reference_digest=reference_digest,
            captured_at=derived.captured_at,
            source_frozen_at=derived.source_frozen_at,
            campaign_total=derived.campaign_total,
            row_total=derived.row_total,
            actor=actor,
            actor_ref=_actor_ref(context, actor),
            reason_code=reason_code,
        )
        for candidate, campaign, cohort in mapped:
            CourseRegistrationCountRevision.objects.create(
                source_run=run,
                campaign=campaign,
                cohort=cohort,
                campaign_slug_snapshot=candidate.campaign_slug,
                cohort_slug_snapshot=candidate.cohort_slug,
                baseline_count=candidate.baseline_count,
                source_min_created_at=candidate.source_min_created_at,
                source_max_created_at=candidate.source_max_created_at,
                coverage_cutoff_at=candidate.coverage_cutoff_at,
                proposed_native_start_at=candidate.proposed_native_start_at,
                aggregate_checksum=candidate.aggregate_checksum,
            )
    except IntegrityError as error:
        raise CourseRegistrationCountConflict("concurrent_source_stage") from error
    record_audit_event(
        action="courses.registration_count_baseline.staged",
        target_type="courses.registration_count_source_run",
        target_id=run.id,
        target_label="course-registration-count-source",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=_audit_context(
            context,
            actor=actor,
            idempotency_scope="courses.registration_count_baseline.create",
        ),
        changes={
            "state": {"before": None, "after": run.state},
            "revision": {"before": None, "after": run.revision},
        },
        metadata=_source_run_audit_metadata(run, reason_code=reason_code),
    )
    return run, False


def _registered_reference(run: CourseRegistrationCountSourceRun) -> str:
    registry = getattr(settings, "COURSE_REGISTRATION_COUNT_SOURCES", {})
    if not isinstance(registry, Mapping):
        raise CourseCountSourceError("source_registry_invalid")
    matches = [
        reference
        for reference in registry
        if isinstance(reference, str)
        and source_reference_digest(reference) == run.source_reference_digest
    ]
    if len(matches) != 1:
        raise CourseCountSourceError("source_reference_unavailable")
    return matches[0]


def _rederive_exact(
    run: CourseRegistrationCountSourceRun,
) -> tuple[DerivedCourseCounts, tuple[tuple[Any, Any, Any], ...]]:
    derived = derive_registered_source(_registered_reference(run))
    mapped = _mapped_candidates(derived)
    if not _run_matches(run, derived, mapped):
        raise CourseRegistrationCountConflict("source_or_manifest_changed")
    return derived, mapped


def dry_run_source(
    run_id: uuid.UUID,
    *,
    expected_revision: int,
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> dict[str, Any]:
    reason_code = _validate_reason_code(reason_code)
    run = CourseRegistrationCountSourceRun.objects.get(pk=run_id)
    _require_revision(run, expected_revision)
    _derived, _mapped = _rederive_exact(run)
    record_audit_event(
        action="courses.registration_count_baseline.dry_run",
        target_type="courses.registration_count_source_run",
        target_id=run.id,
        target_label="course-registration-count-source",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=_audit_context(
            context,
            actor=actor,
            idempotency_scope="courses.registration_count_baseline.dry-run",
        ),
        changes={
            "state": {"before": run.state, "after": run.state},
            "revision": {"before": expected_revision, "after": run.revision},
        },
        metadata=_source_run_audit_metadata(run, reason_code=reason_code),
    )
    return {
        "run_id": str(run.id),
        "state": run.state,
        "revision": run.revision,
        "matches": True,
    }


def validate_source(
    run_id: uuid.UUID,
    *,
    expected_revision: int,
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> CourseRegistrationCountSourceRun:
    reason_code = _validate_reason_code(reason_code)
    run = CourseRegistrationCountSourceRun.objects.get(pk=run_id)
    _require_revision(run, expected_revision)
    if run.state != CourseRegistrationCountSourceRun.State.STAGED:
        raise CourseRegistrationCountConflict("run_not_staged")
    try:
        _derived, mapped = _rederive_exact(run)
        return _commit_validated_source(
            run_id,
            expected_revision=expected_revision,
            mapped=mapped,
            reason_code=reason_code,
            actor=actor,
            context=context,
        )
    except (CourseCountSourceError, CourseRegistrationCountConflict) as error:
        failure_code = getattr(error, "code", str(error))
        try:
            _quarantine_failed_validation(
                run_id,
                expected_revision=expected_revision,
                reason_code=reason_code,
                failure_code=failure_code,
                actor=actor,
                context=context,
            )
        except (CourseRegistrationCountSourceRun.DoesNotExist, RevisionConflict):
            pass
        raise


@transaction.atomic
def _commit_validated_source(
    run_id: uuid.UUID,
    *,
    expected_revision: int,
    mapped: tuple[tuple[Any, Any, Any], ...],
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> CourseRegistrationCountSourceRun:
    run = CourseRegistrationCountSourceRun.objects.get(pk=run_id)
    _require_revision(run, expected_revision)
    if run.state != CourseRegistrationCountSourceRun.State.STAGED:
        raise RevisionConflict(expected=expected_revision, actual=run.revision)
    revisions = tuple(run.count_revisions.select_related("campaign", "cohort"))
    if len(revisions) != len(mapped) or any(
        revision.state != CourseRegistrationCountRevision.State.STAGED
        or revision.campaign.slug != revision.campaign_slug_snapshot
        or revision.cohort.slug != revision.cohort_slug_snapshot
        or revision.campaign.current_course_id != revision.cohort_id
        for revision in revisions
    ):
        raise CourseRegistrationCountConflict("revision_manifest_incomplete")
    for revision in revisions:
        _transition(
            revision,
            state=CourseRegistrationCountRevision.State.VALIDATED,
            reason_code=reason_code,
        )
    _transition(
        run, state=CourseRegistrationCountSourceRun.State.VALIDATED, reason_code=reason_code
    )
    record_audit_event(
        action="courses.registration_count_baseline.validated",
        target_type="courses.registration_count_source_run",
        target_id=run.id,
        target_label="course-registration-count-source",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=_audit_context(
            context,
            actor=actor,
            idempotency_scope="courses.registration_count_baseline.validate",
        ),
        changes={
            "state": {"before": "staged", "after": run.state},
            "revision": {"before": expected_revision, "after": run.revision},
        },
        metadata=_source_run_audit_metadata(run, reason_code=reason_code),
    )
    return run


@transaction.atomic
def _quarantine_failed_validation(
    run_id: uuid.UUID,
    *,
    expected_revision: int,
    reason_code: str,
    failure_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> None:
    run = CourseRegistrationCountSourceRun.objects.get(pk=run_id)
    _require_revision(run, expected_revision)
    if run.state != CourseRegistrationCountSourceRun.State.STAGED:
        return
    for revision in run.count_revisions.filter(
        state__in=(
            CourseRegistrationCountRevision.State.STAGED,
            CourseRegistrationCountRevision.State.VALIDATED,
        )
    ):
        _transition(
            revision,
            state=CourseRegistrationCountRevision.State.QUARANTINED,
            reason_code=reason_code,
        )
    _transition(
        run,
        state=CourseRegistrationCountSourceRun.State.QUARANTINED,
        reason_code=reason_code,
    )
    record_audit_event(
        action="courses.registration_count_baseline.validation_failed",
        target_type="courses.registration_count_source_run",
        target_id=run.id,
        target_label="course-registration-count-source",
        outcome=AuditEvent.Outcome.FAILED,
        context=_audit_context(
            context,
            actor=actor,
            idempotency_scope="courses.registration_count_baseline.validate",
        ),
        changes={
            "state": {"before": "staged", "after": run.state},
            "revision": {"before": expected_revision, "after": run.revision},
        },
        metadata={
            **_source_run_audit_metadata(run, reason_code=reason_code),
            "failure_code": failure_code,
        },
    )


def _ensure_target_is_current(revision: CourseRegistrationCountRevision) -> None:
    if (
        revision.campaign.slug != revision.campaign_slug_snapshot
        or revision.cohort.slug != revision.cohort_slug_snapshot
        or revision.campaign.current_course_id != revision.cohort_id
    ):
        raise CourseRegistrationCountConflict("target_identity_stale")


def _ensure_native_boundary(revision: CourseRegistrationCountRevision) -> None:
    if (
        revision.source_run.source_frozen_at != revision.coverage_cutoff_at
        or revision.coverage_cutoff_at >= revision.proposed_native_start_at
        or (
            revision.source_max_created_at is not None
            and revision.source_max_created_at >= revision.proposed_native_start_at
        )
    ):
        raise CourseRegistrationCountConflict("cutover_overlap")
    registrations = CourseRegistration.objects.filter(campaign=revision.campaign)
    if registrations.exclude(course=revision.cohort).exists():
        raise CourseRegistrationCountConflict("target_registration_ambiguous")
    if registrations.filter(
        course=revision.cohort,
        created_at__lt=revision.proposed_native_start_at,
    ).exists():
        raise CourseRegistrationCountConflict("target_registration_before_cutover")


@transaction.atomic
def activate_source(
    run_id: uuid.UUID,
    *,
    expected_revision: int,
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> CourseRegistrationCountSourceRun:
    reason_code = _validate_reason_code(reason_code)
    run = CourseRegistrationCountSourceRun.objects.get(pk=run_id)
    _require_revision(run, expected_revision)
    if run.state != CourseRegistrationCountSourceRun.State.VALIDATED:
        raise CourseRegistrationCountConflict("run_not_validated")
    _rederive_exact(run)
    revisions = tuple(
        run.count_revisions.select_related("campaign", "cohort", "source_run").filter(
            state=CourseRegistrationCountRevision.State.VALIDATED
        )
    )
    if len(revisions) != run.campaign_total:
        raise CourseRegistrationCountConflict("validated_manifest_incomplete")
    slots: list[tuple[CourseRegistrationCountRevision, CourseRegistrationCountSlot | None]] = []
    for revision in revisions:
        _ensure_target_is_current(revision)
        _ensure_native_boundary(revision)
        slot = (
            CourseRegistrationCountSlot.objects.select_related("active_baseline_revision")
            .filter(campaign=revision.campaign, cohort=revision.cohort)
            .first()
        )
        if slot is not None and (
            slot.campaign_slug_snapshot != revision.campaign_slug_snapshot
            or slot.cohort_slug_snapshot != revision.cohort_slug_snapshot
        ):
            raise CourseRegistrationCountConflict("slot_identity_stale")
        slots.append((revision, slot))
    for revision, slot in slots:
        if slot is None:
            try:
                slot = CourseRegistrationCountSlot.objects.create(
                    campaign=revision.campaign,
                    cohort=revision.cohort,
                    campaign_slug_snapshot=revision.campaign_slug_snapshot,
                    cohort_slug_snapshot=revision.cohort_slug_snapshot,
                    mode=CourseRegistrationCountSlot.Mode.BASELINE_PLUS_NATIVE,
                    active_baseline_revision=revision,
                    native_start_at=revision.proposed_native_start_at,
                )
            except IntegrityError as error:
                raise CourseRegistrationCountConflict("concurrent_slot_activation") from error
        else:
            previous = slot.active_baseline_revision
            slot.prior_mode = slot.mode
            slot.prior_baseline_revision = previous
            slot.prior_native_start_at = slot.native_start_at
            slot.prior_row_replacement_revision_id = slot.row_replacement_revision_id
            if previous is not None:
                if previous.state != CourseRegistrationCountRevision.State.ACTIVE:
                    raise CourseRegistrationCountConflict("prior_pointer_invalid")
                _transition(
                    previous,
                    state=CourseRegistrationCountRevision.State.SUPERSEDED,
                    reason_code="replaced",
                )
            slot.mode = CourseRegistrationCountSlot.Mode.BASELINE_PLUS_NATIVE
            slot.active_baseline_revision = revision
            slot.native_start_at = revision.proposed_native_start_at
            slot.row_replacement_revision_id = None
            slot.public_total_revision += 1
            slot.revision += 1
            slot.save(
                update_fields=(
                    "prior_mode",
                    "prior_baseline_revision",
                    "prior_native_start_at",
                    "prior_row_replacement_revision_id",
                    "mode",
                    "active_baseline_revision",
                    "native_start_at",
                    "row_replacement_revision_id",
                    "public_total_revision",
                    "revision",
                    "updated_at",
                )
            )
        _transition(
            revision,
            state=CourseRegistrationCountRevision.State.ACTIVE,
            reason_code=reason_code,
        )
    _transition(run, state=CourseRegistrationCountSourceRun.State.ACTIVE, reason_code=reason_code)
    record_audit_event(
        action="courses.registration_count_baseline.activated",
        target_type="courses.registration_count_source_run",
        target_id=run.id,
        target_label="course-registration-count-source",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=_audit_context(
            context,
            actor=actor,
            idempotency_scope="courses.registration_count_baseline.activate",
        ),
        changes={
            "state": {"before": "validated", "after": run.state},
            "revision": {"before": expected_revision, "after": run.revision},
        },
        metadata=_source_run_audit_metadata(run, reason_code=reason_code),
    )
    return run


@transaction.atomic
def cancel_source(
    run_id: uuid.UUID,
    *,
    expected_revision: int,
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> CourseRegistrationCountSourceRun:
    reason_code = _validate_reason_code(reason_code)
    run = CourseRegistrationCountSourceRun.objects.get(pk=run_id)
    _require_revision(run, expected_revision)
    if run.state not in {
        CourseRegistrationCountSourceRun.State.STAGED,
        CourseRegistrationCountSourceRun.State.VALIDATED,
        CourseRegistrationCountSourceRun.State.QUARANTINED,
    }:
        raise CourseRegistrationCountConflict("run_not_cancellable")
    before = run.state
    for revision in run.count_revisions.filter(
        state__in=(
            CourseRegistrationCountRevision.State.STAGED,
            CourseRegistrationCountRevision.State.VALIDATED,
        )
    ):
        _transition(
            revision,
            state=CourseRegistrationCountRevision.State.QUARANTINED,
            reason_code=reason_code,
        )
    _transition(
        run, state=CourseRegistrationCountSourceRun.State.CANCELLED, reason_code=reason_code
    )
    record_audit_event(
        action="courses.registration_count_baseline.cancelled",
        target_type="courses.registration_count_source_run",
        target_id=run.id,
        target_label="course-registration-count-source",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=_audit_context(
            context,
            actor=actor,
            idempotency_scope="courses.registration_count_baseline.cancel",
        ),
        changes={
            "state": {"before": before, "after": run.state},
            "revision": {"before": expected_revision, "after": run.revision},
        },
        metadata=_source_run_audit_metadata(run, reason_code=reason_code),
    )
    return run


@transaction.atomic
def rollback_source(
    run_id: uuid.UUID,
    *,
    expected_revision: int,
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> CourseRegistrationCountSourceRun:
    reason_code = _validate_reason_code(reason_code)
    run = CourseRegistrationCountSourceRun.objects.get(pk=run_id)
    _require_revision(run, expected_revision)
    if run.state != CourseRegistrationCountSourceRun.State.ACTIVE:
        raise CourseRegistrationCountConflict("run_not_active")
    revisions = tuple(
        run.count_revisions.filter(state=CourseRegistrationCountRevision.State.ACTIVE)
    )
    if len(revisions) != run.campaign_total:
        raise CourseRegistrationCountConflict("active_manifest_incomplete")
    resolved: list[tuple[CourseRegistrationCountRevision, CourseRegistrationCountSlot]] = []
    for revision in revisions:
        try:
            slot = CourseRegistrationCountSlot.objects.select_related(
                "prior_baseline_revision"
            ).get(active_baseline_revision=revision)
        except CourseRegistrationCountSlot.DoesNotExist as error:
            raise CourseRegistrationCountConflict("active_pointer_missing") from error
        resolved.append((revision, slot))
    restored = 0
    for revision, slot in resolved:
        prior = slot.prior_baseline_revision
        slot.mode = slot.prior_mode
        slot.active_baseline_revision = prior
        slot.native_start_at = slot.prior_native_start_at
        slot.row_replacement_revision_id = slot.prior_row_replacement_revision_id
        slot.public_total_revision += 1
        slot.revision += 1
        slot.save(
            update_fields=(
                "mode",
                "active_baseline_revision",
                "native_start_at",
                "row_replacement_revision_id",
                "public_total_revision",
                "revision",
                "updated_at",
            )
        )
        _transition(
            revision,
            state=CourseRegistrationCountRevision.State.ROLLED_BACK,
            reason_code=reason_code,
        )
        if prior is not None:
            if prior.state != CourseRegistrationCountRevision.State.SUPERSEDED:
                raise CourseRegistrationCountConflict("prior_baseline_unavailable")
            _transition(
                prior,
                state=CourseRegistrationCountRevision.State.ACTIVE,
                reason_code="rollback_restored",
            )
            restored += 1
        elif slot.mode == CourseRegistrationCountSlot.Mode.ROWS_ONLY:
            restored += 1
    _transition(
        run,
        state=CourseRegistrationCountSourceRun.State.ROLLED_BACK,
        reason_code=reason_code,
    )
    record_audit_event(
        action="courses.registration_count_baseline.rolled_back",
        target_type="courses.registration_count_source_run",
        target_id=run.id,
        target_label="course-registration-count-source",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=_audit_context(
            context,
            actor=actor,
            idempotency_scope="courses.registration_count_baseline.rollback",
        ),
        changes={
            "state": {"before": "active", "after": run.state},
            "revision": {"before": expected_revision, "after": run.revision},
        },
        metadata={
            **_source_run_audit_metadata(run, reason_code=reason_code),
            "restored_pointer_total": restored,
        },
    )
    return run


def public_course_registration_count(
    campaign: RegistrationCampaign,
) -> PublicCourseRegistrationCount | None:
    cohort = campaign.current_course
    if cohort is None:
        return None
    slot = (
        CourseRegistrationCountSlot.objects.select_related(
            "active_baseline_revision",
            "active_baseline_revision__source_run",
        )
        .filter(campaign=campaign, cohort=cohort)
        .first()
    )
    if slot is None or (
        slot.campaign_slug_snapshot != campaign.slug
        or slot.cohort_slug_snapshot != cohort.slug
        or slot.native_start_at is None
    ):
        return None
    rows = CourseRegistration.objects.filter(campaign=campaign, course=cohort)
    if slot.mode == CourseRegistrationCountSlot.Mode.BASELINE_PLUS_NATIVE:
        baseline = slot.active_baseline_revision
        if (
            baseline is None
            or baseline.state != CourseRegistrationCountRevision.State.ACTIVE
            or baseline.source_run.state != CourseRegistrationCountSourceRun.State.ACTIVE
            or baseline.campaign_id != campaign.pk
            or baseline.cohort_id != cohort.pk
            or baseline.campaign_slug_snapshot != campaign.slug
            or baseline.cohort_slug_snapshot != cohort.slug
            or baseline.proposed_native_start_at != slot.native_start_at
            or not registered_source_matches(baseline.source_run, revision=baseline)
        ):
            return None
        historical = rows.filter(created_at__lt=slot.native_start_at)
        if historical.exists():
            if slot.prior_row_replacement_revision_id is None:
                return None
            facts = historical.aggregate(
                count=Count("id"), minimum=Min("created_at"), maximum=Max("created_at")
            )
            if (
                facts["count"] != baseline.baseline_count
                or aggregate_checksum(
                    campaign_slug=slot.campaign_slug_snapshot,
                    cohort_slug=slot.cohort_slug_snapshot,
                    count=facts["count"],
                    minimum=facts["minimum"],
                    maximum=facts["maximum"],
                    cutoff=baseline.coverage_cutoff_at,
                )
                != baseline.aggregate_checksum
            ):
                return None
        count = baseline.baseline_count + rows.filter(created_at__gte=slot.native_start_at).count()
    elif slot.mode == CourseRegistrationCountSlot.Mode.ROWS_ONLY:
        if slot.row_replacement_revision_id is None:
            return None
        count = rows.count()
    else:
        return None
    return PublicCourseRegistrationCount(count=count, revision=slot.public_total_revision)


def registration_count_preview(campaign_slug: str) -> dict[str, Any]:
    campaign = (
        RegistrationCampaign.objects.select_related("current_course")
        .filter(slug=campaign_slug)
        .first()
    )
    if campaign is None:
        raise CourseRegistrationCountInvalid("campaign_unavailable")
    result = public_course_registration_count(campaign)
    cohort_slug = campaign.current_course.slug if campaign.current_course else ""
    slot = (
        CourseRegistrationCountSlot.objects.select_related("active_baseline_revision")
        .filter(campaign=campaign, cohort=campaign.current_course)
        .first()
        if campaign.current_course is not None
        else None
    )
    baseline_count = None
    native_count = None
    mode = ""
    if slot is not None:
        mode = slot.mode
        if slot.active_baseline_revision is not None:
            baseline_count = slot.active_baseline_revision.baseline_count
        if slot.native_start_at is not None:
            native_count = CourseRegistration.objects.filter(
                campaign=campaign,
                course=campaign.current_course,
                created_at__gte=slot.native_start_at,
            ).count()
    return {
        "campaign_slug": campaign.slug,
        "cohort_slug": cohort_slug,
        "complete": result is not None,
        "count": result.count if result is not None else None,
        "total_revision": result.revision if result is not None else None,
        "mode": mode,
        "baseline_count": baseline_count,
        "native_count": native_count,
    }


@transaction.atomic
def replace_baseline_with_rows(
    *,
    campaign_id: int,
    row_replacement_revision_id: uuid.UUID,
    expected_slot_revision: int,
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> CourseRegistrationCountSlot:
    reason_code = _validate_reason_code(reason_code)
    if not isinstance(row_replacement_revision_id, uuid.UUID):
        raise CourseRegistrationCountInvalid("replacement_revision_invalid")
    slot = CourseRegistrationCountSlot.objects.select_related(
        "campaign",
        "cohort",
        "active_baseline_revision",
        "active_baseline_revision__source_run",
    ).get(campaign_id=campaign_id)
    _require_revision(slot, expected_slot_revision)
    if slot.campaign.current_course_id != slot.cohort_id:
        raise CourseRegistrationCountConflict("target_identity_stale")
    baseline = slot.active_baseline_revision
    if (
        slot.mode != CourseRegistrationCountSlot.Mode.BASELINE_PLUS_NATIVE
        or baseline is None
        or baseline.state != CourseRegistrationCountRevision.State.ACTIVE
        or slot.native_start_at is None
    ):
        raise CourseRegistrationCountConflict("active_baseline_unavailable")
    all_campaign_rows = CourseRegistration.objects.filter(campaign=slot.campaign)
    if all_campaign_rows.exclude(course=slot.cohort).exists():
        raise CourseRegistrationCountConflict("replacement_target_ambiguous")
    historical = all_campaign_rows.filter(
        course=slot.cohort,
        created_at__lt=slot.native_start_at,
    )
    facts = historical.aggregate(
        count=Count("id"), minimum=Min("created_at"), maximum=Max("created_at")
    )
    checksum = aggregate_checksum(
        campaign_slug=slot.campaign_slug_snapshot,
        cohort_slug=slot.cohort_slug_snapshot,
        count=facts["count"],
        minimum=facts["minimum"],
        maximum=facts["maximum"],
        cutoff=baseline.coverage_cutoff_at,
    )
    if facts["count"] != baseline.baseline_count or checksum != baseline.aggregate_checksum:
        raise CourseRegistrationCountConflict("replacement_reconciliation_mismatch")
    native_count = all_campaign_rows.filter(
        course=slot.cohort,
        created_at__gte=slot.native_start_at,
    ).count()
    before_count = baseline.baseline_count + native_count
    slot.prior_mode = slot.mode
    slot.prior_baseline_revision = baseline
    slot.prior_native_start_at = slot.native_start_at
    slot.prior_row_replacement_revision_id = None
    slot.mode = CourseRegistrationCountSlot.Mode.ROWS_ONLY
    slot.active_baseline_revision = None
    slot.row_replacement_revision_id = row_replacement_revision_id
    slot.public_total_revision += 1
    slot.revision += 1
    slot.save(
        update_fields=(
            "prior_mode",
            "prior_baseline_revision",
            "prior_native_start_at",
            "prior_row_replacement_revision_id",
            "mode",
            "active_baseline_revision",
            "row_replacement_revision_id",
            "public_total_revision",
            "revision",
            "updated_at",
        )
    )
    _transition(
        baseline,
        state=CourseRegistrationCountRevision.State.SUPERSEDED,
        reason_code="rows_reconciled",
    )
    after = public_course_registration_count(slot.campaign)
    if after is None or after.count != before_count:
        raise CourseRegistrationCountConflict("replacement_total_changed")
    record_audit_event(
        action="courses.registration_count_baseline.replaced",
        target_type="courses.registration_count_slot",
        target_id=slot.id,
        target_label="course-registration-count-slot",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=_audit_context(
            context,
            actor=actor,
            idempotency_scope="courses.registration_count_baseline.replace",
        ),
        changes={
            "state": {"before": "baseline_plus_native", "after": "rows_only"},
            "mode": {"before": "baseline_plus_native", "after": "rows_only"},
            "revision": {"before": expected_slot_revision, "after": slot.revision},
        },
        metadata={
            "campaign_slug": slot.campaign_slug_snapshot,
            "cohort_slug": slot.cohort_slug_snapshot,
            "baseline_count": baseline.baseline_count,
            "aggregate_checksum": baseline.aggregate_checksum,
            "reason_code": reason_code,
        },
    )
    return slot


@transaction.atomic
def restore_baseline_from_rows(
    *,
    campaign_id: int,
    expected_slot_revision: int,
    reason_code: str,
    actor: Any | None,
    context: ServiceContext,
) -> CourseRegistrationCountSlot:
    reason_code = _validate_reason_code(reason_code)
    slot = CourseRegistrationCountSlot.objects.select_related(
        "campaign", "cohort", "prior_baseline_revision"
    ).get(campaign_id=campaign_id)
    _require_revision(slot, expected_slot_revision)
    baseline = slot.prior_baseline_revision
    if (
        slot.mode != CourseRegistrationCountSlot.Mode.ROWS_ONLY
        or slot.row_replacement_revision_id is None
        or baseline is None
        or baseline.state != CourseRegistrationCountRevision.State.SUPERSEDED
        or slot.prior_mode != CourseRegistrationCountSlot.Mode.BASELINE_PLUS_NATIVE
    ):
        raise CourseRegistrationCountConflict("row_replacement_unavailable")
    before = public_course_registration_count(slot.campaign)
    active_row_revision_id = slot.row_replacement_revision_id
    slot.mode = CourseRegistrationCountSlot.Mode.BASELINE_PLUS_NATIVE
    slot.active_baseline_revision = baseline
    slot.native_start_at = slot.prior_native_start_at
    slot.row_replacement_revision_id = None
    slot.prior_row_replacement_revision_id = active_row_revision_id
    slot.public_total_revision += 1
    slot.revision += 1
    slot.save(
        update_fields=(
            "mode",
            "active_baseline_revision",
            "native_start_at",
            "row_replacement_revision_id",
            "prior_row_replacement_revision_id",
            "public_total_revision",
            "revision",
            "updated_at",
        )
    )
    _transition(
        baseline,
        state=CourseRegistrationCountRevision.State.ACTIVE,
        reason_code="row_replacement_rollback",
    )
    after = public_course_registration_count(slot.campaign)
    if before is None or after is None or before.count != after.count:
        raise CourseRegistrationCountConflict("rollback_total_changed")
    record_audit_event(
        action="courses.registration_count_baseline.replacement_rolled_back",
        target_type="courses.registration_count_slot",
        target_id=slot.id,
        target_label="course-registration-count-slot",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=_audit_context(
            context,
            actor=actor,
            idempotency_scope="courses.registration_count_baseline.restore",
        ),
        changes={
            "state": {"before": "rows_only", "after": "baseline_plus_native"},
            "mode": {"before": "rows_only", "after": "baseline_plus_native"},
            "revision": {"before": expected_slot_revision, "after": slot.revision},
        },
        metadata={
            "campaign_slug": slot.campaign_slug_snapshot,
            "cohort_slug": slot.cohort_slug_snapshot,
            "reason_code": reason_code,
        },
    )
    return slot


def serialize_run(run: CourseRegistrationCountSourceRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "adapter_version": run.adapter_version,
        "schema_version": run.schema_version,
        "count_policy_version": run.count_policy_version,
        "source_checksum": run.whole_source_checksum,
        "source_byte_size": run.source_byte_size,
        "schema_checksum": run.schema_contract_checksum,
        "manifest_checksum": run.aggregate_manifest_checksum,
        "captured_at": run.captured_at.isoformat(),
        "source_frozen_at": run.source_frozen_at.isoformat(),
        "campaign_total": run.campaign_total,
        "row_total": run.row_total,
        "state": run.state,
        "revision": run.revision,
        "reason_code": run.reason_code,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def list_runs(*, page: int = 1, page_size: int = 20) -> dict[str, Any]:
    if page < 1 or not 1 <= page_size <= 100:
        raise CourseRegistrationCountInvalid("pagination_invalid")
    queryset = CourseRegistrationCountSourceRun.objects.all()
    total = queryset.count()
    offset = (page - 1) * page_size
    return {
        "items": [serialize_run(run) for run in queryset[offset : offset + page_size]],
        "page": page,
        "page_size": page_size,
        "total_count": total,
    }


def get_run_detail(run_id: uuid.UUID) -> dict[str, Any]:
    run = CourseRegistrationCountSourceRun.objects.get(pk=run_id)
    return {
        **serialize_run(run),
        "counts": [
            {
                "id": str(revision.id),
                "campaign_slug": revision.campaign_slug_snapshot,
                "cohort_slug": revision.cohort_slug_snapshot,
                "baseline_count": revision.baseline_count,
                "coverage_cutoff_at": revision.coverage_cutoff_at.isoformat(),
                "native_start_at": revision.proposed_native_start_at.isoformat(),
                "aggregate_checksum": revision.aggregate_checksum,
                "state": revision.state,
                "revision": revision.revision,
                "reason_code": revision.reason_code,
            }
            for revision in run.count_revisions.all()
        ],
    }
