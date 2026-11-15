"""Newsletter signup service wired to Relay's public list subscribe endpoint."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from community_base.kernel.conf import get
from community_base.mail.relay import RequestsTransport
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

logger = logging.getLogger(__name__)

NEWSLETTER_LIST_SLUG = "dtc-newsletter"
NEWSLETTER_CONTACT_EMAIL = "alexey@datatalks.club"
NEWSLETTER_TIMEOUT_SECONDS = 10
MAX_PAGE_URL_LENGTH = 2048

MESSAGES: dict[str, str] = {
    "subscribed": (
        "You're in! Your Slack invite is on its way. It usually arrives within 3 minutes."
    ),
    "already_subscribed": "You're already subscribed to our newsletter.",
    "not_resubscribed": (
        "This address unsubscribed earlier, so we can't add it again from this form. "
        f"Email {NEWSLETTER_CONTACT_EMAIL} and we'll sort it out."
    ),
    "invalid_email": "Please enter a valid email address.",
    "rate_limited": "Too many attempts. Please try again in a few minutes.",
    "failed": (
        "Something went wrong and we couldn't subscribe you. "
        f"Please try again, or email {NEWSLETTER_CONTACT_EMAIL}."
    ),
    "timeout": (
        f"The request took too long. Please try again, or email {NEWSLETTER_CONTACT_EMAIL}."
    ),
}


@dataclass(frozen=True)
class NewsletterSubscribeResult:
    """Normalized outcome returned by :func:`subscribe_to_newsletter`."""

    ok: bool
    status: str
    message: str
    http_status: int


def map_relay_outcome(status_code: int, body: object) -> NewsletterSubscribeResult:
    """Map one Relay public list subscribe response to a user-facing result."""

    if status_code == 200 and isinstance(body, dict):
        relay_status = body.get("status")
        if isinstance(relay_status, str) and relay_status in MESSAGES and relay_status != "failed":
            return NewsletterSubscribeResult(
                ok=True,
                status=relay_status,
                message=MESSAGES[relay_status],
                http_status=200,
            )

    if status_code == 400 and isinstance(body, dict):
        error = body.get("error")
        fields = error.get("fields") if isinstance(error, dict) else None
        if isinstance(fields, dict) and fields.get("email"):
            return NewsletterSubscribeResult(
                ok=False,
                status="invalid_email",
                message=MESSAGES["invalid_email"],
                http_status=400,
            )

    if status_code == 429:
        return NewsletterSubscribeResult(
            ok=False,
            status="rate_limited",
            message=MESSAGES["rate_limited"],
            http_status=429,
        )

    return NewsletterSubscribeResult(
        ok=False,
        status="failed",
        message=MESSAGES["failed"],
        http_status=502,
    )


def subscribe_to_newsletter(
    email: str,
    *,
    company: str = "",
    page: str = "",
    transport: RequestsTransport | None = None,
) -> NewsletterSubscribeResult:
    """Submit a newsletter signup to Relay's public list endpoint."""

    if company.strip():
        return NewsletterSubscribeResult(
            ok=True,
            status="subscribed",
            message=MESSAGES["subscribed"],
            http_status=200,
        )

    normalized_email = email.strip()
    if not normalized_email:
        return NewsletterSubscribeResult(
            ok=False,
            status="invalid_email",
            message=MESSAGES["invalid_email"],
            http_status=400,
        )
    try:
        validate_email(normalized_email)
    except ValidationError:
        return NewsletterSubscribeResult(
            ok=False,
            status="invalid_email",
            message=MESSAGES["invalid_email"],
            http_status=400,
        )

    base_url = str(get("RELAY_BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        logger.warning("newsletter_relay_unconfigured")
        return NewsletterSubscribeResult(
            ok=False,
            status="failed",
            message=MESSAGES["failed"],
            http_status=503,
        )

    endpoint = f"{base_url}/api/public/lists/{NEWSLETTER_LIST_SLUG}/subscribe"
    safe_page = page.strip()[:MAX_PAGE_URL_LENGTH]
    client_transport = transport or RequestsTransport()
    try:
        response = client_transport.request(
            "POST",
            endpoint,
            json={"email": normalized_email, "company": "", "page": safe_page},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=NEWSLETTER_TIMEOUT_SECONDS,
        )
    except requests.Timeout:
        logger.warning("newsletter_relay_timeout")
        return NewsletterSubscribeResult(
            ok=False,
            status="timeout",
            message=MESSAGES["timeout"],
            http_status=504,
        )
    except requests.RequestException as exc:
        logger.warning("newsletter_relay_request_failed", extra={"error_type": type(exc).__name__})
        return NewsletterSubscribeResult(
            ok=False,
            status="failed",
            message=MESSAGES["failed"],
            http_status=502,
        )

    try:
        body: object = response.json()
    except (ValueError, TypeError):
        body = None
    return map_relay_outcome(response.status_code, body)
