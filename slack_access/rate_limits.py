"""Database-backed per-identity admission for Slack website mutations."""

import hashlib
import math
from datetime import timedelta
from functools import wraps

from django.db import DatabaseError, transaction
from django.db.models import F
from django.http import HttpResponse
from django.utils import timezone

from .models import ActionBucket

LIMIT = 30
WINDOW_SECONDS = 60


class AdmissionDenied(RuntimeError):
    def __init__(self, retry_after):
        self.retry_after = max(1, min(WINDOW_SECONDS, retry_after))


@transaction.atomic
def admit(user_id, *, now=None):
    current = now or timezone.now()
    subject = hashlib.sha256(f"slack-access:user:{user_id}".encode()).hexdigest()
    bucket, _ = ActionBucket.objects.get_or_create(
        subject_hash=subject,
        defaults={"window_started_at": current},
    )
    # Portable write fence: row lock in PostgreSQL, serialized writers in SQLite.
    ActionBucket.objects.filter(pk=bucket.pk).update(revision=F("revision") + 1)
    bucket.refresh_from_db()
    if current >= bucket.window_started_at + timedelta(seconds=WINDOW_SECONDS):
        bucket.window_started_at = current
        bucket.hits = 0
    if bucket.hits >= LIMIT:
        remaining = (
            bucket.window_started_at + timedelta(seconds=WINDOW_SECONDS) - current
        ).total_seconds()
        raise AdmissionDenied(math.ceil(remaining))
    bucket.hits += 1
    bucket.save(update_fields=["hits", "window_started_at"])
    # Only current counters matter. Bound retention without retaining a person's action history.
    ActionBucket.objects.filter(window_started_at__lt=current - timedelta(days=1)).delete()


def business_rate(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.method == "POST" and request.user.is_authenticated:
            try:
                admit(request.user.pk)
            except AdmissionDenied as error:
                response = HttpResponse(
                    "Too many Slack access requests. Please try again shortly.", status=429
                )
                response["Retry-After"] = str(error.retry_after)
                return response
            except DatabaseError:
                response = HttpResponse(
                    "Slack access is temporarily unavailable. Please try again.", status=503
                )
                response["Retry-After"] = "60"
                return response
        return view(request, *args, **kwargs)

    return wrapped
