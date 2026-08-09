from __future__ import annotations

from datetime import datetime

from django.utils import timezone


def database_now(*, using: str = "default") -> datetime:
    """Return the shared aware application clock used by portable lease services."""

    del using
    return timezone.now()
