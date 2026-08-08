from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.context import AuditContext as ExecutionAuditContext
from core.context import current_context, validate_context_id
from core.idempotency import JsonObject, canonical_json_object
from core.models import AuditEvent
from core.services import ServiceContext, validate_actor_ref

_AUDIT_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_IP_CLASSES = frozenset({"", "public", "private", "loopback", "unknown"})


@dataclass(frozen=True, slots=True)
class AuditWriteContext:
    """Writer-only provenance composed with the canonical execution context."""

    actor_id: Any | None = None
    actor_ref: str = ""
    execution: ExecutionAuditContext | None = None
    idempotency_key_hash: str = ""
    source_ip_class: str = ""

    @classmethod
    def from_service_context(
        cls,
        service_context: ServiceContext,
        *,
        actor_id: Any | None = None,
        idempotency_key_hash: str = "",
        source_ip_class: str = "",
    ) -> AuditWriteContext:
        """Bridge safe service metadata into audit attribution, never authorization."""

        return cls(
            actor_id=actor_id,
            actor_ref=service_context.actor_ref or "",
            execution=ExecutionAuditContext(
                request_id=service_context.request_id,
                correlation_id=service_context.correlation_id,
                job_id=service_context.job_id,
            ),
            idempotency_key_hash=idempotency_key_hash,
            source_ip_class=source_ip_class,
        )

    def validated(self) -> AuditWriteContext:
        execution = self.execution or current_context()
        for name, value in (
            ("request_id", execution.request_id),
            ("correlation_id", execution.correlation_id),
            ("job_id", execution.job_id),
        ):
            if value is not None:
                validate_context_id(name, value)
        if self.actor_ref:
            validate_actor_ref(self.actor_ref)
        if self.idempotency_key_hash and not _SHA256.fullmatch(self.idempotency_key_hash):
            raise ValueError("idempotency_key_hash must be a lowercase SHA-256 digest")
        if self.source_ip_class not in _SOURCE_IP_CLASSES:
            raise ValueError("source_ip_class is not an allowlisted classification")
        return self


def _redacted_json_object(value: Mapping[str, Any]) -> JsonObject:
    # Import lazily so audit persistence remains independent of request middleware.
    # core.redaction is the single shared policy used by logs, jobs, and audit events.
    from core.redaction import redact_value

    redacted = redact_value(value)
    return canonical_json_object(redacted)


def _redacted_label(value: str) -> str:
    from core.redaction import redact_value

    redacted = redact_value(value)
    if not isinstance(redacted, str):
        raise ValueError("redaction policy returned a non-string target label")
    return redacted[:255]


def record_audit_event(
    *,
    action: str,
    target_type: str,
    outcome: str,
    context: AuditWriteContext | None = None,
    target_id: uuid.UUID | None = None,
    target_label: str = "",
    changes: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    using: str = "default",
) -> AuditEvent:
    """Append one redacted audit event using explicit, already-safe provenance IDs."""

    if not _AUDIT_IDENTIFIER.fullmatch(action):
        raise ValueError("audit action must be a stable lowercase identifier")
    if not _AUDIT_IDENTIFIER.fullmatch(target_type):
        raise ValueError("audit target type must be a stable lowercase identifier")
    if outcome not in AuditEvent.Outcome.values:
        raise ValueError("audit outcome is invalid")
    context = context or AuditWriteContext()
    context.validated()
    execution = context.execution or current_context()

    return AuditEvent.objects.using(using).create(
        actor_id=context.actor_id,
        actor_ref=context.actor_ref,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_label=_redacted_label(target_label),
        outcome=outcome,
        request_id=execution.request_id or "",
        correlation_id=execution.correlation_id or "",
        job_id=execution.job_id or "",
        idempotency_key_hash=context.idempotency_key_hash,
        changes=_redacted_json_object(changes or {}),
        metadata=_redacted_json_object(metadata or {}),
        source_ip_class=context.source_ip_class,
    )
