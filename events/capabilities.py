"""Management capabilities for current Event identities."""

from __future__ import annotations

from typing import Any

from core.capabilities import (
    AdapterMetadata,
    Capability,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
    staff_or_active_principal,
)

from .identity import get_event_identity, list_event_identities


def _factory() -> dict[str, Any]:
    return {"items": [], "page": 1, "page_size": 20, "total_count": 0}


def _identity_capability(*, detail: bool) -> Capability:
    key = "events.identity.detail" if detail else "events.identity.read"
    route = (
        "/api/v1/admin/events/identities/{event_id}"
        if detail
        else "/api/v1/admin/events/identities"
    )
    return Capability(
        key=key,
        description=(
            "Inspect one Event identity" if detail else "Inspect current Event identities"
        ),
        service_kind=ServiceKind.QUERY,
        service=get_event_identity if detail else list_event_identities,
        django_permission="core.access_studio",
        studio=AdapterMetadata(
            route=("studio:event-identity-detail" if detail else "studio:event-identity-list"),
            method="GET",
            operation_id=f"{key}.html",
        ),
        admin_api=AdapterMetadata(
            route=route,
            method="GET",
            operation_id=key,
            scopes=(key,),
            result_schema="EventIdentity" if detail else "EventIdentityList",
            rate_class="read",
            rate_cost=1,
        ),
        idempotency=IdempotencyPolicy.NONE,
        concurrency=ConcurrencyPolicy.NONE,
        audit_action="events.identity.viewed",
        redacted_fields=("authorization", "cookie", "csrfmiddlewaretoken"),
        test_factory=_factory,
        function_policy=staff_or_active_principal,
    )


EVENT_IDENTITY_CAPABILITIES = (
    _identity_capability(detail=False),
    _identity_capability(detail=True),
)
