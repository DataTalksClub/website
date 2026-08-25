from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from courses.assignment_statistics import calculate_project_statistics
from courses.models.project import Project, ProjectState
from courses.views.url_utils import cohort_url_kwargs, get_cohort_or_404


def incomplete_project_statistics_response(request, course, project):
    messages.error(
        request,
        "This project is not completed yet, so there are no available statistics.",
        extra_tags="project",
    )
    response = redirect(
        "project",
        **cohort_url_kwargs(course),
        project_slug=project.slug,
    )
    return response


def project_statistics(request, course_slug, project_slug, cohort_year=None):
    course = get_cohort_or_404(course_slug, cohort_year)
    project = get_object_or_404(
        Project, course=course, slug=project_slug
    )

    if project.state != ProjectState.COMPLETED.value:
        return incomplete_project_statistics_response(
            request,
            course,
            project,
        )

    stats = calculate_project_statistics(project, force=False)

    context = {
        "course": course,
        "course_family": course.course,
        "project": project,
        "stats": stats,
    }

    response = render(request, "projects/stats.html", context)
    return response
