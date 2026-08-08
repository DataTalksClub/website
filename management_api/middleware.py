from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from .errors import APIError, error_response, strip_cors

ADMIN_PREFIX = "/api/v1/admin/"


class AdminAPIResponseMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        try:
            response = self.get_response(request)
        except Exception:
            if not request.path_info.startswith(ADMIN_PREFIX):
                raise
            response = error_response(
                request,
                APIError(500, "internal_error", "The management request could not be completed."),
            )
        if request.path_info.startswith(ADMIN_PREFIX):
            strip_cors(response)
            if response.status_code >= 400 and not response.headers.get(
                "Content-Type", ""
            ).startswith("application/json"):
                code = "not_found" if response.status_code == 404 else "request_failed"
                response = error_response(
                    request,
                    APIError(response.status_code, code, "The management request failed."),
                )
        return response
