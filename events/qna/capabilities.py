"""Capability declarations for the Event-owned Q&A adapters."""

from __future__ import annotations

from typing import Any

from core.capabilities import (
    AdapterMetadata,
    Capability,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
)

from .services import (
    admin_event_qna,
    create_cohost,
    retry_event_qna_provision,
    revoke_cohost,
    update_question,
    update_session,
)

QNA_PERMISSION = "events.manage_event_qna"
_REDACTED = (
    "authorization",
    "cookie",
    "csrfmiddlewaretoken",
    "name",
    "passcode",
    "participant",
    "question",
    "text",
    "token",
)


def _policy(actor: object, evidence: object) -> bool:
    del evidence
    if hasattr(actor, "kind"):
        return bool(getattr(actor, "is_active", False))
    return bool(
        getattr(actor, "is_authenticated", False)
        and getattr(actor, "is_active", False)
        and getattr(actor, "is_staff", False)
    )


def _factory() -> dict[str, Any]:
    return {"contract": "qna.v1", "state": "draft", "items": [], "counts": {}}


def _capability(
    *,
    key: str,
    description: str,
    service: Any,
    method: str,
    studio_route: str,
    api_route: str,
    api_method: str,
    operation_id: str,
    result_schema: str,
    request_schema: str | None = None,
    fields: tuple[str, ...] = (),
    service_kind: ServiceKind | None = None,
    idempotency: IdempotencyPolicy | None = None,
    concurrency: ConcurrencyPolicy = ConcurrencyPolicy.NONE,
) -> Capability:
    command = method not in {"GET", "HEAD"}
    return Capability(
        key=key,
        description=description,
        service_kind=service_kind or (ServiceKind.COMMAND if command else ServiceKind.QUERY),
        service=service,
        django_permission=QNA_PERMISSION,
        studio=AdapterMetadata(
            route=studio_route,
            method=method,
            operation_id=f"{operation_id}.html",
            writable_fields=fields,
        ),
        admin_api=AdapterMetadata(
            route=api_route,
            method=api_method,
            operation_id=operation_id,
            scopes=(key,),
            request_schema=request_schema,
            result_schema=result_schema,
            writable_fields=fields,
            rate_class="write" if command else "read",
            rate_cost=5 if command else 1,
        ),
        idempotency=idempotency
        or (IdempotencyPolicy.REQUIRED if command else IdempotencyPolicy.NONE),
        concurrency=concurrency,
        audit_action=key.replace(".", "_") + ".audit",
        redacted_fields=_REDACTED,
        test_factory=_factory,
        function_policy=_policy,
        field_policy=(lambda actor, field: field in fields) if command else None,
    )


QNA_READ = _capability(
    key="events.qna.read",
    description="Inspect one Event-linked Q&A session and its safe question metadata",
    service=admin_event_qna,
    method="GET",
    studio_route="/studio/events/{event_id}/qna/",
    api_route="/api/v1/admin/events/{event_id}/qna",
    api_method="GET",
    operation_id="events.qna.read",
    result_schema="EventQnaSession",
)

QNA_MANAGE = _capability(
    key="events.qna.manage",
    description="Update an Event-linked Q&A session lifecycle and settings",
    service=update_session,
    method="POST",
    studio_route="/studio/events/{event_id}/qna/update/",
    api_route="/api/v1/admin/events/{event_id}/qna",
    api_method="PATCH",
    operation_id="events.qna.manage",
    result_schema="EventQnaSession",
    request_schema="EventQnaUpdateRequest",
    fields=("settings", "expires_at", "retention_days", "state"),
    idempotency=IdempotencyPolicy.REQUIRED,
    concurrency=ConcurrencyPolicy.IF_MATCH,
)

QNA_MODERATE = _capability(
    key="events.qna.moderate",
    description="Moderate, answer, delete, and pin Event-linked questions",
    service=update_question,
    method="POST",
    studio_route="/studio/events/{event_id}/qna/questions/{question_id}/",
    api_route="/api/v1/admin/events/{event_id}/qna/questions/{question_id}",
    api_method="PATCH",
    operation_id="events.qna.moderate",
    result_schema="EventQnaQuestion",
    request_schema="EventQnaQuestionUpdateRequest",
    fields=("text", "status", "pinned"),
    idempotency=IdempotencyPolicy.REQUIRED,
)

QNA_RETRY = _capability(
    key="events.qna.provision.retry",
    description="Retry a blocked Event-linked Q&A provisioning intent",
    service=retry_event_qna_provision,
    method="POST",
    studio_route="/studio/events/{event_id}/qna/retry/",
    api_route="/api/v1/admin/events/{event_id}/qna/retry",
    api_method="POST",
    operation_id="events.qna.provision.retry",
    result_schema="EventQnaProvisioning",
    request_schema="EventQnaRetryRequest",
    fields=("confirmed",),
)

QNA_COHOST_CREATE = _capability(
    key="events.qna.cohost.create",
    description="Create one revocable Event-linked Q&A co-host grant",
    service=create_cohost,
    method="POST",
    studio_route="/studio/events/{event_id}/qna/cohosts/",
    api_route="/api/v1/admin/events/{event_id}/qna/cohosts",
    api_method="POST",
    operation_id="events.qna.cohost.create",
    result_schema="EventQnaCohostInvite",
    request_schema="EventQnaCohostCreateRequest",
    fields=("name", "passcode"),
)

QNA_COHOST_REVOKE = _capability(
    key="events.qna.cohost.revoke",
    description="Revoke one Event-linked Q&A co-host grant",
    service=revoke_cohost,
    method="POST",
    studio_route="/studio/events/{event_id}/qna/cohosts/{invite_id}/revoke/",
    api_route="/api/v1/admin/events/{event_id}/qna/cohosts/{invite_id}",
    api_method="DELETE",
    operation_id="events.qna.cohost.revoke",
    result_schema="EventQnaCohostRevoke",
    request_schema="EventQnaCohostRevokeRequest",
    fields=("confirmed",),
)

QNA_CAPABILITIES = (
    QNA_READ,
    QNA_MANAGE,
    QNA_MODERATE,
    QNA_RETRY,
    QNA_COHOST_CREATE,
    QNA_COHOST_REVOKE,
)
