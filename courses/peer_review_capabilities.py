"""Management capabilities for staff peer-review inspection and assignment."""

from __future__ import annotations

from core.capabilities import (
    AdapterMetadata,
    Capability,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
)
from courses.services.peer_review_admin import (
    assign_course_peer_reviews,
    read_course_peer_reviews,
)

COURSE_PEER_REVIEWS_READ = Capability(
    key="courses.peer_reviews.read",
    description="Read peer-review state, counts, and deadlines for a course",
    service_kind=ServiceKind.QUERY,
    service=read_course_peer_reviews,
    django_permission="courses.view_project",
    studio=AdapterMetadata(
        route="studio:course-peer-reviews",
        method="GET",
        operation_id="courses.peer_reviews.read.html",
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/courses/{slug}/peer-reviews",
        method="GET",
        operation_id="admin.courses.peer_reviews.read",
        scopes=("courses.peer_reviews.read",),
        result_schema="CoursePeerReviewsResponse",
        rate_class="read",
        rate_cost=1,
    ),
    idempotency=IdempotencyPolicy.NONE,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action="courses.peer_reviews.read",
    redacted_fields=("authorization", "cookie"),
    test_factory=lambda: {"course_id": 0, "course": "", "course_title": "", "projects": []},
)

COURSE_PEER_REVIEWS_ASSIGN = Capability(
    key="courses.peer_reviews.assign",
    description="Create round-robin peer review assignments for a course",
    service_kind=ServiceKind.COMMAND,
    service=assign_course_peer_reviews,
    django_permission="courses.change_project",
    studio=AdapterMetadata(
        route="studio:course-peer-review-assignments",
        method="POST",
        operation_id="courses.peer_reviews.assign.html",
        writable_fields=("cohort", "project"),
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/courses/{slug}/peer-reviews/assignments",
        method="POST",
        operation_id="admin.courses.peer_reviews.assign",
        scopes=("courses.peer_reviews.assign",),
        request_schema="CoursePeerReviewAssignmentsRequest",
        result_schema="CoursePeerReviewAssignmentsResponse",
        writable_fields=("cohort", "project"),
        rate_class="write",
        rate_cost=5,
    ),
    idempotency=IdempotencyPolicy.OPTIONAL,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action="courses.peer_reviews.assigned",
    redacted_fields=("authorization", "cookie"),
    test_factory=lambda: {
        "course_id": 0,
        "course": "",
        "batched": 0,
        "reviews_assigned": 0,
        "projects": [],
    },
)

PEER_REVIEW_ADMIN_CAPABILITIES = (
    COURSE_PEER_REVIEWS_READ,
    COURSE_PEER_REVIEWS_ASSIGN,
)
