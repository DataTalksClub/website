"""Fail-closed local fixture for the DE Zoomcamp Project 1 review phase.

This is deliberately separate from the catalog seed.  The catalog seed owns
course and assignment identity; this service owns only a clearly synthetic
learner/review scenario for local development and tests, then delegates the
state transition and deterministic review assignment to the existing project
assignment service.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from courses.models import (
    Enrollment,
    PeerReview,
    Project,
    ProjectState,
    ProjectSubmission,
    ReviewCriteria,
    ReviewCriteriaTypes,
    User,
)
from courses.project_assignment import (
    ProjectActionStatus,
    assign_peer_reviews_for_project,
)
from courses.services.local_course_seed import (
    LocalCourseSeedError,
    assert_local_database,
    seed_local_courses,
)

DEFAULT_COURSE_SLUG = "de-zoomcamp-2026"
DEFAULT_PROJECT_SLUG = "project-01-project-attempt-1"
SYNTHETIC_USERNAME_PREFIX = "local-de-project1-"
SYNTHETIC_CRITERIA_PREFIX = "Local DE Project 1 — "

SCENARIO_SUBMISSIONS = (
    {
        "topic": "Bike-share demand forecasting",
        "repository": "https://github.com/example/de-zoomcamp-2026-bike-share",
        "commit": "a1b2c3d",
        "learning_links": [
            "https://example.com/de-zoomcamp-2026/bike-share-progress",
        ],
        "hours": 18.5,
        "comment": "Compared weekday and weekend demand with a reproducible dbt model.",
    },
    {
        "topic": "Weather station ingestion",
        "repository": "https://github.com/example/de-zoomcamp-2026-weather",
        "commit": "b2c3d4e",
        "learning_links": [
            "https://example.com/de-zoomcamp-2026/weather-progress",
        ],
        "hours": 15.0,
        "comment": "Documented late-arriving measurements and a small backfill job.",
    },
    {
        "topic": "Public transit reliability",
        "repository": "https://github.com/example/de-zoomcamp-2026-transit",
        "commit": "c3d4e5f",
        "learning_links": [
            "https://example.com/de-zoomcamp-2026/transit-progress",
        ],
        "hours": 22.0,
        "comment": "Built a daily reliability aggregate and checked it with warehouse tests.",
    },
    {
        "topic": "Food inspection trends",
        "repository": "https://github.com/example/de-zoomcamp-2026-food",
        "commit": "d4e5f6a",
        "learning_links": [
            "https://example.com/de-zoomcamp-2026/food-progress",
        ],
        "hours": 12.0,
        "comment": "Explored inspection outcomes by neighborhood using a documented pipeline.",
    },
    {
        "topic": "Energy consumption dashboard",
        "repository": "https://github.com/example/de-zoomcamp-2026-energy",
        "commit": "e5f6a7b",
        "learning_links": [
            "https://example.com/de-zoomcamp-2026/energy-progress",
        ],
        "hours": 20.5,
        "comment": "Added an incremental load and a dashboard-ready dimensional model.",
    },
    {
        "topic": "Library circulation analysis",
        "repository": "https://github.com/example/de-zoomcamp-2026-library",
        "commit": "f6a7b8c",
        "learning_links": [
            "https://example.com/de-zoomcamp-2026/library-progress",
        ],
        "hours": 16.0,
        "comment": "Tracked seasonal circulation patterns and included a reproducible report.",
    },
)

REVIEW_CRITERIA = (
    (
        "data model",
        ReviewCriteriaTypes.RADIO_BUTTONS.value,
        [
            {"criteria": "Needs work", "score": 0},
            {"criteria": "Solid", "score": 1},
            {"criteria": "Clear and useful", "score": 2},
            {"criteria": "Excellent", "score": 3},
        ],
    ),
    (
        "reproducibility",
        ReviewCriteriaTypes.RADIO_BUTTONS.value,
        [
            {"criteria": "Missing", "score": 0},
            {"criteria": "Partial", "score": 1},
            {"criteria": "Complete", "score": 2},
            {"criteria": "Thorough", "score": 3},
        ],
    ),
    (
        "engineering practices",
        ReviewCriteriaTypes.CHECKBOXES.value,
        [
            {"criteria": "Tests", "score": 1},
            {"criteria": "Documentation", "score": 1},
            {"criteria": "Version control", "score": 1},
            {"criteria": "Useful logging", "score": 1},
        ],
    ),
)


class LocalProjectReviewSeedError(LocalCourseSeedError):
    """The local scenario refused to overwrite unrelated project data."""


@dataclass(frozen=True, slots=True)
class LocalProjectReviewSeedResult:
    course_slug: str
    project_slug: str
    submission_count: int
    peer_review_count: int
    review_criteria_count: int
    state: str

    def summary(self) -> dict[str, Any]:
        return {
            "course_slug": self.course_slug,
            "project_slug": self.project_slug,
            "submissions": self.submission_count,
            "peer_reviews": self.peer_review_count,
            "review_criteria": self.review_criteria_count,
            "state": self.state,
        }


def _cohort_model():
    """Resolve Project.course without importing the legacy Course symbol."""

    return Project._meta.get_field("course").remote_field.model


def _ensure_review_criteria(course) -> list[ReviewCriteria]:
    criteria = list(ReviewCriteria.objects.filter(course=course).order_by("id"))
    for suffix, criteria_type, options in REVIEW_CRITERIA:
        if len(criteria) >= len(REVIEW_CRITERIA):
            break
        criterion, _ = ReviewCriteria.objects.get_or_create(
            course=course,
            description=f"{SYNTHETIC_CRITERIA_PREFIX}{suffix}",
            defaults={
                "review_criteria_type": criteria_type,
                "options": options,
            },
        )
        criteria.append(criterion)
    return criteria


def _assert_project_owns_only_synthetic_submissions(project: Project) -> None:
    has_unowned_submissions = (
        ProjectSubmission.objects.filter(
            project=project,
        )
        .exclude(
            student__username__startswith=SYNTHETIC_USERNAME_PREFIX,
        )
        .exists()
    )
    if has_unowned_submissions:
        raise LocalProjectReviewSeedError("project-has-unowned-submissions")


def _synthetic_user(index: int) -> User:
    username = f"{SYNTHETIC_USERNAME_PREFIX}{index:02d}"
    email = f"{username}@example.invalid"
    user, created = User.objects.get_or_create(
        username=username,
        defaults={"email": email, "is_active": True},
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user


def _create_synthetic_submissions(
    course,
    project: Project,
) -> list[ProjectSubmission]:
    submissions = []
    for index, scenario in enumerate(SCENARIO_SUBMISSIONS, start=1):
        user = _synthetic_user(index)
        enrollment, _ = Enrollment.objects.get_or_create(
            student=user,
            course=course,
            defaults={
                "display_name": f"Local Project 1 learner {index:02d}",
            },
        )
        submission = ProjectSubmission.objects.create(
            project=project,
            student=user,
            enrollment=enrollment,
            github_link=scenario["repository"],
            commit_id=scenario["commit"],
            learning_in_public_links=scenario["learning_links"],
            time_spent=scenario["hours"],
            problems_comments=(f"{scenario['topic']}: {scenario['comment']}"),
        )
        submissions.append(submission)
    return submissions


def seed_local_project_review(
    *,
    course_slug: str = DEFAULT_COURSE_SLUG,
    project_slug: str = DEFAULT_PROJECT_SLUG,
) -> LocalProjectReviewSeedResult:
    """Create the local Project 1 scenario and move it into peer review."""

    assert_local_database()
    seed_local_courses()
    cohort = _cohort_model()
    try:
        course = cohort.objects.get(slug=course_slug)
    except cohort.DoesNotExist as error:
        raise LocalProjectReviewSeedError("course-not-found") from error
    try:
        project = Project.objects.get(course=course, slug=project_slug)
    except Project.DoesNotExist as error:
        raise LocalProjectReviewSeedError("project-not-found") from error

    with transaction.atomic():
        _assert_project_owns_only_synthetic_submissions(project)
        ProjectSubmission.objects.filter(project=project).delete()

        project.state = ProjectState.COLLECTING_SUBMISSIONS.value
        if project.submission_due_date > timezone.now():
            project.submission_due_date = timezone.now() - timedelta(hours=1)
        project.save(update_fields=["state", "submission_due_date"])

        _ensure_review_criteria(course)
        _create_synthetic_submissions(course, project)

        status, _ = assign_peer_reviews_for_project(project)
        if status is not ProjectActionStatus.OK:
            raise LocalProjectReviewSeedError("peer-review-assignment-failed")

        project.refresh_from_db()
        submission_count = ProjectSubmission.objects.filter(project=project).count()
        peer_review_count = PeerReview.objects.filter(
            submission_under_evaluation__project=project,
        ).count()
        criteria_count = ReviewCriteria.objects.filter(course=course).count()

    return LocalProjectReviewSeedResult(
        course_slug=course.slug,
        project_slug=project.slug,
        submission_count=submission_count,
        peer_review_count=peer_review_count,
        review_criteria_count=criteria_count,
        state=project.state,
    )
