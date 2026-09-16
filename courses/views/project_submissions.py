from django.contrib import messages
from django.shortcuts import redirect

from accounts.navigation import can_access_course_studio
from courses.views.url_utils import canonical_cohort_url_kwargs, get_cohort_or_404


def project_submissions(request, course_slug, project_slug, cohort_identifier=None):
    course = get_cohort_or_404(course_slug, cohort_identifier)
    if not can_access_course_studio(request.user):
        messages.error(
            request,
            "You do not have permission to view this page.",
            extra_tags="project",
        )
        response = redirect(
            "cohort_project",
            **canonical_cohort_url_kwargs(course),
            project_slug=project_slug,
        )
        return response

    response = redirect(
        "studio_courses_project_submissions",
        course_slug=course.slug,
        project_slug=project_slug,
    )
    return response
