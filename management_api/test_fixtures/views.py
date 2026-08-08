from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from django.http import HttpRequest, JsonResponse

from core.high_risk import (
    DeterministicHighRiskPolicy,
    HighRiskDenied,
    HighRiskEvidence,
    HighRiskRequest,
    execute_test_only_high_risk,
)
from core.models import Operation, RevisionConflict, StaffSession
from core.operations import finish_operation, start_operation
from core.services import ServiceContext
from management_api.bulk import bounded_bulk_errors, parse_bulk_items
from management_api.concurrency import require_if_match
from management_api.dispatch import admin_capability
from management_api.errors import APIError
from management_api.json_input import parse_json_object
from management_api.operations import (
    cancel_principal_operation,
    create_principal_operation,
    present_operation,
)
from management_api.policies import enforce_writable_fields, scoped_object_or_404
from management_auth.idempotency import (
    ManagementIdempotencyConflict,
    OneTimeCommandResult,
    SecretUnavailableOnReplay,
    execute_one_time_idempotent,
)
from management_auth.models import APICredential, APIPrincipal
from management_auth.services import (
    CredentialStateConflict,
    issue_credential_once,
    lock_actor_authority,
    revoke_credential_once,
    rotate_credential_once,
)

from .policy import policy_mode


def _idempotency_key(request: HttpRequest) -> str:
    raw = request.META.get("HTTP_IDEMPOTENCY_KEY", "")
    if not isinstance(raw, str) or not raw or "," in raw or len(raw.encode("utf-8")) > 512:
        raise APIError(400, "invalid_idempotency_key", "A valid Idempotency-Key is required.")
    return raw


def _only(request: HttpRequest, payload: dict, fields: set[str]) -> None:
    identity = request.api_identity  # type: ignore[attr-defined]
    capability = request.management_capability  # type: ignore[attr-defined]
    enforce_writable_fields(identity, capability, payload)
    if set(payload) != fields:
        raise APIError(400, "invalid_fields", "The request fields are invalid.")


def _authorize_high_risk(
    request: HttpRequest,
    *,
    target_revision: int,
    scope: str,
    impact: str,
    confirmed: bool,
    expected_count: int = 1,
) -> None:
    identity = request.api_identity  # type: ignore[attr-defined]
    capability = request.management_capability  # type: ignore[attr-defined]
    user = identity.principal.user
    if identity.principal.kind != APIPrincipal.Kind.HUMAN or user is None:
        raise APIError(403, "high_risk_denied", "The fixture high-risk request was denied.")
    session = (
        StaffSession.objects.filter(user=user, revoked_at__isnull=True)
        .order_by("-authenticated_at", "-id")
        .first()
    )
    if session is None:
        raise APIError(403, "high_risk_denied", "The fixture high-risk request was denied.")
    evidence = HighRiskEvidence(
        capability_key=capability.key,
        actor_id=user.pk,
        session_id=session.id,
        authenticated_at=session.authenticated_at,
        target_revision=target_revision,
        scope=scope,
        expected_count=expected_count,
        impact=impact,
        confirmed=confirmed,
    )
    mode = policy_mode()
    selected_capability = (
        replace(capability, high_risk_policy=None) if mode == "absent" else capability
    )
    preview = replace(evidence, expected_count=2) if mode == "mismatch" else evidence
    request_contract = HighRiskRequest(
        evidence=evidence,
        preview_evidence=preview,
        idempotency_key=f"{identity.principal.id}:{_idempotency_key(request)}",
        cancelled=mode == "cancelled",
    )
    policy = None
    if mode not in {"absent", "unresolved"}:
        policy = DeterministicHighRiskPolicy(
            expected=evidence,
            fixture_not_before=(
                session.authenticated_at + timedelta(seconds=1)
                if mode == "stale"
                else session.authenticated_at - timedelta(seconds=1)
            ),
            unavailable=mode == "error",
        )
    try:
        execute_test_only_high_risk(
            capability=selected_capability,
            request=request_contract,
            policy=policy,
            context=ServiceContext.from_current(actor_ref=f"user:{user.pk}"),
        )
    except HighRiskDenied as error:
        raise APIError(
            403,
            "high_risk_denied",
            "The fixture high-risk request was denied.",
        ) from error


