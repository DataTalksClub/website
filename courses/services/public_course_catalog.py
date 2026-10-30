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
from datetime import date

from django.db import DatabaseError, models
from django.db.models import Count, IntegerField, OuterRef, Prefetch, Subquery
from django.db.models.functions import Coalesce

from courses.models.cohort import Cohort, CohortBuildItem, Enrollment
from courses.models.curriculum import Module
from courses.models.homework import Homework
from courses.models.project import Project

logger = logging.getLogger(__name__)


def _cohort_relation_count(model: type[models.Model], fk_field: str) -> Coalesce:
    return Coalesce(
        Subquery(
            model.objects.filter(**{fk_field: OuterRef("pk")})
            .order_by()
            .values(fk_field)
            .annotate(total=Count("pk"))
            .values("total"),
            output_field=IntegerField(),
        ),
        0,
    )


def visible_course_list_queryset(*, prefetch_assignments: bool = True):
    """The annotated visible cohorts with the relations public surfaces read.

    ``homework_set``/``project_set`` feed the course list's assignment labels
    (``courses.views.course_homepage.get_course_assignments``); the homepage
    catalogue reads only the annotations and ``build_items``, so it opts out
    with ``prefetch_assignments=False`` and skips two full-table prefetches
    per render.  ``build_items`` stays prefetched in position order for every
    caller: ``core.home_content`` reads the cached relation.
    """

    courses = Cohort.objects.filter(visible=True, course__visible=True)
    # Correlated subqueries avoid the 4-way LEFT OUTER JOIN Cartesian product
    # across homework x project x enrollment x modules on every catalogue read.
    courses = courses.annotate(
        homework_count=_cohort_relation_count(Homework, "course"),
        project_count=_cohort_relation_count(Project, "course"),
        learner_count=_cohort_relation_count(Enrollment, "course"),
        module_count=_cohort_relation_count(Module, "cohort"),
    )
    courses = courses.select_related("course")
    if prefetch_assignments:
        courses = courses.prefetch_related("homework_set", "project_set")
    courses = courses.prefetch_related(
        Prefetch(
            "build_items",
            queryset=CohortBuildItem.objects.order_by("position", "pk"),
        ),
    )
    return courses.order_by("-id")


def cohort_recency_key(cohort: Cohort) -> tuple[int, date, int]:
    """Order editions of one family deterministically: year, then start, then row."""

    return (cohort.year, cohort.start_date or date.min, cohort.id)


def latest_visible_cohort_per_family(*, prefetch_assignments: bool = False) -> dict[str, Cohort]:
    """Map every visible course family slug to its newest visible cohort.

    Returns an empty mapping when the database is empty or unreachable; the callers
    are public pages that must still render.

    The homepage catalogue (the only production caller, via
    ``core.home_content.course_catalog``) reads the annotations and
    ``build_items`` but never the assignment rows, so assignment prefetches
    stay off by default.  Pass ``prefetch_assignments=True`` when the caller
    reads ``homework_set``/``project_set`` (the course list does).
    """

    latest: dict[str, Cohort] = {}
    try:
        cohorts = list(visible_course_list_queryset(prefetch_assignments=prefetch_assignments))
    except DatabaseError:
        logger.warning("Course catalogue read failed; rendering an empty catalogue.")
        return {}
    for cohort in cohorts:
        family_slug = cohort.course.slug
        current = latest.get(family_slug)
        if current is None or cohort_recency_key(cohort) > cohort_recency_key(current):
            latest[family_slug] = cohort
    return latest
