"""Declarations for credential lifecycle adapters that exist only in test URLConfs."""

from core.capabilities import (
    AdapterMetadata,
    Capability,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
)

_SAFE_FIELDS = frozenset(
    {
        "confirmed",
        "expected_revision",
        "name",
        "overlap_seconds",
        "scopes",
        "target_principal_id",
        "items",
    }
)


def _fixture_object_scope(actor: object, queryset):
    from core.models import Operation

    from .models import APICredential, APIPrincipal

    if not isinstance(actor, APIPrincipal) or actor.kind != APIPrincipal.Kind.HUMAN:
        return queryset.none()
    if queryset.model is APIPrincipal:
        return queryset.filter(kind=APIPrincipal.Kind.SERVICE, is_active=True)
    if queryset.model is APICredential:
        return queryset.filter(
            principal__kind=APIPrincipal.Kind.SERVICE,
            principal__is_active=True,
        )
    if queryset.model is Operation:
        return queryset.filter(api_principal=actor)
    return queryset.none()


def _fixture_object_policy(actor: object, target: object) -> bool:
    from core.models import Operation

    from .models import APICredential, APIPrincipal

    if not isinstance(actor, APIPrincipal) or actor.kind != APIPrincipal.Kind.HUMAN:
        return False
    if isinstance(target, Operation):
        return target.api_principal_id == actor.id
    principal = target.principal if isinstance(target, APICredential) else target
    return bool(
        isinstance(principal, APIPrincipal)
        and principal.kind == APIPrincipal.Kind.SERVICE
        and principal.is_active
    )


def _fixture_field_policy(actor: object, field: str) -> bool:
    from .models import APIPrincipal

    return bool(
        isinstance(actor, APIPrincipal)
        and actor.kind == APIPrincipal.Kind.HUMAN
        and field in _SAFE_FIELDS
    )


def _authorized_fixture(evidence: object, *, context: object) -> dict[str, bool]:
    del evidence, context
    return {"authorized": True}


def _factory() -> dict[str, bool]:
    return {"authorized": True}


def _capability(
    key: str,
    action: str,
    route: str,
    operation_id: str,
    writable_fields: tuple[str, ...],
) -> Capability:
    return Capability(
        key=key,
        description=f"Test-only credential {action} lifecycle adapter",
        service_kind=ServiceKind.COMMAND,
        service=_authorized_fixture,
        django_permission="core.execute_high_risk_fixture",
        studio=AdapterMetadata(
            route=f"/studio/_fixtures/credentials/{action}/",
            method="POST",
            operation_id=f"fixture.credentials.{action}.html",
            test_only=True,
            scopes=(key,),
            request_schema="CredentialFixtureRequest",
            result_schema="CredentialFixtureResult",
            writable_fields=writable_fields,
            rate_class="write",
            rate_cost=5,
        ),
        admin_api=AdapterMetadata(
            route=route,
            method="POST",
            operation_id=operation_id,
            test_only=True,
            scopes=(key,),
            request_schema="CredentialFixtureRequest",
            result_schema="CredentialFixtureResult",
            writable_fields=writable_fields,
            rate_class="write",
            rate_cost=5,
        ),
        idempotency=IdempotencyPolicy.REQUIRED,
        concurrency=ConcurrencyPolicy.REVISION,
        audit_action=f"management.credential.{action}.fixture",
        redacted_fields=("authorization", "token", "secret", "digest", "email"),
        test_factory=_factory,
        object_policy=_fixture_object_policy,
        object_scope=_fixture_object_scope,
        field_policy=_fixture_field_policy,
        high_risk_policy="fixture.explicit-confirmation",
        test_only=True,
    )


CREDENTIAL_CREATE_FIXTURE = _capability(
    "management.credentials.create.fixture",
    "create",
    "/api/v1/admin/_fixtures/credentials",
    "fixture.credentials.create",
    ("target_principal_id", "name", "scopes", "confirmed"),
)
CREDENTIAL_ROTATE_FIXTURE = _capability(
    "management.credentials.rotate.fixture",
    "rotate",
    "/api/v1/admin/_fixtures/credentials/{credential_id}/rotate",
    "fixture.credentials.rotate",
    ("expected_revision", "overlap_seconds", "confirmed"),
)
CREDENTIAL_REVOKE_FIXTURE = _capability(
    "management.credentials.revoke.fixture",
    "revoke",
    "/api/v1/admin/_fixtures/credentials/{credential_id}/revoke",
    "fixture.credentials.revoke",
    ("expected_revision", "confirmed"),
)

