from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction

from test_support.messaging import CapturedMessage, CaptureMailbox, SimulatedOutcome

from .catalog import BUNDLE_LEAVES, SCENARIO_STATES
from .context import FactoryContext, canonical_json_bytes, canonical_sha256


@dataclass(frozen=True, slots=True)
class RejectedDomainValue:
    model_label: str
    reason: str


@dataclass(frozen=True, slots=True)
class ProviderSimulatorValue:
    kind: str
    outcome: str
    attempt: int
    redacted_recipient: str = "[REDACTED]"


@dataclass(frozen=True, slots=True)
class CurrentDomainIdentity:
    factory: str
    state: str
    model_label: str
    logical_id: str
    physical_id: str
    database_key: str | None
    value: Any = field(compare=False, repr=False)

    def logical_payload(self) -> dict[str, object]:
        return {
            "factory": self.factory,
            "logical_id": self.logical_id,
            "model_label": self.model_label,
            "state": self.state,
            "values": _normalized_domain_value(self.value),
        }


@dataclass(frozen=True, slots=True)
class CurrentDomainScenario:
    bundle: str
    state: str
    identities: tuple[CurrentDomainIdentity, ...]

    def by_factory(self) -> dict[str, CurrentDomainIdentity]:
        return {identity.factory: identity for identity in self.identities}

    def logical_payload(self) -> dict[str, object]:
        return {
            "bundle": self.bundle,
            "identities": sorted(
                (identity.logical_payload() for identity in self.identities),
                key=lambda value: str(value["factory"]),
            ),
            "state": self.state,
        }

    def canonical_json(self) -> bytes:
        return canonical_json_bytes(self.logical_payload())

    def sha256(self) -> str:
        return canonical_sha256(self.logical_payload())


def create_current_scenario(
    context: FactoryContext,
    *,
    bundle: str,
    state: str = "minimal_valid",
) -> CurrentDomainScenario:
    """Create one composed, deterministic current-domain ORM/service scenario."""

    if bundle not in BUNDLE_LEAVES:
        raise ValueError(f"unknown factory bundle: {bundle}")
    if state not in SCENARIO_STATES:
        raise ValueError(f"unknown factory scenario state: {state}")
    builders = {
        "accounts_management": _accounts,
        "editorial_content": _content,
        "adopted_courses": _courses,
        "historical_event_totals": _events,
        "operations_jobs": _operations,
        "provider_neutral_messaging": _messaging,
    }
    with (
        transaction.atomic(),
        context.frozen_environment(),
        patch("django.utils.timezone.now", return_value=context.frozen_at),
    ):
        values = builders[bundle](context, state)
    expected = {f"{bundle}.{leaf}" for leaf in BUNDLE_LEAVES[bundle]}
    if set(values) != expected:
        raise RuntimeError(f"current-domain builder is incomplete for {bundle}")
    identities = tuple(
        _identity(context, factory, state, values[factory])
        for factory in (f"{bundle}.{leaf}" for leaf in BUNDLE_LEAVES[bundle])
    )
    return CurrentDomainScenario(bundle=bundle, state=state, identities=identities)


def create_current_leaf(
    context: FactoryContext,
    *,
    bundle: str,
    leaf: str,
    state: str = "minimal_valid",
) -> CurrentDomainIdentity:
    """Compose named leaves through one context-owned persisted scenario graph."""

    if leaf not in BUNDLE_LEAVES.get(bundle, ()):
        raise ValueError(f"unknown factory leaf: {bundle}.{leaf}")
    cache_key = (bundle, state)
    scenario = context._current_domain_scenarios.get(cache_key)
    if scenario is None:
        scenario = create_current_scenario(context, bundle=bundle, state=state)
        context._current_domain_scenarios[cache_key] = scenario
    return scenario.by_factory()[f"{bundle}.{leaf}"]


_PHYSICAL_HEX_RE = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{8,}(?![0-9a-f])")
_PHYSICAL_UUID_RE = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)
_EMAIL_RE = re.compile(r"(?i)\b[^\s@]+@[^\s@]+\.[^\s@]+\b")


def _normalized_domain_value(value: object) -> object:
    """Serialize current values while replacing namespace-only physical identities."""

    if hasattr(value, "_meta") and hasattr(value, "pk"):
        fields: dict[str, object] = {}
        for model_field in sorted(value._meta.concrete_fields, key=lambda item: item.name):
            if model_field.primary_key:
                continue
            if model_field.is_relation:
                related_model = model_field.remote_field.model._meta.label_lower
                fields[model_field.name] = {"related_model": related_model}
                continue
            fields[model_field.name] = _normalize_domain_scalar(
                model_field.value_from_object(value),
                field_name=model_field.name,
                physical_identity=model_field.unique,
            )
        return {"fields": fields}
    if isinstance(value, RejectedDomainValue):
        return {"reason": value.reason}
    if isinstance(value, CapturedMessage):
        return value.redacted_metadata()
    if isinstance(value, ProviderSimulatorValue):
        return {
            "attempt": value.attempt,
            "outcome": value.outcome,
            "redacted_recipient": value.redacted_recipient,
        }
    raise TypeError(f"unsupported current-domain value: {type(value).__name__}")