def _one_time_error(error: Exception) -> APIError:
    if isinstance(error, SecretUnavailableOnReplay):
        return APIError(
            409,
            "secret_unavailable_on_replay",
            "The one-time secret is unavailable; rotate with a new authorization.",
        )
    if isinstance(error, ManagementIdempotencyConflict):
        return APIError(409, "idempotency_conflict", "The idempotency key conflicts.")
    if isinstance(error, (CredentialStateConflict, RevisionConflict)):
        return APIError(409, "state_conflict", "The credential state changed.")
    if isinstance(error, PermissionError):
        return APIError(403, "permission_denied", "Permission is denied.")
    return APIError(400, "invalid_request", "The credential request is invalid.")


@admin_capability("management.bulk.fixture", test_only=True)
def bulk_fixture(request: HttpRequest) -> JsonResponse:
    payload = parse_json_object(request)
    _only(request, payload, {"items", "confirmed"})
    identity = request.api_identity  # type: ignore[attr-defined]
    capability = request.management_capability  # type: ignore[attr-defined]
    items = parse_bulk_items(payload["items"], writable_fields=("name", "valid"))
    _authorize_high_risk(
        request,
        target_revision=1,
        scope="management.bulk",
        impact="fixture-bulk-operation",
        confirmed=payload["confirmed"] is True,
        expected_count=len(items),
    )
    errors = bounded_bulk_errors(
        [
            {"index": index, "code": "fixture_rejected"}
            for index, item in enumerate(items)
            if item.get("valid") is not True
        ]
    )

    def command() -> OneTimeCommandResult:
        actor = lock_actor_authority(
            identity.principal,
            permission=capability.django_permission,
            using="default",
            actor_credential=identity.credential,
            actor_capability=capability,
        )
        operation = create_principal_operation(
            principal=actor,
            kind="fixture.bulk",
            cancellable=True,
            progress_total=len(items),
        )
        operation = start_operation(
            operation_id=operation.id,
            expected_revision=operation.revision,
        )
        operation = finish_operation(
            operation_id=operation.id,
            expected_revision=operation.revision,
            succeeded=not errors,
            result_summary={"accepted": len(items) - len(errors)},
            errors=errors,
        )
        safe = present_operation(operation)
        return OneTimeCommandResult(response=safe, safe_result=safe)

    try:
        result = execute_one_time_idempotent(
            principal=identity.principal,
            operation="management.bulk.fixture",
            key=_idempotency_key(request),
            request={"items": list(items)},
            command=command,
            replay_safe=True,
        )
    except Exception as error:
        raise _one_time_error(error) from error
    return JsonResponse(result.response, status=202)


@admin_capability("management.operations.detail.fixture", test_only=True)
def operation_detail_fixture(request: HttpRequest, operation_id: str) -> JsonResponse:
    identity = request.api_identity  # type: ignore[attr-defined]
    capability = request.management_capability  # type: ignore[attr-defined]
    operation = scoped_object_or_404(
        identity,
        capability,
        Operation.objects.all(),
        object_id=operation_id,
    )
    return JsonResponse(present_operation(operation))


@admin_capability("management.operations.cancel.fixture", test_only=True)
def operation_cancel_fixture(request: HttpRequest, operation_id: str) -> JsonResponse:
    payload = parse_json_object(request)
    _only(request, payload, {"confirmed"})
    identity = request.api_identity  # type: ignore[attr-defined]
    capability = request.management_capability  # type: ignore[attr-defined]
    operation = scoped_object_or_404(
        identity,
        capability,
        Operation.objects.all(),
        object_id=operation_id,
    )
    expected_revision = require_if_match(request)
    _authorize_high_risk(
        request,
        target_revision=expected_revision,
        scope="management.operations",
        impact="fixture-operation-cancel",
        confirmed=payload["confirmed"] is True,
    )

    def command() -> OneTimeCommandResult:
        actor = lock_actor_authority(
            identity.principal,
            permission=capability.django_permission,
            using="default",
            actor_credential=identity.credential,
            actor_capability=capability,
        )
        cancelled = cancel_principal_operation(
            principal=actor,
            operation_id=operation.id,
            expected_revision=expected_revision,
        )
        safe = present_operation(cancelled)
        return OneTimeCommandResult(response=safe, safe_result=safe)

    try:
        result = execute_one_time_idempotent(
            principal=identity.principal,
            operation="management.operation.cancel.fixture",
            key=_idempotency_key(request),
            request={
                "operation_id": str(operation.id),
                "expected_revision": expected_revision,
            },
            command=command,
            replay_safe=True,
        )
    except Exception as error:
        raise _one_time_error(error) from error
    return JsonResponse(result.response)


