from django.contrib import messages
from django.http import HttpRequest
from django.shortcuts import get_object_or_404, redirect, render

from courses.assignment_statistics import calculate_homework_statistics
from courses.homework_question_stats import homework_question_stats
from courses.models.cohort import Cohort
from courses.models.homework import Homework
from courses.views.url_utils import cohort_url_kwargs, get_cohort_or_404


def unscored_homework_statistics_response(
    request: HttpRequest,
    course: Cohort,
    homework: Homework,
):
    messages.error(
        request,
        "This homework is not scored yet, so there are no available statistics.",
        extra_tags="homework",
    )
    response = redirect(
        "homework",
        **cohort_url_kwargs(course),
        homework_slug=homework.slug,
    )
    return response


def scored_homework_statistics_response(
    request: HttpRequest,
    course: Cohort,
    homework: Homework,
):
    stats = calculate_homework_statistics(homework, force=False)
    question_stats = homework_question_stats(homework)
    context = {
        "course": course,
        "course_family": course.course,
        "homework": homework,
        "stats": stats,
        "question_stats": question_stats,
    }

    response = render(request, "homework/stats.html", context)
    return response


def homework_statistics(
    request: HttpRequest,
    course_slug: str,
    homework_slug: str,
    cohort_year: str | int | None = None,
):
    course = get_cohort_or_404(course_slug, cohort_year)
    homework = get_object_or_404(
        Homework,
        course=course,
        slug=homework_slug,
    )

    if not homework.is_scored():
        response = unscored_homework_statistics_response(
            request,
            course,
            homework,
        )
        return response

    response = scored_homework_statistics_response(
        request,
        course,
        homework,
    )
    return response
