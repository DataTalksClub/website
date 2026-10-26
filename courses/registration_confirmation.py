"""Double opt-in email confirmation for course registrations.

Flow: a new registration is stored with ``email_confirmed=False`` and a
secret token. The confirmation email carries a confirm link with
that token; clicking it marks the registration confirmed. Tokens expire
after ``EMAIL_CONFIRMATION_TOKEN_TTL_DAYS``; resends are rate-limited by
a cooldown plus a per-registration cap.

Mirrors DataTalksClub/course-management-platform
``courses/registration_confirmation.py`` (CMP commit c2f7d87): same TTL
(7 days), same 60-second cooldown, same cap of 10 sends including the
initial message with a 24-hour reset, same idempotent confirm, same
signed management capability (which can resend/correct but never
confirms ownership — the email secret is never put in pending/profile
forms).
"""

from __future__ import annotations

import logging
import math
import secrets
from datetime import timedelta

from django.core import signing
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

EMAIL_CONFIRMATION_TOKEN_TTL_DAYS = 7
EMAIL_CONFIRMATION_RESEND_COOLDOWN_SECONDS = 60
EMAIL_CONFIRMATION_MAX_RESENDS = 10


def generate_confirmation_token():
    return secrets.token_urlsafe(32)


def confirmation_token_expired(registration, now=None):
    """True when the registration's token is older than the TTL."""
    now = now or timezone.now()
    sent_at = registration.email_confirmation_sent_at
    if sent_at is None:
        return True
    age = now - sent_at
    return age.days >= EMAIL_CONFIRMATION_TOKEN_TTL_DAYS


def issue_confirmation_token(registration, now=None):
    """Assign a fresh token and stamp the send time (call before send)."""
    now = now or timezone.now()
    if (
        registration.email_confirmation_sent_at is not None
        and now - registration.email_confirmation_sent_at >= timedelta(days=1)
    ):
        registration.email_confirmation_resend_count = 0
    registration.email_confirmation_token = generate_confirmation_token()
    registration.email_confirmation_sent_at = now
    registration.email_confirmation_resend_count = (
        registration.email_confirmation_resend_count or 0
    ) + 1
    registration.save(
        update_fields=[
            "email_confirmation_token",
            "email_confirmation_sent_at",
            "email_confirmation_resend_count",
        ]
    )
    return registration


def mark_registration_confirmed(registration, now=None):
    """Mark a registration confirmed; idempotent."""
    now = now or timezone.now()
    if registration.email_confirmed:
        return False
    registration.email_confirmed = True
    registration.email_confirmation_confirmed_at = now
    registration.save(
        update_fields=[
            "email_confirmed",
            "email_confirmation_confirmed_at",
        ]
    )
    return True


def resend_cooldown_remaining_seconds(registration, now=None):
    now = now or timezone.now()
    sent_at = registration.email_confirmation_sent_at
    if sent_at is None:
        return 0
    elapsed = (now - sent_at).total_seconds()
    remaining = EMAIL_CONFIRMATION_RESEND_COOLDOWN_SECONDS - elapsed
    return max(0, math.ceil(remaining))


def resend_allowed(registration, now=None):
    """(allowed, reason) rate-limit check for confirmation resends."""
    if registration.email_confirmed:
        return False, "already_confirmed"
    now = now or timezone.now()
    recent = (
        registration.email_confirmation_sent_at is not None
        and now - registration.email_confirmation_sent_at < timedelta(days=1)
    )
    if recent and (registration.email_confirmation_resend_count or 0) >= (
        EMAIL_CONFIRMATION_MAX_RESENDS
    ):
        return False, "too_many_resends"
    remaining = resend_cooldown_remaining_seconds(registration, now=now)
    if remaining > 0:
        return False, f"cooldown_{remaining}s"
    return True, ""


@transaction.atomic
def confirm_registration_by_token(model_class, token, now=None):
    """Confirm a registration by token.

    Returns (registration_or_None, outcome) where outcome is one of
    "confirmed", "already_confirmed", "expired", "invalid".
    """
    now = now or timezone.now()
    token = (token or "").strip()
    if not token:
        return None, "invalid"
    try:
        # Lock the row without joining the nullable ``course`` relation:
        # PostgreSQL rejects ``SELECT ... FOR UPDATE`` over the outer join
        # that ``select_related("course")`` generates ("FOR UPDATE cannot be
        # applied to the nullable side of an outer join"), while SQLite
        # silently accepts it. ``campaign`` is non-nullable (inner join),
        # so it stays prefetched; ``course`` loads lazily below instead.
        registration = (
            model_class.objects.select_for_update()
            .select_related("campaign")
            .get(email_confirmation_token=token)
        )
    except model_class.DoesNotExist:
        return None, "invalid"
    if registration.email_confirmed:
        return registration, "already_confirmed"
    if confirmation_token_expired(registration, now=now):
        return registration, "expired"
    mark_registration_confirmed(registration, now=now)
    return registration, "confirmed"


MANAGEMENT_TOKEN_SALT = "registration-email-management"


def management_token(registration):
    """Browser capability to resend/correct, never to confirm ownership."""
    return signing.dumps(
        [registration.pk, registration.email_normalized],
        salt=MANAGEMENT_TOKEN_SALT,
    )


def managed_registration(model_class, token):
    try:
        pk, email = signing.loads(token, salt=MANAGEMENT_TOKEN_SALT, max_age=7 * 24 * 60 * 60)
    except (signing.BadSignature, ValueError, TypeError):
        return None
    return (
        # Same FOR UPDATE/outer-join constraint as
        # ``confirm_registration_by_token``: never ``select_related("course")``
        # on a locked query; ``campaign`` is non-nullable and stays prefetched.
        model_class.objects.select_for_update()
        .select_related("campaign")
        .filter(pk=pk, email_normalized=email)
        .first()
    )
