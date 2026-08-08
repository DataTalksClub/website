from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt

from management_auth.models import APIRateAdmission
from management_auth.rate_limits import RateLimitExceeded, RateLimitUnavailable, admit
from management_registry import CAPABILITY_REGISTRY

from .authentication import authenticate, authorize, mark_used
from .errors import APIError, error_response, permission_denied, strip_cors


def admin_capability(
    capability_key: str,
    *,
    test_only: bool = False,
) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    def decorate(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @csrf_exempt
        @wraps(view)
        def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
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
                    retry_after = error.retry_after if isinstance(error, RateLimitExceeded) else 60
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
                return error_response(request, error)
            except Exception:
                return error_response(
                    request,
                    APIError(
                        500,
                        "internal_error",
                        "The management request could not be completed.",
                    ),
                )

        wrapped.management_capability_key = capability_key  # type: ignore[attr-defined]
        capability = CAPABILITY_REGISTRY.get(capability_key)
        wrapped.management_operation_id = (  # type: ignore[attr-defined]
            capability.admin_api.operation_id if capability is not None else ""
        )
        return wrapped

    return decorate
