"""Preference resolution for the package mail sends.

Since D1.2ca the category opt-outs live on the site user (see
``accounts.email_preferences``): the resolver suppresses a category only
when the recipient has an account and has explicitly opted out. Sends
without a user row (guest registrations) and unset fields stay allowed;
Relay-side global unsubscribe still applies on top.
"""

from __future__ import annotations

from accounts.email_preferences import CATEGORY_FIELDS


def resolve_mail_preference(*, purpose: str, category: str, to: str, user):
    """Allow or suppress one delivery; a string return is a reason code."""

    del purpose, to

    if user is None or not category:
        return True

    field = CATEGORY_FIELDS.get(category)
    if field is None:
        return True

    if getattr(user, field, None) is False:
        return f"opted out of {category}"
    return True
