"""Application services for Event-owned Q&A provisioning.

This boundary deliberately adapts the DataQnA room-create/idempotency seam
instead of adding a view or model signal.  The Event service creates the local
one-to-one relation and durable intent atomically; the worker re-reads current
rows and invokes the selected server-side backend after commit.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.audit import AuditWriteContext, record_audit_event
from core.models import RevisionConflict
from jobs.clock import database_now
from jobs.dispatch import best_effort_wake, dispatch_after_commit
from jobs.models import DurableJob

from ..models import (
    Event,
    EventQnaCohostInvite,
    EventQnaQuestion,
    EventQnaRateLimit,
    EventQnaSession,
    EventQnaVote,
)
from .backend import BackendSession, get_qna_backend
from .errors import QnaArchived, QnaError, QnaNotFound
from .ids import normalize_cohost_name, normalize_passcode, opaque_id
from .security import (
    constant_time_equals,
    new_passcode,
    participant_digest,
    passcode_digest,
    passcode_matches,
)

PROVISION_HANDLER = "events.qna.provision"
PROVISION_VERSION = 1
MAX_QUESTION_LENGTH = 315
MAX_NAME_LENGTH = 60
QUESTION_EDIT_WINDOW = 300
ARCHIVE_DELETE_DAYS = 7
COHOST_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,47}$")
PUBLIC_EVENT_LIFECYCLES = frozenset({Event.Lifecycle.PUBLISHED, Event.Lifecycle.COMPLETED})
PUBLIC_STATUSES = frozenset({EventQnaQuestion.Status.VISIBLE, EventQnaQuestion.Status.ANSWERED})
QUESTION_STATUSES = frozenset(EventQnaQuestion.Status.values)
DEFAULT_SETTINGS = {
    "listed": True,
    "allow_names": True,
    "require_names": False,
    "answered_placement": EventQnaSession.AnsweredPlacement.SEPARATE,
    "default_sort": EventQnaSession.DefaultSort.POPULAR,
}
_TRANSITIONS: dict[str, set[str]] = {
    EventQnaSession.State.DRAFT: {EventQnaSession.State.OPEN, EventQnaSession.State.ARCHIVED},
    EventQnaSession.State.OPEN: {EventQnaSession.State.CLOSED, EventQnaSession.State.ARCHIVED},
    EventQnaSession.State.CLOSED: {EventQnaSession.State.OPEN, EventQnaSession.State.ARCHIVED},
    EventQnaSession.State.ARCHIVED: {EventQnaSession.State.OPEN, EventQnaSession.State.CLOSED},
}


class EventQnaError(ValueError):
    """A safe Event/Q&A provisioning contract error."""


class EventQnaNotFound(LookupError):
    """The requested Event does not exist."""


class QnaProvisioningState(StrEnum):
    PENDING = "pending"
    RETRYING = "retrying"
    READY = "ready"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class EventQnaProvisioning:
    session: EventQnaSession
    job: DurableJob
    session_created: bool
    job_created: bool


def _coerce_event_id(event_id: uuid.UUID | str) -> uuid.UUID:
    if isinstance(event_id, uuid.UUID):
        if event_id.variant != uuid.RFC_4122:
            raise EventQnaError("event_id_invalid")
        return event_id
    if not isinstance(event_id, str):
        raise EventQnaError("event_id_invalid")
    try:
        parsed = uuid.UUID(event_id)
    except ValueError as exc:
        raise EventQnaError("event_id_invalid") from exc
    if str(parsed) != event_id or parsed.variant != uuid.RFC_4122:
        raise EventQnaError("event_id_invalid")
    return parsed


def provisioning_deduplication_key(event_id: uuid.UUID | str) -> str:
    parsed = _coerce_event_id(event_id)
    return f"event-qna-provision:{parsed}:v{PROVISION_VERSION}"


def provisioning_payload(event_id: uuid.UUID | str) -> dict[str, object]:
    parsed = _coerce_event_id(event_id)
    return {"event_id": str(parsed), "version": PROVISION_VERSION}


def _get_or_create_session(
    event: Event,
    *,
    using: str,
) -> tuple[EventQnaSession, bool]:
    manager = EventQnaSession.objects.using(using)
    session, created = manager.get_or_create(
        event_id=event.id,
        defaults={
            "state": EventQnaSession.State.DRAFT,
            "backend_key": "native",
            "backend_reference": "",
            "revision": 1,
        },
    )
    if not session.backend_key:
        raise EventQnaError("qna_backend_missing")
    # Validate a replayed/imported row before creating a new durable intent.  This
    # keeps a corrupt backend choice blocked instead of silently switching it.
    get_qna_backend(session.backend_key)
    return session, created


def _ensure_provisioning(
    event_id: uuid.UUID,
    *,
    using: str,
    dispatch: bool,
) -> EventQnaProvisioning | EventQnaSession:
    try:
        event = Event.objects.using(using).get(pk=event_id)
    except Event.DoesNotExist as exc:
        raise EventQnaNotFound("event_not_found") from exc

    session, session_created = _get_or_create_session(event, using=using)
    if not dispatch:
        return session

    job, job_created = dispatch_after_commit(
        handler=PROVISION_HANDLER,
        deduplication_key=provisioning_deduplication_key(event.id),
        payload=provisioning_payload(event.id),
        using=using,
    )
    if session.provisioning_job_id not in {None, job.id}:
        raise EventQnaError("qna_provisioning_intent_conflict")
    if session.provisioning_job_id is None:
        updated = (
            EventQnaSession.objects.using(using)
            .filter(pk=session.pk, provisioning_job_id__isnull=True)
            .update(provisioning_job_id=job.id, updated_at=timezone.now())
        )
        if updated == 1:
            session.provisioning_job_id = job.id
        else:
            session.refresh_from_db(using=using)
            if session.provisioning_job_id != job.id:
                raise EventQnaError("qna_provisioning_intent_conflict")
    return EventQnaProvisioning(session, job, session_created, job_created)


def ensure_event_qna(
    event_id: uuid.UUID | str,
    *,
    using: str = DEFAULT_DB_ALIAS,
) -> EventQnaProvisioning:
    """Ensure one draft session and one stable durable provisioning intent.

    Callers may already own an Event transaction; this nested atomic block keeps
    the relation, job row, and on-commit wake part of the same outer commit.
    """

    parsed = _coerce_event_id(event_id)
    with transaction.atomic(using=using):
        result = _ensure_provisioning(parsed, using=using, dispatch=True)
        assert isinstance(result, EventQnaProvisioning)
        return result


def ensure_native_event_qna(
    event_id: uuid.UUID | str,
    *,
    using: str = DEFAULT_DB_ALIAS,
) -> EventQnaSession:
    """Re-read and converge the native session in a leased worker."""

    parsed = _coerce_event_id(event_id)
    with transaction.atomic(using=using):
        session = _ensure_provisioning(parsed, using=using, dispatch=False)
        assert isinstance(session, EventQnaSession)
        backend = get_qna_backend(session.backend_key)
        result: BackendSession = backend.ensure_session(
            event_id=parsed,
            session_id=session.id,
            idempotency_key=provisioning_deduplication_key(parsed),
        )
        if session.backend_reference != result.backend_reference:
            session.backend_reference = result.backend_reference
            session.save(using=using, update_fields=("backend_reference", "updated_at"))
        return session


def provisioning_state(session: EventQnaSession) -> QnaProvisioningState:
    """Return a safe non-secret status for Studio/future management adapters."""

    if session.provisioning_job_id is None:
        return QnaProvisioningState.PENDING
    job = session.provisioning_job
    if job is None:
        return QnaProvisioningState.PENDING
    if job.status == DurableJob.Status.SUCCEEDED:
        return QnaProvisioningState.READY
    if job.status in {
        DurableJob.Status.PENDING,
        DurableJob.Status.RUNNING,
        DurableJob.Status.RETRY_WAIT,
    }:
        return QnaProvisioningState.RETRYING
    return QnaProvisioningState.BLOCKED


def retry_event_qna_provision(
    event_id: uuid.UUID | str,
    *,
    using: str = DEFAULT_DB_ALIAS,
    audit_context: AuditWriteContext | None = None,
) -> EventQnaProvisioning:
    """Reuse a blocked durable intent without creating another session or job."""

    parsed = _coerce_event_id(event_id)
    with transaction.atomic(using=using):
        result = ensure_event_qna(parsed, using=using)
        job = result.job
        if job.status in {DurableJob.Status.FAILED, DurableJob.Status.CANCELLED}:
            now = database_now(using=using)
            reset = (
                DurableJob.objects.using(using)
                .filter(
                    id=job.id,
                    status__in=(DurableJob.Status.FAILED, DurableJob.Status.CANCELLED),
                )
                .update(
                    status=DurableJob.Status.PENDING,
                    attempt_count=0,
                    available_at=now,
                    next_wakeup_at=now,
                    lease_token=None,
                    lease_expires_at=None,
                    claimed_by="",
                    last_error_code="",
                    completed_at=None,
                    updated_at=now,
                )
            )
            if reset == 1:
                transaction.on_commit(
                    lambda: best_effort_wake(job.id, using=using),
                    using=using,
                    robust=True,
                )
        job.refresh_from_db(using=using)
        if audit_context is not None:
            _audit(
                "events.qna.provision_retry_requested",
                result.session,
                audit_context=audit_context,
                metadata={"job_id": str(job.id), "status": job.status},
                using=using,
            )
        return EventQnaProvisioning(
            result.session,
            job,
            result.session_created,
            result.job_created,
        )


# ---------------------------------------------------------------------------
# Native Q&A domain services.  These functions are the only place where the
# public, Studio, management API, and durable-job adapters mutate Q&A rows.


def _qna_session(event_id: uuid.UUID | str, *, using: str = DEFAULT_DB_ALIAS) -> EventQnaSession:
    try:
        return (
            EventQnaSession.objects.using(using)
            .select_related("event")
            .get(event_id=_coerce_event_id(event_id))
        )
    except (EventQnaSession.DoesNotExist, Event.DoesNotExist) as exc:
        raise QnaNotFound() from exc


def _public_session(event_id: uuid.UUID | str, *, using: str = DEFAULT_DB_ALIAS) -> EventQnaSession:
    session = _qna_session(event_id, using=using)
    if session.event.lifecycle not in PUBLIC_EVENT_LIFECYCLES:
        raise QnaNotFound()
    if session.state == EventQnaSession.State.ARCHIVED:
        raise QnaArchived()
    if session.state == EventQnaSession.State.DRAFT:
        raise QnaNotFound()
    return session


def _refresh_expiry(session: EventQnaSession) -> EventQnaSession:
    if (
        session.state == EventQnaSession.State.OPEN
        and session.expires_at is not None
        and session.expires_at <= timezone.now()
    ):
        _transition_locked(session, EventQnaSession.State.CLOSED)
    return session


def _bump_session(session: EventQnaSession, *, fields: tuple[str, ...] = ()) -> None:
    using = session._state.db or DEFAULT_DB_ALIAS
    previous_revision = session.revision
    session.revision = previous_revision + 1
    session.updated_at = timezone.now()
    values = {field: getattr(session, field) for field in fields}
    values.update(revision=session.revision, updated_at=session.updated_at)
    updated = (
        EventQnaSession.objects.using(using)
        .filter(pk=session.pk, revision=previous_revision)
        .update(**values)
    )
    if updated != 1:
        raise RevisionConflict(expected=previous_revision, actual=session.revision)


def _transition_locked(session: EventQnaSession, target: str) -> EventQnaSession:
    current = session.state
    if target == current:
        return session
    if target not in _TRANSITIONS.get(current, set()):
        raise QnaError(
            400,
            "invalid_transition",
            f"Cannot move a Q&A session from {current} to {target}.",
        )
    now = timezone.now()
    session.state = target
    session.state_changed_at = now
    session.archive_delete_at = (
        now + timedelta(days=ARCHIVE_DELETE_DAYS)
        if target == EventQnaSession.State.ARCHIVED
        else None
    )
    _bump_session(session, fields=("state", "state_changed_at", "archive_delete_at"))
    return session


def transition_session(
    event_id: uuid.UUID | str,
    target: str,
    *,
    using: str = DEFAULT_DB_ALIAS,
    audit_context: AuditWriteContext | None = None,
) -> EventQnaSession:
    with transaction.atomic(using=using):
        session = _qna_session(event_id, using=using)
        session = EventQnaSession.objects.using(using).select_related("event").get(pk=session.pk)
        _transition_locked(session, target)
        if audit_context is not None:
            _audit(
                "events.qna.lifecycle_changed",
                session,
                audit_context=audit_context,
                metadata={"state": session.state},
                using=using,
            )
        return session


def _clean_settings(raw: object, base: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise QnaError(400, "invalid_settings", "The Q&A settings are invalid.")
    result = {key: (base or {}).get(key, value) for key, value in DEFAULT_SETTINGS.items()}
    for key, value in raw.items():
        if key not in DEFAULT_SETTINGS:
            raise QnaError(400, "invalid_settings", "The Q&A settings are invalid.")
        if key in {"answered_placement", "default_sort"}:
            allowed = (
                {choice.value for choice in EventQnaSession.AnsweredPlacement}
                if key == "answered_placement"
                else {choice.value for choice in EventQnaSession.DefaultSort}
            )
            if value not in allowed:
                raise QnaError(400, "invalid_settings", "The Q&A settings are invalid.")
        elif not isinstance(value, bool):
            raise QnaError(400, "invalid_settings", "The Q&A settings are invalid.")
        result[key] = value
    return result


def _parse_expiry(value: object) -> Any:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise QnaError(400, "invalid_request", "expires_at must be an RFC 3339 timestamp.")
    parsed = parse_datetime(value.replace("Z", "+00:00"))
    if parsed is None or timezone.is_naive(parsed):
        raise QnaError(400, "invalid_request", "expires_at must be an RFC 3339 timestamp.")
    return parsed


def update_session(
    event_id: uuid.UUID | str,
    payload: dict[str, Any],
    *,
    using: str = DEFAULT_DB_ALIAS,
    actor_role: str = "operator",
    expected_revision: int | None = None,
    audit_context: AuditWriteContext | None = None,
) -> EventQnaSession:
    if actor_role == "cohost":
        # The pinned docs and tests disagree about co-host lifecycle/settings
        # authority.  The accepted website architecture deliberately chooses
        # the conservative rule until product/security resolves that conflict.
        raise QnaError(403, "cohost_scope", "A co-host may moderate questions only.")
    allowed = {"settings", "expires_at", "retention_days", "state"}
    if set(payload) - allowed:
        raise QnaError(400, "invalid_fields", "The Q&A settings are invalid.")
    with transaction.atomic(using=using):
        session = _qna_session(event_id, using=using)
        session = EventQnaSession.objects.using(using).get(pk=session.pk)
        if expected_revision is not None and session.revision != expected_revision:
            raise RevisionConflict(expected=expected_revision, actual=session.revision)
        _refresh_expiry(session)
        if session.state == EventQnaSession.State.ARCHIVED and set(payload) - {"state"}:
            raise QnaError(409, "archived", "An archived session can only be reopened.")
        fields: list[str] = []
        if "settings" in payload:
            settings_value = _clean_settings(
                payload["settings"],
                {
                    "listed": session.listed,
                    "allow_names": session.allow_names,
                    "require_names": session.require_names,
                    "answered_placement": session.answered_placement,
                    "default_sort": session.default_sort,
                },
            )
            session.listed = settings_value["listed"]
            session.allow_names = settings_value["allow_names"]
            session.require_names = settings_value["require_names"]
            session.answered_placement = settings_value["answered_placement"]
            session.default_sort = settings_value["default_sort"]
            fields.extend(
                ["listed", "allow_names", "require_names", "answered_placement", "default_sort"]
            )
        if "expires_at" in payload:
            session.expires_at = _parse_expiry(payload["expires_at"])
            fields.append("expires_at")
        if "retention_days" in payload:
            value = payload["retention_days"]
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise QnaError(400, "invalid_request", "retention_days must be at least 1 or null.")
            session.retention_days = value
            fields.append("retention_days")
        if fields:
            _bump_session(session, fields=tuple(fields))
        if "state" in payload:
            _transition_locked(session, payload["state"])
        if audit_context is not None:
            _audit(
                "events.qna.session_updated",
                session,
                audit_context=audit_context,
                metadata={
                    "state": session.state,
                    "fields": sorted(set(fields) | ({"state"} if "state" in payload else set())),
                },
                using=using,
            )
        return session


def _validate_text(value: object) -> str:
    if not isinstance(value, str):
        raise QnaError(400, "invalid_request", "text must be 1 to 315 characters.")
    text = value.strip()
    if not 1 <= len(text) <= MAX_QUESTION_LENGTH:
        raise QnaError(400, "invalid_request", "text must be 1 to 315 characters.")
    return text


def _validate_name(value: object, session: EventQnaSession) -> str:
    if value in (None, ""):
        name = ""
    elif isinstance(value, str):
        name = value.strip()
    else:
        raise QnaError(400, "invalid_request", "The name is invalid.")
    if len(name) > MAX_NAME_LENGTH:
        raise QnaError(400, "invalid_request", "The name must be 60 characters or fewer.")
    if name and not session.allow_names:
        raise QnaError(400, "names_disabled", "This Q&A session does not accept names.")
    if not name and session.require_names:
        raise QnaError(400, "name_required", "This Q&A session requires a name.")
    return name


def _question_digest(question: EventQnaQuestion) -> str:
    return (
        f"{question.question_id}:{question.text}:{question.status}:{question.score}:"
        f"{int(question.pinned)}:{question.answered_at or ''}"
    )


def can_author_edit(question: EventQnaQuestion, participant: str | None, *, now=None) -> bool:
    if not participant or question.participant_digest != participant_digest(participant):
        return False
    if question.status != EventQnaQuestion.Status.VISIBLE or question.score > 1:
        return False
    current = now or timezone.now()
    return (question.created_at + timedelta(seconds=QUESTION_EDIT_WINDOW)) > current


def submit_question(
    event_id: uuid.UUID | str,
    *,
    text: object,
    author_name: object = None,
    participant: str,
    using: str = DEFAULT_DB_ALIAS,
) -> EventQnaQuestion:
    if not participant:
        raise QnaError(400, "participant_required", "A participant identity is required.")
    with transaction.atomic(using=using):
        session = _qna_session(event_id, using=using)
        session = EventQnaSession.objects.using(using).get(pk=session.pk)
        _refresh_expiry(session)
        if session.state != EventQnaSession.State.OPEN:
            raise QnaError(409, "questions_closed", "This Q&A session is not accepting questions.")
        question = EventQnaQuestion.objects.using(using).create(
            question_id=opaque_id(),
            session=session,
            text=_validate_text(text),
            author_name=_validate_name(author_name, session),
            participant_digest=participant_digest(participant),
            status=EventQnaQuestion.Status.VISIBLE,
            score=0,
        )
        EventQnaVote.objects.using(using).create(
            question=question,
            participant_digest=question.participant_digest,
        )
        question.score = 1
        question.save(using=using, update_fields=("score",))
        session.q_total += 1
        _bump_session(session, fields=("q_total",))
        return question


def _set_status_locked(
    session: EventQnaSession,
    question: EventQnaQuestion,
    target: str,
    *,
    using: str,
) -> EventQnaQuestion:
    if target not in QUESTION_STATUSES:
        raise QnaError(400, "invalid_status", "The question status is invalid.")
    current = question.status
    if current == target:
        return question
    question.status = target
    question.answered_at = timezone.now() if target == EventQnaQuestion.Status.ANSWERED else None
    if target in {EventQnaQuestion.Status.ANSWERED, EventQnaQuestion.Status.DELETED}:
        question.pinned = False
    question.save(using=using, update_fields=("status", "answered_at", "pinned"))
    if current == EventQnaQuestion.Status.ANSWERED:
        session.q_answered = max(0, session.q_answered - 1)
    if target == EventQnaQuestion.Status.ANSWERED:
        session.q_answered += 1
    if current == EventQnaQuestion.Status.DELETED:
        session.q_total += 1
    if target == EventQnaQuestion.Status.DELETED:
        session.q_total = max(0, session.q_total - 1)
    _bump_session(session, fields=("q_total", "q_answered"))
    return question


def _set_pin_locked(
    session: EventQnaSession,
    question: EventQnaQuestion,
    pinned: bool,
    *,
    using: str,
) -> EventQnaQuestion:
    if question.status == EventQnaQuestion.Status.DELETED:
        raise QnaError(409, "deleted", "A deleted question cannot be pinned.")
    if pinned:
        EventQnaQuestion.objects.using(using).filter(
            session=session,
            pinned=True,
        ).exclude(pk=question.pk).update(pinned=False)
    question.pinned = bool(pinned)
    question.save(using=using, update_fields=("pinned",))
    _bump_session(session)
    return question


def update_question(
    event_id: uuid.UUID | str,
    question_id: str,
    payload: dict[str, Any],
    *,
    participant: str | None = None,
    moderator: bool = False,
    using: str = DEFAULT_DB_ALIAS,
    audit_context: AuditWriteContext | None = None,
) -> EventQnaQuestion:
    with transaction.atomic(using=using):
        session = _qna_session(event_id, using=using)
        session = EventQnaSession.objects.using(using).get(pk=session.pk)
        if session.state == EventQnaSession.State.ARCHIVED:
            raise QnaArchived()
        try:
            question = EventQnaQuestion.objects.using(using).get(
                question_id=question_id, session=session
            )
        except EventQnaQuestion.DoesNotExist as exc:
            raise QnaNotFound() from exc
        if not moderator:
            if not can_author_edit(question, participant):
                raise QnaError(403, "forbidden", "This question can no longer be changed.")
            if set(payload) - {"text", "status"} or payload.get("status") not in {None, "deleted"}:
                raise QnaError(403, "forbidden", "You may only edit or withdraw your question.")
        elif set(payload) - {"text", "status", "pinned"}:
            raise QnaError(400, "invalid_fields", "The question update is invalid.")
        if "text" in payload:
            question.text = _validate_text(payload["text"])
            question.save(using=using, update_fields=("text",))
            _bump_session(session)
        if "status" in payload and payload["status"] is not None:
            _set_status_locked(session, question, payload["status"], using=using)
        if "pinned" in payload:
            if not moderator:
                raise QnaError(403, "forbidden", "Only moderators can pin questions.")
            _set_pin_locked(session, question, bool(payload["pinned"]), using=using)
        question.refresh_from_db(using=using)
        if audit_context is not None and moderator:
            _audit(
                "events.qna.question_moderated",
                session,
                audit_context=audit_context,
                metadata={
                    "question_id": question.question_id,
                    "status": question.status,
                    "pinned": question.pinned,
                },
                using=using,
            )
        return question


def vote_question(
    event_id: uuid.UUID | str,
    question_id: str,
    *,
    participant: str,
    add: bool,
    using: str = DEFAULT_DB_ALIAS,
) -> tuple[int, bool]:
    if not participant:
        raise QnaError(400, "participant_required", "A participant identity is required.")
    digest = participant_digest(participant)
    with transaction.atomic(using=using):
        session = _qna_session(event_id, using=using)
        session = EventQnaSession.objects.using(using).get(pk=session.pk)
        _refresh_expiry(session)
        if session.state != EventQnaSession.State.OPEN:
            raise QnaError(409, "voting_closed", "Voting is closed for this Q&A session.")
        try:
            question = EventQnaQuestion.objects.using(using).get(
                question_id=question_id, session=session
            )
        except EventQnaQuestion.DoesNotExist as exc:
            raise QnaNotFound() from exc
        if question.status not in PUBLIC_STATUSES:
            raise QnaNotFound()
        existing = (
            EventQnaVote.objects.using(using)
            .filter(question=question, participant_digest=digest)
            .first()
        )
        if add and existing is None:
            try:
                with transaction.atomic(using=using):
                    EventQnaVote.objects.using(using).create(
                        question=question, participant_digest=digest
                    )
            except IntegrityError:
                pass
            else:
                question.score = F("score") + 1
                question.save(using=using, update_fields=("score",))
        elif not add and existing is not None:
            existing.delete()
            question.score = max(0, question.score - 1)
            question.save(using=using, update_fields=("score",))
        question.refresh_from_db(using=using)
        _bump_session(session)
        voted = (
            EventQnaVote.objects.using(using)
            .filter(question=question, participant_digest=digest)
            .exists()
        )
        return question.score, voted


def _rank_questions(
    questions: list[EventQnaQuestion],
    *,
    sort: str,
    answered_placement: str,
) -> list[EventQnaQuestion]:
    if sort not in {choice.value for choice in EventQnaSession.DefaultSort}:
        raise QnaError(400, "invalid_sort", "sort must be popular or recent.")

    def base(question: EventQnaQuestion) -> tuple[Any, ...]:
        if sort == EventQnaSession.DefaultSort.RECENT:
            return (not question.pinned, -question.created_at.timestamp(), question.question_id)
        return (
            not question.pinned,
            -question.score,
            question.created_at.timestamp(),
            question.question_id,
        )

    ordered = sorted(questions, key=base)
    if answered_placement == EventQnaSession.AnsweredPlacement.BOTTOM:
        ordered.sort(key=lambda question: question.status == EventQnaQuestion.Status.ANSWERED)
    return ordered


def serialize_question(
    question: EventQnaQuestion,
    *,
    participant: str | None = None,
    voted: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "question_id": question.question_id,
        "text": question.text,
        "author_name": question.author_name or None,
        "status": question.status,
        "score": int(question.score),
        "pinned": bool(question.pinned),
        "created_at": question.created_at.isoformat().replace("+00:00", "Z"),
        "answered_at": (
            question.answered_at.isoformat().replace("+00:00", "Z")
            if question.answered_at is not None
            else None
        ),
    }
    if participant is not None:
        result.update(
            {
                "own": question.participant_digest == participant_digest(participant),
                "voted": voted,
                "editable": can_author_edit(question, participant),
            }
        )
    return result


def list_questions(
    event_id: uuid.UUID | str,
    *,
    participant: str | None = None,
    moderator: bool = False,
    sort: str | None = None,
    statuses: set[str] | None = None,
    using: str = DEFAULT_DB_ALIAS,
) -> tuple[list[dict[str, Any]], dict[str, int], str, EventQnaSession]:
    session = _qna_session(event_id, using=using)
    if session.state == EventQnaSession.State.ARCHIVED and not moderator:
        raise QnaArchived()
    if session.state == EventQnaSession.State.DRAFT and not moderator:
        raise QnaNotFound()
    session = _refresh_expiry(session)
    selected_statuses = statuses or (set(QUESTION_STATUSES) if moderator else set(PUBLIC_STATUSES))
    if not selected_statuses.issubset(QUESTION_STATUSES):
        raise QnaError(400, "invalid_status", "The question status is invalid.")
    if not moderator:
        selected_statuses &= PUBLIC_STATUSES
    raw = list(
        EventQnaQuestion.objects.using(using).filter(session=session, status__in=selected_statuses)
    )
    digest = participant_digest(participant) if participant else None
    voted_ids = (
        set(
            EventQnaVote.objects.using(using)
            .filter(question__in=raw, participant_digest=digest)
            .values_list("question_id", flat=True)
        )
        if digest
        else set()
    )
    ordered = _rank_questions(
        raw,
        sort=sort or session.default_sort,
        answered_placement=session.answered_placement,
    )
    items = [
        serialize_question(
            question, participant=participant, voted=question.question_id in voted_ids
        )
        for question in ordered
    ]
    counts = {
        "visible": sum(question.status == EventQnaQuestion.Status.VISIBLE for question in raw),
        "answered": sum(question.status == EventQnaQuestion.Status.ANSWERED for question in raw),
    }
    digest_builder = hashlib.sha256()
    for question in ordered:
        digest_builder.update(_question_digest(question).encode("utf-8"))
    return items, counts, f'W/"{digest_builder.hexdigest()[:20]}"', session


def create_cohost(
    event_id: uuid.UUID | str,
    *,
    name: object = None,
    passcode: object = None,
    actor_ref: str,
    using: str = DEFAULT_DB_ALIAS,
    audit_context: AuditWriteContext | None = None,
) -> dict[str, Any]:
    requested = normalize_cohost_name(name)
    if requested and COHOST_NAME_RE.fullmatch(requested) is None:
        raise QnaError(400, "invalid_name", "The co-host link name is invalid.")
    supplied = str(passcode).strip() if passcode not in (None, "") else new_passcode()
    if len(normalize_passcode(supplied)) < 6:
        raise QnaError(400, "invalid_passcode", "A co-host passcode is too short.")
    with transaction.atomic(using=using):
        session = _qna_session(event_id, using=using)
        session = EventQnaSession.objects.using(using).get(pk=session.pk)
        if session.state == EventQnaSession.State.ARCHIVED:
            raise QnaArchived()
        invite_name = requested
        for _ in range(5):
            if not invite_name:
                invite_name = f"{opaque_id()[:4].lower()}-{opaque_id()[:4].lower()}"
            try:
                invite = EventQnaCohostInvite.objects.using(using).create(
                    invite_id=opaque_id(),
                    session=session,
                    name=invite_name,
                    passcode_digest=passcode_digest(supplied),
                    created_by_ref=actor_ref,
                )
            except IntegrityError:
                if requested:
                    raise QnaError(
                        409,
                        "name_taken",
                        "This session already has that co-host link.",
                    ) from None
                invite_name = ""
                continue
            break
        else:
            raise QnaError(503, "name_exhausted", "A co-host link could not be allocated.")
        if audit_context is not None:
            _audit(
                "events.qna.cohost_created",
                session,
                audit_context=audit_context,
                metadata={"invite_id": invite.invite_id},
                using=using,
            )
        return {
            "invite_id": invite.invite_id,
            "name": invite.name,
            "passcode": supplied,
            "join_url": f"{event_qna_path(session.event)}/cohost/{invite.name}/",
            "created_at": invite.created_at.isoformat().replace("+00:00", "Z"),
        }


def redeem_cohost(
    event_id: uuid.UUID | str,
    name: object,
    passcode: object,
    *,
    using: str = DEFAULT_DB_ALIAS,
) -> tuple[EventQnaCohostInvite | None, str | None]:
    generic = "That link and passcode do not match. Check them with the host."
    try:
        session = _public_session(event_id, using=using)
    except QnaError as error:
        return None, error.message
    normalized_name = normalize_cohost_name(name)
    invite = (
        EventQnaCohostInvite.objects.using(using)
        .filter(session=session, name=normalized_name, revoked_at__isnull=True)
        .first()
    )
    if invite is None or not passcode_matches(passcode, invite.passcode_digest):
        return None, generic
    return invite, None


def cohost_for_request(
    session_id: uuid.UUID | str, token: object, *, using: str = DEFAULT_DB_ALIAS
) -> EventQnaCohostInvite | None:
    from .security import cohost_claim

    claim = cohost_claim(token)
    if claim is None or not constant_time_equals(claim[0], str(session_id)):
        return None
    try:
        parsed_session_id = uuid.UUID(str(session_id))
    except ValueError:
        return None
    return (
        EventQnaCohostInvite.objects.using(using)
        .filter(invite_id=claim[1], session_id=parsed_session_id, revoked_at__isnull=True)
        .first()
    )


def revoke_cohost(
    event_id: uuid.UUID | str,
    invite_id: str,
    *,
    using: str = DEFAULT_DB_ALIAS,
    audit_context: AuditWriteContext | None = None,
) -> None:
    with transaction.atomic(using=using):
        session = _qna_session(event_id, using=using)
        invite = (
            EventQnaCohostInvite.objects.using(using)
            .filter(session=session, invite_id=invite_id, revoked_at__isnull=True)
            .first()
        )
        if invite is None:
            raise QnaNotFound()
        invite.revoked_at = timezone.now()
        invite.save(using=using, update_fields=("revoked_at",))
        if audit_context is not None:
            _audit(
                "events.qna.cohost_revoked",
                session,
                audit_context=audit_context,
                metadata={"invite_id": invite.invite_id},
                using=using,
            )


def bulk_moderate(
    event_id: uuid.UUID | str,
    *,
    action: str,
    question_ids: object,
    using: str = DEFAULT_DB_ALIAS,
    audit_context: AuditWriteContext | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(question_ids, list) or not question_ids or len(question_ids) > 100:
        raise QnaError(400, "invalid_question_ids", "question_ids must contain 1 to 100 IDs.")
    mapping: dict[str, dict[str, Any]] = {
        "answer": {"status": EventQnaQuestion.Status.ANSWERED},
        "delete": {"status": EventQnaQuestion.Status.DELETED},
        "pin": {"pinned": True},
        "unpin": {"pinned": False},
    }
    if action not in mapping:
        raise QnaError(400, "invalid_action", "The moderation action is invalid.")
    results = []
    for question_id in question_ids:
        if not isinstance(question_id, str):
            results.append({"question_id": None, "ok": False, "error": "invalid_id"})
            continue
        try:
            update_question(
                event_id,
                question_id,
                mapping[action],
                moderator=True,
                using=using,
                audit_context=audit_context,
            )
        except QnaError as error:
            results.append({"question_id": question_id, "ok": False, "error": error.code})
        else:
            results.append({"question_id": question_id, "ok": True})
    return results


def admit_rate(
    scope: str, *, window_seconds: int, limit: int, using: str = DEFAULT_DB_ALIAS
) -> int | None:
    """Admit a fixed-window anonymous action and return retry seconds on denial."""

    if not scope or window_seconds < 1 or limit < 1:
        raise ValueError("invalid Q&A rate-limit arguments")
    now = timezone.now()
    epoch = int(now.timestamp())
    bucket = datetime.fromtimestamp(epoch - (epoch % window_seconds), tz=UTC)
    scope_digest = hashlib.sha256(f"{settings.SECRET_KEY}:{scope}".encode()).hexdigest()
    with transaction.atomic(using=using):
        try:
            row = EventQnaRateLimit.objects.using(using).get(
                scope_digest=scope_digest,
                window_seconds=window_seconds,
                window_started_at=bucket,
            )
        except EventQnaRateLimit.DoesNotExist:
            try:
                with transaction.atomic(using=using):
                    row = EventQnaRateLimit.objects.using(using).create(
                        scope_digest=scope_digest,
                        window_seconds=window_seconds,
                        window_started_at=bucket,
                        hits=0,
                    )
            except IntegrityError:
                row = EventQnaRateLimit.objects.using(using).get(
                    scope_digest=scope_digest,
                    window_seconds=window_seconds,
                    window_started_at=bucket,
                )
        if row.hits >= limit:
            return max(1, window_seconds - (epoch % window_seconds))
        updated = (
            EventQnaRateLimit.objects.using(using)
            .filter(pk=row.pk, hits__lt=limit)
            .update(hits=F("hits") + 1)
        )
        if updated == 0:
            return max(1, window_seconds - (epoch % window_seconds))
    return None


def event_qna_path(event: Event) -> str:
    if event.public_id is None:
        raise QnaNotFound()
    return f"/events/{event.public_id}/{event.slug}/qna"


def serialize_session(
    session: EventQnaSession,
    *,
    moderator: bool = False,
    include_questions: bool = False,
    using: str = DEFAULT_DB_ALIAS,
) -> dict[str, Any]:
    event = session.event
    result: dict[str, Any] = {
        "contract": "qna.v1",
        "api_base": f"{event_qna_path(event)}/api",
        "max_length": MAX_QUESTION_LENGTH,
        "session_id": str(session.id),
        "event_id": str(event.id),
        "state": session.state,
        "settings": {
            "listed": session.listed,
            "allow_names": session.allow_names,
            "require_names": session.require_names,
            "answered_placement": session.answered_placement,
            "default_sort": session.default_sort,
        },
        "share_url": f"{settings.CANONICAL_ORIGIN.rstrip('/')}{event_qna_path(event)}/",
        "qr_url": f"{event_qna_path(event)}/qr.svg",
        "present_url": f"/events/{event.public_id}/{event.slug}/qna/present/",
        "expires_at": session.expires_at.isoformat().replace("+00:00", "Z")
        if session.expires_at
        else None,
        "retention_days": session.retention_days,
        "counts": {"questions": int(session.q_total), "answered": int(session.q_answered)},
        "revision": session.revision,
    }
    if moderator:
        result.update(
            {
                "can_ask": session.state == EventQnaSession.State.OPEN,
                "can_vote": session.state == EventQnaSession.State.OPEN,
                "host_links": {
                    "studio": f"/studio/events/{event.id}/qna/",
                    "api": f"{event_qna_path(event)}/api/questions/",
                },
            }
        )
    if include_questions:
        items, counts, etag, _ = list_questions(
            event.id,
            moderator=moderator,
            sort=session.default_sort,
            using=using,
        )
        result.update({"items": items, "counts": counts, "etag": etag})
    return result


def admin_event_qna(event_id: uuid.UUID | str, *, using: str = DEFAULT_DB_ALIAS) -> dict[str, Any]:
    session = _qna_session(event_id, using=using)
    return serialize_session(session, moderator=True, include_questions=True, using=using)


def _audit(
    action: str,
    session: EventQnaSession,
    *,
    audit_context: AuditWriteContext,
    metadata: dict[str, Any],
    using: str,
) -> None:
    # This allowlist intentionally excludes question text, names, participant
    # digests, passcodes, co-host cookies, and share tokens.
    record_audit_event(
        action=action,
        target_type="events.qna_session",
        target_id=session.id,
        target_label="event-qna",
        outcome="succeeded",
        context=audit_context,
        metadata=metadata,
        using=using,
    )
