from __future__ import annotations

from typing import Any

from django.db.models import Model, QuerySet

from core.capabilities import Capability

from .authentication import APIIdentity
from .errors import APIError


def enforce_writable_fields(
    identity: APIIdentity,
    capability: Capability,
    payload: dict[str, Any],
) -> None:
    """Fail closed when an adapter receives undeclared or field-denied input."""

    declared = frozenset(capability.admin_api.writable_fields)
    if set(payload) - declared:
        raise APIError(400, "invalid_fields", "The request fields are invalid.")
    policy = capability.field_policy
    if policy is None:
        raise APIError(403, "permission_denied", "Permission is denied.")
    try:
        allowed = all(policy(identity.principal, field) is True for field in payload)
    except Exception as error:
        raise APIError(403, "permission_denied", "Permission is denied.") from error
    if not allowed:
        raise APIError(403, "permission_denied", "Permission is denied.")


def scoped_object_or_404[Object: Model](
    identity: APIIdentity,
    capability: Capability,
    queryset: QuerySet[Object],
    *,
    object_id: Any,
) -> Object:
    """Apply the declared SQL scope before lookup and collapse excluded/absent objects."""

    scope = capability.object_scope
    policy = capability.object_policy
    if scope is None or policy is None:
        raise APIError(403, "permission_denied", "Permission is denied.")
    try:
        selected = scope(identity.principal, queryset).filter(pk=object_id).first()
        allowed = selected is not None and policy(identity.principal, selected) is True
    except Exception as error:
        raise APIError(
            404,
            "not_found",
            "The requested management resource was not found.",
        ) from error
    if not allowed:
        raise APIError(404, "not_found", "The requested management resource was not found.")
    return selected
