from dataclasses import dataclass

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from course_management.observability import record_event
from courses.models.cohort import Cohort, Enrollment
from courses.views.url_utils import canonical_cohort_url_kwargs, get_cohort_or_404

from .course_leaderboard_data import invalidate_leaderboard_cache
from .forms import EnrollmentForm

ENROLLMENT_TOGGLE_FIELDS = {
    "display_on_leaderboard",
    "display_public_profile",
}


@dataclass(frozen=True)
class EnrollmentToggleUpdate:
    enrollment: Enrollment
    course: Cohort
    field: str
    enabled: bool


@login_required
@require_POST
def update_enrollment_toggle(request, course_slug, cohort_identifier=None):
    course = get_cohort_or_404(course_slug, cohort_identifier)
    # Parse and allowlist the toggle input before any lookup or write: an
    # unsupported field is a 400 that leaves the tables unchanged (BE-07).
    toggle_input = enrollment_toggle_input_from_post(request)
    if toggle_input is None:
        payload = {"error": "Unsupported enrollment setting."}
        response = JsonResponse(payload, status=400)
        return response

    # A preference toggle requires an existing enrollment; it never creates
    # one as a side effect of a rejected or missing-target request (BE-07).
    enrollment = get_object_or_404(
        Enrollment,
        student=request.user,
        course=course,
    )
    field, enabled = toggle_input
    toggle_update = EnrollmentToggleUpdate(
        enrollment=enrollment,
        course=course,
        field=field,
        enabled=enabled,
    )

    update_enrollment_toggle_value(toggle_update)
    record_event(
        "enrollment.toggle_updated",
        request=request,
        properties={
            "course_slug": course.course.slug,
            "cohort_identifier": course.identifier,
            "enrollment_id": enrollment.id,
            "field": toggle_update.field,
            "enabled": toggle_update.enabled,
        },
    )

    payload = {
        "field": toggle_update.field,
        "value": toggle_update.enabled,
    }
    response = JsonResponse(payload)
    return response


def enrollment_toggle_input_from_post(request):
    """Return the allowlisted ``(field, enabled)`` pair, or None."""

    field = request.POST.get("field", "")
    if field not in ENROLLMENT_TOGGLE_FIELDS:
        return None

    value = request.POST.get("value", "")
    enabled = value.lower() in {"1", "true", "yes", "on"}
    return field, enabled


def update_enrollment_toggle_value(toggle_update):
    enrollment = toggle_update.enrollment
    previous_display_on_leaderboard = enrollment.display_on_leaderboard
    setattr(enrollment, toggle_update.field, toggle_update.enabled)
    enrollment.save(update_fields=[toggle_update.field])

    if toggle_update.field != "display_on_leaderboard":
        return
    if previous_display_on_leaderboard == toggle_update.enabled:
        return

    invalidate_leaderboard_cache(toggle_update.course.id)


def _render_enrollment_form(request, course, enrollment, form):
    context = {
        "form": form,
        "course": course,
        "course_family": course.course,
        "enrollment": enrollment,
    }
    response = render(
        request,
        "courses/enrollment.html",
        context,
    )
    return response


def _save_enrollment_form(form, course, enrollment) -> None:
    previous_display_on_leaderboard = enrollment.display_on_leaderboard
    form.save()
    if previous_display_on_leaderboard != form.instance.display_on_leaderboard:
        invalidate_leaderboard_cache(course.id)


def record_enrollment_created(request, course, enrollment):
    record_event(
        "enrollment.created",
        request=request,
        properties={
            "course_slug": course.course.slug,
            "cohort_identifier": course.identifier,
            "enrollment_id": enrollment.id,
        },
    )


def _handle_enrollment_post(request, course, enrollment):
    form = EnrollmentForm(
        request.POST,
        instance=enrollment,
        user=request.user,
    )
    if form.is_valid():
        created = enrollment.pk is None
        _save_enrollment_form(form, course, enrollment)
        if created:
            record_enrollment_created(request, course, enrollment)
        record_event(
            "enrollment.updated",
            request=request,
            properties={
                "course_slug": course.course.slug,
                "cohort_identifier": course.identifier,
                "enrollment_id": enrollment.id,
            },
        )
        response = redirect("cohort", **canonical_cohort_url_kwargs(course))
        return response

    return _render_enrollment_form(request, course, enrollment, form)


@login_required
def enrollment_view(request, course_slug, cohort_identifier=None):
    course = get_cohort_or_404(course_slug, cohort_identifier)

    # Reading the settings page is a pure GET: it renders from an existing
    # enrollment or an unsaved stand-in and creates nothing (audit BE-07).
    # The POST below is the accepted enrollment mutation and the only place
    # this view may persist a new enrollment.
    enrollment = Enrollment.objects.filter(
        student=request.user,
        course=course,
    ).first()
    if enrollment is None:
        enrollment = Enrollment(student=request.user, course=course)

    if request.method == "POST":
        return _handle_enrollment_post(request, course, enrollment)

    form = EnrollmentForm(instance=enrollment, user=request.user)
    return _render_enrollment_form(request, course, enrollment, form)
