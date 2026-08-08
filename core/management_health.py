from django.conf import settings


def read_management_health(query: object, *, context: object) -> dict[str, str]:
    del query, context
    return {"status": "ok", "version": settings.APP_VERSION}


def management_health_factory() -> dict[str, str]:
    return {"status": "ok", "version": settings.APP_VERSION}
