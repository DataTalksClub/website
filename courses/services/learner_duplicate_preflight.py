"""Read-only duplicate and reference preflight for the learner tables (BE-12).

Adding uniqueness constraints for the learner logical identities is a graded
migration, and its explicit prerequisite is a preflight that reports where
existing data would already violate the intended identity -- keyed only by
record and user IDs.  An answer, a name, an email address, or any other
learner payload never enters this module's output: a bucket name, a count, and
the IDs an operator needs to adjudicate are the entire vocabulary.

The intended identities (audit BE-12, confirmed against the writers):

* one homework submission per ``(homework, student)``;
* one answer per ``(submission, question)``;
* one project submission per ``(project, student, volunteer_review_only)``;
* one peer-review pair per ``(reviewer submission, evaluated submission)``;
* one criteria response per ``(review, criteria)``.

Reference consistency the foreign keys cannot express:

* a submission's enrollment belongs to the same student and course;
* a review pair's two submissions belong to the same project;
* a criteria response's criterion belongs to the reviewed project's course.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.db.models import Count, F, Q

from courses.models import (
    Answer,
    CriteriaResponse,
    PeerReview,
    ProjectSubmission,
    Submission,
)

#: ``(bucket name, model, group-by fields)`` for every intended identity.
IDENTITY_BUCKETS: tuple[tuple[str, Any, tuple[str, ...]], ...] = (
    ("homework_submission", Submission, ("homework_id", "student_id")),
    ("answer", Answer, ("submission_id", "question_id")),
    (
        "project_submission",
        ProjectSubmission,
        ("project_id", "student_id", "volunteer_review_only"),
    ),
    (
        "peer_review_pair",
        PeerReview,
        ("reviewer_id", "submission_under_evaluation_id"),
    ),
    ("criteria_response", CriteriaResponse, ("review_id", "criteria_id")),
)

#: Upper bound on the group members listed per duplicate bucket, so one
#: catastrophically duplicated table cannot produce an unbounded report.
MAX_LISTED_MEMBERS = 20


@dataclass(frozen=True)
class LearnerDuplicatePreflight:
    """Counts and IDs only -- never learner payload."""

    duplicate_groups: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    inconsistent_references: dict[str, int] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        # The reference buckets always carry all their keys; they are dirty
        # only when a count is nonzero.
        return not self.duplicate_groups and not any(self.inconsistent_references.values())

    def summary(self) -> dict[str, Any]:
        return {
            "duplicates": {
                bucket: groups for bucket, groups in sorted(self.duplicate_groups.items())
            },
            "inconsistent_references": dict(sorted(self.inconsistent_references.items())),
            "clean": self.clean,
        }


def _duplicate_groups() -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for bucket, model, group_fields in IDENTITY_BUCKETS:
        rows = (
            model.objects.values(*group_fields)
            .annotate(record_count=Count("pk"))
            .filter(record_count__gt=1)
            .order_by(*group_fields)
        )
        bucket_groups = []
        for row in rows[:MAX_LISTED_MEMBERS]:
            members = list(
                model.objects.filter(**{name: row[name] for name in group_fields}).values_list(
                    "pk", flat=True
                )[:MAX_LISTED_MEMBERS]
            )
            bucket_groups.append(
                {
                    **{name: row[name] for name in group_fields},
                    "record_count": row["record_count"],
                    "record_ids": members,
                }
            )
        if bucket_groups:
            groups[bucket] = bucket_groups
    return groups


def _inconsistent_counts() -> dict[str, int]:
    return {
        # A submission's enrollment must carry the same student and course.
        "project_submission_enrollment_mismatch": ProjectSubmission.objects.filter(
            ~Q(enrollment__student_id=F("student_id"))
            | ~Q(enrollment__course_id=F("project__course_id"))
        ).count(),
        "homework_submission_enrollment_mismatch": Submission.objects.filter(
            ~Q(enrollment__student_id=F("student_id"))
            | ~Q(enrollment__course_id=F("homework__course_id"))
        ).count(),
        # A review pair's two sides must belong to one project.
        "peer_review_cross_project": PeerReview.objects.filter(
            ~Q(reviewer__project_id=F("submission_under_evaluation__project_id"))
        ).count(),
        # The answered criterion must belong to the reviewed project's course.
        "criteria_response_cross_course": CriteriaResponse.objects.filter(
            ~Q(criteria__course_id=F("review__submission_under_evaluation__project__course_id"))
        ).count(),
    }


def run_learner_duplicate_preflight() -> LearnerDuplicatePreflight:
    return LearnerDuplicatePreflight(
        duplicate_groups=_duplicate_groups(),
        inconsistent_references=_inconsistent_counts(),
    )
