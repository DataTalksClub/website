import re
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
    ProjectState,
    ReviewCriteria,
    ReviewCriteriaTypes,
)
from courses.views.homework_learning_links import (
    clean_learning_in_public_links,
)


class ProjectCriteriaValidationError(ValidationError):
    """A safe, atomic rejection of criteria outside the current project."""


POSTED_ANSWER_INDEX_PATTERN = re.compile(r"[1-9][0-9]*")


def locked_peer_reviewing_project(project: Project) -> Project:
    """Re-read the project under row lock and require an open review form.

    The view-level state gate is advisory UI; this check inside the mutation
    transaction rejects a review that races the project being closed or
    scored.
    """

    locked = Project.objects.select_for_update().get(pk=project.pk)
    if locked.state != ProjectState.PEER_REVIEWING.value:
        raise ValidationError("Peer review form is closed.")
    return locked


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
        project = locked_peer_reviewing_project(project)
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
    """Reject forged, stale, or cross-project criterion identifiers upfront,
    and reject answer values the rubric cannot score."""

    allowed_ids = {str(criteria.id): criteria for criteria in review_criteria}
    posted_ids = {
        field_name.removeprefix("answer_")
        for field_name in answers_by_field
    }
    if posted_ids - allowed_ids.keys():
        raise ProjectCriteriaValidationError(
            "The review contains a criterion that is not assigned to this project."
        )
    for criteria_id in posted_ids:
        validate_criteria_answer_value(
            allowed_ids[criteria_id],
            answers_by_field[f"answer_{criteria_id}"],
        )


def validate_criteria_answer_value(criteria, raw_answer):
    """Validate one posted answer against its criterion definition.

    Values must be canonical 1-based option indexes without repeats; a radio
    criterion takes exactly one choice, a checkbox criterion any distinct
    subset. An empty or absent value means "not answered" and stays allowed
    for optional answers.
    """

    indexes = _posted_answer_indexes(criteria, raw_answer)
    if not indexes:
        return

    if (
        criteria.review_criteria_type
        == ReviewCriteriaTypes.RADIO_BUTTONS.value
    ):
        if len(indexes) != 1:
            raise ValidationError(
                f"Select exactly one option for '{criteria.description}'."
            )
    elif (
        criteria.review_criteria_type
        != ReviewCriteriaTypes.CHECKBOXES.value
    ):
        raise ValidationError(
            f"'{criteria.description}' has an unsupported criterion type."
        )


def _posted_answer_indexes(criteria, raw_answer):
    if raw_answer is None or raw_answer == "":
        return []

    indexes = []
    seen = set()
    for token in raw_answer.split(","):
        if not POSTED_ANSWER_INDEX_PATTERN.fullmatch(token):
            raise ValidationError(
                f"The selected options for '{criteria.description}' "
                "are not valid choices."
            )
        index = int(token)
        if not 1 <= index <= len(criteria.options):
            raise ValidationError(
                f"The selected option for '{criteria.description}' "
                "is not one of the available choices."
            )
        if index in seen:
            raise ValidationError(
                f"Select each option at most once for "
                f"'{criteria.description}'."
            )
        seen.add(index)
        indexes.append(index)
    return indexes


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
