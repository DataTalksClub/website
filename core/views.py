from typing import Any

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    HttpResponsePermanentRedirect,
    JsonResponse,
)
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_safe

from content.public_data import event_groups, ordered_podcasts, public_projection
from core.home_content import (
    FEATURED_BUILD_ITEMS,
    FEATURED_COHORT_FORMAT,
    FEATURED_COHORT_SUMMARY,
    FEATURED_FAMILY,
    MEMBER_STORIES,
    course_catalog,
    event_time_display,
    published_display,
    reading_minutes,
    spelled_count,
    wiki_graph,
    wiki_topics,
)
from core.sponsor_history import PAST_SUPPORTERS, featured_supporters

DEVELOPMENT_ROBOTS_BODY = "User-agent: *\nDisallow: /\n"
PRODUCTION_ROBOTS_BODY = (
    "User-agent: *\n"
    "Disallow: /admin/\n"
    "Disallow: /_site/\n"
    "Disallow: /drafts/\n"
    "Disallow: /config/\n"
    "Disallow: /scripts/\n"
    "Disallow: /styles/\n"
    "Sitemap: https://datatalks.club/sitemap.xml\n"
    "Sitemap: https://datatalks.club/sitemaps/wiki.xml\n"
)
ROBOTS_CONTENT_TYPE = "text/plain; charset=utf-8"
ROBOTS_ALLOWED_METHODS = ("GET", "HEAD")


def management_slash_redirect(request: HttpRequest) -> HttpResponse:
    """Canonicalize only safe slashless management requests in one hop."""

    if request.method not in {"GET", "HEAD"}:
        return HttpResponseNotAllowed(("GET", "HEAD"))
    query = request.META.get("QUERY_STRING", "")
    destination = f"{request.path}/"
    if query:
        destination = f"{destination}?{query}"
    return HttpResponsePermanentRedirect(destination)


@require_safe
def home(request: HttpRequest):
    projection = public_projection()
    events = event_groups()
    catalog = course_catalog()
    article = projection["articles"][0]
    upcoming = tuple(
        {**event, "home_time": event_time_display(event["starts_at"])}
        for event in events.upcoming[:3]
    )
    # The featured panel is the newest visible cohort of the featured family, or nothing
    # at all when the database holds none; the template renders no hero in that case.
    #
    # The call to action goes to that cohort's own database-backed course page, the same
    # `/courses/<family>/<year>` route the catalogue cards use.  It used to go to a
    # hardcoded per-cohort landing route whose copy was written in Python, including a
    # "materials are still being finalized" notice that had gone stale; that route and its
    # page are gone, so there is one cohort page and the hero links to it.
    featured_entry = next((entry for entry in catalog if entry.family == FEATURED_FAMILY), None)
    featured_cohort_path = featured_entry.public_path if featured_entry is not None else ""
    return render(
        request,
        "core/home.html",
        {
            "canonical_url": "https://datatalks.club/",
            "upcoming_events": upcoming,
            "recent_events": events.recent[:1],
            "featured_cohort_path": featured_cohort_path,
            "featured_catalog_entry": featured_entry,
            "featured_cohort_format": FEATURED_COHORT_FORMAT,
            "featured_cohort_summary": FEATURED_COHORT_SUMMARY,
            "featured_build_items": FEATURED_BUILD_ITEMS,
            "catalog_courses": tuple(entry for entry in catalog if entry.family != FEATURED_FAMILY),
            "course_family_count": len(catalog),
            "course_family_word": spelled_count(len(catalog)),
            "member_stories": MEMBER_STORIES,
            "article": article,
            "article_published": published_display(article["published"]),
            "article_minutes": reading_minutes(article),
            "podcast": ordered_podcasts(projection["podcasts"])[0],
            "wiki_topics": wiki_topics(),
            "wiki_graph": wiki_graph(),
            "counts": projection["manifest"]["counts"],
            "sponsors": featured_supporters(),
        },
    )


@require_safe
def sponsors(request: HttpRequest) -> HttpResponse:
    return render(
        request,
        "core/sponsors.html",
        {
            "canonical_url": "https://datatalks.club/sponsors",
            "sponsors": featured_supporters(),
            "past_supporters": PAST_SUPPORTERS,
        },
    )


def _development_seo_response(body: str, content_type: str) -> HttpResponse:
    if not settings.NOINDEX:
        raise Http404
    return HttpResponse(body, content_type=content_type)


def robots(request: HttpRequest) -> HttpResponse:
    if request.method not in ROBOTS_ALLOWED_METHODS:
        not_allowed_response = HttpResponseNotAllowed(ROBOTS_ALLOWED_METHODS)
        not_allowed_response["Cache-Control"] = "no-store, max-age=0"
        return not_allowed_response
    if settings.NOINDEX:
        return _development_seo_response(DEVELOPMENT_ROBOTS_BODY, ROBOTS_CONTENT_TYPE)
    response = HttpResponse(PRODUCTION_ROBOTS_BODY, content_type=ROBOTS_CONTENT_TYPE)
    response["Cache-Control"] = "max-age=0, must-revalidate"
    return response


@require_safe
def sitemap(request: HttpRequest) -> HttpResponse:
    del request
    from content.public_views import production_sitemap

    body = production_sitemap()
    return HttpResponse(body, content_type="application/xml; charset=utf-8")


@require_GET
def liveness(request: HttpRequest) -> JsonResponse:
    del request
    return JsonResponse(settings.RUNTIME_IDENTITY.payload())


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
    payload: dict[str, Any] = settings.RUNTIME_IDENTITY.payload(
        status="ready" if ready else "not_ready"
    )
    payload["checks"] = checks
    return JsonResponse(payload, status=200 if ready else 503)
