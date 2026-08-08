from typing import Any

from django.conf import settings
from django.http import HttpRequest


def site_context(request: HttpRequest) -> dict[str, Any]:
    del request
    return {
        "brand_name": settings.SITE_NAME,
        "app_version": settings.APP_VERSION,
        # Canonicals are supplied explicitly by an owning view/content source.
        "canonical_url": None,
    }
