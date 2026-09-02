"""Database-owned selectors for the public course catalogue surfaces.

Specification 01 ("Data ownership") and specification 04 make ``courses.Course`` and
``courses.Cohort`` the owners of course facts, so every public surface that advertises a
cohort reads them here rather than a checked projection artefact.

These selectors serve the site's front door.  A partially populated or unreachable
database must degrade to an empty catalogue, never to a 500, so the reads fail soft and
say so in the log instead of propagating.  Visibility is enforced in one place: only a
``visible`` cohort of a ``visible`` course is ever returned.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import date

from django.db import DatabaseError
from django.db.models import Count

from courses.models.cohort import Cohort

logger = logging.getLogger(__name__)


def visible_course_list_queryset():
    courses = Cohort.objects.filter(visible=True, course__visible=True)
    homework_count = Count("homework", distinct=True)
    project_count = Count("project", distinct=True)
    learner_count = Count("enrollment", distinct=True)
    courses = courses.annotate(
        homework_count=homework_count,
        project_count=project_count,
        learner_count=learner_count,
    )
    courses = courses.select_related("course").prefetch_related(
        "homework_set", "project_set"
    )
    return courses.order_by("-id")


def cohort_recency_key(cohort: Cohort) -> tuple[int, date, int]:
    """Order editions of one family deterministically: year, then start, then row."""

    return (cohort.year, cohort.start_date or date.min, cohort.id)


def newest_cohort(cohorts: Iterable[Cohort]) -> Cohort | None:
    """Return the newest edition of the given cohorts, or ``None`` for none at all."""

    return max(cohorts, key=cohort_recency_key, default=None)


def latest_visible_cohort_per_family() -> dict[str, Cohort]:
    """Map every visible course family slug to its newest visible cohort.

    Returns an empty mapping when the database is empty or unreachable; the callers
    are public pages that must still render.
    """

    latest: dict[str, Cohort] = {}
    try:
        cohorts = list(visible_course_list_queryset())
    except DatabaseError:
        logger.warning("Course catalogue read failed; rendering an empty catalogue.")
        return {}
    for cohort in cohorts:
        family_slug = cohort.course.slug
        current = latest.get(family_slug)
        if current is None or cohort_recency_key(cohort) > cohort_recency_key(current):
            latest[family_slug] = cohort
    return latest


def latest_visible_cohort_for_families(family_slugs: Sequence[str]) -> Cohort | None:
    """Return the newest visible cohort across the given family slugs.

    A page that speaks about one course passes every family slug that course is
    currently stored under, so a split family (issue #308) still resolves to its
    newest edition instead of an older duplicate.
    """

    latest = latest_visible_cohort_per_family()
    return newest_cohort(
        cohort for slug, cohort in latest.items() if slug in set(family_slugs)
    )
