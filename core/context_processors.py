import logging
from typing import Any

from django.conf import settings
from django.db import DatabaseError
from django.http import HttpRequest

from core.configuration import InvalidOperationalSetting
from core.navigation import public_primary_navigation
from core.site_settings import public_announcement
from courses.models import Cohort, Course

logger = logging.getLogger(__name__)

EXPLICIT_PUBLIC_CANONICALS = {
    "/courses": "https://datatalks.club/courses",
}

PUBLIC_COURSE_COHORT_ROUTE_NAMES = frozenset(
    {
        "course",
        "course_calendar",
        "dashboard",
        "enrollment",
        "update_enrollment_toggle",
        "leaderboard",
        "leaderboard_score_breakdown",
        "leaderboard_complaint",
        "list_all_project_submissions",
        "homework",
        "homework_statistics",
        "homework_submissions",
        "project",
        "project_list",
        "projects_eval",
        "project_results",
        "project_statistics",
        "project_submissions",
        "projects_eval_submit",
        "projects_eval_add",
        "projects_eval_delete",
    }
)

PRIMARY_NAVIGATION_PREFIXES = (
    ("events", ("/events",)),
    ("courses", ("/courses",)),
    ("blog", ("/blog",)),
    ("podcast", ("/podcast",)),
    ("wiki", ("/wiki",)),
    ("books", ("/books",)),
    ("docs", ("/docs",)),
    ("faq", ("/faq",)),
    ("slack", ("/slack",)),
)


def _primary_navigation_current(path: str) -> str:
    for key, prefixes in PRIMARY_NAVIGATION_PREFIXES:
        if any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes):
            return key
    return ""


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
    primary_navigation = public_primary_navigation()
    canonical_url = EXPLICIT_PUBLIC_CANONICALS.get(request.path)
    if (
        canonical_url is None
        and resolver_match is not None
        and not resolver_match.namespace
        and resolver_match.url_name in PUBLIC_COURSE_COHORT_ROUTE_NAMES
    ):
        course_slug = resolver_match.kwargs.get("course_slug")
        cohort_year = resolver_match.kwargs.get("cohort_year")
        if course_slug:
            if cohort_year is None:
                cohort = Cohort.objects.filter(slug=course_slug).first()
            else:
                cohort = (
                    Cohort.objects.filter(
                        course__slug=course_slug,
                        year=cohort_year,
                    )
                    .select_related("course")
                    .first()
                )
            if cohort is not None:
                canonical_url = f"https://datatalks.club{cohort.canonical_url_path}"
    if (
        canonical_url is None
        and resolver_match is not None
        and not resolver_match.namespace
        and resolver_match.url_name == "course_family"
    ):
        course_slug = resolver_match.kwargs.get("course_slug")
        if course_slug and Course.objects.filter(slug=course_slug).exists():
            canonical_url = f"https://datatalks.club/courses/{course_slug}"
    if (
        canonical_url is None
        and resolver_match is not None
        and not resolver_match.namespace
        and resolver_match.url_name == "registration_campaign"
    ):
        campaign_slug = resolver_match.kwargs.get("campaign_slug")
        if campaign_slug:
            canonical_url = f"https://datatalks.club/register/{campaign_slug}"
    return {
        "brand_name": settings.SITE_NAME,
        "VERSION": settings.VERSION,
        "app_version": settings.APP_VERSION,
        "primary_navigation_current": _primary_navigation_current(request.path),
        "primary_navigation": primary_navigation,
        "site_announcement": announcement,
        # Every shared-view canonical is an explicit mapping, never host/path inference.
        "canonical_url": canonical_url,
    }
