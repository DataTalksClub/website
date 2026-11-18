"""Staff peer-review state and assignment services for courses."""

from __future__ import annotations

from typing import Any

from django.db.models import Count, Q

from courses.models.cohort import Cohort, Course
from courses.models.project import (
    PeerReview,
    PeerReviewState,
    Project,
    ProjectState,
    ProjectSubmission,
)
from courses.package_notifications import send_peer_review_assignment_notification
from courses.project_assignment import (
    ProjectActionStatus,
    assign_peer_reviews_for_project,
)


class PeerReviewAdminError(Exception):
    """Domain error mapped to HTTP status codes by management_api."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def _isoformat_or_none(value) -> str | None:
    return value.isoformat() if value is not None else None


def _resolve_scope(course_slug: str, cohort_slug: str, project_slug: str):
    course = Course.objects.filter(slug=course_slug).first()
    cohort = None
    if course is None:
        cohort = Cohort.objects.select_related("course").filter(slug=course_slug).first()
        if cohort is None:
            raise PeerReviewAdminError(404, "unknown_course", "Course was not found.")
        course = cohort.course
    elif cohort_slug:
        cohort = Cohort.objects.filter(
            Q(course=course) & (Q(identifier=cohort_slug) | Q(slug=cohort_slug))
        ).first()
        if cohort is None:
            raise PeerReviewAdminError(404, "unknown_cohort", "Cohort was not found.")

    project = None
    if project_slug:
        qs = Project.objects.filter(course__course=course, slug=project_slug)
        if cohort is not None:
            qs = qs.filter(course=cohort)
        project = qs.select_related("course").first()
        if project is None:
            raise PeerReviewAdminError(404, "unknown_project", "Project was not found.")
    return course, cohort, project


def _annotated_projects(
    course: Course, cohort: Cohort | None, project: Project | None
) -> list[Project]:
    qs = Project.objects.filter(course__course=course).select_related("course")
    if cohort is not None:
        qs = qs.filter(course=cohort)
    if project is not None:
        qs = qs.filter(pk=project.pk)
    real = Q(projectsubmission__volunteer_review_only=False)
    annotations = {
        "submissions_count": Count("projectsubmission", filter=real, distinct=True),
        "assignments_count": Count(
            "projectsubmission__reviews_under_evaluation", filter=real, distinct=True
        ),
        "completed_reviews_count": Count(
            "projectsubmission__reviews_under_evaluation",
            filter=real
            & Q(projectsubmission__reviews_under_evaluation__state=PeerReviewState.SUBMITTED.value),
            distinct=True,
        ),
    }
    return list(qs.annotate(**annotations).order_by("submission_due_date", "pk"))


def _serialize_cohort_ref(cohort: Cohort) -> dict[str, Any]:
    return {
        "id": cohort.pk,
        "slug": cohort.slug,
        "identifier": cohort.identifier,
        "title": cohort.title,
    }


def _serialize_project(project: Any) -> dict[str, Any]:
    assignments = project.assignments_count
    completed = project.completed_reviews_count
    review_due = _isoformat_or_none(project.peer_review_due_date)
    return {
        "id": project.pk,
        "slug": project.slug,
        "title": project.title,
        "cohort": _serialize_cohort_ref(project.course),
        "state": project.state,
        "state_label": project.get_state_display(),
        "submission_due_at": _isoformat_or_none(project.submission_due_date),
        "review_due_at": review_due,
        "deadline": review_due,
        "peer_review_count": project.number_of_peers_to_evaluate,
        "submissions_count": project.submissions_count,
        "assignments_count": assignments,
        "completed_reviews_count": completed,
        "pending_reviews_count": max(0, assignments - completed),
    }


def read_course_peer_reviews(
    payload: dict[str, Any],
    *,
    course_slug: str,
    context=None,
) -> dict[str, Any]:
    del context
    cohort_slug = str(payload.get("cohort") or "").strip()
    project_slug = str(payload.get("project") or "").strip()
    course, cohort, project = _resolve_scope(course_slug, cohort_slug, project_slug)
    projects = _annotated_projects(course, cohort, project)
    return {
        "course_id": course.pk,
        "course": course.slug,
        "course_title": course.title,
        "projects": [_serialize_project(p) for p in projects],
    }


def assign_course_peer_reviews(
    payload: dict[str, Any],
    *,
    course_slug: str,
    context=None,
) -> dict[str, Any]:
    del context
    cohort_slug = str(payload.get("cohort") or "").strip()
    project_slug = str(payload.get("project") or "").strip()
    course, cohort, project = _resolve_scope(course_slug, cohort_slug, project_slug)

    qs = Project.objects.filter(course__course=course).select_related("course")
    if cohort is not None:
        qs = qs.filter(course=cohort)
    if project is not None:
        qs = qs.filter(pk=project.pk)

    batched = 0
    reviews_assigned = 0
    for item in qs.order_by("submission_due_date", "pk"):
        if item.state != ProjectState.COLLECTING_SUBMISSIONS.value:
            continue
        status, _message = assign_peer_reviews_for_project(item)
        if status == ProjectActionStatus.OK:
            send_peer_review_assignment_notification(item)
            batched += ProjectSubmission.objects.filter(
                project=item, volunteer_review_only=False
            ).count()
            reviews_assigned += PeerReview.objects.filter(
                submission_under_evaluation__project=item, optional=False
            ).count()

    projects = _annotated_projects(course, cohort, project)
    return {
        "course_id": course.pk,
        "course": course.slug,
        "batched": batched,
        "reviews_assigned": reviews_assigned,
        "projects": [_serialize_project(p) for p in projects],
    }
