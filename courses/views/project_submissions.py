from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from accounts.navigation import can_access_course_studio
from courses.models.cohort import Cohort
from courses.models.project import (
    Project,
)
from courses.views.project_submission_listing import (
    project_submissions_page,
    projects_list_context,
)
from courses.views.project_submission_viewer import project_viewer_state
from courses.views.project_submission_votes import (
    project_vote_response,
)
from courses.views.url_utils import cohort_url_kwargs, get_cohort_or_404


def projects_list_view(request, course_slug, project_slug, cohort_year=None):
    course = get_cohort_or_404(course_slug, cohort_year)
    project = get_object_or_404(Project, course=course, slug=project_slug)

    if request.method == "POST":
        return project_vote_response(request, course, project)

    user = request.user
    viewer_state = project_viewer_state(project, course, user)
    submissions_page = project_submissions_page(request, project, viewer_state)
    context = projects_list_context(course, project, submissions_page, viewer_state)

    response = render(request, "projects/list.html", context)
    return response


def project_submissions(request, course_slug, project_slug, cohort_year=None):
    course = get_cohort_or_404(course_slug, cohort_year)
    if not can_access_course_studio(request.user):
        messages.error(
            request,
            "You do not have permission to view this page.",
            extra_tags="project",
        )
        response = redirect(
            "project",
            **cohort_url_kwargs(course),
            project_slug=project_slug,
        )
        return response

    response = redirect(
        "studio_courses_project_submissions",
        course_slug=course.slug,
        project_slug=project_slug,
    )
    return response
