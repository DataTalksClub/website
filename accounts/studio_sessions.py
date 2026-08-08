"""Provider-neutral Staff Session lifecycle and deterministic adapters."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db import transaction
from django.dispatch import receiver
from django.http import HttpRequest
from django.utils import timezone

from core.models import StaffSession

SESSION_REFERENCE_KEY = "studio_staff_session_id"


@dataclass(frozen=True, slots=True)
class StaffSessionEvidence:
    session_id: uuid.UUID
    user_id: Any
    authenticated_at: datetime


class StaffSessionAdapter(Protocol):
    def resolve(self, *, reference: object, user_id: Any) -> StaffSessionEvidence | None: ...


class DatabaseStaffSessionAdapter:
    """Resolve an opaque model UUID without retaining Django's raw session key."""

    def resolve(self, *, reference: object, user_id: Any) -> StaffSessionEvidence | None:
        try:
            session_id = uuid.UUID(str(reference))
        except (AttributeError, TypeError, ValueError):
            return None
        session = (
            StaffSession.objects.filter(id=session_id, user_id=user_id, revoked_at__isnull=True)
            .only("id", "user_id", "authenticated_at")
            .first()
        )
        if session is None:
            return None
        return StaffSessionEvidence(
            session_id=session.id,
            user_id=session.user_id,
            authenticated_at=session.authenticated_at,
        )


class DeterministicStaffSessionAdapter:
    """Pure adapter for tests; exceptions are explicit fail-closed fixtures."""

    def __init__(
        self,
        evidence: StaffSessionEvidence | None,
        *,
        expected_reference: uuid.UUID | None = None,
        unavailable: bool = False,
    ) -> None:
        self.evidence = evidence
        self.expected_reference = expected_reference or (
            evidence.session_id if evidence is not None else None
        )
        self.unavailable = unavailable

    def resolve(self, *, reference: object, user_id: Any) -> StaffSessionEvidence | None:
        if self.unavailable:
            raise RuntimeError("staff session adapter unavailable")
        try:
            supplied_reference = uuid.UUID(str(reference))
        except (AttributeError, TypeError, ValueError):
            return None
        if (
            self.evidence is None
            or self.expected_reference is None
            or supplied_reference != self.expected_reference
            or supplied_reference != self.evidence.session_id
            or self.evidence.user_id != user_id
        ):
            return None
        return self.evidence


def create_staff_session(
    *, user: Any, request: HttpRequest, at: datetime | None = None
) -> StaffSession:
    """Create a revocable record and bind only its opaque UUID to Django's session."""

    existing_reference = session_reference(request)
    try:
        existing_id = uuid.UUID(str(existing_reference))
    except (AttributeError, TypeError, ValueError):
        existing_id = None
    if existing_id is not None:
        revoke_staff_session(existing_id, user=user)
    session = StaffSession.objects.create(user=user, authenticated_at=at or timezone.now())
    request.session[SESSION_REFERENCE_KEY] = str(session.id)
    return session


def revoke_staff_session(
    session_id: uuid.UUID,
    *,
    user: Any | None = None,
    at: datetime | None = None,
) -> bool:
    queryset = StaffSession.objects.filter(id=session_id, revoked_at__isnull=True)
    if user is not None:
        queryset = queryset.filter(user=user)
    session = queryset.only("authenticated_at").first()
    if session is None:
        return False
    revoked_at = at or timezone.now()
    if revoked_at < session.authenticated_at:
        raise ValueError("revocation time cannot predate authentication")
    changed = queryset.update(revoked_at=revoked_at)
    return changed == 1


def revoke_all_staff_sessions(user: Any, *, at: datetime | None = None) -> int:
    with transaction.atomic():
        sessions = StaffSession.objects.filter(user=user, revoked_at__isnull=True)
        revoked_at = at or timezone.now()
        if sessions.filter(authenticated_at__gt=revoked_at).exists():
            raise ValueError("revocation time cannot predate authentication")
        return sessions.update(revoked_at=revoked_at)


def session_reference(request: HttpRequest) -> object:
    return request.session.get(SESSION_REFERENCE_KEY)


@receiver(user_logged_in, dispatch_uid="accounts.create_provider_neutral_staff_session")
def create_session_after_login(sender: Any, request: HttpRequest, user: Any, **kwargs: Any) -> None:
    del sender, kwargs
    if user.is_active and user.is_staff:
        create_staff_session(user=user, request=request)


@receiver(user_logged_out, dispatch_uid="accounts.revoke_provider_neutral_staff_session")
def revoke_session_after_logout(
    sender: Any,
    request: HttpRequest | None,
    user: Any,
    **kwargs: Any,
) -> None:
    del sender, kwargs
    if request is None:
        return
    try:
        reference = uuid.UUID(str(session_reference(request)))
    except (AttributeError, TypeError, ValueError):
        return
    revoke_staff_session(reference, user=user)


def refresh_user(user_id: Any) -> Any | None:
    """Return current database state without trusting request or permission caches."""

    return get_user_model()._default_manager.filter(pk=user_id).first()
