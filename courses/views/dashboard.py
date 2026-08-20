from django.shortcuts import redirect, render

from courses.views.dashboard_context import dashboard_context
from courses.views.url_utils import cohort_url_kwargs, get_cohort_or_404


def dashboard_view(request, course_slug: str, cohort_year: str | int | None = None):
    course = get_cohort_or_404(course_slug, cohort_year)
    if not course.first_homework_scored:
        response = redirect("course", **cohort_url_kwargs(course))
        return response

    context = dashboard_context(course)
    response = render(
        request,
        "courses/dashboard.html",
        context,
    )
    return response
