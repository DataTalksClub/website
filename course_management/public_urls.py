"""Absolute public URLs for notification emails.

Relocated from the retired Datamailer client (D1.2ca). Notifications are
built without a request, so they cannot fall back to the request host the
way in-app confirmation emails do. If ``PUBLIC_BASE_URL`` is unset or
lacks a scheme/host we must still emit an absolute URL with a real host -
otherwise emails go out with unusable hostless links like
``http:///course-slug/leaderboard``.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse
from django.urls import reverse
from django.conf import settings

logger = logging.getLogger(__name__)


def public_url(path: str) -> str:
    base_url = notification_base_url()
    normalized_base_url = f"{base_url}/"
    normalized_path = path.lstrip("/")
    return urljoin(normalized_base_url, normalized_path)


def notification_base_url() -> str:
    base_url = (getattr(settings, "PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme and parsed.netloc:
        return base_url

    fallback = _fallback_base_url()
    if base_url:
        logger.warning(
            "PUBLIC_BASE_URL=%r has no scheme/host; using %r for "
            "notification links.",
            base_url,
            fallback,
        )
    return fallback


def _fallback_base_url() -> str:
    for host in getattr(settings, "ALLOWED_HOSTS", []):
        if host and host != "*":
            return f"https://{host}"
    return "https://courses.datatalks.club"


def cohort_route_kwargs(cohort):
    """Return canonical public route arguments for a cohort-owned object."""

    return {
        "course_slug": cohort.course.slug,
        "cohort_identifier": cohort.identifier,
    }


def public_route_url(route_name, route_kwargs=None):
    if route_kwargs is None:
        path = reverse(route_name)
    else:
        path = reverse(route_name, kwargs=route_kwargs)
    return public_url(path)
