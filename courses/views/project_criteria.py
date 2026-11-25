"""The Evaluation criteria page of a cohort's project attempts."""

from django.http import Http404
from django.shortcuts import get_object_or_404, render

from courses.models.project import Project
from courses.services.attempt_flow import criteria_page, submit_url
from courses.views.url_utils import get_cohort_or_404


def project_criteria(request, course_slug, project_slug, cohort_identifier=None):
    """The rubric peers score with, plus a pointer to the attempt still open."""
    course = get_cohort_or_404(course_slug, cohort_identifier)
    project = get_object_or_404(Project, course=course, slug=project_slug)
    page = criteria_page(request.user, course)
    if page is None:
        raise Http404("This cohort has no evaluation criteria.")
    open_project = page["open_project"]
    return render(
        request,
        "projects/criteria.html",
        {
            "course": course,
            "course_family": course.course,
            "project": project,
            "project_criteria": page["project_criteria"],
            "open_project": open_project,
            "open_project_url": submit_url(open_project) if open_project else "",
        },
    )