def _normalize_domain_scalar(
    value: object,
    *,
    field_name: str = "",
    physical_identity: bool = False,
) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_domain_scalar(value[key], field_name=str(key))
            for key in sorted(value, key=str)
        }
    if isinstance(value, (list, tuple)):
        return [
            _normalize_domain_scalar(
                item,
                field_name=field_name,
                physical_identity=physical_identity,
            )
            for item in value
        ]
    if isinstance(value, (set, frozenset)):
        return sorted(
            (
                _normalize_domain_scalar(
                    item,
                    field_name=field_name,
                    physical_identity=physical_identity,
                )
                for item in value
            ),
            key=canonical_json_bytes,
        )
    if isinstance(value, uuid.UUID):
        return "[PHYSICAL_UUID]"
    if isinstance(value, str):
        normalized = _EMAIL_RE.sub("[REDACTED]", value)
        normalized = _PHYSICAL_UUID_RE.sub("[PHYSICAL_UUID]", normalized)
        return _PHYSICAL_HEX_RE.sub("[PHYSICAL_ID]", normalized)
    if isinstance(value, (datetime, date, Decimal, Path)):
        return value
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        field_tokens = set(field_name.casefold().split("_"))
        if physical_identity or field_tokens & {"id", "ids", "identifier", "identifiers"}:
            return "[PHYSICAL_ID]"
        return value
    if isinstance(value, float):
        return value
    raise TypeError(f"unsupported current-domain field value: {type(value).__name__}")


def _identity(
    context: FactoryContext,
    factory: str,
    state: str,
    value: object,
) -> CurrentDomainIdentity:
    if hasattr(value, "_meta") and hasattr(value, "pk"):
        model_label = value._meta.label_lower
        database_key = str(value.pk)
    elif isinstance(value, RejectedDomainValue):
        model_label = value.model_label
        database_key = None
    elif isinstance(value, CapturedMessage):
        model_label = "messaging.captured_message"
        database_key = None
    elif isinstance(value, ProviderSimulatorValue):
        model_label = f"messaging.{value.kind}"
        database_key = None
    else:
        raise TypeError(f"unsupported current-domain value: {type(value).__name__}")
    return CurrentDomainIdentity(
        factory=factory,
        state=state,
        model_label=model_label,
        logical_id=str(context.logical_uuid(factory, state)),
        physical_id=str(context.physical_uuid(factory, state)),
        database_key=database_key,
        value=value,
    )


def _model(label: str):
    model = apps.get_model(label)
    if model is None:
        raise RuntimeError(f"current-domain model is unavailable: {label}")
    return model


def _uuid(context: FactoryContext, factory: str, state: str, suffix: str = "row"):
    return context.physical_uuid(factory, f"{state}.{suffix}")


def _integer(context: FactoryContext, factory: str, state: str, suffix: str = "row") -> int:
    return context.physical_int(factory, f"{state}.{suffix}")


def _key(context: FactoryContext, factory: str, state: str, *, length: int = 20) -> str:
    return context.physical_key(factory, state, length=length)


def _physical_digest(
    context: FactoryContext,
    factory: str,
    state: str,
    suffix: str = "row",
) -> str:
    return hashlib.sha256(str(_uuid(context, factory, state, suffix)).encode()).hexdigest()


def _rejected(model_label: str, reason: str) -> RejectedDomainValue:
    return RejectedDomainValue(model_label=model_label, reason=reason)


def _accounts(context: FactoryContext, state: str) -> dict[str, object]:
    User = _model("accounts.CustomUser")
    Alias = _model("accounts.AccountIdentityAlias")
    Quarantine = _model("accounts.AccountIdentityQuarantine")
    Reconciliation = _model("accounts.AccountReconciliationRun")
    Token = _model("accounts.Token")
    Group = _model("auth.Group")
    Permission = _model("auth.Permission")
    Principal = _model("management_auth.APIPrincipal")
    Credential = _model("management_auth.APICredential")
    StaffSession = _model("core.StaffSession")
    prefix = "accounts_management"
    user_factory = f"{prefix}.custom_user"
    user = User.objects.create(
        id=_integer(context, user_factory, state),
        username=f"synthetic-{_key(context, user_factory, state)}",
        email=context.synthetic_email(user_factory, state),
        password="!synthetic-unusable",
        is_staff=True,
        identity_state="active" if state != "stale_conflict" else "quarantined",
        date_joined=context.frozen_at,
    )
    group = Group.objects.create(
        id=_integer(context, f"{prefix}.staff_group", state),
        name=f"synthetic-role-{_key(context, f'{prefix}.staff_group', state)}",
    )
    permission = Permission.objects.order_by("content_type_id", "codename").first()
    if permission is None:
        raise RuntimeError("Django permissions are unavailable to the account factory")
    group.permissions.add(permission)
    user.groups.add(group)
    alias = Alias.objects.create(
        id=_integer(context, f"{prefix}.account_identity_alias", state),
        source_user_id=_integer(context, f"{prefix}.account_identity_alias", state, "source"),
        survivor=user,
        source_snapshot_id=f"synthetic-{_key(context, f'{prefix}.account_identity_alias', state)}",
        mapping_checksum=canonical_sha256({"factory": "alias", "state": state}),
        review_reference=f"synthetic-{state}",
    )
    quarantine = Quarantine.objects.create(
        id=_uuid(context, f"{prefix}.account_identity_quarantine", state),
        fingerprint=_physical_digest(
            context,
            f"{prefix}.account_identity_quarantine",
            state,
            "fingerprint",
        ),
        source_snapshot_id=f"synthetic-{state}",
        source_user_ids=[alias.source_user_id],
        reason_codes=["synthetic_conflict"],
        status="open" if state == "stale_conflict" else "resolved",
        resolution_reference="" if state == "stale_conflict" else "synthetic-reviewed",
        resolved_at=None if state == "stale_conflict" else context.frozen_at,
    )
    reconciliation = Reconciliation.objects.create(
        id=_uuid(context, f"{prefix}.account_reconciliation_run", state),
        source_snapshot_id=(
            f"synthetic-{_key(context, f'{prefix}.account_reconciliation_run', state)}"
        ),
        mapping_checksum=canonical_sha256({"factory": "reconciliation", "state": state}),
        mode="apply",
        source_account_count=2,
        survivor_account_count=1,
        alias_count=1,
        quarantine_count=1,
        relationship_counts={"synthetic": 1},
        relationship_checksums={"synthetic": "0" * 64},
        report_checksum=canonical_sha256({"factory": "report", "state": state}),
    )
    principal = Principal.objects.create(
        id=_uuid(context, f"{prefix}.management_principal", state),
        kind="human",
        name=f"Synthetic principal {state}",
        identity_snapshot=f"synthetic-{_key(context, f'{prefix}.management_principal', state)}",
        is_active=state != "stale_conflict",
        user=user,
        created_by=user,
    )
    principal.permissions.add(permission)
    credential = Credential.objects.create(
        id=_uuid(context, f"{prefix}.management_credential", state),
        principal=principal,
        name="Synthetic credential",
        prefix=_key(context, f"{prefix}.management_credential", state, length=12),
        secret_digest=canonical_sha256({"factory": "credential", "state": state}),
        digest_algorithm="sha256",
        digest_version=1,
        scopes=["synthetic.read"],
        expires_at=context.frozen_at + timedelta(days=3650),
        revoked_at=context.frozen_at if state == "stale_conflict" else None,
        created_by=user,
    )
    staff_session = StaffSession.objects.create(
        id=_uuid(context, f"{prefix}.staff_session", state),
        user=user,
        authenticated_at=context.frozen_at,
        revoked_at=context.frozen_at if state in {"boundary_valid", "stale_conflict"} else None,
    )
    token = Token.objects.create(
        key=_key(context, f"{prefix}.one_time_token_representation", state, length=40),
        user=user,
    )
    custom_user_value: object = user
    if state == "invalid_rejected":
        invalid = User(username="", email="invalid@example.invalid")
        try:
            invalid.full_clean()
        except ValidationError:
            custom_user_value = _rejected("accounts.customuser", "required_username")
        else:
            raise RuntimeError("invalid account factory state was not rejected")
    return {
        f"{prefix}.custom_user": custom_user_value,
        f"{prefix}.staff_role": group,
        f"{prefix}.staff_group": group,
        f"{prefix}.staff_permission": permission,
        f"{prefix}.account_identity_alias": alias,
        f"{prefix}.account_identity_quarantine": quarantine,
        f"{prefix}.account_reconciliation_run": reconciliation,
        f"{prefix}.management_principal": principal,
        f"{prefix}.management_credential": credential,
        f"{prefix}.staff_session": staff_session,
        f"{prefix}.one_time_token_representation": token,
    }


