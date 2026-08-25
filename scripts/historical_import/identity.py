"""Learner identity for historical zoomcamp-scoring imports.

Every learner's *account* uses their real, recovered email -- see
``email_recovery.py`` -- so that if they already have (or later create) a
real DataTalks.Club account with that same email, their historical cohorts,
scores, and certificates attach to it instead of living on an orphaned
duplicate. Every learner's *displayed* identity (leaderboard name,
certificate name) is a freshly generated placeholder -- the same generator
``Enrollment`` already uses for anonymous leaderboard identities -- because
we do not have their real, chosen leaderboard name from back then, and a
real name does not belong on a public leaderboard by default anyway (that is
already how every current, non-historical enrollment on this platform
behaves).

When no real email can be recovered for a learner (rare -- see the coverage
gaps ``email_recovery.py`` documents), they fall back to a synthetic,
clearly-marked local account keyed only by the upstream scoring hash.
"""

from __future__ import annotations

import hashlib
import re

from accounts.identity_values import normalize_account_email
from courses.models import Cohort, Enrollment, User
from courses.random_names import generate_random_name

USERNAME_PREFIX = "zc-hist-"
_USERNAME_SANITIZE_RE = re.compile(r"[^\w.@+-]")
_MAX_USERNAME_LENGTH = 150


def sha1_hex(value: str) -> str:
    """The same join key upstream's own scoring pipeline computes from an email."""

    return hashlib.sha1(value.strip().lower().encode("utf-8")).hexdigest()


def username_for_key(source_key: str) -> str:
    return f"{USERNAME_PREFIX}{source_key}"


def _username_candidate(email: str) -> str:
    local_part = email.split("@", 1)[0]
    sanitized = _USERNAME_SANITIZE_RE.sub("_", local_part).strip("_")
    return (sanitized or "learner")[: _MAX_USERNAME_LENGTH - 6]


def _unique_username(email: str) -> str:
    base = _username_candidate(email)
    candidate = base
    suffix = 1
    while User.objects.filter(username=candidate).exists():
        suffix += 1
        candidate = f"{base}-{suffix}"[:_MAX_USERNAME_LENGTH]
    return candidate


def _get_or_create_by_real_email(real_email: str) -> tuple[User, bool]:
    normalized = normalize_account_email(real_email)
    if not normalized or "@" not in normalized:
        return _get_or_create_synthetic(sha1_hex(real_email))

    existing = User.objects.filter(normalized_email=normalized).order_by("id").first()
    if existing is not None:
        return existing, False

    user = User.objects.create(username=_unique_username(normalized), email=real_email.strip())
    return user, True


def _get_or_create_synthetic(source_key: str) -> tuple[User, bool]:
    username = username_for_key(source_key)
    return User.objects.get_or_create(
        username=username,
        defaults={"email": f"{username}@example.com"},
    )


def get_or_create_learner(source_key: str, real_email: str | None = None) -> tuple[User, bool]:
    """Get or create the account behind one historical learner.

    ``source_key`` is a ``sha1_hex`` value, either copied verbatim from an
    upstream hashed export or computed from a plaintext email. ``real_email``,
    when recovered, is used as the account's actual email so it can merge
    with an existing or future real account; when it is not available, the
    account falls back to a synthetic, clearly-marked identity keyed only by
    ``source_key``.
    """

    if real_email:
        return _get_or_create_by_real_email(real_email)
    return _get_or_create_synthetic(source_key)


def get_or_create_enrollment(user: User, cohort: Cohort) -> tuple[Enrollment, bool]:
    return Enrollment.objects.get_or_create(student=user, course=cohort)


def anonymous_display_name() -> str:
    return generate_random_name()
