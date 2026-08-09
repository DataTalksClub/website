from typing import Any

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_safe

from content.public_data import event_groups, public_projection
from content.review_projection import review_projection

DEVELOPMENT_ROBOTS_BODY = "User-agent: *\nDisallow: /\n"


@require_safe
def home(request: HttpRequest):
    projection = public_projection()
    events = event_groups()
    return render(
        request,
        "core/home.html",
        {
            "canonical_url": "https://datatalks.club/",
            "upcoming_events": events.upcoming[:3],
            "recent_events": events.recent[:1],
            "featured_course": review_projection()["course"],
            "article": projection["articles"][0],
            "podcast": projection["podcasts"][0],
            "book": projection["books"][0],
            "wiki_page": projection["wiki"][0],
            "counts": projection["manifest"]["counts"],
        },
    )


def _development_seo_response(body: str, content_type: str) -> HttpResponse:
    if not settings.NOINDEX:
        raise Http404
    return HttpResponse(body, content_type=content_type)


@require_safe
def robots(request: HttpRequest) -> HttpResponse:
    del request
    return _development_seo_response(
        DEVELOPMENT_ROBOTS_BODY,
        "text/plain; charset=utf-8",
    )


@require_safe
def sitemap(request: HttpRequest) -> HttpResponse:
    del request
    from content.public_views import production_sitemap

    body = production_sitemap()
    return HttpResponse(body, content_type="application/xml; charset=utf-8")


@require_GET
def liveness(request: HttpRequest) -> JsonResponse:
    del request
    return JsonResponse({"status": "ok", "version": settings.APP_VERSION})


def _configuration_status() -> tuple[str, list[str]]:
    missing = [
        name for name in settings.REQUIRED_BOOTSTRAP_SETTINGS if not getattr(settings, name, None)
    ]
    return ("ok", []) if not missing else ("error", missing)


def _database_status() -> tuple[str, str | None]:
    try:
        connection.ensure_connection()
    except Exception:
        return "error", "database unavailable"
    return "ok", None


def _migration_status() -> tuple[str, str | None]:
    try:
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        if executor.migration_plan(targets):
            return "error", "unapplied migrations"
    except Exception:
        return "error", "migration state unavailable"
    return "ok", None


@require_GET
def readiness(request: HttpRequest) -> JsonResponse:
    del request
    config_status, missing = _configuration_status()
    database_status, database_error = _database_status()
    migration_status, migration_error = (
        _migration_status() if database_status == "ok" else ("error", "database unavailable")
    )

    checks: dict[str, dict[str, Any]] = {
        "configuration": {"status": config_status},
        "database": {"status": database_status},
        "migrations": {"status": migration_status},
    }
    if missing:
        checks["configuration"]["missing"] = missing
    if database_error:
        checks["database"]["message"] = database_error
    if migration_error:
        checks["migrations"]["message"] = migration_error

    ready = all(check["status"] == "ok" for check in checks.values())
    return JsonResponse(
        {"status": "ready" if ready else "not_ready", "checks": checks},
        status=200 if ready else 503,
    )
