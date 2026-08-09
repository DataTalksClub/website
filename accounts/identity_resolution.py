from __future__ import annotations

from typing import Any

from accounts.models import AccountIdentityAlias, CustomUser


def resolve_durable_user_id(user_id: int) -> int | None:
    alias = (
        AccountIdentityAlias.objects.select_related("survivor")
        .filter(source_user_id=user_id)
        .first()
    )
    if alias is None:
        return user_id
    survivor = alias.survivor
    if (
        not survivor.is_active
        or survivor.identity_state == CustomUser.IdentityState.ABSORBED
    ):
        return None
    return survivor.pk


def resolve_durable_user(user: Any) -> CustomUser | None:
    if user is None or getattr(user, "pk", None) is None:
        return None
    if user.identity_state != CustomUser.IdentityState.ABSORBED:
        return user
    survivor_id = resolve_durable_user_id(user.pk)
    if survivor_id is None or survivor_id == user.pk:
        return None
    return CustomUser.objects.filter(pk=survivor_id).first()
