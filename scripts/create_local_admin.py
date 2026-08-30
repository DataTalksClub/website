#!/usr/bin/env python
"""Seed the documented local administrator and social-login previews."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import django

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings.local")
django.setup()

from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.db import transaction  # noqa: E402

from accounts.identity_values import normalize_account_email  # noqa: E402
from accounts.models import CustomUser  # noqa: E402
from accounts.services.local_provider_seed import (  # noqa: E402
    seed_local_social_providers,
)
from core.bootstrap import RuntimeEnvironment  # noqa: E402

EMAIL = "admin@aishippinglabs.com"
PASSWORD = "admin123"
SQLITE_ENGINE = "django.db.backends.sqlite3"


def assert_local_sqlite() -> None:
    """Refuse to write the placeholder account outside local SQLite."""

    if settings.RUNTIME_ENVIRONMENT is not RuntimeEnvironment.LOCAL:
        raise RuntimeError("create_local_admin: environment-not-local")
    if settings.DATABASES["default"].get("ENGINE") != SQLITE_ENGINE:
        raise RuntimeError("create_local_admin: database-not-local-sqlite")


@transaction.atomic
def create_or_reset_local_admin() -> tuple[CustomUser, bool]:
    """Create the local admin or restore its documented login and privileges."""

    normalized_email = normalize_account_email(EMAIL)
    matches = list(
        get_user_model().objects.filter(normalized_email=normalized_email).order_by("pk")
    )
    if len(matches) > 1:
        raise RuntimeError("create_local_admin: duplicate-email")

    created = not matches
    if created:
        username_conflict = get_user_model().objects.filter(username=EMAIL).exists()
        if username_conflict:
            raise RuntimeError("create_local_admin: username-conflict")
        user = get_user_model()(username=EMAIL, email=EMAIL)
    else:
        user = matches[0]
        user.email = EMAIL

    user.identity_state = CustomUser.IdentityState.ACTIVE
    user.is_active = True
    user.is_staff = True
    user.is_superuser = True
    user.set_password(PASSWORD)
    user.save()
    return user, created


def main() -> None:
    assert_local_sqlite()
    user, created = create_or_reset_local_admin()
    providers = seed_local_social_providers()
    action = "created" if created else "reset"
    print(f"create_local_admin: {action} id={user.pk} email={user.email}")
    print(
        "create_local_admin: social-provider-previews "
        f"providers={','.join(item.provider for item in providers.providers)}"
    )


if __name__ == "__main__":
    main()
