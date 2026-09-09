"""Preference resolution for the package mail sends (D1.2b).

The datamailer preference store (backed by the profile page) keeps
holding opt-outs while the sends run through the package, so the site
resolves the package's ``MAIL_PREFERENCE_RESOLVER`` hook against it.
Failures fail open: an unreachable datamailer must not silently drop a
transactional confirmation, and the datamailer is configured away in
tests, which then allow everything. D1.2c replaces the store.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def resolve_mail_preference(*, purpose: str, category: str, to: str, user):
    """Allow or suppress one delivery; a string return is a reason code."""

    if user is None or not category:
        return True

    from course_management.datamailer.preferences import (
        get_email_preferences_for_user,
    )

    try:
        preferences = get_email_preferences_for_user(user)
    except Exception:
        # Fail open: an unreachable datamailer (including the test
        # runtime's network guard) must not silently drop a confirmation.
        logger.exception("mail preference lookup failed for user_id=%s", user.pk)
        return True
    if preferences is None:
        return True
    if preferences.get(category) is False:
        return f"opted out of {category}"
    return True
