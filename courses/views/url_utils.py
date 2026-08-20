import logging
from urllib.parse import urljoin

from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django.urls import reverse

from courses.models import Cohort

logger = logging.getLogger(__name__)


def cohort_url_kwargs(cohort: Cohort) -> dict[str, object]:
    """Return the canonical public route arguments for a cohort."""

    return {
        "course_slug": cohort.course.slug,
        "cohort_year": cohort.year,
    }


def cohort_url(cohort: Cohort, route_name: str = "course", **kwargs) -> str:
    """Build a public cohort URL without reusing the legacy edition slug."""

    return reverse(
        route_name,
        kwargs={**cohort_url_kwargs(cohort), **kwargs},
    )


def get_cohort_or_404(
    course_slug: str,
    cohort_year: int | None = None,
    **filters,
) -> Cohort:
    """Resolve a canonical family/year route, with a legacy test shim."""

    if cohort_year is None:
        return get_object_or_404(Cohort, slug=course_slug, **filters)
    return get_object_or_404(
        Cohort,
        course__slug=course_slug,
        year=cohort_year,
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
