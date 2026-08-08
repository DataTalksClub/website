"""Deny-by-default Studio authorization independent of Django superuser shortcuts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.capabilities import Capability

from .studio_sessions import (
    DatabaseStaffSessionAdapter,
    StaffSessionAdapter,
    StaffSessionEvidence,
    refresh_user,
)


class StudioAuthenticationRequired(PermissionError):
    pass


class StudioAuthorizationDenied(PermissionError):
    pass


_TARGET_NOT_SUPPLIED = object()


@dataclass(frozen=True, slots=True)
class StudioPrincipal:
    user: Any
    session: StaffSessionEvidence
    capability: Capability


def has_explicit_permission(user: Any, permission: str) -> bool:
    """Check explicit assignments only; is_staff/superuser never bypass the registry."""

    try:
        app_label, codename = permission.split(".", 1)
    except ValueError:
        return False
    direct = user.user_permissions.filter(
        content_type__app_label=app_label,
        codename=codename,
    ).exists()
    if direct:
        return True
    return user.groups.filter(
        permissions__content_type__app_label=app_label,
        permissions__codename=codename,
    ).exists()


def authorize_studio_request(
    *,
    request_user: Any,
    session_reference: object,
    capability: Capability,
    adapter: StaffSessionAdapter | None = None,
    target: Any = _TARGET_NOT_SUPPLIED,
    fields: tuple[str, ...] = (),
) -> StudioPrincipal:
    if not bool(getattr(request_user, "is_authenticated", False)):
        raise StudioAuthenticationRequired
    user = refresh_user(getattr(request_user, "pk", None))
    if user is None or not user.is_active or not user.is_staff:
        raise StudioAuthorizationDenied
    if not has_explicit_permission(user, capability.django_permission):
        raise StudioAuthorizationDenied
    try:
        evidence = (adapter or DatabaseStaffSessionAdapter()).resolve(
            reference=session_reference,
            user_id=user.pk,
        )
    except Exception as error:
        raise StudioAuthorizationDenied from error
    if evidence is None:
        raise StudioAuthorizationDenied
    if capability.function_policy is not None:
        try:
            if capability.function_policy(user, evidence) is not True:
                raise StudioAuthorizationDenied
        except StudioAuthorizationDenied:
            raise
        except Exception as error:
            raise StudioAuthorizationDenied from error
    if target is not _TARGET_NOT_SUPPLIED:
        if capability.object_policy is None:
            raise StudioAuthorizationDenied
        try:
            if capability.object_policy(user, target) is not True:
                raise StudioAuthorizationDenied
        except StudioAuthorizationDenied:
            raise
        except Exception as error:
            raise StudioAuthorizationDenied from error
    if fields:
        if capability.field_policy is None:
            raise StudioAuthorizationDenied
        for field in fields:
            try:
                if capability.field_policy(user, field) is not True:
                    raise StudioAuthorizationDenied
            except StudioAuthorizationDenied:
                raise
            except Exception as error:
                raise StudioAuthorizationDenied from error
    return StudioPrincipal(user=user, session=evidence, capability=capability)
