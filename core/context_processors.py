from typing import Any

from django.conf import settings
from django.http import HttpRequest


def site_context(request: HttpRequest) -> dict[str, Any]:
    return {
        "brand_name": settings.SITE_NAME,
        "app_version": settings.APP_VERSION,
        "canonical_url": f"{settings.CANONICAL_ORIGIN}{request.path}",
    }