def _content(context: FactoryContext, state: str) -> dict[str, object]:
    Source = _model("content.ContentSource")
    Release = _model("content.ContentRelease")
    ActivePath = _model("content.ActiveContentPath")
    Document = _model("content.ContentDocument")
    Relation = _model("content.ContentRelation")
    Asset = _model("content.ContentAsset")
    prefix = "editorial_content"
    source_factory = f"{prefix}.content_source"
    source = Source.objects.create(
        id=_uuid(context, source_factory, state),
        stable_id=f"synthetic-{_key(context, source_factory, state)}",
        display_name=f"Synthetic content {state}",
        repository_owner="synthetic-owner",
        repository_name=f"synthetic-{_key(context, source_factory, state, length=12)}",
        branch="main",
        path_allowlist=["synthetic/"],
        adapter_type="fixture",
        mount_path="/",
        enabled=True,
    )
    release_factory = f"{prefix}.content_release"
    release = Release.objects.create(
        id=_uuid(context, release_factory, state),
        source=source,
        sequence=1,
        commit_sha="a" * 40,
        parser_version="synthetic-v1",
        rendering_version="synthetic-v1",
        status="fetching",
        requested_at=context.frozen_at,
        request_provenance={"kind": "synthetic"},
    )
    document_leaves = (
        "article_document",
        "podcast_document",
        "transcript_document",
        "book_document",
        "person_document",
        "public_course_document",
        "public_event_document",
        "faq_document",
        "docs_document",
        "wiki_document",
    )
    documents: dict[str, Any] = {}
    for index, leaf in enumerate(document_leaves):
        factory = f"{prefix}.{leaf}"
        path = f"/synthetic/{_key(context, factory, state)}.html"
        documents[leaf] = Document.objects.create(
            id=_uuid(context, factory, state),
            release=release,
            content_kind=leaf.removesuffix("_document"),
            stable_key=f"synthetic-{_key(context, factory, state)}",
            source_path=f"synthetic/{index}-{leaf}.md",
            checksum=canonical_sha256({"factory": factory, "state": state}),
            exact_public_path=path,
            slug=f"synthetic-{_key(context, factory, state, length=12)}",
            title=f"Synthetic {leaf.replace('_', ' ')}",
            rendered_html="<p>Synthetic content</p>",
            is_published=True,
        )
    article = documents["article_document"]
    relation = Relation.objects.create(
        id=_uuid(context, f"{prefix}.content_relation", state),
        source_document=article,
        relation_type="synthetic-related",
        target_kind="wiki",
        target_key="synthetic-target",
        resolved_target_document=documents["wiki_document"],
        order=0,
        is_required=True,
    )
    asset_factory = f"{prefix}.content_asset"
    asset = Asset.objects.create(
        id=_uuid(context, asset_factory, state),
        release=release,
        source_path="synthetic/image.svg",
        stable_public_path=f"/assets/{_key(context, asset_factory, state)}.svg",
        storage_key=f"content/{source.stable_id}/{release.id}/synthetic.svg",
        content_type="image/svg+xml",
        size=1,
        checksum=canonical_sha256({"factory": asset_factory, "state": state}),
    )
    from content.services import (
        ActivateContentRelease,
        MarkReleaseReady,
        TransitionContentRelease,
        activate_content_release,
        asset_manifest_checksum_for,
        begin_release_validation,
        mark_release_ready,
    )
    from core.models import AuditEvent
    from core.services import ServiceContext

    service_context = ServiceContext(
        correlation_id=f"factory-{_key(context, release_factory, state)}",
        actor_ref="test:synthetic",
    )
    audit_sequence = iter(range(1, 100))
    with (
        patch("django.utils.timezone.now", return_value=context.frozen_at),
        patch.object(
            AuditEvent._meta.get_field("id"),
            "default",
            lambda: _uuid(
                context,
                release_factory,
                state,
                f"audit-{next(audit_sequence)}",
            ),
        ),
    ):
        release = begin_release_validation(
            TransitionContentRelease(
                release_id=release.id,
                expected_revision=release.revision,
            ),
            context=service_context,
        )
        release = mark_release_ready(
            MarkReleaseReady(
                release_id=release.id,
                expected_revision=release.revision,
                asset_manifest_checksum=asset_manifest_checksum_for(release.id),
            ),
            context=service_context,
        )
        activate_content_release(
            ActivateContentRelease(
                source_id=source.id,
                release_id=release.id,
                expected_source_revision=source.revision,
                expected_release_revision=release.revision,
                reason=f"synthetic-{state}",
            ),
            context=service_context,
        )
    source.refresh_from_db()
    release.refresh_from_db()
    path = ActivePath.objects.get(
        path_digest=hashlib.sha256(article.exact_public_path.encode()).hexdigest()
    )
    article_value: object = article
    if state == "invalid_rejected":
        invalid = Document(
            release=release,
            content_kind="article",
            stable_key="invalid",
            source_path="invalid.md",
            checksum="0" * 64,
            exact_public_path="relative/path",
            title="Invalid",
        )
        try:
            invalid.full_clean()
        except ValidationError:
            article_value = _rejected("content.contentdocument", "invalid_public_path")
        else:
            raise RuntimeError("invalid content factory state was not rejected")
    values: dict[str, object] = {
        f"{prefix}.content_source": source,
        f"{prefix}.content_release": release,
        f"{prefix}.active_content_path": path,
        f"{prefix}.content_relation": relation,
        f"{prefix}.content_asset": asset,
        f"{prefix}.frozen_release_child": documents["wiki_document"],
    }
    values.update({f"{prefix}.{leaf}": value for leaf, value in documents.items()})
    values[f"{prefix}.article_document"] = article_value
    return values


