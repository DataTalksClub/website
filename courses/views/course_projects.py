from dataclasses import dataclass

from django.db.models import Count, Prefetch, Q
from django.utils import timezone

from courses import coursework_badges
from courses.models.course import Course, User
from courses.models.project import (
    PeerReviewState,
    Project,
    ProjectState,
    ProjectSubmission,
)


@dataclass(frozen=True)
class ProjectBadgeData:
    """A project's state: the words, the pill it wears, and the score behind it.

    `pill` is named from `courses.coursework_badges`, so a project and a piece
    of homework in the same state look the same.  `css_class` is kept for the
    adopted platform's own screens.
    """

    name: str
    css_class: str
    pill: str
    score: object = None


def get_projects_for_course(
    course: Course, user: User
) -> list[Project]:
    if user.is_authenticated:
        queryset = ProjectSubmission.objects.filter(
            student=user
        ).annotate(
            completed_reviews_count=Count(
                "reviewers",
                filter=Q(
                    reviewers__optional=False,
                    reviewers__state=PeerReviewState.SUBMITTED.value,
                ),
            )
        )
    else:
        queryset = ProjectSubmission.objects.none()

    submissions_prefetch = Prefetch(
        "projectsubmission_set",
        queryset=queryset,
        to_attr="submissions",
    )

    projects = (
        Project.objects.filter(course=course)
        .prefetch_related(submissions_prefetch)
        .order_by("id")
    )

    for project in projects:
        update_project_with_additional_info(project)

    return list(projects)


def project_days_until(due_date) -> int:
    now = timezone.now()
    if due_date > now:
        return (due_date - now).days
    return 0


def base_project_badge(state):
    if state == ProjectState.CLOSED.value:
        return ProjectBadgeData("Closed", "bg-secondary", coursework_badges.PAST)
    if state == ProjectState.COLLECTING_SUBMISSIONS.value:
        return ProjectBadgeData("Open", "bg-warning", coursework_badges.YOUR_MOVE)
    return ProjectBadgeData("Not submitted", "bg-secondary", coursework_badges.PAST)


def peer_review_project_badge(project, submission):
    completed_reviews_count = submission.completed_reviews_count
    if completed_reviews_count >= project.number_of_peers_to_evaluate:
        return ProjectBadgeData(
            "Review completed", "bg-success", coursework_badges.DONE
        )

    return ProjectBadgeData("Review", "bg-danger", coursework_badges.YOUR_MOVE)


def completed_project_badge(submission):
    score = submission.total_score
    if submission.passed:
        label = f"Passed ({score})"
        badge = ProjectBadgeData(
            label, "bg-success", coursework_badges.RESULT, score
        )
        return badge

    label = f"Failed ({score})"
    badge = ProjectBadgeData(label, "bg-secondary", coursework_badges.PAST, score)
    return badge


def submitted_project_badge(project, submission):
    state = project.state
    if state == ProjectState.COLLECTING_SUBMISSIONS.value:
        return ProjectBadgeData("Submitted", "bg-info", coursework_badges.DONE)
    if state == ProjectState.PEER_REVIEWING.value:
        return peer_review_project_badge(project, submission)
    if state == ProjectState.COMPLETED.value:
        return completed_project_badge(submission)
    return None


def update_project_with_additional_info(project: Project) -> None:
    project.days_until_submission_due = project_days_until(
        project.submission_due_date
    )
    project.days_until_pr_due = project_days_until(
        project.peer_review_due_date
    )

    project.submitted = False
    project.score = None
    badge = base_project_badge(project.state)
    project.badge_state_name = badge.name
    project.badge_css_class = badge.css_class
    project.badge_pill = badge.pill

    if not project.submissions:
        return

    submission = project.submissions[0]
    project.submitted = True
    project.submitted_at = submission.submitted_at

    override = submitted_project_badge(project, submission)
    if override is not None:
        project.badge_state_name = override.name
        project.badge_css_class = override.css_class
        project.badge_pill = override.pill
        project.score = override.score
