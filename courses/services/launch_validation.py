"""Shared validation for the launch services and their adapters."""

from datetime import datetime

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

LAUNCH_IDEMPOTENCY_KEY_MAX = 128


class LaunchError(ValueError):
    def __init__(self, message, *, code="invalid_launch", status=400):
        self.code = code
        self.status = status
        super().__init__(message)


def require_revision(row, expected, field="revision"):
    if isinstance(expected, bool) or not isinstance(expected, int):
        raise LaunchError("Supply the current revision.")
    if getattr(row, field) != expected:
        raise LaunchError(
            "This record changed. Reload before editing.", code="revision_conflict", status=409
        )


def aware_datetime(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = parse_datetime(value)
        except ValueError:
            value = None
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise LaunchError("Use an ISO 8601 timestamp with a UTC offset.")
    return value


def text(value, name, *, limit=10000, blank=False):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise LaunchError(f"Invalid {name}.")
    if not blank and not value.strip():
        raise LaunchError(f"Enter {name}.")
    return value


def live_cohort(cohort):
    if cohort.delivery_mode != "live" or cohort.curriculum_source == "github_archive":
        raise LaunchError("Announcements require a live cohort using the current curriculum.")


def load_row(model, identity, *, lock=False):
    try:
        rows = model.objects.filter(pk=identity)
        if lock:
            serialize_rows(rows)
        return rows.get()
    except (model.DoesNotExist, ValueError, TypeError) as error:
        raise LaunchError("Record not found.", code="not_found", status=404) from error


def serialize_rows(rows):
    """Serialize writers portably, then let the caller read fresh values.

    A same-value UPDATE holds the database's write lock until this transaction
    ends. Missing child rows require their existing parent to be serialized first.
    """
    if not transaction.get_connection(rows.db).in_atomic_block:
        raise RuntimeError("Launch serialization requires an atomic transaction.")
    field = rows.model._meta.pk.name
    rows.update(**{field: F(field)})


def iso(value):
    if value is None:
        return None
    return value.isoformat()
