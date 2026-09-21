from dataclasses import dataclass

from community_base.homework_steps.services import clear_draft
from community_base.homework_steps.views import handle_stepper
from django.http import HttpRequest
from django.shortcuts import render

from courses.models.cohort import Cohort
from courses.models.homework import (
    Homework,
    Question,
)
from courses.views.homework_context import (
    AuthenticatedHomeworkContext,
    authenticated_homework_context,
    homework_detail_build_context_not_authenticated,
    homework_detail_objects,
)
from courses.views.homework_post_preview import (
    handle_homework_post,
)
from courses.views.homework_steps import (
    DtcHomeworkStepsAdapter,
    assignment_key,
    build_assignment,
)
from courses.views.homework_submission import (
    HomeworkPostData,
)


@dataclass(frozen=True)
class HomeworkRequestData:
    request: HttpRequest
    course: Cohort
    homework: Homework
    questions: list[Question]


def authenticated_homework_response(data: HomeworkRequestData):
    authenticated_context = authenticated_homework_context(
        user=data.request.user,
        course=data.course,
        homework=data.homework,
        questions=data.questions,
        # A POST submission is the only path that may enroll; a read render
        # of the page must never create an Enrollment as a side effect.
        create_enrollment=data.request.method == "POST",
    )

    if data.request.method != "POST":
        response = render(
            data.request,
            "homework/homework.html",
            authenticated_context.context,
        )
        return response

    return authenticated_homework_post_response(
        data,
        authenticated_context,
    )


def authenticated_homework_post_response(
    data: HomeworkRequestData,
    authenticated_context: AuthenticatedHomeworkContext,
):
    post_data = HomeworkPostData(
        request=data.request,
        course=data.course,
        homework=data.homework,
        questions=data.questions,
        submission=authenticated_context.submission,
        enrollment=authenticated_context.enrollment,
    )
    post_result = handle_homework_post(post_data)
    if not isinstance(post_result, dict):
        if post_result.status_code in (301, 302, 303):
            clear_draft(data.request.user, assignment_key(data.homework))
        return post_result

    response = render(data.request, "homework/homework.html", post_result)
    return response


def homework_view(
    request: HttpRequest,
    course_slug: str,
    homework_slug: str,
    cohort_identifier: str | int | None = None,
):
    detail_objects = homework_detail_objects(
        course_slug,
        homework_slug,
        cohort_identifier,
    )
    course = detail_objects.course
    homework = detail_objects.homework
    questions = detail_objects.questions
    user = request.user

    if not user.is_authenticated:
        context = homework_detail_build_context_not_authenticated(
            course=course, homework=homework, questions=questions
        )
        response = render(request, "homework/homework.html", context)
        return response

    request_data = HomeworkRequestData(
        request=request,
        course=course,
        homework=homework,
        questions=questions,
    )
    is_step_post = request.method == "POST" and "assignment_key" in request.POST
    use_stepper = (
        request.method == "GET"
        and questions
        and homework.state == "OP"
        and request.GET.get("homework_view") != "classic"
    ) or is_step_post
    if use_stepper:
        try:
            assignment = build_assignment(
                request=request,
                course=course,
                homework=homework,
                questions=questions,
            )
            adapter = DtcHomeworkStepsAdapter(course, homework, questions)
            return handle_stepper(
                request,
                assignment,
                adapter,
                action=request.path,
                template_name="homework/steps.html",
                step_param="homework_step",
            )
        except (ValueError, KeyError, IndexError):
            # An old or malformed question binding keeps the working classic
            # form available; it cannot attach a draft to the wrong question.
            if is_step_post:
                raise
    return authenticated_homework_response(request_data)