@admin_capability("management.credentials.create.fixture", test_only=True)
def create_credential(request: HttpRequest) -> JsonResponse:
    payload = parse_json_object(request)
    _only(request, payload, {"target_principal_id", "name", "scopes", "confirmed"})
    identity = request.api_identity  # type: ignore[attr-defined]
    capability = request.management_capability  # type: ignore[attr-defined]
    target = scoped_object_or_404(
        identity,
        capability,
        APIPrincipal.objects.all(),
        object_id=payload["target_principal_id"],
    )
    _authorize_high_risk(
        request,
        target_revision=target.revision,
        scope="management.credentials",
        impact="fixture-credential-create",
        confirmed=payload["confirmed"] is True,
    )
    try:
        result = issue_credential_once(
            actor_principal=identity.principal,
            actor_credential=identity.credential,
            actor_capability=capability,
            target_principal_id=target.id,
            name=str(payload["name"]),
            scopes=tuple(payload["scopes"]),
            idempotency_key=_idempotency_key(request),
            actor_permission=capability.django_permission,
            created_by=identity.principal.user,
        )
    except Exception as error:
        raise _one_time_error(error) from error
    return JsonResponse(result.response, status=201)


@admin_capability("management.credentials.rotate.fixture", test_only=True)
def rotate_credential(request: HttpRequest, credential_id: str) -> JsonResponse:
    payload = parse_json_object(request)
    _only(request, payload, {"expected_revision", "overlap_seconds", "confirmed"})
    identity = request.api_identity  # type: ignore[attr-defined]
    capability = request.management_capability  # type: ignore[attr-defined]
    credential = scoped_object_or_404(
        identity,
        capability,
        APICredential.objects.select_related("principal"),
        object_id=credential_id,
    )
    _authorize_high_risk(
        request,
        target_revision=int(payload["expected_revision"]),
        scope="management.credentials",
        impact="fixture-credential-rotate",
        confirmed=payload["confirmed"] is True,
    )
    try:
        result = rotate_credential_once(
            actor_principal=identity.principal,
            actor_credential=identity.credential,
            actor_capability=capability,
            credential_id=credential.id,
            expected_revision=int(payload["expected_revision"]),
            idempotency_key=_idempotency_key(request),
            actor_permission=capability.django_permission,
            overlap=timedelta(seconds=int(payload["overlap_seconds"])),
            created_by=identity.principal.user,
        )
    except Exception as error:
        raise _one_time_error(error) from error
    return JsonResponse(result.response, status=201)


@admin_capability("management.credentials.revoke.fixture", test_only=True)
def revoke_credential_view(request: HttpRequest, credential_id: str) -> JsonResponse:
    payload = parse_json_object(request)
    _only(request, payload, {"expected_revision", "confirmed"})
    identity = request.api_identity  # type: ignore[attr-defined]
    capability = request.management_capability  # type: ignore[attr-defined]
    credential = scoped_object_or_404(
        identity,
        capability,
        APICredential.objects.select_related("principal"),
        object_id=credential_id,
    )
    _authorize_high_risk(
        request,
        target_revision=int(payload["expected_revision"]),
        scope="management.credentials",
        impact="fixture-credential-revoke",
        confirmed=payload["confirmed"] is True,
    )
    try:
        result = revoke_credential_once(
            actor_principal=identity.principal,
            actor_credential=identity.credential,
            actor_capability=capability,
            credential_id=credential.id,
            expected_revision=int(payload["expected_revision"]),
            idempotency_key=_idempotency_key(request),
            actor_permission=capability.django_permission,
        )
    except Exception as error:
        raise _one_time_error(error) from error
    return JsonResponse(result.response)
