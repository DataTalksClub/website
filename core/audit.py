from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from community_base.kernel.context import AuditContext as ExecutionAuditContext
from community_base.kernel.context import current_context, validate_context_id
from community_base.kernel.services import ServiceContext, validate_actor_ref

from core.idempotency import JsonObject, canonical_json_object
from core.models import AuditEvent

_AUDIT_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_IP_CLASSES = frozenset({"", "public", "private", "loopback", "unknown"})


@dataclass(frozen=True, slots=True)
class AuditWriteContext:
    """Writer-only provenance composed with the canonical execution context."""

    actor_id: Any | None = None
    api_principal_id: uuid.UUID | None = None
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
        api_principal_id: uuid.UUID | None = None,
        idempotency_key_hash: str = "",
        source_ip_class: str = "",
    ) -> AuditWriteContext:
        """Bridge safe service metadata into audit attribution, never authorization."""

        return cls(
            actor_id=actor_id,
            api_principal_id=api_principal_id,
            actor_ref=service_context.actor_ref or "",
            execution=ExecutionAuditContext(
                request_id=service_context.request_id,
                correlation_id=service_context.correlation_id,
                job_id=service_context.job_id,
            ),
            idempotency_key_hash=idempotency_key_hash,
            source_ip_class=source_ip_class,
        )

    @classmethod
    def for_actor_ref(
        cls, service_context: ServiceContext, *, using: str = "default"
    ) -> AuditWriteContext:
        """Bridge a service context and resolve its actor_ref to the attribution FKs.

        ``user:<pk>`` and ``staff:<pk>`` set ``actor_id``; ``api_principal:<uuid>``
        sets ``api_principal_id`` and the principal's ``actor_id``, the same
        attribution the admin API writes on its own records.  A reference that
        names no existing row (or any other kind, e.g. ``operator:...``) stays in
        ``actor_ref`` only, so a stale reference can never break the write.
        """

        actor_id, api_principal_id = _resolve_actor_ref(service_context.actor_ref or "", using)
        return cls.from_service_context(
            service_context, actor_id=actor_id, api_principal_id=api_principal_id
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
        if self.api_principal_id is not None and not isinstance(self.api_principal_id, uuid.UUID):
            raise ValueError("api_principal_id must be a UUID")
        if self.idempotency_key_hash and not _SHA256.fullmatch(self.idempotency_key_hash):
            raise ValueError("idempotency_key_hash must be a lowercase SHA-256 digest")
        if self.source_ip_class not in _SOURCE_IP_CLASSES:
            raise ValueError("source_ip_class is not an allowlisted classification")
        return self


def _resolve_actor_ref(actor_ref: str, using: str) -> tuple[Any | None, uuid.UUID | None]:
    kind, _, raw = actor_ref.partition(":")
    if kind in {"user", "staff"}:
        if not (raw.isascii() and raw.isdecimal() and not raw.startswith("0")):
            return None, None
        from django.contrib.auth import get_user_model

        user_id = int(raw)
        exists = get_user_model().objects.using(using).filter(pk=user_id).exists()
        return (user_id if exists else None), None
    if kind == "api_principal":
        try:
            principal_id = uuid.UUID(raw)
        except ValueError:
            return None, None
        from django.apps import apps

        principal_model = apps.get_model("management_auth", "APIPrincipal")
        row = (
            principal_model.objects.using(using)
            .filter(pk=principal_id)
            .values_list("pk", "user_id")
            .first()
        )
        if row is None:
            return None, None
        return row[1], principal_id
    return None, None


def _redacted_json_object(value: Mapping[str, Any]) -> JsonObject:
    # Import lazily so audit persistence remains independent of request middleware.
    # community_base.kernel.redaction is the single shared policy for logs, jobs, and audits.
    from community_base.kernel.redaction import redact_value

    redacted = redact_value(value)
    return canonical_json_object(redacted)


def _redacted_label(value: str) -> str:
    from community_base.kernel.redaction import redact_value

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
        api_principal_id=context.api_principal_id,
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
