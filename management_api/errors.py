from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.http import HttpRequest, JsonResponse

from core.redaction import redact


@dataclass(slots=True)
class APIError(Exception):
    status: int
    code: str
    message: str
    fields: dict[str, list[str]] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    safe_result: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        Exception.__init__(self, self.message)


def error_response(request: HttpRequest, error: APIError) -> JsonResponse:
    payload: dict[str, Any] = {
        "error": {
            "code": error.code,
            # APIError messages are code-owned, generic strings.  Dynamic
            # fields are redacted below; preserving this allowlisted message
            # keeps the stable public error envelope.
            "message": error.message,
            "request_id": getattr(request, "request_id", ""),
        }
    }
    if error.fields:
        payload["error"]["fields"] = redact(error.fields)
    if error.safe_result is not None:
        payload["result"] = error.safe_result
    response = JsonResponse(payload, status=error.status)
    for name, value in error.headers.items():
        response[name] = value
    strip_cors(response)
    return response


def strip_cors(response) -> None:
    for name in (
        "Access-Control-Allow-Origin",
        "Access-Control-Allow-Credentials",
        "Access-Control-Allow-Methods",
        "Access-Control-Allow-Headers",
        "Access-Control-Expose-Headers",
        "Access-Control-Max-Age",
    ):
        if name in response.headers:
            del response.headers[name]


def authentication_required() -> APIError:
    return APIError(
        401,
        "authentication_required",
        "Valid Bearer authentication is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def permission_denied() -> APIError:
    return APIError(403, "permission_denied", "Permission is denied.")
