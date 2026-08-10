from django.contrib import messages
from django.http import HttpRequest
from django.shortcuts import redirect

from accounts.navigation import can_access_course_studio


def homework_submissions(
    request: HttpRequest,
    course_slug: str,
    homework_slug: str,
):
    user = request.user

    if not can_access_course_studio(user):
        messages.error(
            request,
            "You do not have permission to view this page.",
            extra_tags="homework",
        )
        response = redirect(
            "homework",
            course_slug=course_slug,
            homework_slug=homework_slug,
        )
        return response

    response = redirect(
        "studio_courses_homework_submissions",
        course_slug=course_slug,
        homework_slug=homework_slug,
    )
    return response