def _courses(context: FactoryContext, state: str) -> dict[str, object]:
    User = _model("accounts.CustomUser")
    Cohort = _model("courses.Cohort")
    Campaign = _model("courses.RegistrationCampaign")
    Registration = _model("courses.CourseRegistration")
    Enrollment = _model("courses.Enrollment")
    Homework = _model("courses.Homework")
    Question = _model("courses.Question")
    Submission = _model("courses.Submission")
    Answer = _model("courses.Answer")
    HomeworkStatistics = _model("courses.HomeworkStatistics")
    Project = _model("courses.Project")
    ProjectSubmission = _model("courses.ProjectSubmission")
    ProjectVote = _model("courses.ProjectVote")
    ReviewCriteria = _model("courses.ReviewCriteria")
    PeerReview = _model("courses.PeerReview")
    CriteriaResponse = _model("courses.CriteriaResponse")
    Evaluation = _model("courses.ProjectEvaluationScore")
    ProjectStatistics = _model("courses.ProjectStatistics")
    Complaint = _model("courses.LeaderboardComplaint")
    Wrapped = _model("courses.WrappedStatistics")
    UserWrapped = _model("courses.UserWrappedStatistics")
    prefix = "adopted_courses"
    user = User.objects.create(
        id=_integer(context, f"{prefix}.enrollment", state, "student"),
        username=f"synthetic-course-{_key(context, f'{prefix}.enrollment', state)}",
        email=context.synthetic_email(f"{prefix}.enrollment", state),
        password="!synthetic-unusable",
    )
    reviewer = User.objects.create(
        id=_integer(context, f"{prefix}.peer_review", state, "reviewer"),
        username=f"synthetic-reviewer-{_key(context, f'{prefix}.peer_review', state)}",
        email=context.synthetic_email(f"{prefix}.peer_review", state),
        password="!synthetic-unusable",
    )
    course_factory = f"{prefix}.course"
    course = Cohort.objects.create(
        id=_integer(context, course_factory, state),
        slug=f"synthetic-{_key(context, course_factory, state)}",
        title=f"Synthetic course {state}",
        description="Synthetic course",
        start_date=context.frozen_at.date(),
        end_date=(context.frozen_at + timedelta(days=30)).date(),
        project_passing_score=1,
    )
    campaign = Campaign.objects.create(
        id=_integer(context, f"{prefix}.registration_campaign", state),
        slug=f"synthetic-{_key(context, f'{prefix}.registration_campaign', state)}",
        title="Synthetic registration campaign",
        edition_label="Synthetic edition",
        current_course=course,
        is_active=state != "stale_conflict",
    )
    registration = Registration.objects.create(
        id=_integer(context, f"{prefix}.course_registration", state),
        campaign=campaign,
        course=course,
        user=user,
        email=context.synthetic_email(f"{prefix}.course_registration", state),
        name="Synthetic learner",
        country="Synthetic country",
        region="Synthetic region",
        role="student_stem",
    )
    enrollment = Enrollment.objects.create(
        id=_integer(context, f"{prefix}.enrollment", state),
        student=user,
        course=course,
        display_name="Synthetic learner",
    )
    reviewer_enrollment = Enrollment.objects.create(
        id=_integer(context, f"{prefix}.peer_review", state, "enrollment"),
        student=reviewer,
        course=course,
        display_name="Synthetic reviewer",
    )
    homework = Homework.objects.create(
        id=_integer(context, f"{prefix}.homework", state),
        course=course,
        slug=f"synthetic-{_key(context, f'{prefix}.homework', state)}",
        title="Synthetic homework",
        description="Synthetic",
        due_date=context.frozen_at + timedelta(days=7),
    )
    question = Question.objects.create(
        id=_integer(context, f"{prefix}.question", state),
        homework=homework,
        text="Synthetic question",
        question_type="MC",
        possible_answers="Alpha\nBeta",
        correct_answer="1",
    )
    submission = Submission.objects.create(
        id=_integer(context, f"{prefix}.submission", state),
        homework=homework,
        student=user,
        enrollment=enrollment,
        submitted_at=context.frozen_at,
        total_score=1,
    )
    answer = Answer.objects.create(
        id=_integer(context, f"{prefix}.answer", state),
        submission=submission,
        question=question,
        answer_text="1",
        is_correct=True,
    )
    homework_stats = HomeworkStatistics.objects.create(
        id=_integer(context, f"{prefix}.homework_statistics", state),
        homework=homework,
        total_submissions=1,
    )
    project = Project.objects.create(
        id=_integer(context, f"{prefix}.project", state),
        course=course,
        slug=f"synthetic-{_key(context, f'{prefix}.project', state)}",
        title="Synthetic project",
        description="Synthetic",
        submission_due_date=context.frozen_at + timedelta(days=14),
        peer_review_due_date=context.frozen_at + timedelta(days=21),
        number_of_peers_to_evaluate=1,
    )
    project_submission = ProjectSubmission.objects.create(
        id=_integer(context, f"{prefix}.project_submission", state),
        project=project,
        student=user,
        enrollment=enrollment,
        github_link="https://repository.example.invalid/synthetic",
        commit_id="a" * 40,
        submitted_at=context.frozen_at,
    )
    reviewer_submission = ProjectSubmission.objects.create(
        id=_integer(context, f"{prefix}.peer_review", state, "submission"),
        project=project,
        student=reviewer,
        enrollment=reviewer_enrollment,
        github_link="https://repository.example.invalid/reviewer",
        commit_id="b" * 40,
        submitted_at=context.frozen_at,
    )
    vote = ProjectVote.objects.create(
        id=_integer(context, f"{prefix}.project_vote", state),
        submission=project_submission,
        voter=reviewer,
    )
    criteria = ReviewCriteria.objects.create(
        id=_integer(context, f"{prefix}.review_criteria", state),
        course=course,
        description="Synthetic quality",
        options=[{"criteria": "Accept", "score": 1}],
        review_criteria_type="RB",
    )
    review = PeerReview.objects.create(
        id=_integer(context, f"{prefix}.peer_review", state),
        submission_under_evaluation=project_submission,
        reviewer=reviewer_submission,
        note_to_peer="Synthetic review",
        state="TR" if state == "stale_conflict" else "SU",
        submitted_at=None if state == "stale_conflict" else context.frozen_at,
    )
    response = CriteriaResponse.objects.create(
        id=_integer(context, f"{prefix}.criteria_response", state),
        review=review,
        criteria=criteria,
        answer="1",
    )
    evaluation = Evaluation.objects.create(
        id=_integer(context, f"{prefix}.project_evaluation_score", state),
        submission=project_submission,
        review_criteria=criteria,
        score=1,
    )
    project_stats = ProjectStatistics.objects.create(
        id=_integer(context, f"{prefix}.project_statistics", state),
        project=project,
        total_submissions=1,
    )
    complaint = Complaint.objects.create(
        id=_integer(context, f"{prefix}.leaderboard_complaint", state),
        enrollment=enrollment,
        reporter=reviewer,
        issue_type="other",
        description="Synthetic complaint",
        resolved=state != "stale_conflict",
        resolved_at=None if state == "stale_conflict" else context.frozen_at,
        resolved_by=None if state == "stale_conflict" else reviewer,
    )
    year = 3000 + (_integer(context, f"{prefix}.wrapped_statistics", state) % 100_000)
    wrapped = Wrapped.objects.create(
        id=_integer(context, f"{prefix}.wrapped_statistics", state),
        year=year,
        is_visible=True,
        total_participants=1,
        total_enrollments=1,
        course_stats=[{"course": "synthetic", "count": 1}],
        leaderboard=[],
    )
    user_wrapped = UserWrapped.objects.create(
        id=_integer(context, f"{prefix}.user_wrapped_statistics", state),
        wrapped=wrapped,
        user=user,
        total_points=1,
        courses=[{"course": "synthetic", "points": 1}],
        rank=1,
        display_name="[REDACTED]" if state == "privacy_redaction" else "Synthetic learner",
    )
    course_value: object = course
    if state == "invalid_rejected":
        invalid = Cohort(
            slug=f"invalid-{_key(context, course_factory, state)}",
            title="Invalid course",
            description="Synthetic",
            start_date=context.frozen_at.date(),
            end_date=(context.frozen_at - timedelta(days=1)).date(),
        )
        try:
            invalid.full_clean()
        except ValidationError:
            course_value = _rejected("courses.cohort", "end_before_start")
        else:
            raise RuntimeError("invalid course factory state was not rejected")
    return {
        f"{prefix}.course": course_value,
        f"{prefix}.registration_campaign": campaign,
        f"{prefix}.course_registration": registration,
        f"{prefix}.enrollment": enrollment,
        f"{prefix}.homework": homework,
        f"{prefix}.question": question,
        f"{prefix}.answer": answer,
        f"{prefix}.submission": submission,
        f"{prefix}.homework_statistics": homework_stats,
        f"{prefix}.project": project,
        f"{prefix}.project_submission": project_submission,
        f"{prefix}.project_vote": vote,
        f"{prefix}.review_criteria": criteria,
        f"{prefix}.peer_review": review,
        f"{prefix}.criteria_response": response,
        f"{prefix}.project_evaluation_score": evaluation,
        f"{prefix}.project_statistics": project_stats,
        f"{prefix}.leaderboard_complaint": complaint,
        f"{prefix}.wrapped_statistics": wrapped,
        f"{prefix}.user_wrapped_statistics": user_wrapped,
    }


