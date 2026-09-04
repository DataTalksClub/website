"""Production credential-management capability declarations and policies."""

from __future__ import annotations

from core.capabilities import (
    AdapterMetadata,
    Capability,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
)

from .models import APICredential, APIPrincipal
from .policies import HIGH_RISK_FRESH_CONFIRMATION_POLICY
from .services import (
    issue_manageable_credential_once,
    list_manageable_credentials,
    revoke_manageable_credential_once,
    rotate_manageable_credential_once,
)

MANAGE_API_CREDENTIALS = "management_auth.manage_api_credentials"
_REDACTED_FIELDS = (
    "authorization",
    "cookie",
    "csrfmiddlewaretoken",
    "email",
    "password",
    "secret",
    "secret_digest",
    "session",
    "token",
)
_SAFE_CREATE_FIELDS = frozenset(
    {"confirmed", "expires_at", "name", "scopes", "target_principal_id"}
)
_SAFE_ROTATE_FIELDS = frozenset({"confirmed", "expires_at", "overlap_seconds"})
_SAFE_REVOKE_FIELDS = frozenset({"confirmed"})


def _management_function_policy(actor: object, evidence: object) -> bool:
    del evidence
    if isinstance(actor, APIPrincipal):
        return actor.kind == APIPrincipal.Kind.HUMAN and actor.user_id is not None
    return bool(
        getattr(actor, "is_authenticated", False)
        and getattr(actor, "is_active", False)
        and getattr(actor, "is_staff", False)
    )


def _manageable_object_scope(actor: object, queryset):
    if not isinstance(actor, APIPrincipal) or actor.kind != APIPrincipal.Kind.HUMAN:
        return queryset.none()
    if queryset.model is APIPrincipal:
        return queryset.filter(
            kind=APIPrincipal.Kind.SERVICE,
            is_active=True,
            identity_snapshot="service:development-automation",
        )
    if queryset.model is APICredential:
        return queryset.filter(
            principal__kind=APIPrincipal.Kind.SERVICE,
            principal__is_active=True,
            principal__identity_snapshot="service:development-automation",
        )
    return queryset.none()


def _manageable_object_policy(actor: object, target: object) -> bool:
    if not isinstance(actor, APIPrincipal) or actor.kind != APIPrincipal.Kind.HUMAN:
        return False
    principal = target.principal if isinstance(target, APICredential) else target
    return bool(
        isinstance(principal, APIPrincipal)
        and principal.kind == APIPrincipal.Kind.SERVICE
        and principal.is_active
        and principal.identity_snapshot == "service:development-automation"
    )


def _field_policy(allowed: frozenset[str]):
    def policy(actor: object, field: str) -> bool:
        return bool(
            isinstance(actor, APIPrincipal)
            and actor.kind == APIPrincipal.Kind.HUMAN
            and field in allowed
        )

    return policy


def _factory() -> dict[str, object]:
    return {"items": [], "page": 1, "page_size": 20, "total_count": 0}


CREDENTIAL_LIST = Capability(
    key="management.credentials.list",
    description="List safe metadata for manageable service credentials",
    service_kind=ServiceKind.QUERY,
    service=list_manageable_credentials,
    django_permission=MANAGE_API_CREDENTIALS,
    studio=AdapterMetadata(
        route="studio:credential-list",
        method="GET",
        operation_id="management.credentials.list.html",
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/credentials",
        method="GET",
        operation_id="management.credentials.list",
        scopes=("management.credentials.list",),
        result_schema="CredentialList",
        filter_fields=("principal_id",),
        sort_fields=("created_at", "expires_at", "name"),
        paginated=True,
        rate_class="read",
        rate_cost=1,
    ),
    idempotency=IdempotencyPolicy.NONE,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action="management.credential.listed",
    redacted_fields=_REDACTED_FIELDS,
    test_factory=_factory,
    function_policy=_management_function_policy,
    object_policy=_manageable_object_policy,
    object_scope=_manageable_object_scope,
)

