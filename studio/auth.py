"""Studio presentation adapter authorization."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
    HttpResponseRedirect,
)
from django.shortcuts import resolve_url
from django.utils.http import urlencode

from accounts.studio_authorization import (
    StudioAuthenticationRequired,
    StudioAuthorizationDenied,
    authorize_studio_request,
)
from accounts.studio_sessions import session_reference
from management_registry import CAPABILITY_REGISTRY


def _safe_login_redirect(request: HttpRequest) -> HttpResponseRedirect:
    login_url = resolve_url("login")
    return HttpResponseRedirect(f"{login_url}?{urlencode({'next': request.path})}")


def capability_required(
    key: str,
) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    def decorate(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @wraps(view)
        def wrapped(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            capability = CAPABILITY_REGISTRY.get(key)
            if capability is None:
                return HttpResponseForbidden("Studio access denied")
            try:
                request.studio_principal = authorize_studio_request(  # type: ignore[attr-defined]
                    request_user=request.user,
                    session_reference=session_reference(request),
                    capability=capability,
                )
            except StudioAuthenticationRequired:
                if session_reference(request):
                    return HttpResponseForbidden("Studio access denied")
                return _safe_login_redirect(request)
            except StudioAuthorizationDenied:
                return HttpResponseForbidden("Studio access denied")
            allowed_methods = {capability.studio.method}
            if capability.studio.method == "GET":
                allowed_methods.add("HEAD")
            if request.method not in allowed_methods:
                return HttpResponseNotAllowed(sorted(allowed_methods))
            return view(request, *args, **kwargs)

        return wrapped

    return decorate


def staff_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Compatibility name for the shell's explicit registered capability."""

    return capability_required("studio.home.read")(view)
