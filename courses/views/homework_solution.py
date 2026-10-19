from django.shortcuts import get_object_or_404, render
from django.utils.cache import add_never_cache_headers
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_safe

from courses.models.homework import Homework
from courses.models.homework_solution import HomeworkSolution
from courses.registration import render_markdown
from courses.views.url_utils import cohort_url, get_cohort_or_404


@require_safe
def homework_solution(request, course_slug, cohort_identifier, homework_slug):
    course = get_cohort_or_404(course_slug, cohort_identifier)
    homework = get_object_or_404(Homework, course=course, slug=homework_slug)
    solution = HomeworkSolution.objects.published().filter(homework=homework).first()
    if solution is None:
        # Never cache a pre-release denial: a scheduled release needs no deploy.
        response = render(request, "404.html", status=404)
    else:
        response = render(
            request,
            "homework/solution.html",
            {
                "course": course,
                "homework": homework,
                "solution_html": mark_safe(render_markdown(solution.markdown)),
                "answers": solution.answers,
                "homework_url": cohort_url(course, "cohort_homework", homework_slug=homework.slug),
            },
        )
    add_never_cache_headers(response)
    response["X-Robots-Tag"] = "noindex"
    return response
