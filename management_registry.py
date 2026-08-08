"""Neutral composition root shared by Studio and the management API."""

from __future__ import annotations

from typing import Any

from core.audit_queries import (
    audit_field_policy,
    audit_object_policy,
    audit_object_scope,
    browse_audit_events,
    get_audit_event,
)
from core.capabilities import (
    AdapterMetadata,
    Capability,
    CapabilityRegistry,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
)
from core.management_health import management_health_factory, read_management_health
from management_auth.fixture_capabilities import CREDENTIAL_FIXTURE_CAPABILITIES


def studio_audit_factory() -> dict[str, Any]:
    return {"capability": "studio.audit.browse"}


def studio_audit_detail_factory() -> dict[str, Any]:
    return {"capability": "studio.audit.detail"}


STUDIO_HOME = Capability(
    key="studio.home.read",
    description="View management health and the private Studio workspace shell",
    service_kind=ServiceKind.QUERY,
    service=read_management_health,
    django_permission="core.access_studio",
    studio=AdapterMetadata(
        route="studio:home",
        method="GET",
        operation_id="studio.home.read.html",
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/health",
        method="GET",
        operation_id="admin.health.read",
        scopes=("studio.home.read",),
        result_schema="AdminHealth",
        rate_class="read",
        rate_cost=1,
    ),
    idempotency=IdempotencyPolicy.NONE,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action="studio.home.viewed",
    redacted_fields=("authorization", "cookie", "email"),
    test_factory=management_health_factory,
)

STUDIO_AUDIT = Capability(
    key="studio.audit.browse",
    description="Browse immutable redacted audit evidence",
    service_kind=ServiceKind.QUERY,
    service=browse_audit_events,
    django_permission="core.browse_audit",
    studio=AdapterMetadata(
        route="studio:audit-list",
        method="GET",
        operation_id="studio.audit.browse.html",
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/_fixtures/audit-events",
        method="GET",
        operation_id="studio.audit.browse.api",
        test_only=True,
    ),
    idempotency=IdempotencyPolicy.NONE,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action="audit.browsed",
    redacted_fields=(
        "authorization",
        "credentials",
        "tokens",
        "cookies",
        "request_body",
        "management_links",
        "email",
    ),
    test_factory=studio_audit_factory,
    object_policy=audit_object_policy,
    object_scope=audit_object_scope,
    field_policy=audit_field_policy,
)

STUDIO_AUDIT_DETAIL = Capability(
    key="studio.audit.detail",
    description="View one immutable redacted audit event",
    service_kind=ServiceKind.QUERY,
    service=get_audit_event,
    django_permission="core.browse_audit",
    studio=AdapterMetadata(
        route="studio:audit-detail",
        method="GET",
        operation_id="studio.audit.detail.html",
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/_fixtures/audit-events/{event_id}",
        method="GET",
        operation_id="studio.audit.detail.api",
        test_only=True,
    ),
    idempotency=IdempotencyPolicy.NONE,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action="audit.detail.viewed",
    redacted_fields=STUDIO_AUDIT.redacted_fields,
    test_factory=studio_audit_detail_factory,
    object_policy=audit_object_policy,
    object_scope=audit_object_scope,
    field_policy=audit_field_policy,
)

CAPABILITY_REGISTRY = CapabilityRegistry(
    (
        STUDIO_HOME,
        STUDIO_AUDIT,
        STUDIO_AUDIT_DETAIL,
        *CREDENTIAL_FIXTURE_CAPABILITIES,
    )
)
