from django.contrib import messages
from django.http import HttpRequest
from django.shortcuts import redirect

from accounts.navigation import can_access_course_studio
from courses.views.url_utils import cohort_url_kwargs, get_cohort_or_404


def homework_submissions(
    request: HttpRequest,
    course_slug: str,
    homework_slug: str,
    cohort_year: str | int | None = None,
):
    user = request.user
    course = get_cohort_or_404(course_slug, cohort_year)

    if not can_access_course_studio(user):
        messages.error(
            request,
            "You do not have permission to view this page.",
            extra_tags="homework",
        )
        response = redirect(
            "homework",
            **cohort_url_kwargs(course),
            homework_slug=homework_slug,
        )
        return response

    response = redirect(
        "studio_courses_homework_submissions",
        course_slug=course.slug,
        homework_slug=homework_slug,
    )
    return response
