import re
import uuid
from collections.abc import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponsePermanentRedirect
from django.middleware.common import CommonMiddleware
from django.utils.cache import patch_cache_control

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
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
        request_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else uuid.uuid4().hex
        request.request_id = request_id  # type: ignore[attr-defined]
        response = self.get_response(request)
        response["X-Request-ID"] = request_id
        return response


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
