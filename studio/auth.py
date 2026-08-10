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
from core.audit import AuditWriteContext, record_audit_event
from core.idempotency import hash_idempotency_key
from core.models import AuditEvent
from management_registry import CAPABILITY_REGISTRY


def _safe_login_redirect(request: HttpRequest) -> HttpResponseRedirect:
    login_url = resolve_url("login")
    return HttpResponseRedirect(f"{login_url}?{urlencode({'next': request.path})}")


def _current_studio_denial_actor(request: HttpRequest) -> Any | None:
    """Resolve a current staff identity without trusting the rejected capability."""

    try:
        principal = authorize_studio_request(
            request_user=request.user,
            session_reference=session_reference(request),
            capability=CAPABILITY_REGISTRY.require("studio.home.read"),
        )
    except (StudioAuthenticationRequired, StudioAuthorizationDenied):
        return None
    return principal.user


def audit_site_settings_denial(
    request: HttpRequest,
    *,
    reason: str,
    idempotency_key: str = "",
    actor: Any | None = None,
) -> None:
    """Match settings API denial evidence for a validated Studio identity."""

    capability = CAPABILITY_REGISTRY.require("site.settings.write")
    principal = getattr(request, "studio_principal", None)
    user = actor if actor is not None else getattr(principal, "user", None)
    actor_id = getattr(user, "pk", None)
    if not bool(getattr(user, "is_authenticated", False)) or actor_id is None:
        return
    actor_ref = f"user:{actor_id}"
    key_hash = ""
    if idempotency_key:
        try:
            key_hash = hash_idempotency_key(
                f"site.settings.write:{actor_ref}",
                idempotency_key,
            )
        except (TypeError, ValueError):
            pass
    record_audit_event(
        action=capability.audit_action,
        target_type="management.command",
        target_label="management-command",
        outcome=AuditEvent.Outcome.DENIED,
        context=AuditWriteContext(
            actor_id=actor_id,
            actor_ref=actor_ref,
            idempotency_key_hash=key_hash,
        ),
        changes={},
        metadata={"reason": reason, "state": "denied"},
    )


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
                if capability.key == "site.settings.write":
                    actor = _current_studio_denial_actor(request)
                    if actor is not None:
                        audit_site_settings_denial(
                            request,
                            reason="permission_denied",
                            actor=actor,
                        )
                return HttpResponseForbidden("Studio access denied")
            allowed_methods = {capability.studio.method}
            if capability.studio.method == "GET":
                allowed_methods.add("HEAD")
            if request.method not in allowed_methods:
                if capability.key == "site.settings.write":
                    audit_site_settings_denial(
                        request,
                        reason="method_not_allowed",
                    )
                return HttpResponseNotAllowed(sorted(allowed_methods))
            return view(request, *args, **kwargs)

        return wrapped

    return decorate


def staff_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Compatibility name for the shell's explicit registered capability."""

    return capability_required("studio.home.read")(view)