def _events(context: FactoryContext, state: str) -> dict[str, object]:
    SourceRun = _model("events.HistoricalRegistrationSourceRun")
    Aggregate = _model("events.HistoricalRegistrationAggregateRevision")
    Slot = _model("events.HistoricalRegistrationAggregateSlot")
    Displacement = _model("events.HistoricalRegistrationPointerDisplacement")
    Total = _model("events.HistoricalRegistrationTotalState")
    prefix = "historical_event_totals"
    run = SourceRun.objects.create(
        id=_uuid(context, f"{prefix}.historical_source_run", state),
        provider="luma",
        adapter_version="synthetic-v1",
        schema_version="synthetic-v1",
        whole_source_checksum=_physical_digest(
            context,
            f"{prefix}.historical_source_run",
            state,
            "whole-source",
        ),
        source_reference_digest=canonical_sha256({"factory": "reference", "state": state}),
        manifest_entry_total=1,
        manifest_event_total=1,
        parsed_row_total=3,
        eligible_row_total=3,
        excluded_row_total=0,
        status_totals={"accepted": 3},
        state_totals={"synthetic": 3},
        mapping_set_revision=1,
        policy_version="synthetic-v1",
        state="quarantined" if state == "stale_conflict" else "active",
        actor_ref="synthetic-actor",
    )
    aggregate_factory = f"{prefix}.historical_aggregate_revision"
    aggregate_key = _key(context, aggregate_factory, state)
    from events.identity import create_event_identity

    canonical_event = create_event_identity(
        event_id=_uuid(context, f"{prefix}.event", state),
        title=f"Synthetic Historical Event {aggregate_key}",
        source_repository="DataTalksClub/synthetic",
        source_revision="a" * 40,
        source_key=f"synthetic/{aggregate_key}.md",
        source_path=f"synthetic/{aggregate_key}.md",
        source_checksum=canonical_sha256({"event": aggregate_key}),
    )
    external_event_identifier = f"synthetic-{aggregate_key}"
    aggregate = Aggregate.objects.create(
        id=_uuid(context, f"{prefix}.historical_aggregate_revision", state),
        source_run=run,
        external_event_identifier=external_event_identifier,
        event=canonical_event,
        eligible_count=3,
        coverage_boundary="synthetic-all",
        status_policy_version="synthetic-v1",
        combination_policy="additive_disjoint",
        aggregate_checksum=canonical_sha256({"factory": "aggregate", "state": state}),
        state="active",
    )
    replacement = Aggregate.objects.create(
        id=_uuid(context, f"{prefix}.aggregate_rollback", state),
        source_run=run,
        external_event_identifier=external_event_identifier,
        event=canonical_event,
        eligible_count=2,
        coverage_boundary="synthetic-all",
        status_policy_version="synthetic-v1",
        combination_policy="replacement",
        aggregate_checksum=canonical_sha256({"factory": "replacement", "state": state}),
        state="active",
    )
    slot = Slot.objects.create(
        id=_uuid(context, f"{prefix}.historical_aggregate_slot", state),
        canonical_repository=canonical_event.source_repository,
        canonical_revision=canonical_event.source_revision,
        canonical_source_key=canonical_event.source_key,
        canonical_slug_snapshot=canonical_event.slug,
        provider="luma",
        coverage_boundary="synthetic-all",
        active_revision=aggregate,
    )
    displacement = Displacement.objects.create(
        id=_uuid(context, f"{prefix}.historical_pointer_displacement", state),
        replacing_revision=replacement,
        slot=slot,
        displaced_revision=aggregate,
    )
    total = Total.objects.create(
        id=_uuid(context, f"{prefix}.historical_total_state", state),
        canonical_repository=canonical_event.source_repository,
        canonical_revision=canonical_event.source_revision,
        canonical_source_key=canonical_event.source_key,
        canonical_slug_snapshot=canonical_event.slug,
        complete=state != "stale_conflict",
    )
    boundary_factory = f"{prefix}.aggregate_to_native_boundary"
    boundary = Slot.objects.create(
        id=_uuid(context, boundary_factory, state),
        canonical_repository=canonical_event.source_repository,
        canonical_revision=canonical_event.source_revision,
        canonical_source_key=canonical_event.source_key,
        canonical_slug_snapshot=canonical_event.slug,
        provider="luma",
        coverage_boundary="synthetic-native",
        active_revision=replacement,
    )
    from core.models import AuditEvent
    from core.services import ServiceContext
    from events.services import replace_aggregate_with_row_projection

    with (
        patch(
            "events.services.event_public_record",
            return_value={
                "identity_id": str(canonical_event.id),
                "slug": canonical_event.slug,
                "provenance": {
                    "repository": canonical_event.source_repository,
                    "revision": canonical_event.source_revision,
                    "source_key": canonical_event.source_key,
                },
            },
        ),
        patch.object(
            AuditEvent._meta.get_field("id"),
            "default",
            lambda: _uuid(
                context,
                boundary_factory,
                state,
                "audit",
            ),
        ),
    ):
        boundary = replace_aggregate_with_row_projection(
            event_id=canonical_event.id,
            provider="luma",
            coverage_boundary="synthetic-native",
            replacement_revision_id=_uuid(
                context,
                boundary_factory,
                state,
                "row",
            ),
            eligible_count=3,
            expected_slot_revision=boundary.revision,
            reason_code="synthetic_row_replacement",
            actor=None,
            context=ServiceContext(
                correlation_id=f"factory-{_key(context, boundary_factory, state)}",
                actor_ref="test:synthetic",
            ),
        )
    replacement.refresh_from_db()
    total.refresh_from_db()
    aggregate_value: object = aggregate
    if state == "invalid_rejected":
        invalid = Aggregate(
            source_run=run,
            external_event_identifier="",
            event=canonical_event,
            eligible_count=0,
            coverage_boundary="synthetic-all",
            status_policy_version="synthetic-v1",
            combination_policy="replacement",
            aggregate_checksum="0" * 64,
        )
        try:
            invalid.full_clean()
        except ValidationError:
            aggregate_value = _rejected(
                "events.historicalregistrationaggregaterevision",
                "missing_external_event_identifier",
            )
        else:
            raise RuntimeError("invalid historical aggregate revision was not rejected")
    return {
        f"{prefix}.historical_source_run": run,
        f"{prefix}.historical_aggregate_revision": aggregate_value,
        f"{prefix}.historical_aggregate_slot": slot,
        f"{prefix}.historical_pointer_displacement": displacement,
        f"{prefix}.historical_total_state": total,
        f"{prefix}.aggregate_activation": aggregate,
        f"{prefix}.aggregate_rollback": replacement,
        f"{prefix}.aggregate_to_native_boundary": boundary,
    }


