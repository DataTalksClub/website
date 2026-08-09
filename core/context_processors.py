from typing import Any

from django.conf import settings
from django.http import HttpRequest

EXPLICIT_PUBLIC_CANONICALS = {
    "/courses/": "https://datatalks.club/courses/",
}


def site_context(request: HttpRequest) -> dict[str, Any]:
    return {
        "brand_name": settings.SITE_NAME,
        "app_version": settings.APP_VERSION,
        # Every shared-view canonical is an explicit mapping, never host/path inference.
        "canonical_url": EXPLICIT_PUBLIC_CANONICALS.get(request.path),
    }
