from django.shortcuts import render

from courses.views.dashboard_context import dashboard_context
from courses.views.url_utils import get_cohort_or_404


def dashboard_view(request, course_slug: str, cohort_year: str | int | None = None):
    course = get_cohort_or_404(course_slug, cohort_year)
    context = dashboard_context(course)
    response = render(
        request,
        "courses/dashboard.html",
        context,
    )
    return response
