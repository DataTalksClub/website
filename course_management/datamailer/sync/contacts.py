import logging

import requests

from ..client import DatamailerClient, DatamailerConfig
from ..payloads.base import contact_payload_for_user


logger = logging.getLogger(__name__)


def payload_with_configured_from_email(payload, config):
    if config.from_email and "from_email" not in payload:
        return payload | {"from_email": config.from_email}
    return payload


def handle_contact_sync_error(config, user):
    logger.exception(
        "Datamailer contact sync failed for user_id=%s",
        user.pk,
    )
    if config.strict:
        raise


def sync_contact(user, course=None) -> None:
    config = DatamailerConfig.from_settings()
    if config is None:
        return

    payload = contact_payload_for_user(user, course=course)
    if payload is None:
        return

    client = DatamailerClient(config)
    payload = payload_with_configured_from_email(payload, config)

    try:
        client.contacts.upsert_contact(payload)
    except requests.RequestException:
        handle_contact_sync_error(config, user)


def erase_contact_from_datamailer(
    user=None, *, user_id=None, email=None
) -> None:
    config = DatamailerConfig.from_settings()
    if config is None:
        return

    user_id, email = contact_erase_target(
        user, user_id=user_id, email=email
    )
    if not email:
        return

    erase_contact_now(config, user_id=user_id, email=email)


def contact_erase_target(user, *, user_id, email):
    user_id = contact_erase_user_id(user, user_id)
    email = contact_erase_email(user, email)
    return user_id, email


def contact_erase_user_id(user, user_id):
    if user_id is None and user is not None:
        return user.pk
    return user_id


def contact_erase_email(user, email):
    if email is None and user is not None:
        email = user.email
    email_value = email or ""
    stripped_email = email_value.strip()
    normalized_email = stripped_email.lower()
    return normalized_email


def contact_erase_ordering_key(user_id, email):
    if user_id is not None:
        return f"user:{user_id}"
    return f"email:{email}"


def erase_contact_now(config, *, user_id, email):
    """Erase the contact directly, best-effort like the upsert.

    The outbox that used to carry this erase is gone (D1.2b); the row is
    recipient-identifying, so the erase retries on the next deletion
    attempt rather than living in a queue. ``user_id`` is kept in the
    signature for the erase audit callers.
    """

    client = DatamailerClient(config)
    try:
        client.contacts.erase_contact(email)
    except requests.RequestException:
        handle_contact_sync_error(config, type("E", (), {"pk": user_id})())
