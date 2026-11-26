"""The learner-profile gate in front of course registration.

Registering for a course (a campaign registration or a cohort enrollment)
needs a complete :class:`~courses.models.learner_profile.LearnerProfile`, and
one the learner has looked at recently.  This module is the one place that
decides which of three states a signed-in account is in:

* ``missing`` -- no profile row, or a required field is empty: registration
  first sends the learner to the profile form;
* ``stale`` -- complete, but last saved or confirmed longer ago than
  ``settings.LEARNER_PROFILE_CONFIRMATION_MAX_AGE_DAYS`` (or never): a short
  "Is this still up to date?" review comes first;
* ``ok`` -- straight to registration.

Every redirect it builds carries ``next=`` back to where the learner was, and
only a same-site path survives (:func:`safe_next_url`).
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from courses.models.learner_profile import LearnerProfile, learner_profile_for

#: The profile fields registration cannot go ahead without: the certificate
#: name, the country (registration derives the region from it) and the role,
#: which doubles as work status (student, engineer, founder, ...).
REQUIRED_PROFILE_FIELDS = ("certificate_name", "country", "registration_role")

GATE_MISSING = "missing"
GATE_STALE = "stale"
GATE_OK = "ok"


def confirmation_max_age() -> timedelta:
    return timedelta(days=int(settings.LEARNER_PROFILE_CONFIRMATION_MAX_AGE_DAYS))


def missing_required_fields(profile: LearnerProfile | None) -> tuple[str, ...]:
    """The required profile fields that are still empty, in form order."""

    if profile is None:
        return REQUIRED_PROFILE_FIELDS
    return tuple(
        name for name in REQUIRED_PROFILE_FIELDS if not (getattr(profile, name) or "").strip()
    )


def confirmation_is_due(profile: LearnerProfile, now=None) -> bool:
    """Whether the profile's last confirmation is older than the max age.

    Exactly the max age old still counts as current; one second more is due.
    A profile never confirmed (imported, or written before the timestamp
    existed) is due.
    """

    confirmed_at = profile.profile_confirmed_at
    if confirmed_at is None:
        return True
    now = now or timezone.now()
    return now - confirmed_at > confirmation_max_age()


def profile_gate_state(user, now=None) -> str:
    profile = learner_profile_for(user)
    if missing_required_fields(profile):
        return GATE_MISSING
    assert profile is not None
    if confirmation_is_due(profile, now):
        return GATE_STALE
    return GATE_OK


def mark_profile_confirmed(profile: LearnerProfile, now=None) -> None:
    profile.profile_confirmed_at = now or timezone.now()


def safe_next_url(request, candidate: str | None, default: str = "/") -> str:
    """``candidate`` when it is a same-site URL, else ``default``.

    Absolute URLs to another host, scheme-relative ``//evil`` and
    ``javascript:`` all fall back, so ``next=`` can never become an open
    redirect.
    """

    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return default


def _with_next(url_name: str, next_url: str) -> str:
    return f"{reverse(url_name)}?{urlencode({'next': next_url})}"


def profile_form_url(next_url: str) -> str:
    return _with_next("account_welcome", next_url)


def profile_review_url(next_url: str) -> str:
    return _with_next("account_profile_review", next_url)


def profile_gate_redirect_url(user, next_url: str, now=None) -> str | None:
    """Where a signed-in learner must go before registering, or ``None``."""

    state = profile_gate_state(user, now)
    if state == GATE_MISSING:
        return profile_form_url(next_url)
    if state == GATE_STALE:
        return profile_review_url(next_url)
    return None
