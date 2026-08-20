from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.safestring import mark_safe

from courses.models import CurriculumFormat, Unit
from courses.registration import render_markdown
from courses.views.url_utils import get_cohort_or_404


def _unit_url(unit: Unit) -> str:
    cohort = unit.module.cohort
    return reverse(
        "unit",
        kwargs={
            "course_slug": cohort.course.slug,
            "cohort_identifier": cohort.identifier,
            "module_slug": unit.module.slug,
            "unit_slug": unit.slug,
        },
    )


def _adjacent_units(unit: Unit) -> tuple[Unit | None, Unit | None]:
    siblings = Unit.objects.filter(module=unit.module)
    previous_unit = (
        siblings.filter(Q(position__lt=unit.position) | Q(position=unit.position, id__lt=unit.id))
        .order_by("-position", "-id")
        .first()
    )
    next_unit = (
        siblings.filter(Q(position__gt=unit.position) | Q(position=unit.position, id__gt=unit.id))
        .order_by("position", "id")
        .first()
    )
    return previous_unit, next_unit


def unit_view(
    request: HttpRequest,
    course_slug: str,
    cohort_identifier: str,
    module_slug: str,
    unit_slug: str,
) -> HttpResponse:
    cohort = get_cohort_or_404(
        course_slug,
        cohort_identifier,
        curriculum_format=CurriculumFormat.MODULES,
    )
    unit = get_object_or_404(
        Unit.objects.select_related(
            "module",
            "module__cohort",
            "module__cohort__course",
            "module__terminal_homework",
        ),
        module__cohort=cohort,
        module__slug=module_slug,
        slug=unit_slug,
    )
    previous_unit, next_unit = _adjacent_units(unit)
    canonical_path = _unit_url(unit)
    rendered_content = mark_safe(render_markdown(unit.content_markdown))

    return render(
        request,
        "courses/unit.html",
        {
            "course_family": cohort.course,
            "cohort": cohort,
            "module": unit.module,
            "unit": unit,
            "unit_content_html": rendered_content,
            "previous_unit": previous_unit,
            "next_unit": next_unit,
            "canonical_url": f"https://datatalks.club{canonical_path}",
        },
    )
