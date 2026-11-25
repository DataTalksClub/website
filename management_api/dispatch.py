from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from community_base.kernel.redaction import redact
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DatabaseError, IntegrityError, NotSupportedError, OperationalError
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt

from core.audit import AuditWriteContext, record_audit_event
from core.capabilities import ServiceKind
from core.models import AuditEvent
from management_auth.idempotency import (
    credential_idempotency_operation,
    hash_management_idempotency_key,
)
from management_auth.models import APIRateAdmission
from management_auth.rate_limits import RateLimitExceeded, RateLimitUnavailable, admit
from management_registry import CAPABILITY_REGISTRY

from .authentication import authenticate, authorize, mark_used
from .errors import APIError, error_response, permission_denied, strip_cors

logger = logging.getLogger(__name__)


def _classify_unhandled_error(exc: Exception, capability_key: str) -> APIError:
    """Turn an unexpected exception into a specific, safe APIError."""
    exc_name = type(exc).__name__
    if isinstance(exc, DjangoValidationError):
        detail = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        safe = str(redact(detail))[:300]
        return APIError(400, "validation_error", safe or "Validation failed.")
    if isinstance(exc, NotSupportedError):
        return APIError(
            500,
            "database_not_supported",
            f"Database operation is not supported by the backend ({exc_name}) "
            f"while executing {capability_key}.",
        )
    if isinstance(exc, IntegrityError):
        return APIError(
            409,
            "database_integrity_error",
            f"Database integrity constraint failed ({exc_name}) while executing {capability_key}.",
        )
    if isinstance(exc, OperationalError):
        return APIError(
            503,
            "database_unavailable",
            f"Database is temporarily unavailable ({exc_name}) while executing {capability_key}.",
        )
    if isinstance(exc, DatabaseError):
        return APIError(
            500,
            "database_error",
            f"Database error ({exc_name}) while executing {capability_key}.",
        )
    raw_detail = str(exc).strip()
    safe_detail = str(redact(raw_detail))[:200] if raw_detail else ""
    if safe_detail:
        message = f"Unhandled {exc_name} while executing {capability_key}: {safe_detail}"
    else:
        message = f"Unhandled {exc_name} while executing {capability_key}."
    return APIError(500, "internal_error", message)


def admin_capability(
    capability_key: str,
    *,
    test_only: bool = False,
) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    def decorate(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @csrf_exempt
        @wraps(view)
        def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            capability = None
            identity = None
            try:
                capability = CAPABILITY_REGISTRY.get(capability_key)
                if capability is None or capability.admin_api.test_only is not test_only:
                    raise permission_denied()
                identity = authenticate(request)
                authorize(identity, capability)
                allowed_methods = {capability.admin_api.method}
                if capability.admin_api.method == "GET":
                    allowed_methods.add("HEAD")
                if request.method not in allowed_methods:
                    raise APIError(
                        405,
                        "method_not_allowed",
                        "The request method is not allowed.",
                        headers={"Allow": ", ".join(sorted(allowed_methods))},
                    )
                is_banner_continuation = (
                    capability.key == "events.manage.banner"
                    and request.headers.get("X-Banner-Upload-Chunk") == "1"
                )
                if not is_banner_continuation:
                    rate_class = (
                        APIRateAdmission.CostClass.READ
                        if capability.admin_api.rate_class == "read"
                        else APIRateAdmission.CostClass.WRITE
                    )
                    try:
                        admit(
                            cost_class=rate_class,
                            cost=capability.admin_api.rate_cost,
                            principal=identity.principal,
                        )
                    except (RateLimitExceeded, RateLimitUnavailable) as error:
                        retry_after = (
                            error.retry_after if isinstance(error, RateLimitExceeded) else 60
                        )
                        raise APIError(
                            429,
                            "rate_limited",
                            "The management API rate limit was reached.",
                            headers={"Retry-After": str(retry_after)},
                        ) from error
                request.api_identity = identity  # type: ignore[attr-defined]
                request.management_capability = capability  # type: ignore[attr-defined]
                response = view(request, *args, **kwargs)
                mark_used(identity)
                strip_cors(response)
                return response
            except APIError as error:
                if (
                    capability is not None
                    and not capability.test_only
                    and capability.service_kind is ServiceKind.COMMAND
                    and identity is not None
                    and error.status >= 400
                ):
                    key_hash = ""
                    try:
                        raw_key = request.headers.get("Idempotency-Key", "")
                        if capability.key.startswith("management.credentials."):
                            key_hash = hash_management_idempotency_key(
                                identity.principal.id,
                                credential_idempotency_operation(capability.key),
                                raw_key,
                            )
                        else:
                            key_hash = hash_management_idempotency_key(
                                identity.principal.id,
                                capability.key,
                                raw_key,
                            )
                    except (AttributeError, TypeError, ValueError):
                        pass
                    record_audit_event(
                        action=capability.audit_action,
                        target_type=(
                            "management.credential"
                            if capability.key.startswith("management.credentials.")
                            else "management.command"
                        ),
                        target_label=(
                            "credential-request"
                            if capability.key.startswith("management.credentials.")
                            else "management-command"
                        ),
                        outcome=AuditEvent.Outcome.DENIED,
                        context=AuditWriteContext(
                            actor_id=identity.principal.user_id,
                            api_principal_id=identity.principal.id,
                            actor_ref=f"api_principal:{identity.principal.id}",
                            idempotency_key_hash=key_hash,
                        ),
                        changes={},
                        metadata=(
                            {
                                "reason": error.code,
                                "scopes": [],
                                "expires_at": None,
                                "state": "denied",
                            }
                            if capability.key.startswith("management.credentials.")
                            else {"reason": error.code, "state": "denied"}
                        ),
                    )
                return error_response(request, error)
            except Exception as exc:
                logger.exception(
                    "Unhandled exception in management API capability=%s request_id=%s",
                    capability_key,
                    getattr(request, "request_id", ""),
                )
                return error_response(request, _classify_unhandled_error(exc, capability_key))

        wrapped.management_capability_key = capability_key  # type: ignore[attr-defined]
        capability = CAPABILITY_REGISTRY.get(capability_key)
        wrapped.management_operation_id = (  # type: ignore[attr-defined]
            capability.admin_api.operation_id if capability is not None else ""
        )
        wrapped.management_service = (  # type: ignore[attr-defined]
            capability.service if capability is not None else None
        )
        wrapped.management_result_schema = (  # type: ignore[attr-defined]
            capability.admin_api.result_schema if capability is not None else ""
        )
        wrapped.management_audit_action = (  # type: ignore[attr-defined]
            capability.audit_action if capability is not None else ""
        )
        return wrapped

    return decorate
