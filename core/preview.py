from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import resolve_url
from django.utils.http import urlencode

from core.middleware import ROBOTS_HEADER_VALUE, apply_private_no_store

SENSITIVE_PREVIEW_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "auth",
        "authorization",
        "code",
        "credential",
        "jwt",
        "password",
        "preview_token",
        "refresh_token",
        "secret",
        "session",
        "sig",
        "signature",
        "token",
    }
)


def _private_preview_response(response: HttpResponse) -> HttpResponse:
    response["X-Robots-Tag"] = ROBOTS_HEADER_VALUE
    apply_private_no_store(response)
    return response


def staff_preview_required(
    view: Callable[..., HttpResponse],
) -> Callable[..., HttpResponse]:
    """Require an active staff session and reject credential-shaped query keys."""

    @wraps(view)
    def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        request.private_response_required = True  # type: ignore[attr-defined]
        if any(key.casefold() in SENSITIVE_PREVIEW_QUERY_KEYS for key in request.GET):
            return _private_preview_response(
                HttpResponse("Invalid preview request.", status=400, content_type="text/plain")
            )

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            login_url = resolve_url("login")
            response = HttpResponseRedirect(f"{login_url}?{urlencode({'next': request.path})}")
            return _private_preview_response(response)
        if not user.is_active or not user.is_staff:
            return _private_preview_response(
                HttpResponse("Forbidden", status=403, content_type="text/plain")
            )
        return _private_preview_response(view(request, *args, **kwargs))

    return wrapped