CREDENTIAL_CREATE = Capability(
    key="management.credentials.create",
    description="Issue a scoped, expiring credential for a manageable service principal",
    service_kind=ServiceKind.COMMAND,
    service=issue_manageable_credential_once,
    django_permission=MANAGE_API_CREDENTIALS,
    studio=AdapterMetadata(
        route="studio:credential-list",
        method="POST",
        operation_id="management.credentials.create.html",
        writable_fields=tuple(sorted(_SAFE_CREATE_FIELDS)),
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/credentials",
        method="POST",
        operation_id="management.credentials.create",
        scopes=("management.credentials.create",),
        request_schema="CredentialCreateRequest",
        result_schema="CredentialSecret",
        writable_fields=tuple(sorted(_SAFE_CREATE_FIELDS)),
        rate_class="write",
        rate_cost=5,
        success_status=201,
    ),
    idempotency=IdempotencyPolicy.REQUIRED,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action="management.credential.created",
    redacted_fields=_REDACTED_FIELDS,
    test_factory=_factory,
    function_policy=_management_function_policy,
    object_policy=_manageable_object_policy,
    object_scope=_manageable_object_scope,
    field_policy=_field_policy(_SAFE_CREATE_FIELDS),
    high_risk_policy=HIGH_RISK_FRESH_CONFIRMATION_POLICY,
)

CREDENTIAL_ROTATE = Capability(
    key="management.credentials.rotate",
    description="Rotate a manageable credential with bounded optional overlap",
    service_kind=ServiceKind.COMMAND,
    service=rotate_manageable_credential_once,
    django_permission=MANAGE_API_CREDENTIALS,
    studio=AdapterMetadata(
        route="studio:credential-rotate",
        method="POST",
        operation_id="management.credentials.rotate.html",
        writable_fields=tuple(sorted(_SAFE_ROTATE_FIELDS)),
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/credentials/{credential_id}/rotate",
        method="POST",
        operation_id="management.credentials.rotate",
        scopes=("management.credentials.rotate",),
        request_schema="CredentialRotateRequest",
        result_schema="CredentialSecret",
        writable_fields=tuple(sorted(_SAFE_ROTATE_FIELDS)),
        rate_class="write",
        rate_cost=5,
        success_status=201,
    ),
    idempotency=IdempotencyPolicy.REQUIRED,
    concurrency=ConcurrencyPolicy.IF_MATCH,
    audit_action="management.credential.rotated",
    redacted_fields=_REDACTED_FIELDS,
    test_factory=_factory,
    function_policy=_management_function_policy,
    object_policy=_manageable_object_policy,
    object_scope=_manageable_object_scope,
    field_policy=_field_policy(_SAFE_ROTATE_FIELDS),
    high_risk_policy=HIGH_RISK_FRESH_CONFIRMATION_POLICY,
)

CREDENTIAL_REVOKE = Capability(
    key="management.credentials.revoke",
    description="Immediately revoke a manageable service credential",
    service_kind=ServiceKind.COMMAND,
    service=revoke_manageable_credential_once,
    django_permission=MANAGE_API_CREDENTIALS,
    studio=AdapterMetadata(
        route="studio:credential-revoke",
        method="POST",
        operation_id="management.credentials.revoke.html",
        writable_fields=tuple(sorted(_SAFE_REVOKE_FIELDS)),
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/credentials/{credential_id}/revoke",
        method="POST",
        operation_id="management.credentials.revoke",
        scopes=("management.credentials.revoke",),
        request_schema="CredentialRevokeRequest",
        result_schema="CredentialMetadata",
        writable_fields=tuple(sorted(_SAFE_REVOKE_FIELDS)),
        rate_class="write",
        rate_cost=5,
    ),
    idempotency=IdempotencyPolicy.REQUIRED,
    concurrency=ConcurrencyPolicy.IF_MATCH,
    audit_action="management.credential.revoked",
    redacted_fields=_REDACTED_FIELDS,
    test_factory=_factory,
    function_policy=_management_function_policy,
    object_policy=_manageable_object_policy,
    object_scope=_manageable_object_scope,
    field_policy=_field_policy(_SAFE_REVOKE_FIELDS),
    high_risk_policy=HIGH_RISK_FRESH_CONFIRMATION_POLICY,
)

CREDENTIAL_RUNTIME_CAPABILITIES = (
    CREDENTIAL_LIST,
    CREDENTIAL_CREATE,
    CREDENTIAL_ROTATE,
    CREDENTIAL_REVOKE,
)
