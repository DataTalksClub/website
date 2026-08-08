"""Provider-neutral, fail-closed authorization for test-only high-risk fixtures."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NoReturn, Protocol

from django.conf import settings
from django.utils import timezone

from core.audit import AuditWriteContext, record_audit_event
from core.capabilities import (
    Capability,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
    validate_capability,
)
from core.idempotency import (
    IdempotencyConflict,
    JsonObject,
    execute_idempotent,
    hash_idempotency_key,
)
from core.models import AuditEvent
from core.redaction import is_sensitive_text, redact
from core.services import ServiceContext


class HighRiskDenied(PermissionError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("high-risk authorization denied")


_CAPABILITY_KEY = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class HighRiskEvidence:
    capability_key: str
    actor_id: Any
    session_id: uuid.UUID
    authenticated_at: datetime
    target_revision: int
    scope: str
    expected_count: int
    impact: str
    confirmed: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.capability_key, str)
            or _CAPABILITY_KEY.fullmatch(self.capability_key) is None
        ):
            raise ValueError("capability key is invalid")
        if (
            not isinstance(self.actor_id, int)
            or isinstance(self.actor_id, bool)
            or self.actor_id < 1
        ):
            raise ValueError("actor ID is invalid")
        if not isinstance(self.session_id, uuid.UUID):
            raise ValueError("session evidence is invalid")
        if not isinstance(self.authenticated_at, datetime) or not timezone.is_aware(
            self.authenticated_at
        ):
            raise ValueError("authentication evidence is invalid")
        if (
            not isinstance(self.target_revision, int)
            or isinstance(self.target_revision, bool)
            or self.target_revision < 1
        ):
            raise ValueError("target revision must be positive")
        if (
            not isinstance(self.expected_count, int)
            or isinstance(self.expected_count, bool)
            or not 1 <= self.expected_count <= 10_000
        ):
            raise ValueError("expected count is outside fixture bounds")
        for name, value in (("scope", self.scope), ("impact", self.impact)):
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 128
                or is_sensitive_text(value)
            ):
                raise ValueError(f"{name} must be a safe bounded fixture value")
        if not isinstance(self.confirmed, bool):
            raise ValueError("confirmation evidence is invalid")


@dataclass(frozen=True, slots=True)
class HighRiskRequest:
    evidence: HighRiskEvidence
    preview_evidence: HighRiskEvidence
    idempotency_key: str
    cancelled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, HighRiskEvidence) or not isinstance(
            self.preview_evidence, HighRiskEvidence
        ):
            raise ValueError("high-risk evidence is invalid")
        if not isinstance(self.cancelled, bool):
            raise ValueError("cancellation evidence is invalid")


@dataclass(frozen=True, slots=True)
class HighRiskDecision:
    allowed: bool
    reason: str


class HighRiskPolicyAdapter(Protocol):
    policy_ref: str

    def authorize(self, request: HighRiskRequest) -> HighRiskDecision: ...


class DeterministicHighRiskPolicy:
    """An injected test policy; its clock threshold is not a production freshness choice."""

    policy_ref = "fixture.explicit-confirmation"

    def __init__(
        self,
        *,
        expected: HighRiskEvidence,
        fixture_not_before: datetime,
        unavailable: bool = False,
    ) -> None:
        self.expected = expected
        self.fixture_not_before = fixture_not_before
        self.unavailable = unavailable

    def authorize(self, request: HighRiskRequest) -> HighRiskDecision:
        if self.unavailable:
            raise RuntimeError("fixture policy adapter unavailable")
        if request.cancelled:
            return HighRiskDecision(False, "cancelled")
        if not request.evidence.confirmed:
            return HighRiskDecision(False, "confirmation_missing")
        if request.evidence.authenticated_at < self.fixture_not_before:
            return HighRiskDecision(False, "fixture_session_stale")
        if request.preview_evidence != request.evidence:
            return HighRiskDecision(False, "preview_mismatch")
        if request.evidence != self.expected:
            return HighRiskDecision(False, "evidence_mismatch")
        return HighRiskDecision(True, "allowed")


def _context_actor_id(context: ServiceContext) -> int | None:
    actor_ref = context.actor_ref or ""
    if not actor_ref.startswith("user:"):
        return None
    raw_id = actor_ref.removeprefix("user:")
    if not raw_id.isascii() or not raw_id.isdecimal() or raw_id.startswith("0"):
        return None
    return int(raw_id)


def _audit_context(context: ServiceContext) -> AuditWriteContext:
    return AuditWriteContext.from_service_context(
        context,
        actor_id=_context_actor_id(context),
    )


def _record_attempt(
    *,
    capability: Capability,
    request: HighRiskRequest,
    context: ServiceContext,
    outcome: str,
    reason: str,
    replayed: bool = False,
) -> AuditEvent:
    key_hash = ""
    try:
        key_hash = hash_idempotency_key(capability.key, request.idempotency_key)
    except (AttributeError, TypeError, ValueError):
        pass
    audit_context = _audit_context(context)
    audit_context = AuditWriteContext(
        actor_id=audit_context.actor_id,
        actor_ref=audit_context.actor_ref,
        execution=audit_context.execution,
        idempotency_key_hash=key_hash,
    )
    redacted_metadata = redact(
        {
            "reason": reason,
            "replayed": replayed,
            "capability": request.evidence.capability_key,
            "target_revision": request.evidence.target_revision,
            "scope": request.evidence.scope,
            "expected_count": request.evidence.expected_count,
            "impact": request.evidence.impact,
        },
        canaries=getattr(settings, "STUDIO_AUDIT_REDACTION_CANARIES", ()),
    )
    if not isinstance(redacted_metadata, Mapping):
        redacted_metadata = {}
    return record_audit_event(
        action=capability.audit_action,
        target_type="fixture.high_risk",
        outcome=outcome,
        context=audit_context,
        changes={},
        metadata=redacted_metadata,
    )


def _deny(
    *,
    capability: Capability,
    request: HighRiskRequest,
    context: ServiceContext,
    reason: str,
) -> NoReturn:
    _record_attempt(
        capability=capability,
        request=request,
        context=context,
        outcome=AuditEvent.Outcome.DENIED,
        reason=reason,
    )
    raise HighRiskDenied(reason)


def execute_test_only_high_risk(
    *,
    capability: Capability,
    request: HighRiskRequest,
    policy: HighRiskPolicyAdapter | None,
    context: ServiceContext,
) -> JsonObject:
    """Authorize, execute the registered service once, and audit every attempt."""

    capability_errors = validate_capability(capability)
    if (
        capability_errors
        or not capability.test_only
        or capability.service_kind is not ServiceKind.COMMAND
        or capability.idempotency is not IdempotencyPolicy.REQUIRED
        or capability.concurrency is ConcurrencyPolicy.NONE
    ):
        _deny(capability=capability, request=request, context=context, reason="not_test_only")
    if request.evidence.capability_key != capability.key:
        _deny(capability=capability, request=request, context=context, reason="capability_mismatch")
    if request.evidence.actor_id != _context_actor_id(context):
        _deny(capability=capability, request=request, context=context, reason="actor_mismatch")
    if capability.high_risk_policy is None:
        _deny(capability=capability, request=request, context=context, reason="policy_absent")
    try:
        hash_idempotency_key(capability.key, request.idempotency_key)
    except (AttributeError, TypeError, ValueError):
        _deny(
            capability=capability,
            request=request,
            context=context,
            reason="invalid_idempotency",
        )
    if policy is None:
        _deny(capability=capability, request=request, context=context, reason="policy_unresolved")
    try:
        policy_ref = policy.policy_ref
    except Exception:
        _deny(capability=capability, request=request, context=context, reason="policy_error")
    if not isinstance(policy_ref, str) or policy_ref != capability.high_risk_policy:
        _deny(capability=capability, request=request, context=context, reason="policy_unresolved")
    try:
        decision = policy.authorize(request)
    except Exception:
        _deny(capability=capability, request=request, context=context, reason="policy_error")
    if (
        not isinstance(decision, HighRiskDecision)
        or not isinstance(decision.allowed, bool)
        or not isinstance(decision.reason, str)
        or not decision.reason
        or len(decision.reason) > 128
        or is_sensitive_text(decision.reason)
    ):
        _deny(capability=capability, request=request, context=context, reason="policy_error")
    if decision.allowed is not True:
        _deny(capability=capability, request=request, context=context, reason=decision.reason)

    payload: JsonObject = {
        "capability": request.evidence.capability_key,
        "actor_id": str(request.evidence.actor_id),
        "session_id": str(request.evidence.session_id),
        "authenticated_at": request.evidence.authenticated_at.isoformat(),
        "target_revision": request.evidence.target_revision,
        "scope": request.evidence.scope,
        "expected_count": request.evidence.expected_count,
        "impact": request.evidence.impact,
        "confirmed": request.evidence.confirmed,
    }

    def audited_command() -> JsonObject:
        _record_attempt(
            capability=capability,
            request=request,
            context=context,
            outcome=AuditEvent.Outcome.SUCCEEDED,
            reason="allowed",
        )
        return capability.service(request.evidence, context=context)

    try:
        result = execute_idempotent(
            scope=capability.key,
            key=request.idempotency_key,
            request=payload,
            command=audited_command,
        )
    except IdempotencyConflict:
        _deny(capability=capability, request=request, context=context, reason="replay_conflict")
    except Exception:
        _record_attempt(
            capability=capability,
            request=request,
            context=context,
            outcome=AuditEvent.Outcome.FAILED,
            reason="service_failed",
        )
        raise
    if result.replayed:
        _record_attempt(
            capability=capability,
            request=request,
            context=context,
            outcome=AuditEvent.Outcome.SUCCEEDED,
            reason="replayed",
            replayed=True,
        )
    return result.value
