"""Studio/admin API declarations for course registration-count baselines."""

from __future__ import annotations

from typing import Any

from core.capabilities import (
    AdapterMetadata,
    Capability,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
)
from courses.services.registration_counts import (
    MANAGE_PERMISSION,
    activate_source,
    cancel_source,
    dry_run_source,
    get_run_detail,
    list_runs,
    registration_count_preview,
    rollback_source,
    stage_registered_source,
    validate_source,
)

_REDACTED = (
    "authorization",
    "cookie",
    "csrfmiddlewaretoken",
    "email",
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
        paginated=method == "GET" and route.endswith("course-registration-count-imports"),
        rate_class="write" if method != "GET" else "read",
        rate_cost=5 if method != "GET" else 1,
        success_status=success_status,
    )


def _capability(
    *,
    key: str,
    description: str,
    service: Any,
    studio_route: str,
    studio_method: str,
    api: AdapterMetadata,
    audit_action: str,
    fields: tuple[str, ...] = (),
    concurrency: ConcurrencyPolicy = ConcurrencyPolicy.NONE,
) -> Capability:
    command = studio_method == "POST"

    def field_policy(actor: object, field: str) -> bool:
        del actor
        return field in fields

    return Capability(
        key=key,
        description=description,
        service_kind=ServiceKind.COMMAND if command else ServiceKind.QUERY,
        service=service,
        django_permission=MANAGE_PERMISSION,
        studio=AdapterMetadata(
            route=studio_route,
            method=studio_method,
            operation_id=f"{key}.html",
            writable_fields=fields,
        ),
        admin_api=api,
        idempotency=IdempotencyPolicy.REQUIRED if command else IdempotencyPolicy.NONE,
        concurrency=concurrency,
        audit_action=audit_action,
        redacted_fields=_REDACTED,
        test_factory=_factory,
        function_policy=_policy,
        field_policy=field_policy if command else None,
    )


IMPORT_LIST = _capability(
    key="courses.registration_count_baseline.manage",
    description="List safe course registration-count baseline metadata",
    service=list_runs,
    studio_route="studio:course-registration-count-list",
    studio_method="GET",
    api=_adapter(
        route="/api/v1/admin/course-registration-count-imports",
        method="GET",
        operation_id="courses.registration_count_baseline.manage",
        schema="CourseRegistrationCountImportList",
    ),
    audit_action="courses.registration_count_baseline.listed",
)

_CREATE_FIELDS = ("confirmed", "reason_code", "source_reference")
IMPORT_CREATE = _capability(
    key="courses.registration_count_baseline.create",
    description="Stage one registered course count source",
    service=stage_registered_source,
    studio_route="studio:course-registration-count-list",
    studio_method="POST",
    api=_adapter(
        route="/api/v1/admin/course-registration-count-imports",
        method="POST",
        operation_id="courses.registration_count_baseline.create",
        schema="CourseRegistrationCountImport",
        request_schema="CourseRegistrationCountImportCreateRequest",
        fields=_CREATE_FIELDS,
        success_status=201,
    ),
    audit_action="courses.registration_count_baseline.staged",
    fields=_CREATE_FIELDS,
)

IMPORT_DETAIL = _capability(
    key="courses.registration_count_baseline.detail",
    description="Read safe course count source and campaign revisions",
    service=get_run_detail,
    studio_route="studio:course-registration-count-detail",
    studio_method="GET",
    api=_adapter(
        route="/api/v1/admin/course-registration-count-imports/{run_id}",
        method="GET",
        operation_id="courses.registration_count_baseline.detail",
        schema="CourseRegistrationCountImportDetail",
    ),
    audit_action="courses.registration_count_baseline.viewed",
)


def _action(action: str, service: Any) -> Capability:
    fields = ("confirmed", "reason_code")
    return _capability(
        key=f"courses.registration_count_baseline.{action}",
        description=f"{action.replace('-', ' ').title()} a course count source",
        service=service,
        studio_route=(
            f"/studio/courses/registration-count-baselines/{{run_id}}/{action}/"
        ),
        studio_method="POST",
        api=_adapter(
            route=f"/api/v1/admin/course-registration-count-imports/{{run_id}}/{action}",
            method="POST",
            operation_id=f"courses.registration_count_baseline.{action}",
            schema="CourseRegistrationCountActionResult",
            request_schema="CourseRegistrationCountActionRequest",
            fields=fields,
        ),
        audit_action=f"courses.registration_count_baseline.{action.replace('-', '_')}",
        fields=fields,
        concurrency=ConcurrencyPolicy.IF_MATCH,
    )


IMPORT_DRY_RUN = _action("dry-run", dry_run_source)
IMPORT_VALIDATE = _action("validate", validate_source)
IMPORT_ACTIVATE = _action("activate", activate_source)
IMPORT_CANCEL = _action("cancel", cancel_source)
IMPORT_ROLLBACK = _action("rollback", rollback_source)

TOTAL_PREVIEW = _capability(
    key="courses.registration_count_baseline.total",
    description="Read course campaign public-count completeness and contributions",
    service=registration_count_preview,
    studio_route="studio:course-registration-count-total",
    studio_method="GET",
    api=_adapter(
        route="/api/v1/admin/registration-campaigns/{campaign_slug}/public-count",
        method="GET",
        operation_id="courses.registration_count_baseline.total",
        schema="CourseRegistrationPublicCount",
    ),
    audit_action="courses.registration_count_baseline.total_viewed",
)

COURSE_REGISTRATION_COUNT_CAPABILITIES = (
    IMPORT_LIST,
    IMPORT_CREATE,
    IMPORT_DETAIL,
    IMPORT_DRY_RUN,
    IMPORT_VALIDATE,
    IMPORT_ACTIVATE,
    IMPORT_CANCEL,
    IMPORT_ROLLBACK,
    TOTAL_PREVIEW,
)
