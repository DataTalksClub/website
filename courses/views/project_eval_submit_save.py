from collections.abc import Iterable

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from course_management.observability import record_event
from courses.models.project import (
    CriteriaResponse,
    PeerReview,
    PeerReviewState,
    Project,
    ReviewCriteria,
)
from courses.views.homework_learning_links import (
    clean_learning_in_public_links,
)


class ProjectCriteriaValidationError(ValidationError):
    """A safe, atomic rejection of criteria outside the current project."""


def project_eval_post_submission(
    request: HttpRequest,
    project: Project,
    review: PeerReview,
    review_criteria: Iterable[ReviewCriteria],
) -> None:
    review_criteria = tuple(review_criteria)
    answers_by_field = project_eval_answers_from_post(request.POST)
    validate_project_criteria_answers(review_criteria, answers_by_field)

    with transaction.atomic():
        save_project_eval_criteria_responses(
            review,
            review_criteria,
            answers_by_field,
        )
        apply_review_learning_in_public_links(request, project, review)
        apply_review_time_spent(request, project, review)
        if project.problems_comments_field:
            problems_comments = request.POST.get("problems_comments", "")
            review.problems_comments = problems_comments.strip()

        note_to_peer = request.POST.get("note_to_peer", "")
        review.note_to_peer = note_to_peer.strip()

        review.submitted_at = timezone.now()
        review.state = PeerReviewState.SUBMITTED.value
        review.save()
    criteria_count = len(review_criteria)
    record_event(
        "project.review_submitted",
        request=request,
        properties={
            "course_slug": project.course.slug,
            "project_slug": project.slug,
            "project_id": project.id,
            "review_id": review.id,
            "reviewer_submission_id": review.reviewer_id,
            "submission_id": review.submission_under_evaluation_id,
            "criteria_count": criteria_count,
        },
    )

    messages.success(
        request,
        "Thank you for submitting your evaluation, it is now saved. You can update it at any point.",
        extra_tags="homework",
    )


def project_eval_answers_from_post(post_data):
    answers = {}
    posted_answers = post_data.lists()
    for answer_id, answer in posted_answers:
        if not answer_id.startswith("answer_"):
            continue
        cleaned_answer_items = []
        for value in answer:
            cleaned_value = value.strip()
            cleaned_answer_items.append(cleaned_value)
        answers[answer_id] = ",".join(cleaned_answer_items)
    return answers


def validate_project_criteria_answers(review_criteria, answers_by_field):
    """Reject forged, stale, or cross-project criterion identifiers upfront."""

    allowed_ids = {str(criteria.id) for criteria in review_criteria}
    posted_ids = {
        field_name.removeprefix("answer_")
        for field_name in answers_by_field
    }
    if posted_ids - allowed_ids:
        raise ProjectCriteriaValidationError(
            "The review contains a criterion that is not assigned to this project."
        )


def save_project_eval_criteria_responses(
    review,
    review_criteria,
    answers_by_field,
):
    for criteria in review_criteria:
        answer = answers_by_field.get(f"answer_{criteria.id}")
        CriteriaResponse.objects.update_or_create(
            review=review,
            criteria=criteria,
            defaults={"answer": answer},
        )


def apply_review_learning_in_public_links(request, project, review):
    if project.learning_in_public_cap_review <= 0:
        return

    links = request.POST.getlist("learning_in_public_links[]")
    review.learning_in_public_links = clean_learning_in_public_links(
        links,
        project.learning_in_public_cap_review,
    )


def apply_review_time_spent(request, project, review):
    if not project.time_spent_evaluation_field:
        return

    time_spent_reviewing = request.POST.get("time_spent_reviewing")
    if time_spent_reviewing is not None and time_spent_reviewing != "":
        review.time_spent_reviewing = float(time_spent_reviewing)