def _operations(context: FactoryContext, state: str) -> dict[str, object]:
    AuditEvent = _model("core.AuditEvent")
    Setting = _model("core.OperationalSetting")
    Idempotency = _model("core.IdempotencyRecord")
    Operation = _model("core.Operation")
    DurableJob = _model("jobs.DurableJob")
    Heartbeat = _model("jobs.WorkerHeartbeat")
    SchedulerLease = _model("jobs.SchedulerLease")
    prefix = "operations_jobs"
    audit = AuditEvent.objects.create(
        id=_uuid(context, f"{prefix}.audit_event", state),
        actor_ref="synthetic-actor",
        action="synthetic.factory",
        target_type="synthetic.record",
        outcome="succeeded",
        changes={},
        metadata={},
    )
    setting = Setting.objects.create(
        id=_uuid(context, f"{prefix}.operational_setting", state),
        key=f"synthetic.{_key(context, f'{prefix}.operational_setting', state)}",
        value_type="boolean",
        value=True,
        source="synthetic-factory",
        definition_version=1,
    )
    idempotency = Idempotency.objects.create(
        id=_uuid(context, f"{prefix}.idempotency_record", state),
        scope="synthetic.factory",
        key_hash=_physical_digest(
            context,
            f"{prefix}.idempotency_record",
            state,
            "key",
        ),
        request_hash=canonical_sha256({"factory": "request", "state": state}),
        status="completed",
        owner_token=_uuid(context, f"{prefix}.idempotency_record", state, "owner"),
        result={"status": "[REDACTED]" if state == "privacy_redaction" else "synthetic"},
        completed_at=context.frozen_at,
    )
    from core.operations import create_operation

    operation_factory = f"{prefix}.operation"
    operation_id = _uuid(context, operation_factory, state)
    audit_default = AuditEvent._meta.get_field("id")
    audit_sequence = iter(range(1, 100))
    with (
        patch("core.operations.uuid.uuid4", return_value=operation_id),
        patch.object(
            audit_default,
            "default",
            lambda: _uuid(
                context,
                f"{prefix}.audit_event",
                state,
                f"operation-{next(audit_sequence)}",
            ),
        ),
        patch("django.utils.timezone.now", return_value=context.frozen_at),
    ):
        operation = create_operation(
            kind=f"synthetic.factory.{_key(context, operation_factory, state, length=12)}",
            cancellable=True,
            progress_total=1,
            message="[REDACTED]" if state == "privacy_redaction" else "Synthetic operation",
        )
    if state == "stale_conflict":
        Operation.objects.filter(pk=operation.pk).update(
            revision=2,
            status="running",
            started_at=context.frozen_at,
        )
        operation.refresh_from_db()
    job_states: dict[str, dict[str, Any]] = {
        "minimal_valid": {"status": "pending"},
        "complete_valid": {
            "status": "succeeded",
            "attempt_count": 1,
            "completed_at": context.frozen_at,
        },
        "boundary_valid": {
            "status": "running",
            "attempt_count": 1,
            "lease_token": _uuid(context, f"{prefix}.durable_job", state, "lease"),
            "lease_expires_at": context.frozen_at + timedelta(minutes=1),
            "claimed_by": "synthetic-boundary-worker",
        },
        "invalid_rejected": {
            "status": "failed",
            "attempt_count": 3,
            "completed_at": context.frozen_at,
            "last_error_code": "synthetic_terminal_failure",
        },
        "stale_conflict": {
            "status": "retry_wait",
            "attempt_count": 1,
            "last_error_code": "synthetic_retry",
        },
        "privacy_redaction": {
            "status": "failed",
            "attempt_count": 3,
            "completed_at": context.frozen_at,
            "last_error_code": "redacted_failure",
        },
    }
    job_state = job_states[state]
    job = DurableJob.objects.create(
        id=_uuid(context, f"{prefix}.durable_job", state),
        operation=operation,
        handler="synthetic.handler",
        deduplication_key_hash=_physical_digest(
            context,
            f"{prefix}.durable_job",
            state,
            "deduplication-key",
        ),
        payload_hash=canonical_sha256({"factory": "job-payload", "state": state}),
        payload={"kind": "[REDACTED]" if state == "privacy_redaction" else "synthetic"},
        status=job_state["status"],
        attempt_count=job_state.get("attempt_count", 0),
        max_attempts=3,
        available_at=context.frozen_at,
        next_wakeup_at=context.frozen_at + timedelta(minutes=1),
        lease_token=job_state.get("lease_token"),
        lease_expires_at=job_state.get("lease_expires_at"),
        claimed_by=job_state.get("claimed_by", ""),
        last_error_code=job_state.get("last_error_code", ""),
        completed_at=job_state.get("completed_at"),
    )
    lease_job = DurableJob.objects.create(
        id=_uuid(context, f"{prefix}.job_lease", state),
        operation=operation,
        handler=f"synthetic.lease.{_key(context, f'{prefix}.job_lease', state)}",
        deduplication_key_hash=_physical_digest(context, f"{prefix}.job_lease", state, "key"),
        payload_hash=canonical_sha256({"factory": "job-lease", "state": state}),
        payload={"kind": "synthetic-lease"},
        status="running",
        attempt_count=1,
        max_attempts=3,
        available_at=context.frozen_at,
        next_wakeup_at=context.frozen_at + timedelta(minutes=1),
        lease_token=_uuid(context, f"{prefix}.job_lease", state, "lease"),
        lease_expires_at=context.frozen_at + timedelta(minutes=1),
        claimed_by="synthetic-lease-worker",
    )
    result_job = DurableJob.objects.create(
        id=_uuid(context, f"{prefix}.job_result", state),
        operation=operation,
        handler=f"synthetic.result.{_key(context, f'{prefix}.job_result', state)}",
        deduplication_key_hash=_physical_digest(context, f"{prefix}.job_result", state, "key"),
        payload_hash=canonical_sha256({"factory": "job-result", "state": state}),
        payload={"result": "[REDACTED]" if state == "privacy_redaction" else "synthetic-result"},
        status="failed" if state in {"invalid_rejected", "privacy_redaction"} else "succeeded",
        attempt_count=1,
        max_attempts=3,
        available_at=context.frozen_at,
        next_wakeup_at=context.frozen_at + timedelta(minutes=1),
        last_error_code=(
            "synthetic_terminal_failure"
            if state in {"invalid_rejected", "privacy_redaction"}
            else ""
        ),
        completed_at=context.frozen_at,
    )
    heartbeat = Heartbeat.objects.create(
        worker_id=f"synthetic-{_key(context, f'{prefix}.worker_heartbeat', state)}",
        lease_token=_uuid(context, f"{prefix}.worker_heartbeat", state),
        started_at=context.frozen_at,
        heartbeat_at=context.frozen_at,
        expires_at=context.frozen_at + timedelta(minutes=1),
        metadata={"kind": "synthetic"},
    )
    scheduler, _created = SchedulerLease.objects.get_or_create(key="default")
    operation_value: object = operation
    if state == "invalid_rejected":
        invalid = Operation(kind="Invalid kind", progress_total=0)
        try:
            invalid.full_clean()
        except ValidationError:
            operation_value = _rejected("core.operation", "invalid_operation_kind")
        else:
            operation_value = _rejected("core.operation", "service_rejects_invalid_kind")
    return {
        f"{prefix}.audit_event": audit,
        f"{prefix}.operational_setting": setting,
        f"{prefix}.idempotency_record": idempotency,
        f"{prefix}.operation": operation_value,
        f"{prefix}.operation_revision_conflict": operation,
        f"{prefix}.durable_job": job,
        f"{prefix}.job_lease": lease_job,
        f"{prefix}.job_result": result_job,
        f"{prefix}.worker_heartbeat": heartbeat,
        f"{prefix}.scheduler_lease": scheduler,
    }


