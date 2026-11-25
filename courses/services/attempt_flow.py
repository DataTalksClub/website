"""DTC's adapter for the shared learner attempt flow (community-base ``attempt_flow``).

Reads one learner's project submissions and peer reviews for a cohort and hands
them to the package, which computes phases, status rows, the attempt sidebar
and the Evaluation criteria page. URLs are this site's cohort project routes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from community_base.coursework.attempt_flow import (
    attempt_nav_items,
    criteria_page_context,
    learner_attempt_phases,
    phase_row,
)
from django.urls import reverse
from django.utils import timezone

from courses.models.project import (
    PeerReview,
    PeerReviewState,
    Project,
    ProjectState,
    ProjectSubmission,
    project_yaml_criteria,
)
from courses.views.url_utils import canonical_cohort_url_kwargs


def _url(name: str, project: Project, **extra) -> str:
    return reverse(
        name,
        kwargs={
            **canonical_cohort_url_kwargs(project.course),
            "project_slug": project.slug,
            **extra,
        },
    )


def submit_url(project: Project) -> str:
    return _url("cohort_project", project)


def reviews_url(project: Project) -> str:
    return _url("cohort_projects_eval", project)


def review_url(review: PeerReview) -> str:
    project = review.submission_under_evaluation.project
    return _url("cohort_projects_eval_submit", project, review_id=review.pk)


def feedback_url(project: Project) -> str:
    return _url("cohort_project_results", project)


def criteria_url(project: Project) -> str:
    return _url("cohort_project_criteria", project)


@dataclass(frozen=True)
class LearnerAttempts:
    projects: list
    submissions: dict
    assigned: dict
    phases: dict


def cohort_projects(cohort):
    """The cohort's attempts learners can see: closed projects are hidden."""
    return list(
        Project.objects.filter(course=cohort)
        .exclude(state=ProjectState.CLOSED.value)
        .select_related("course__course", "criteria_set")
        .order_by("submission_due_date", "pk")
    )


def learner_attempts(user, cohort, projects=None) -> LearnerAttempts:
    projects = cohort_projects(cohort) if projects is None else list(projects)
    submissions: dict = {}
    assigned: dict = defaultdict(list)
    received: dict = defaultdict(list)
    if getattr(user, "is_authenticated", False) and projects:
        for submission in ProjectSubmission.objects.filter(
            student=user, project__in=projects, volunteer_review_only=False
        ):
            submissions[submission.project_id] = submission
        for review in (
            PeerReview.objects.filter(reviewer__student=user, reviewer__project__in=projects)
            .select_related("submission_under_evaluation__project__course__course")
            .order_by("optional", "pk")
        ):
            assigned[review.submission_under_evaluation.project_id].append(review)
        for review in PeerReview.objects.filter(
            submission_under_evaluation__in=list(submissions.values()),
            state=PeerReviewState.SUBMITTED.value,
        ):
            received[review.submission_under_evaluation_id].append(review)
    phases = learner_attempt_phases(
        projects,
        submissions=submissions,
        assigned=assigned,
        received=received,
        criteria_for=project_yaml_criteria,
        now=timezone.now(),
        passing_score=projects[0].course.project_passing_score if projects else 0,
    )
    return LearnerAttempts(projects, submissions, dict(assigned), phases)


def attempt_rows(attempts: LearnerAttempts) -> list[dict]:
    """One status row per attempt: title, status label/tone, link, action, date."""
    rows = []
    for project in attempts.projects:
        row = phase_row(
            attempts.phases[project.pk],
            submit_url=submit_url(project),
            reviews_url=reviews_url(project),
            when=project.submission_due_date,
        )
        rows.append({"project": project, **row})
    return rows


def attempt_steps(attempts: LearnerAttempts, project, *, page="", current_review_id=None):
    """The attempt's sidebar sub-steps for ``project``."""
    return attempt_nav_items(
        attempts.phases.get(project.pk),
        has_submission=project.pk in attempts.submissions,
        assigned_reviews=attempts.assigned.get(project.pk, []),
        course_id=project.course_id,
        submit_url=submit_url(project),
        review_url=review_url,
        feedback_url=feedback_url(project),
        page=page,
        current_review_id=current_review_id,
    )


def criteria_page(user, cohort) -> dict | None:
    attempts = learner_attempts(user, cohort)
    page = criteria_page_context(
        attempts.projects, criteria_for=project_yaml_criteria, phases=attempts.phases
    )
    if page is None:
        return None
    return {**page, "attempts": attempts}
