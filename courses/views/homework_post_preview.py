from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest
from django.shortcuts import redirect

from course_management.observability import record_event
from courses.models.cohort import Cohort
from courses.models.homework import (
    Homework,
    HomeworkState,
)
from courses.views.homework_post_context import homework_validation_context
from courses.views.homework_submission import (
    HomeworkPostData,
    process_homework_submission,
)
from courses.views.url_utils import cohort_url_kwargs


def closed_homework_submission_response(
    request: HttpRequest,
    course: Cohort,
    homework: Homework,
):
    messages.error(
        request,
        "This homework is not open for submissions.",
        extra_tags="homework",
    )
    response = redirect(
        "homework",
        **cohort_url_kwargs(course),
        homework_slug=homework.slug,
    )
    return response


def handle_homework_post(data: HomeworkPostData):
    if data.homework.state != HomeworkState.OPEN.value:
        record_event(
            "homework.submission_rejected",
            request=data.request,
            properties={
                "course_slug": data.course.slug,
                "homework_slug": data.homework.slug,
                "homework_id": data.homework.id,
                "reason": "closed",
                "state": data.homework.state,
            },
        )
        return closed_homework_submission_response(
            data.request,
            data.course,
            data.homework,
        )

    try:
        with transaction.atomic():
            return process_homework_submission(data)
    except ValidationError as error:
        record_event(
            "homework.validation_failed",
            request=data.request,
            properties={
                "course_slug": data.course.slug,
                "homework_slug": data.homework.slug,
                "homework_id": data.homework.id,
                "error_count": len(error.messages),
            },
        )
        return homework_validation_context(
            data=data,
            error=error,
        )
