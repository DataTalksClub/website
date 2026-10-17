"""Announcements use the same canonical routes as the learning flow."""

from django.conf import settings
from django.urls import reverse

from courses.services.course_context import context_query


def absolute_route(name, **kwargs):
    return settings.CANONICAL_ORIGIN.rstrip("/") + reverse(name, kwargs=kwargs)


def cohort_kwargs(cohort):
    return {"course_slug": cohort.course.slug, "cohort_identifier": cohort.identifier}


def cohort_url(cohort):
    return absolute_route("cohort", **cohort_kwargs(cohort))


def module_url(kind, row, cohort):
    if kind == "shared_module_placement":
        return absolute_route(
            "shared_module", course_slug=cohort.course.slug, module_slug=row.shared_module.slug
        ) + context_query(cohort)
    return absolute_route("cohort_module", **cohort_kwargs(cohort), module_slug=row.slug)


def assignment_url(kind, row, cohort):
    kwargs = cohort_kwargs(cohort)
    if kind == "project":
        return absolute_route("cohort_project", **kwargs, project_slug=row.slug)
    return absolute_route("cohort_homework", **kwargs, homework_slug=row.slug)
