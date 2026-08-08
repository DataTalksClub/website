from __future__ import annotations

from datetime import UTC, datetime

from django.db import connections
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive, make_aware


def database_now(*, using: str = "default") -> datetime:
    """Use the database clock for leases so process clock skew cannot steal work."""

    with connections[using].cursor() as cursor:
        cursor.execute("SELECT CURRENT_TIMESTAMP")
        value = cursor.fetchone()[0]
    if isinstance(value, str):
        parsed = parse_datetime(value)
        if parsed is None:
            raise RuntimeError("database returned an invalid timestamp")
        value = parsed
    if not isinstance(value, datetime):
        raise RuntimeError("database returned an invalid timestamp type")
    if is_naive(value):
        return make_aware(value, UTC)
    return value.astimezone(UTC)
