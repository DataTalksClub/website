import logging
from typing import Any

import requests

from ..client import DatamailerClient, DatamailerConfig
from ..redacted_errors import redacted_contact_error

logger = logging.getLogger(__name__)


def get_contact_status(email: str) -> dict[str, Any] | None:
    config = DatamailerConfig.from_settings()
    if config is None:
        return None

    client = DatamailerClient(config)

    try:
        return client.contacts.contact_status(email)
    except requests.RequestException as error:
        # The looked-up address is a query parameter on this call, so the
        # original exception message and its traceback both contain it.  Log
        # the restated failure without `exc_info`.
        failure = redacted_contact_error("contact status lookup", error)
        logger.error("%s", failure)
        if not config.strict:
            return None

    # Raised after the handler has exited, not `from None` inside it: `from
    # None` clears `__cause__` but leaves `__context__` pointing at the
    # original exception, whose message is the URL with the address in it.
    # Out here nothing is being handled, so there is no context to inherit.
    raise failure


def get_contact_history(
    contact_id: int,
    *,
    limit: int = 25,
) -> dict[str, Any] | None:
    config = DatamailerConfig.from_settings()
    if config is None:
        return None

    client = DatamailerClient(config)

    try:
        return client.contacts.contact_history(contact_id, limit=limit)
    except requests.RequestException:
        logger.exception("Datamailer contact history lookup failed")
        if config.strict:
            raise
        return None


def get_email_status(
    email: str, *, limit: int = 25
) -> dict[str, Any] | None:
    status = get_contact_status(email)
    if status is None:
        return None

    contact_id = status.get("contact_id")
    history = None
    if contact_id:
        contact_id_value = int(contact_id)
        history = get_contact_history(contact_id_value, limit=limit)

    return {
        "status": status,
        "history": history,
    }


def get_transactional_message_status(
    message_id: int,
) -> dict[str, Any] | None:
    config = DatamailerConfig.from_settings()
    if config is None:
        return None

    client = DatamailerClient(config)

    try:
        return client.transactional.transactional_message_status(message_id)
    except requests.RequestException:
        logger.exception(
            "Datamailer transactional message status lookup failed"
        )
        if config.strict:
            raise
        return None
