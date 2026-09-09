from __future__ import annotations

from collections.abc import Callable

from django.contrib.auth import HASH_SESSION_KEY, SESSION_KEY
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest, HttpResponse

from accounts.identity_resolution import (
    identity_state_eligible,
    resolve_durable_user,
)
from accounts.models import CustomUser
from course_management.observability import record_event


class DurableAccountSessionMiddleware:
    """Resolve a reviewed absorbed ID without flushing unrelated sessions.

    Identity-state eligibility is rechecked on every request (audit BE-08):
    quarantine is an immediate containment control, so a session whose owner
    becomes quarantined is flushed here, not merely blocked at the next login.
    A disabled account keeps Django's own separate inactive-session denial.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return self.get_response(request)
        if user.identity_state == CustomUser.IdentityState.ABSORBED:
            source_user_id = user.pk
            survivor = resolve_durable_user(user)
            if survivor is None or not survivor.is_active:
                request.session.flush()
                request.user = AnonymousUser()
                record_event(
                    "auth.session_failure",
                    request=request,
                    properties={"reason": "absorbed_identity_unresolved"},
                )
            else:
                request.session[SESSION_KEY] = str(survivor.pk)
                request.session[HASH_SESSION_KEY] = survivor.get_session_auth_hash()
                request.user = survivor
                request._cached_user = survivor  # type: ignore[attr-defined]
                _record_session_rebound(
                    source_user_id=source_user_id,
                    survivor_user_id=survivor.pk,
                )
                record_event(
                    "auth.session_rebound",
                    request=request,
                    user=survivor,
                    properties={"account_created": False},
                )
        elif not identity_state_eligible(user):
            source_user_id = user.pk
            request.session.flush()
            request.user = AnonymousUser()
            if hasattr(request, "_cached_user"):
                del request._cached_user  # type: ignore[attr-defined]
            record_event(
                "auth.session_failure",
                request=request,
                properties={
                    "reason": "identity_quarantined",
                    "source_user_id": source_user_id,
                },
            )
        return self.get_response(request)


def _record_session_rebound(*, source_user_id: int, survivor_user_id: int) -> None:
    from core.audit import AuditWriteContext, record_audit_event
    from core.models import AuditEvent

    record_audit_event(
        action="accounts.identity.session_rebound",
        target_type="accounts.identity",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        context=AuditWriteContext(
            actor_id=survivor_user_id,
            actor_ref=f"user:{survivor_user_id}",
        ),
        changes={"session_owner": "resolved_survivor"},
        metadata={
            "source_user_id": source_user_id,
            "survivor_user_id": survivor_user_id,
        },
    )
