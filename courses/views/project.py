import logging

from community_base.coursework.attempt_flow import SUBMIT_PAGE
from community_base.coursework.project_forms import update_commit_action
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest
from django.shortcuts import get_object_or_404, redirect, render

from course_management.observability import record_event
from courses.models.cohort import Cohort
from courses.models.project import Project, project_yaml_criteria
from courses.registration_wall import require_registration_approval
from courses.services.attempt_flow import criteria_url, project_rail_context
from courses.views.project_page_context import (
    project_accepting_submissions,
    project_build_context,
    project_context_submission,
)
from courses.views.project_submission_edit import (
    project_delete_submission,
    project_submit_form,
    project_submit_post,
)
from courses.views.url_utils import canonical_cohort_url_kwargs, get_cohort_or_404

logger = logging.getLogger(__name__)

PROJECT_SUBMISSION_DELETED_MESSAGE = (
    "Your project submission is deleted. You can still make a new submission if you want."
)
PROJECT_SUBMISSION_SAVED_MESSAGE = "Thank you for submitting your project, it is now saved. You can update your submission at any point before the due date."


def project_login_required_response(request: HttpRequest, course: Cohort, project: Project):
    messages.error(
        request,
        "You need to be logged in to submit a project",
        extra_tags="homework",
    )
    return redirect(
        "cohort_project",
        **canonical_cohort_url_kwargs(course),
        project_slug=project.slug,
    )


def closed_project_submission_response(request: HttpRequest, course: Cohort, project: Project):
    messages.error(
        request,
        "Project submission form is closed.",
        extra_tags="homework",
    )
    context = project_build_context(request, course, project)
    return render(request, "projects/project.html", context)


def project_validation_error_response(
    request: HttpRequest,
    course: Cohort,
    project: Project,
    form,
):
    context = project_build_context(request, course, project)
    # The bound shared form re-renders the learner's raw entries with the
    # field errors; its save is the only write path, so a rejected POST
    # leaves nothing behind (audit BE-06).
    context["project_form"] = form
    return render(request, "projects/project.html", context)


def delete_project_submission_response(
    request: HttpRequest,
    course: Cohort,
    project: Project,
):
    project_delete_submission(request, project)
    messages.success(
        request,
        PROJECT_SUBMISSION_DELETED_MESSAGE,
        extra_tags="homework",
    )
    return redirect(
        "cohort_project",
        **canonical_cohort_url_kwargs(course),
        project_slug=project.slug,
    )


def project_validation_failed_event(request: HttpRequest, course: Cohort, project: Project, form):
    record_event(
        "project.validation_failed",
        request=request,
        properties={
            "course_slug": course.course.slug,
            "cohort_identifier": course.identifier,
            "project_slug": project.slug,
            "project_id": project.id,
            "error_count": sum(len(messages) for messages in form.errors.values()),
        },
    )


def save_project_submission_response(
    request: HttpRequest,
    course: Cohort,
    project: Project,
):
    form = project_submit_form(request, project)
    if not form.is_valid():
        project_validation_failed_event(request, course, project, form)
        return project_validation_error_response(
            request,
            course,
            project,
            form,
        )
    try:
        project_submit_post(request, project, form)
    except ValidationError as error:
        form.add_model_errors(error)
        project_validation_failed_event(request, course, project, form)
        return project_validation_error_response(
            request,
            course,
            project,
            form,
        )

    messages.success(
        request,
        PROJECT_SUBMISSION_SAVED_MESSAGE,
        extra_tags="homework",
    )
    return redirect(
        "cohort_project",
        **canonical_cohort_url_kwargs(course),
        project_slug=project.slug,
    )


def handle_project_post(request: HttpRequest, course: Cohort, project: Project):
    if not request.user.is_authenticated:
        return project_login_required_response(request, course, project)

    require_registration_approval(course, request.user)

    if not project_accepting_submissions(project):
        return closed_project_submission_response(request, course, project)

    action = request.POST.get("action")
    if action == "delete":
        return delete_project_submission_response(
            request,
            course,
            project,
        )
    if action == "update_commit":
        submission = project_context_submission(request.user, project)
        if submission is not None:
            form = project_submit_form(request, project)
            ok, message = update_commit_action(form, submission)
            if not ok:
                if message:
                    messages.error(request, message, extra_tags="homework")
                return project_validation_error_response(
                    request,
                    course,
                    project,
                    form,
                )
            messages.success(
                request,
                "Commit updated to the latest commit on GitHub.",
                extra_tags="homework",
            )
            return redirect(
                "cohort_project",
                **canonical_cohort_url_kwargs(course),
                project_slug=project.slug,
            )

    return save_project_submission_response(
        request,
        course,
        project,
    )


def project_view(request, course_slug, project_slug, cohort_identifier=None):
    course = get_cohort_or_404(course_slug, cohort_identifier)
    project = get_object_or_404(Project, course=course, slug=project_slug)

    if request.method == "POST":
        return handle_project_post(
            request,
            course,
            project,
        )

    context = project_build_context(request, course, project)
    if project_yaml_criteria(project):
        context["project_criteria_url"] = criteria_url(project)
    context.update(project_rail_context(project, page=SUBMIT_PAGE))

    return render(request, "projects/project.html", context)
