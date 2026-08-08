from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.middleware.common import CommonMiddleware
from django.utils.cache import patch_cache_control

from core.context import (
    CONTEXT_ID_PATTERN,
    bind_context,
    external_context_id_or_new,
    is_safe_external_context_id,
    reset_context,
)

REQUEST_ID_PATTERN = CONTEXT_ID_PATTERN
PRIVATE_PREFIXES = ("/studio/", "/api/v1/admin/", "/accounts/")
ALB_READINESS_PATH = "/health/ready"


class ReadinessProbeCommonMiddleware(CommonMiddleware):
    """Skip dynamic ALB target-host validation only for the readiness probe."""

    def process_request(self, request: HttpRequest) -> HttpResponsePermanentRedirect | None:
        if request.path_info == ALB_READINESS_PATH:
            return None
        return super().process_request(request)


class RequestIdMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        supplied = request.headers.get("X-Request-ID", "")
        request_id = external_context_id_or_new(supplied)
        supplied_correlation = request.headers.get("X-Correlation-ID", "")
        correlation_id = (
            supplied_correlation
            if is_safe_external_context_id(supplied_correlation)
            else request_id
        )
        request.request_id = request_id  # type: ignore[attr-defined]
        request.correlation_id = correlation_id  # type: ignore[attr-defined]
        tokens = bind_context(request_id=request_id, correlation_id=correlation_id)
        try:
            response = self.get_response(request)
            response["X-Request-ID"] = request_id
            response["X-Correlation-ID"] = correlation_id
            return response
        finally:
            reset_context(tokens)


class PrivateSurfaceMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if request.path.startswith(PRIVATE_PREFIXES):
            patch_cache_control(response, private=True, no_store=True, max_age=0)
        return response


class NoIndexMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if settings.NOINDEX:
            response["X-Robots-Tag"] = "noindex, nofollow"
        return response
