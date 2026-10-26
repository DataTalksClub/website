"""Preference resolution for the package mail sends.

Since D1.2ca the category opt-outs live on the site user (see
``accounts.email_preferences``): the resolver suppresses a category only
when the recipient has an account and has explicitly opted out. Sends
without a user row (guest registrations) and unset fields stay allowed;
Relay-side global unsubscribe still applies on top.

Transactional send policy: while ``relay.transactional_dry_run`` is true
(``RELAY_TRANSACTIONAL_DRY_RUN=1``), each new package mail request is
suppressed as ``transactional_dry_run`` -- an audited SUPPRESSED delivery
with no Relay contact. The package Relay backend has no dry-run of its own
(the legacy ``DATAMAILER_TRANSACTIONAL_DRY_RUN`` flag guards nothing since
the Datamailer client was retired), so this resolver is the enqueue guard.
Already queued deliveries do not consult it again. The current per-environment
policy and activation checks live in ``_docs/runbooks/transactional-send-policy.md``.
"""

from __future__ import annotations

from accounts.email_preferences import CATEGORY_FIELDS
from core.runtime_config import get_bool_setting


def resolve_mail_preference(*, purpose: str, category: str, to: str, user):
    """Allow or suppress one delivery; a string return is a reason code."""

    del purpose, to

    if get_bool_setting("relay.transactional_dry_run"):
        return "transactional_dry_run"

    if user is None or not category:
        return True

    field = CATEGORY_FIELDS.get(category)
    if field is None:
        return True

    if getattr(user, field, None) is False:
        return f"opted out of {category}"
    return True
