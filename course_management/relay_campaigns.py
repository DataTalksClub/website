"""Submit-only Relay client helpers for bulk campaigns and status reads."""

from __future__ import annotations

import logging

import requests
from community_base.kernel.conf import get as get_cb_setting

logger = logging.getLogger(__name__)

DEFAULT_AUDIENCE = "datatalksclub"
DEFAULT_TIMEOUT_SECONDS = 30.0


class RelayCampaignError(RuntimeError):
    """Raised when a submit-only Relay request is rejected or unconfigured."""


def _relay_config() -> tuple[str, str]:
    base_url = str(get_cb_setting("RELAY_BASE_URL") or "").strip().rstrip("/")
    api_key = str(get_cb_setting("RELAY_API_KEY") or "").strip()
    return base_url, api_key


def _relay_request(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    session=None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    base_url, api_key = _relay_config()
    if not base_url or not api_key:
        raise RelayCampaignError("RELAY_BASE_URL and RELAY_API_KEY must be configured")

    http = session or requests
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    response = http.request(
        method,
        f"{base_url}{path}",
        json=payload,
        headers=headers,
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise RelayCampaignError(
            f"Relay {method} {path} returned HTTP {response.status_code}: {response.text[:200]}"
        )
    return response.json() if response.content else {}


def submit_campaign(
    external_key: str,
    *,
    subject: str,
    template_key: str,
    recipients: list[dict],
    context: dict | None = None,
    category_tag: str = "",
    sender_id: str = "",
    audience: str = DEFAULT_AUDIENCE,
    allow_unverified: bool = False,
    session=None,
) -> dict:
    """Submit a campaign and all recipients in ONE request to Relay."""
    payload = {
        "audience": audience,
        "subject": subject,
        "template_key": template_key,
        "context": context or {},
        "recipients": recipients,
        "allow_unverified": allow_unverified,
        "queue": True,
    }
    if category_tag:
        payload["category_tag"] = category_tag
    if sender_id:
        payload["sender_id"] = sender_id
    return _relay_request(
        "POST",
        f"/api/campaigns/{external_key}/submit",
        payload=payload,
        session=session,
    )


def submit_transient_list(
    *,
    list_key: str,
    name: str,
    template_key: str,
    idempotency_key: str,
    members: list[dict],
    context: dict | None = None,
    category: str = "",
    audience: str = DEFAULT_AUDIENCE,
    session=None,
) -> dict:
    """Submit a transient recipient list and all members in ONE request to Relay."""
    payload = {
        "audience": audience,
        "list": {"key": list_key, "name": name},
        "template_key": template_key,
        "idempotency_key": idempotency_key,
        "context": context or {},
        "members": members,
    }
    if category:
        payload["category"] = category
    return _relay_request(
        "POST",
        "/api/transient-recipient-lists/transactional-send",
        payload=payload,
        session=session,
    )


def get_campaign_status(external_key: str, *, session=None) -> dict | None:
    """Read campaign sent / delivered / pending counts from Relay."""
    base_url, api_key = _relay_config()
    if not base_url or not api_key or not external_key:
        return None
    try:
        data = _relay_request(
            "GET",
            f"/api/campaigns/{external_key}",
            session=session,
            timeout=10.0,
        )
    except (RelayCampaignError, requests.RequestException):
        return None
    campaign = (data or {}).get("campaign")
    if not isinstance(campaign, dict):
        return None
    recipient_count = int(campaign.get("recipient_count") or 0)
    sent_count = int(campaign.get("sent_count") or 0)
    skipped_count = int(campaign.get("skipped_count") or 0)
    delivered_count = int(campaign.get("delivered_count") or 0)
    pending_count = campaign.get("pending_count")
    if pending_count is None:
        pending_count = max(recipient_count - sent_count - skipped_count, 0)
    return {
        "external_key": external_key,
        "status": campaign.get("status") or "",
        "recipient_count": recipient_count,
        "sent_count": sent_count,
        "delivered_count": delivered_count,
        "pending_count": int(pending_count),
        "skipped_count": skipped_count,
    }
