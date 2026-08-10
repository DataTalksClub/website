"""Deterministic factories for Studio authorization tests."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client

from .studio_roles import ROLE_PERMISSIONS, synchronize_studio_roles


def make_studio_user(*, username: str, roles: tuple[str, ...] = ()) -> Any:
    synchronize_studio_roles()
    unknown_roles = set(roles).difference(ROLE_PERMISSIONS)
    if unknown_roles:
        raise ValueError("unknown Studio role")
    user = get_user_model().objects.create_user(
        username=username,
        email="",
        is_active=True,
        is_staff=True,
    )
    if roles:
        user.groups.set(Group.objects.filter(name__in=roles))
    return user


def grant_studio_role(user: Any, role: str) -> Any:
    """Grant one synchronized Studio role to an existing test user."""
    synchronize_studio_roles()
    if role not in ROLE_PERMISSIONS:
        raise ValueError("unknown Studio role")
    user.groups.add(Group.objects.get(name=role))
    return user


def authenticated_studio_client(user: Any) -> Client:
    client = Client()
    client.force_login(user)
    return client
