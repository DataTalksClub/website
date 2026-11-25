from __future__ import annotations

import logging
from collections.abc import Callable

from community_base.kernel.redaction import redact
from django.http import HttpRequest, HttpResponse

from .errors import APIError, error_response, strip_cors

ADMIN_PREFIX = "/api/v1/admin/"
logger = logging.getLogger(__name__)


class AdminAPIResponseMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        try:
            response = self.get_response(request)
        except Exception as exc:
            if not request.path_info.startswith(ADMIN_PREFIX):
                raise
            logger.exception(
                "Unhandled exception in management API middleware path=%s request_id=%s",
                request.path_info,
                getattr(request, "request_id", ""),
            )
            exc_name = type(exc).__name__
            detail = str(redact(str(exc).strip()))[:200] if str(exc).strip() else ""
            message = (
                f"Unhandled {exc_name} in management API: {detail}"
                if detail
                else f"Unhandled {exc_name} in management API."
            )
            response = error_response(
                request,
                APIError(500, "internal_error", message),
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
