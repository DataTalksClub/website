from typing import Any

from django.conf import settings


def read_management_health(query: object, *, context: object) -> dict[str, Any]:
    del query, context
    return settings.RUNTIME_IDENTITY.payload()


def management_health_factory() -> dict[str, Any]:
    return settings.RUNTIME_IDENTITY.payload()
