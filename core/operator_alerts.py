"""Send bounded operational failure notices without recipient or provider data."""

import re
from collections.abc import Mapping
from uuid import UUID

import requests
from django.conf import settings

EVENTS = frozenset(
    {
        "registration_contacts_failed",
        "application_notification_failed",
        "application_notification_queue_failed",
    }
)
IDENTIFIERS = frozenset({"registration_id", "delivery_id", "job_id", "notification_id", "user_id"})


class OperatorAlertError(RuntimeError):
    """Only a fixed diagnostic code may escape the alert transport boundary."""


def _identifier(value):
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        try:
            parsed = UUID(value)
        except ValueError:
            pass
        else:
            if str(parsed) == value:
                return value
    raise OperatorAlertError("invalid_identifier")


def notify_operator_alert(event: str, identifiers: Mapping) -> None:
    """Return only after Telegram acknowledges a safe notice; otherwise raise.

    Callers retain their failed state and handle this error independently of
    registration submission. Never pass request bodies or exception text here.
    """
    if not isinstance(event, str) or event not in EVENTS or not isinstance(identifiers, Mapping):
        raise OperatorAlertError("invalid_event")
    if not identifiers or not identifiers.keys() <= IDENTIFIERS:
        raise OperatorAlertError("invalid_identifiers")
    lines = [f"Website operational alert: {event}"]
    for key, value in sorted(identifiers.items()):
        lines.append(f"{key}={_identifier(value)}")

    token = getattr(settings, "OPERATOR_ALERT_TELEGRAM_BOT_TOKEN", "")
    chat = getattr(settings, "OPERATOR_ALERT_TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise OperatorAlertError("missing_configuration")
    if (
        not isinstance(token, str)
        or not isinstance(chat, str)
        or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token)
        or not re.fullmatch(r"-?[0-9]+", chat)
    ):
        raise OperatorAlertError("invalid_configuration")
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": "\n".join(lines)},
            timeout=10,
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise OperatorAlertError("rejected")
        result = response.json()
    except OperatorAlertError:
        raise
    except Exception:
        # Requests errors contain the credential-bearing URL; Telegram bodies
        # can contain arbitrary data. Neither may escape, even as a cause.
        raise OperatorAlertError("transport_failed") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise OperatorAlertError("unacknowledged")
    message = result.get("result")
    if (
        not isinstance(message, dict)
        or not isinstance(message.get("message_id"), int)
        or isinstance(message["message_id"], bool)
        or message["message_id"] < 1
    ):
        raise OperatorAlertError("unacknowledged")