def _messaging(context: FactoryContext, state: str) -> dict[str, object]:
    prefix = "provider_neutral_messaging"
    mailbox = CaptureMailbox()
    captured = mailbox.send(
        purpose=f"synthetic-{state}",
        recipient=context.synthetic_email(f"{prefix}.captured_message", state),
        subject="Synthetic subject",
        body="Synthetic body",
        outcome=(
            SimulatedOutcome.AMBIGUOUS if state == "stale_conflict" else SimulatedOutcome.ACCEPTED
        ),
    )
    values: dict[str, object] = {f"{prefix}.captured_message": captured}
    leaf_outcomes = dict(
        zip(
            (leaf for leaf in BUNDLE_LEAVES[prefix] if leaf != "captured_message"),
            (outcome.value for outcome in SimulatedOutcome),
            strict=True,
        )
    )
    for attempt, leaf in enumerate(BUNDLE_LEAVES[prefix], start=1):
        if leaf == "captured_message":
            continue
        values[f"{prefix}.{leaf}"] = ProviderSimulatorValue(
            kind=leaf,
            outcome=leaf_outcomes[leaf],
            attempt=attempt,
        )
    if state == "invalid_rejected":
        values[f"{prefix}.captured_message"] = _rejected(
            "messaging.captured_message",
            "unreserved_recipient",
        )
    return values
