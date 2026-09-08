import logging
from urllib.parse import urljoin

from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.urls import reverse

from courses.models import Cohort

logger = logging.getLogger(__name__)


def canonical_cohort_url_kwargs(cohort: Cohort) -> dict[str, object]:
    """Route arguments for the canonical ``cohorts/<identifier>`` namespace."""

    return {
        "course_slug": cohort.course.slug,
        "cohort_identifier": cohort.identifier,
    }


def course_family_url(course) -> str:
    """The canonical course family landing path."""

    return reverse("course_family", kwargs={"course_slug": course.slug})


def shared_module_url(module) -> str:
    """The canonical, cohort-free shared module path."""

    return reverse(
        "shared_module",
        kwargs={
            "course_slug": module.curriculum.course.slug,
            "module_slug": module.slug,
        },
    )


def shared_lesson_url(lesson, *, cohort: Cohort | None = None) -> str:
    """The canonical shared lesson path, with optional explicit context."""

    path = reverse(
        "shared_lesson",
        kwargs={
            "course_slug": lesson.module.curriculum.course.slug,
            "module_slug": lesson.module.slug,
            "lesson_slug": lesson.slug,
        },
    )
    if cohort is not None:
        return f"{path}?cohort={cohort.identifier}"
    return path


def cohort_url(cohort: Cohort, route_name: str = "cohort", **kwargs) -> str:
    """Build a canonical ``cohorts/<identifier>`` public URL for a cohort.

    The identifier, not a legacy edition slug and not the calendar year, is
    the route identity.  Callers that still need the old two-segment shape
    reverse the legacy route names explicitly (W6 retires that surface).
    """

    return reverse(
        route_name,
        kwargs={**canonical_cohort_url_kwargs(cohort), **kwargs},
    )


def get_cohort_or_404(
    course_slug: str,
    cohort_identifier: str | int | None = None,
    **filters,
) -> Cohort:
    """Resolve a canonical family/cohort-identifier route.

    ``cohort_identifier`` is retained as the route argument name for the existing
    view contract, but its value is a slug-like cohort identifier rather than
    necessarily a calendar year.
    """

    if cohort_identifier is None:
        return get_object_or_404(Cohort, slug=course_slug, **filters)
    return get_object_or_404(
        Cohort,
        course__slug=course_slug,
        identifier=str(cohort_identifier),
        **filters,
    )


def absolute_url_with_fallback(
    request: HttpRequest, path: str, *, label: str
) -> str:
    """Resolve a path to an absolute URL.

    Prefers settings.PUBLIC_BASE_URL, then the request host, falling back to
    the first concrete ALLOWED_HOSTS entry when the request host is disallowed.
    ``label`` identifies the caller in the fallback warning log.
    """
    if public_base_url := absolute_public_base_url(path):
        return public_base_url

    try:
        return request.build_absolute_uri(path)
    except DisallowedHost:
        return fallback_absolute_url(request, path, label=label)


def absolute_public_base_url(path):
    if not settings.PUBLIC_BASE_URL:
        return ""
    base_url = f"{settings.PUBLIC_BASE_URL}/"
    normalized_path = path.lstrip("/")
    return urljoin(base_url, normalized_path)


def concrete_allowed_host():
    allowed_hosts = settings.ALLOWED_HOSTS
    for host in allowed_hosts:
        if host and host != "*":
            return host
    return "localhost"


def fallback_absolute_url(request, path, *, label):
    fallback_host = concrete_allowed_host()
    logger.warning(
        "Falling back to ALLOWED_HOSTS for %s update URL "
        "because request host is not allowed: %s",
        label,
        fallback_host,
    )
    fallback_base_url = f"{request.scheme}://{fallback_host}"
    return urljoin(fallback_base_url, path)
