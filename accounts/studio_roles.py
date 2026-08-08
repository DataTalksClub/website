"""Deterministic code-owned Studio groups and foundation permissions."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from django.contrib.auth.models import Group, Permission
from django.db import transaction

STUDIO_ACCESS = "core.access_studio"
AUDIT_BROWSE = "core.browse_audit"
HIGH_RISK_FIXTURE = "core.execute_high_risk_fixture"

_ROLE_PERMISSIONS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "site_admin": frozenset({STUDIO_ACCESS, AUDIT_BROWSE}),
        "content_operator": frozenset({STUDIO_ACCESS}),
        "course_operator": frozenset({STUDIO_ACCESS}),
        "event_operator": frozenset({STUDIO_ACCESS}),
        "email_operator": frozenset({STUDIO_ACCESS}),
        "support_operator": frozenset({STUDIO_ACCESS}),
        "auditor": frozenset({STUDIO_ACCESS, AUDIT_BROWSE}),
    }
)
ROLE_PERMISSIONS = _ROLE_PERMISSIONS


def _permission_objects(permission_names: frozenset[str]) -> list[Permission]:
    permissions: list[Permission] = []
    for name in sorted(permission_names):
        app_label, codename = name.split(".", 1)
        permission = Permission.objects.filter(
            content_type__app_label=app_label,
            codename=codename,
        ).first()
        if permission is None:
            raise RuntimeError(f"required Studio permission is unavailable: {name}")
        permissions.append(permission)
    return permissions


@transaction.atomic
def synchronize_studio_roles() -> tuple[Group, ...]:
    """Create/update only the seven named groups with their exact declared permissions."""

    groups: list[Group] = []
    for name, permission_names in ROLE_PERMISSIONS.items():
        group, _created = Group.objects.get_or_create(name=name)
        group.permissions.set(_permission_objects(permission_names))
        groups.append(group)
    return tuple(groups)
