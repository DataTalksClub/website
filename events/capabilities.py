"""Code-owned Studio/admin API capabilities for historical totals."""

from __future__ import annotations

from typing import Any

from core.capabilities import (
    AdapterMetadata,
    Capability,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
)

from .identity import get_event_identity, list_event_identities
from .qna.capabilities import QNA_CAPABILITIES
from .services import (
    IMPORT_PERMISSION,
    MAPPING_PERMISSION,
    activate_source,
    cancel_source,
    dry_run_source,
    get_run_detail,
    list_runs,
    registration_total_preview,
    rollback_source,
    stage_registered_source,
    validate_source,
)

_REDACTED = (
    "authorization",
    "cookie",
    "csrfmiddlewaretoken",
    "email",
    "external_event_identifier",
    "filename",
    "name",
    "path",
    "payload",
    "secret",
    "source_reference",
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
    return {"items": [], "page": 1, "page_size": 20, "total_count": 0}


def _adapter(
    *,
    route: str,
    method: str,
    operation_id: str,
    schema: str,
    request_schema: str | None = None,
    fields: tuple[str, ...] = (),
    success_status: int = 200,
) -> AdapterMetadata:
    return AdapterMetadata(
        route=route,
        method=method,
        operation_id=operation_id,
        scopes=(operation_id,),
        request_schema=request_schema,
        result_schema=schema,
        writable_fields=fields,
        rate_class="write" if method not in {"GET", "HEAD"} else "read",
        rate_cost=5 if method not in {"GET", "HEAD"} else 1,
        success_status=success_status,
    )


def _capability(
    *,
    key: str,
    description: str,
    service: Any,
    permission: str,
    studio_route: str,
    studio_method: str,
    api: AdapterMetadata,
    audit_action: str,
    fields: tuple[str, ...] = (),
    concurrency: ConcurrencyPolicy = ConcurrencyPolicy.NONE,
    idempotency: IdempotencyPolicy | None = None,
) -> Capability:
    command = studio_method not in {"GET", "HEAD"}

    def field_policy(actor: object, field: str) -> bool:
        del actor
        return field in fields

    return Capability(
        key=key,
        description=description,
        service_kind=ServiceKind.COMMAND if command else ServiceKind.QUERY,
        service=service,
        django_permission=permission,
        studio=AdapterMetadata(
            route=studio_route,
            method=studio_method,
            operation_id=f"{key}.html",
            writable_fields=fields,
        ),
        admin_api=api,
        idempotency=(
            idempotency
            if idempotency is not None
            else IdempotencyPolicy.REQUIRED
            if command
            else IdempotencyPolicy.NONE
        ),
        concurrency=concurrency,
        audit_action=audit_action,
        redacted_fields=_REDACTED,
        test_factory=_factory,
        function_policy=_policy,
        field_policy=field_policy if command else None,
    )


IMPORT_LIST = _capability(
    key="events.historical_registration_import.manage",
    description="List safe historical registration import metadata",
    service=list_runs,
    permission=IMPORT_PERMISSION,
    studio_route="studio:historical-registration-list",
    studio_method="GET",
    api=_adapter(
        route="/api/v1/admin/historical-registration-imports",
        method="GET",
        operation_id="events.historical_registration_import.manage",
        schema="HistoricalRegistrationImportList",
    ),
    audit_action="events.historical_registration_import.listed",
)

IMPORT_CREATE = _capability(
    key="events.historical_registration_import.create",
    description="Stage one registered protected source as aggregate-only evidence",
    service=stage_registered_source,
    permission=IMPORT_PERMISSION,
    studio_route="studio:historical-registration-list",
    studio_method="POST",
    api=_adapter(
        route="/api/v1/admin/historical-registration-imports",
        method="POST",
        operation_id="events.historical_registration_import.create",
        schema="HistoricalRegistrationImport",
        request_schema="HistoricalRegistrationImportCreateRequest",
        fields=("mapping_set_revision", "provider", "source_reference"),
        success_status=201,
    ),
    audit_action="events.historical_registration_import.staged",
    fields=("mapping_set_revision", "provider", "source_reference"),
)

IMPORT_DETAIL = _capability(
    key="events.historical_registration_import.detail",
    description="Read safe historical registration reconciliation metadata",
    service=get_run_detail,
    permission=IMPORT_PERMISSION,
    studio_route="studio:historical-registration-detail",
    studio_method="GET",
    api=_adapter(
        route="/api/v1/admin/historical-registration-imports/{run_id}",
        method="GET",
        operation_id="events.historical_registration_import.detail",
        schema="HistoricalRegistrationImportDetail",
    ),
    audit_action="events.historical_registration_import.viewed",
)


def _action_capability(action: str, service: Any) -> Capability:
    return _capability(
        key=f"events.historical_registration_import.{action}",
        description=f"{action.replace('_', ' ').title()} a historical registration import",
        service=service,
        permission=IMPORT_PERMISSION,
        studio_route=f"/studio/events/historical-registration-totals/{{run_id}}/{action}/",
        studio_method="POST",
        api=_adapter(
            route=f"/api/v1/admin/historical-registration-imports/{{run_id}}/{action}",
            method="POST",
            operation_id=f"events.historical_registration_import.{action}",
            schema="HistoricalRegistrationImportActionResult",
            request_schema="HistoricalRegistrationImportActionRequest",
            fields=("confirmed", "reason_code"),
        ),
        audit_action=f"events.historical_registration_import.{action}",
        fields=("confirmed", "reason_code"),
    )


IMPORT_DRY_RUN = _action_capability("dry-run", dry_run_source)
IMPORT_VALIDATE = _action_capability("validate", validate_source)
IMPORT_ACTIVATE = _action_capability("activate", activate_source)
IMPORT_CANCEL = _action_capability("cancel", cancel_source)
IMPORT_ROLLBACK = _action_capability("rollback", rollback_source)

TOTAL_PREVIEW = _capability(
    key="events.historical_registration_total.read",
    description="Read protected registration-total contributions and completeness",
    service=registration_total_preview,
    permission=IMPORT_PERMISSION,
    studio_route="studio:historical-registration-total",
    studio_method="GET",
    api=_adapter(
        route="/api/v1/admin/events/{event_id}/registration-total",
        method="GET",
        operation_id="events.historical_registration_total.read",
        schema="HistoricalRegistrationTotal",
    ),
    audit_action="events.historical_registration_total.viewed",
)

IDENTITY_LIST = _capability(
    key="events.identity.read",
    description="Inspect reviewed Event UUID identities and aliases",
    service=list_event_identities,
    permission=MAPPING_PERMISSION,
    studio_route="studio:event-identity-list",
    studio_method="GET",
    api=_adapter(
        route="/api/v1/admin/events/identities",
        method="GET",
        operation_id="events.identity.read",
        schema="EventIdentityList",
        fields=(),
        success_status=200,
    ),
    audit_action="events.identity.viewed",
)

IDENTITY_DETAIL = _capability(
    key="events.identity.detail",
    description="Inspect one reviewed Event UUID identity and aliases",
    service=get_event_identity,
    permission=MAPPING_PERMISSION,
    studio_route="studio:event-identity-detail",
    studio_method="GET",
    api=_adapter(
        route="/api/v1/admin/events/identities/{event_id}",
        method="GET",
        operation_id="events.identity.detail",
        schema="EventIdentity",
        fields=(),
        success_status=200,
    ),
    audit_action="events.identity.viewed",
)

EVENT_CAPABILITIES = (
    IMPORT_LIST,
    IMPORT_CREATE,
    IMPORT_DETAIL,
    IMPORT_DRY_RUN,
    IMPORT_VALIDATE,
    IMPORT_ACTIVATE,
    IMPORT_CANCEL,
    IMPORT_ROLLBACK,
    TOTAL_PREVIEW,
    IDENTITY_LIST,
    IDENTITY_DETAIL,
    *QNA_CAPABILITIES,
)
