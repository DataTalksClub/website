from functools import partial

from django.db import transaction
from django.http import HttpRequest

from course_management.observability import record_event
from courses.models.project import Project, ProjectSubmission
from courses.services.project_form_adapter import (
    ProjectSubmissionForm,
    build_project_submission_form,
)
from courses.views.project_confirmation import (
    ProjectConfirmationEmailData,
    build_project_update_url,
    send_project_confirmation_email,
)


def project_submit_post(
    request: HttpRequest,
    project: Project,
    form: ProjectSubmissionForm,
) -> tuple[object, bool]:
    """Persist an already-validated shared form as one unit.

    The adapter's save owns the atomic boundary: a rejected field must not
    leave the enrollment creation, the certificate-name change, or any
    submission write behind (audit BE-06).
    """
    submission, created = form.save()
    record_event(
        "project.submitted",
        request=request,
        properties={
            "course_slug": project.course.slug,
            "project_slug": project.slug,
            "project_id": project.id,
            "submission_id": submission.id,
            "enrollment_id": submission.enrollment_id,
            "is_update": not created,
        },
    )
    update_url = build_project_update_url(request, project.course, project)
    confirmation_data = ProjectConfirmationEmailData(
        user=request.user,
        course=project.course,
        project=project,
        submission=submission,
        update_url=update_url,
    )
    email_callback = partial(
        send_project_confirmation_email,
        confirmation_data,
    )
    transaction.on_commit(email_callback)
    return submission, created


def project_submit_form(request: HttpRequest, project: Project) -> ProjectSubmissionForm:
    """The bound shared form for the viewer's own submission."""
    return build_project_submission_form(project, user=request.user, data=request.POST)


def project_delete_submission(request: HttpRequest, project: Project) -> None:
    project_submission = ProjectSubmission.objects.filter(
        project=project,
        student=request.user,
        volunteer_review_only=False,
    ).first()

    if project_submission:
        submission_id = project_submission.id
        enrollment_id = project_submission.enrollment_id
        project_submission.delete()
        record_event(
            "project.deleted",
            request=request,
            properties={
                "course_slug": project.course.slug,
                "project_slug": project.slug,
                "project_id": project.id,
                "submission_id": submission_id,
                "enrollment_id": enrollment_id,
            },
        )