BULK_FIXTURE = Capability(
    key="management.bulk.fixture",
    description="Test-only bounded bulk operation adapter",
    service_kind=ServiceKind.COMMAND,
    service=_authorized_fixture,
    django_permission="core.execute_high_risk_fixture",
    studio=AdapterMetadata(
        route="/studio/_fixtures/bulk/",
        method="POST",
        operation_id="fixture.bulk.html",
        test_only=True,
        scopes=("management.bulk.fixture",),
        request_schema="BulkFixtureRequest",
        result_schema="OperationFixtureResult",
        writable_fields=("items", "confirmed"),
        rate_class="write",
        rate_cost=10,
        success_status=202,
        operation_behavior="resource",
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/_fixtures/bulk",
        method="POST",
        operation_id="fixture.bulk.create",
        test_only=True,
        scopes=("management.bulk.fixture",),
        request_schema="BulkFixtureRequest",
        result_schema="OperationFixtureResult",
        writable_fields=("items", "confirmed"),
        rate_class="write",
        rate_cost=10,
        success_status=202,
        operation_behavior="resource",
    ),
    idempotency=IdempotencyPolicy.REQUIRED,
    concurrency=ConcurrencyPolicy.REVISION,
    audit_action="management.bulk.fixture",
    redacted_fields=("authorization", "token", "secret", "digest", "email"),
    test_factory=_factory,
    field_policy=_fixture_field_policy,
    high_risk_policy="fixture.explicit-confirmation",
    test_only=True,
)

OPERATION_DETAIL_FIXTURE = Capability(
    key="management.operations.detail.fixture",
    description="Test-only principal-scoped operation detail adapter",
    service_kind=ServiceKind.QUERY,
    service=_authorized_fixture,
    django_permission="core.execute_high_risk_fixture",
    studio=AdapterMetadata(
        route="/studio/_fixtures/operations/{operation_id}/",
        method="GET",
        operation_id="fixture.operations.detail.html",
        test_only=True,
        scopes=("management.operations.detail.fixture",),
        result_schema="OperationFixtureResult",
        rate_class="read",
        rate_cost=1,
        operation_behavior="resource",
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/_fixtures/operations/{operation_id}",
        method="GET",
        operation_id="fixture.operations.detail",
        test_only=True,
        scopes=("management.operations.detail.fixture",),
        result_schema="OperationFixtureResult",
        rate_class="read",
        rate_cost=1,
        operation_behavior="resource",
    ),
    idempotency=IdempotencyPolicy.NONE,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action="management.operation.detail.fixture",
    redacted_fields=("authorization", "token", "secret", "digest", "email"),
    test_factory=_factory,
    object_policy=_fixture_object_policy,
    object_scope=_fixture_object_scope,
    field_policy=_fixture_field_policy,
    test_only=True,
)

OPERATION_CANCEL_FIXTURE = Capability(
    key="management.operations.cancel.fixture",
    description="Test-only principal-scoped operation cancellation adapter",
    service_kind=ServiceKind.COMMAND,
    service=_authorized_fixture,
    django_permission="core.execute_high_risk_fixture",
    studio=AdapterMetadata(
        route="/studio/_fixtures/operations/{operation_id}/cancel/",
        method="POST",
        operation_id="fixture.operations.cancel.html",
        test_only=True,
        scopes=("management.operations.cancel.fixture",),
        request_schema="OperationCancelFixtureRequest",
        result_schema="OperationFixtureResult",
        writable_fields=("confirmed",),
        rate_class="write",
        rate_cost=5,
        operation_behavior="resource",
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/_fixtures/operations/{operation_id}/cancel",
        method="POST",
        operation_id="fixture.operations.cancel",
        test_only=True,
        scopes=("management.operations.cancel.fixture",),
        request_schema="OperationCancelFixtureRequest",
        result_schema="OperationFixtureResult",
        writable_fields=("confirmed",),
        rate_class="write",
        rate_cost=5,
        operation_behavior="resource",
    ),
    idempotency=IdempotencyPolicy.REQUIRED,
    concurrency=ConcurrencyPolicy.IF_MATCH,
    audit_action="management.operation.cancel.fixture",
    redacted_fields=("authorization", "token", "secret", "digest", "email"),
    test_factory=_factory,
    object_policy=_fixture_object_policy,
    object_scope=_fixture_object_scope,
    field_policy=_fixture_field_policy,
    high_risk_policy="fixture.explicit-confirmation",
    test_only=True,
)

CREDENTIAL_FIXTURE_CAPABILITIES = (
    CREDENTIAL_CREATE_FIXTURE,
    CREDENTIAL_ROTATE_FIXTURE,
    CREDENTIAL_REVOKE_FIXTURE,
    BULK_FIXTURE,
    OPERATION_DETAIL_FIXTURE,
    OPERATION_CANCEL_FIXTURE,
)
