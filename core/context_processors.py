import logging
from typing import Any

from django.conf import settings
from django.db import DatabaseError
from django.http import HttpRequest

from core.configuration import InvalidOperationalSetting
from core.site_settings import public_announcement

logger = logging.getLogger(__name__)

EXPLICIT_PUBLIC_CANONICALS = {
    "/courses": "https://datatalks.club/courses",
}


def site_context(request: HttpRequest) -> dict[str, Any]:
    announcement = None
    resolver_match = request.resolver_match
    if resolver_match is None or resolver_match.namespace != "studio":
        try:
            announcement = public_announcement()
        except (DatabaseError, InvalidOperationalSetting) as error:
            logger.warning(
                "Public site announcement is unavailable (%s).",
                type(error).__name__,
            )
    return {
        "brand_name": settings.SITE_NAME,
        "VERSION": settings.VERSION,
        "app_version": settings.APP_VERSION,
        "site_announcement": announcement,
        # Every shared-view canonical is an explicit mapping, never host/path inference.
        "canonical_url": EXPLICIT_PUBLIC_CANONICALS.get(request.path),
    }
