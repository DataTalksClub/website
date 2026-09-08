from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render

from course_management.observability import record_event
from courses.models.cohort import Cohort
from courses.models.project import (
    PeerReview,
    Project,
    ProjectState,
    criteria_for_project,
)
from courses.votes import (
    update_project_vote,
)
from courses.views.project_eval_submit_context import (
    ProjectEvalSubmitPage,
    project_eval_submit_context,
)
from courses.views.project_eval_submit_save import (
    ProjectCriteriaValidationError,
    project_eval_post_submission,
)
from courses.views.url_utils import canonical_cohort_url_kwargs, get_cohort_or_404


def project_eval_vote_response(
    request,
    course_slug,
    project_slug,
    review,
    cohort_identifier=None,
):
    action = request.POST.get("action", "vote")
    update_project_vote(
        request.user,
        review.submission_under_evaluation,
        action=action,
    )
    record_event(
        "project.vote_updated",
        request=request,
        properties={
            "course_slug": course_slug,
            "project_slug": project_slug,
            "review_id": review.id,
            "submission_id": review.submission_under_evaluation_id,
            "action": action,
        },
    )
    response = redirect(
        "cohort_projects_eval_submit",
        **canonical_cohort_url_kwargs(get_cohort_or_404(course_slug, cohort_identifier)),
        project_slug=project_slug,
        review_id=review.id,
    )
    return response


def closed_project_eval_response(
    request,
    page: ProjectEvalSubmitPage,
):
    messages.error(
        request,
        "Peer review form is closed.",
        extra_tags="homework",
    )
    context = project_eval_submit_context(request, page)
    response = render(request, "projects/eval_submit.html", context)
    return response


def project_eval_validation_error_response(
    request,
    page: ProjectEvalSubmitPage,
    error: ValidationError,
):
    for message in error.messages:
        messages.error(
            request,
            message,
            extra_tags="alert-danger",
        )
    context = project_eval_submit_context(request, page)
    response = render(request, "projects/eval_submit.html", context)
    return response


def project_eval_submission_response(
    request,
    page: ProjectEvalSubmitPage,
):
    try:
        project_eval_post_submission(
            request,
            page.project,
            page.review,
            page.review_criteria,
        )
    except ProjectCriteriaValidationError:
        return HttpResponseBadRequest(
            "The review criteria do not belong to this project."
        )
    except ValidationError as error:
        return project_eval_validation_error_response(
            request,
            page,
            error,
        )
    response = redirect(
        "cohort_projects_eval",
        **canonical_cohort_url_kwargs(page.course),
        project_slug=page.project.slug,
    )
    return response


def projects_eval_submit_post_response(
    request,
    page: ProjectEvalSubmitPage,
    cohort_identifier=None,
):
    if request.POST.get("form_action") == "vote":
        return project_eval_vote_response(
            request,
            page.course.slug,
            page.project.slug,
            page.review,
            cohort_identifier=cohort_identifier,
        )

    if page.project.state != ProjectState.PEER_REVIEWING.value:
        return closed_project_eval_response(
            request,
            page,
        )

    response = project_eval_submission_response(
        request,
        page,
    )
    return response


def project_eval_submit_page(
    project: Project,
    review: PeerReview,
) -> ProjectEvalSubmitPage:
    return ProjectEvalSubmitPage(
        course=project.course,
        project=project,
        review=review,
        review_criteria=criteria_for_project(project),
    )


def project_eval_review_matches_project(
    review: PeerReview,
    project: Project,
) -> bool:
    """A review is only reachable through its own project's URL.

    Both the reviewer's submission and the evaluated submission must belong
    to the resolved project; the URL project alone grants no authority.
    """

    return (
        review.reviewer.project_id == project.pk
        and review.submission_under_evaluation.project_id == project.pk
    )


def project_eval_unauthorized_response(
    request,
    course_slug,
    project_slug,
    cohort_identifier=None,
):
    messages.error(
        request,
        "You are not allowed to evaluate this submission, choose a different one.",
        extra_tags="homework",
    )
    response = redirect(
        "cohort_projects_eval",
        **canonical_cohort_url_kwargs(get_cohort_or_404(course_slug, cohort_identifier)),
        project_slug=project_slug,
    )
    return response


@login_required
def projects_eval_submit(
    request,
    course_slug,
    project_slug,
    review_id,
    cohort_identifier=None,
):
    course = get_cohort_or_404(course_slug, cohort_identifier)
    project = get_object_or_404(
        Project, slug=project_slug, course=course
    )
    review = get_object_or_404(
        PeerReview.objects.select_related(
            "reviewer__student",
            "submission_under_evaluation",
        ),
        id=review_id,
    )

    if review.reviewer.student != request.user:
        record_event(
            "project.review_unauthorized",
            request=request,
            properties={
                "course_slug": course_slug,
                "project_slug": project_slug,
                "review_id": review.id,
            },
        )
        response = project_eval_unauthorized_response(
            request,
            course_slug,
            project_slug,
            cohort_identifier,
        )
        return response

    if not project_eval_review_matches_project(review, project):
        record_event(
            "project.review_project_mismatch",
            request=request,
            properties={
                "course_slug": course_slug,
                "project_slug": project_slug,
                "review_id": review.id,
                "reviewer_project_id": review.reviewer.project_id,
                "evaluated_project_id": (
                    review.submission_under_evaluation.project_id
                ),
            },
        )
        response = project_eval_unauthorized_response(
            request,
            course_slug,
            project_slug,
            cohort_identifier,
        )
        return response

    page = project_eval_submit_page(
        project,
        review,
    )

    if request.method == "POST":
        return projects_eval_submit_post_response(
            request,
            page,
            cohort_identifier,
        )

    context = project_eval_submit_context(
        request,
        page,
    )

    response = render(request, "projects/eval_submit.html", context)
    return response
