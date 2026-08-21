from django.contrib.auth.decorators import login_required
from django.db.models import BooleanField, Exists, OuterRef, Value
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from courses.models import CurriculumFormat, Module, Unit, UnitReadState
from courses.services.unit_read_state import set_unit_read_state
from courses.views.url_utils import get_cohort_or_404


def module_url(module: Module) -> str:
    """Return the canonical internal URL for a curriculum module."""

    cohort = module.cohort
    return reverse(
        "module",
        kwargs={
            "course_slug": cohort.course.slug,
            "cohort_identifier": cohort.identifier,
            "module_slug": module.slug,
        },
    )


def module_rail_context(
    request: HttpRequest,
    module: Module,
    *,
    current_unit: Unit | None = None,
) -> dict[str, object]:
    """Build the shared module-rail context for overview and unit pages."""

    if request.user.is_authenticated:
        read_state = Exists(
            UnitReadState.objects.filter(
                user=request.user,
                unit_id=OuterRef("pk"),
            )
        )
    else:
        read_state = Value(False, output_field=BooleanField())
    rail_units = list(module.units.annotate(is_read=read_state))
    return {
        "module_rail_units": rail_units,
        "module_rail_read_count": sum(unit.is_read for unit in rail_units),
        "module_rail_current_unit": current_unit,
        "module_url": module_url(module),
    }


def module_view(
    request: HttpRequest,
    course_slug: str,
    cohort_identifier: str,
    module_slug: str,
) -> HttpResponse:
    cohort = get_cohort_or_404(
        course_slug,
        cohort_identifier,
        curriculum_format=CurriculumFormat.MODULES,
    )
    module = get_object_or_404(
        Module.objects.select_related(
            "cohort",
            "cohort__course",
            "terminal_homework",
        ),
        cohort=cohort,
        slug=module_slug,
    )

    rail_context = module_rail_context(request, module)
    units = rail_context["module_rail_units"]
    canonical_path = rail_context["module_url"]

    return render(
        request,
        "courses/module.html",
        {
            "course_family": cohort.course,
            "cohort": cohort,
            "module": module,
            "module_url": canonical_path,
            "units": units,
            "read_unit_count": rail_context["module_rail_read_count"],
            "canonical_url": f"https://datatalks.club{canonical_path}",
            **rail_context,
        },
    )


@login_required
@require_POST
def update_unit_read_state(
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
    module = get_object_or_404(
        Module.objects.select_related("cohort", "cohort__course"),
        cohort=cohort,
        slug=module_slug,
    )
    unit = get_object_or_404(Unit, module=module, slug=unit_slug)

    raw_state = request.POST.get("is_read", "")
    if raw_state not in {"0", "1"}:
        return HttpResponseBadRequest("is_read must be 0 or 1.")

    set_unit_read_state(
        user=request.user,
        module=module,
        unit=unit,
        is_read=raw_state == "1",
    )
    return redirect("module", course_slug, cohort.identifier, module.slug)
