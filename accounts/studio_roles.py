"""Deterministic code-owned Studio groups and foundation permissions."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from django.contrib.auth.models import Group, Permission
from django.db import transaction

STUDIO_ACCESS = "core.access_studio"
AUDIT_BROWSE = "core.browse_audit"
HIGH_RISK_FIXTURE = "core.execute_high_risk_fixture"
MANAGE_API_CREDENTIALS = "management_auth.manage_api_credentials"
HISTORICAL_REGISTRATION_IMPORT_MANAGE = "events.historical_registration_import_manage"
HISTORICAL_REGISTRATION_MAPPING_MANAGE = "events.historical_registration_mapping_manage"
COURSE_REGISTRATION_COUNT_BASELINE_MANAGE = "courses.registration_count_baseline_manage"
SITE_SETTINGS_READ = "core.read_operational_settings"
SITE_SETTINGS_WRITE = "core.change_operational_settings"
SPONSORS_READ = "core.read_sponsors"
SPONSORS_WRITE = "core.change_sponsors"
SPONSORS_EXPORT = "core.export_sponsors"

_ROLE_PERMISSIONS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "site_admin": frozenset(
            {
                STUDIO_ACCESS,
                AUDIT_BROWSE,
                MANAGE_API_CREDENTIALS,
                HISTORICAL_REGISTRATION_IMPORT_MANAGE,
                HISTORICAL_REGISTRATION_MAPPING_MANAGE,
                COURSE_REGISTRATION_COUNT_BASELINE_MANAGE,
                SITE_SETTINGS_READ,
                SITE_SETTINGS_WRITE,
                SPONSORS_READ,
                SPONSORS_WRITE,
                SPONSORS_EXPORT,
            }
        ),
        "content_operator": frozenset(
            {
                STUDIO_ACCESS,
                SITE_SETTINGS_READ,
                SITE_SETTINGS_WRITE,
                SPONSORS_READ,
                SPONSORS_WRITE,
                SPONSORS_EXPORT,
            }
        ),
        "course_operator": frozenset(
            {STUDIO_ACCESS, COURSE_REGISTRATION_COUNT_BASELINE_MANAGE}
        ),
        "event_operator": frozenset(
            {
                STUDIO_ACCESS,
                HISTORICAL_REGISTRATION_IMPORT_MANAGE,
                HISTORICAL_REGISTRATION_MAPPING_MANAGE,
            }
        ),
        "email_operator": frozenset({STUDIO_ACCESS}),
        "support_operator": frozenset({STUDIO_ACCESS}),
        "auditor": frozenset(
            {STUDIO_ACCESS, AUDIT_BROWSE, SITE_SETTINGS_READ, SPONSORS_READ}
        ),
    }
)
ROLE_PERMISSIONS = _ROLE_PERMISSIONS


def _validate_role_dependencies() -> None:
    invalid = sorted(
        role
        for role, permissions in ROLE_PERMISSIONS.items()
        if SITE_SETTINGS_WRITE in permissions and SITE_SETTINGS_READ not in permissions
    )
    if invalid:
        raise RuntimeError("site settings writers must also have read authority")
    invalid_sponsors = sorted(
        role
        for role, permissions in ROLE_PERMISSIONS.items()
        if (
            (SPONSORS_WRITE in permissions or SPONSORS_EXPORT in permissions)
            and SPONSORS_READ not in permissions
        )
    )
    if invalid_sponsors:
        raise RuntimeError("sponsor writers and exporters must also have read authority")


_validate_role_dependencies()


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


@transaction.atomic
def set_single_studio_role(user: Any, role: str) -> Group:
    """Assign one exact code-owned role without materializing unrelated roles."""

    permission_names = ROLE_PERMISSIONS.get(role)
    if permission_names is None:
        raise ValueError("unknown Studio role")
    group, _created = Group.objects.get_or_create(name=role)
    group.permissions.set(_permission_objects(permission_names))
    user.groups.set([group])
    return group
