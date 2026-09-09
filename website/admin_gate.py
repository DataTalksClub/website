"""Production break-glass gate for the built-in admin and loginas routes."""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, HttpResponseForbidden

from accounts.studio_sessions import (
    DatabaseStaffSessionAdapter,
    session_reference,
)
from core.audit import AuditWriteContext, record_audit_event
from core.bootstrap import RuntimeEnvironment
from core.models import AuditEvent


def _is_admin_path(path: str) -> bool:
    return path == "/admin/" or path.startswith("/admin/")


def _break_glass_principal(request: HttpRequest) -> bool:
    """Only an active superuser with current approved staff evidence passes.

    The staff-session adapter rejects revoked and idle/absolute-expired
    evidence, so disabling a Studio session also closes the break-glass admin.
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if not (user.is_active and user.is_superuser):
        return False
    evidence = DatabaseStaffSessionAdapter().resolve(
        reference=session_reference(request),
        user_id=user.pk,
    )
    return evidence is not None


def _audit_break_glass_denial(request: HttpRequest) -> None:
    # Only plausible identities are audited; anonymous scanners hit /admin/
    # constantly and would turn the audit log into noise.
    user = getattr(request, "user", None)
    if user is None or not (getattr(user, "is_authenticated", False) and user.is_active):
        return
    record_audit_event(
        action="admin.break_glass_denied",
        target_type="django.admin",
        target_label="django-admin",
        outcome=AuditEvent.Outcome.DENIED,
        context=AuditWriteContext(actor_id=user.pk, actor_ref=f"user:{user.pk}"),
        metadata={"reason": "break_glass_not_authorized"},
    )


class BreakGlassAdminGateMiddleware:
    """Enforce the break-glass contract for /admin/ in production.

    While an operator has explicitly enabled ``ADMIN_BREAK_GLASS`` in
    production, only a break-glass principal may pass; every other request is
    denied at the site boundary with one audit event per plausible identity.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request: HttpRequest):
        if (
            settings.RUNTIME_ENVIRONMENT is RuntimeEnvironment.PRODUCTION
            and settings.ADMIN_BREAK_GLASS
            and _is_admin_path(request.path)
            and not _break_glass_principal(request)
        ):
            _audit_break_glass_denial(request)
            return HttpResponseForbidden("Admin access requires break-glass authorization.")
        return self.get_response(request)
