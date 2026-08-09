from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from django.http import HttpRequest, JsonResponse
from django.utils import timezone

from core.models import RevisionConflict
from core.services import ServiceContext
from management_auth.idempotency import (
    ManagementIdempotencyConflict,
    SecretUnavailableOnReplay,
)
from management_auth.models import APICredential, APIPrincipal
from management_auth.policies import require_high_risk_policy
from management_auth.services import CredentialStateConflict

from .concurrency import require_if_match
from .dispatch import admin_capability
from .errors import APIError, error_response
from .json_input import parse_json_object
from .policies import enforce_writable_fields, scoped_object_or_404
from .query import parse_page_query


def _idempotency_key(request: HttpRequest) -> str:
    raw = request.META.get("HTTP_IDEMPOTENCY_KEY", "")
    if not isinstance(raw, str) or not raw or "," in raw or len(raw.encode("utf-8")) > 512:
        raise APIError(400, "invalid_idempotency_key", "A valid Idempotency-Key is required.")
    return raw


def _enforce_fields(
    request: HttpRequest,
    payload: dict,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    identity = request.api_identity  # type: ignore[attr-defined]
    capability = request.management_capability  # type: ignore[attr-defined]
    enforce_writable_fields(identity, capability, payload)
    supplied = frozenset(payload)
    if not required.issubset(supplied) or supplied - required - optional:
        raise APIError(400, "invalid_fields", "The request fields are invalid.")


def _confirmation(request: HttpRequest, value: object) -> None:
    capability = request.management_capability  # type: ignore[attr-defined]
    if capability.high_risk_policy is None:
        raise APIError(403, "high_risk_denied", "The credential request was denied.")
    try:
        allowed = require_high_risk_policy(capability.high_risk_policy).authorize(confirmed=value)
    except Exception as error:
        raise APIError(
            403,
            "high_risk_denied",
            "The credential request was denied.",
        ) from error
    if not allowed:
        raise APIError(403, "high_risk_denied", "The credential request was denied.")


def _expiry(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise APIError(400, "invalid_request", "The credential request is invalid.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise APIError(
            400,
            "invalid_request",
            "The credential request is invalid.",
        ) from error
    if not timezone.is_aware(parsed):
        raise APIError(400, "invalid_request", "The credential request is invalid.")
    return parsed


def _credential_error(error: Exception) -> APIError:
    if isinstance(error, SecretUnavailableOnReplay):
        return APIError(
            409,
            "secret_unavailable_on_replay",
            "The one-time secret is unavailable; rotate with a new authorization.",
            safe_result=error.safe_result,
        )
    if isinstance(error, ManagementIdempotencyConflict):
        return APIError(409, "idempotency_conflict", "The idempotency key conflicts.")
    if isinstance(error, (CredentialStateConflict, RevisionConflict)):
        return APIError(409, "state_conflict", "The credential state changed.")
    if isinstance(error, PermissionError):
        return APIError(403, "permission_denied", "Permission is denied.")
    return APIError(400, "invalid_request", "The credential request is invalid.")


@admin_capability("studio.home.read")
def admin_health(request: HttpRequest) -> JsonResponse:
    identity = request.api_identity  # type: ignore[attr-defined]
    capability = request.management_capability  # type: ignore[attr-defined]
    result = capability.service(
        None,
        context=ServiceContext.from_current(actor_ref=f"api_principal:{identity.principal.id}"),
    )
    return JsonResponse(result)


@admin_capability("management.credentials.list")
def credential_list(request: HttpRequest) -> JsonResponse:
    identity = request.api_identity  # type: ignore[attr-defined]
    capability = request.management_capability  # type: ignore[attr-defined]
    try:
        query = parse_page_query(
            request.GET,
            filter_fields=capability.admin_api.filter_fields,
            sort_fields=capability.admin_api.sort_fields,
        )
        principal_id = query.filters.get("principal_id")
        if principal_id:
            uuid.UUID(principal_id)
        result = capability.service(
            query,
            context=ServiceContext.from_current(actor_ref=f"api_principal:{identity.principal.id}"),
            actor_principal=identity.principal,
        )
    except (TypeError, ValueError) as error:
        raise APIError(400, "invalid_query", "Query parameters are invalid.") from error
    return JsonResponse(result)


@admin_capability("management.credentials.create")
def credential_create(request: HttpRequest) -> JsonResponse:
    payload = parse_json_object(request)
    _enforce_fields(
        request,
        payload,
        required=frozenset({"target_principal_id", "name", "scopes", "confirmed"}),
        optional=frozenset({"expires_at"}),
    )
    _confirmation(request, payload["confirmed"])
    identity = request.api_identity  # type: ignore[attr-defined]
    capability = request.management_capability  # type: ignore[attr-defined]
    target = scoped_object_or_404(
        identity,
        capability,
        APIPrincipal.objects.all(),
        object_id=payload["target_principal_id"],
    )
    if not isinstance(payload["name"], str) or not isinstance(payload["scopes"], list):
        raise APIError(400, "invalid_request", "The credential request is invalid.")
    try:
        result = capability.service(
            actor_principal=identity.principal,
            actor_credential=identity.credential,
            actor_capability=capability,
            target_principal_id=target.id,
            name=payload["name"],
            scopes=tuple(payload["scopes"]),
            expires_at=_expiry(payload.get("expires_at")),
            idempotency_key=_idempotency_key(request),
            actor_permission=capability.django_permission,
            created_by=identity.principal.user,
        )
    except Exception as error:
        raise _credential_error(error) from error
    return JsonResponse(result.response, status=201)


@admin_capability("management.credentials.rotate")
def credential_rotate(request: HttpRequest, credential_id: str) -> JsonResponse:
    payload = parse_json_object(request)
    _enforce_fields(
        request,
        payload,
        required=frozenset({"confirmed"}),
        optional=frozenset({"expires_at", "overlap_seconds"}),
    )
    _confirmation(request, payload["confirmed"])
    identity = request.api_identity  # type: ignore[attr-defined]
    capability = request.management_capability  # type: ignore[attr-defined]
    credential = scoped_object_or_404(
        identity,
        capability,
        APICredential.objects.select_related("principal"),
        object_id=credential_id,
    )
    overlap = payload.get("overlap_seconds", 0)
    if isinstance(overlap, bool) or not isinstance(overlap, int):
        raise APIError(400, "invalid_request", "The credential request is invalid.")
    try:
        result = capability.service(
            actor_principal=identity.principal,
            actor_credential=identity.credential,
            actor_capability=capability,
            credential_id=credential.id,
            expected_revision=require_if_match(request),
            idempotency_key=_idempotency_key(request),
            actor_permission=capability.django_permission,
            overlap=timedelta(seconds=overlap),
            expires_at=_expiry(payload.get("expires_at")),
            created_by=identity.principal.user,
        )
    except APIError:
        raise
    except Exception as error:
        raise _credential_error(error) from error
    return JsonResponse(result.response, status=201)


@admin_capability("management.credentials.revoke")
def credential_revoke(request: HttpRequest, credential_id: str) -> JsonResponse:
    payload = parse_json_object(request)
    _enforce_fields(request, payload, required=frozenset({"confirmed"}))
    _confirmation(request, payload["confirmed"])
    identity = request.api_identity  # type: ignore[attr-defined]
    capability = request.management_capability  # type: ignore[attr-defined]
    credential = scoped_object_or_404(
        identity,
        capability,
        APICredential.objects.select_related("principal"),
        object_id=credential_id,
    )
    try:
        result = capability.service(
            actor_principal=identity.principal,
            actor_credential=identity.credential,
            actor_capability=capability,
            credential_id=credential.id,
            expected_revision=require_if_match(request),
            idempotency_key=_idempotency_key(request),
            actor_permission=capability.django_permission,
        )
    except APIError:
        raise
    except Exception as error:
        raise _credential_error(error) from error
    return JsonResponse(result.response)


def admin_not_found(request: HttpRequest, path: str = "") -> JsonResponse:
    del path
    return error_response(
        request,
        APIError(404, "not_found", "The requested management resource was not found."),
    )


admin_not_found.management_api_exempt = True  # type: ignore[attr-defined]
