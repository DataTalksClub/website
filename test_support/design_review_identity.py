"""Django-free deterministic identities shared by issue #237 setup and settings."""

from __future__ import annotations

from datetime import UTC, datetime

from test_support.factories.context import FactoryContext

FROZEN_AT = datetime(2026, 8, 29, 10, 0, tzinfo=UTC)
SEED = "issue-237-design-review-v1"
DEFAULT_EXECUTION_NAMESPACE = "local-review"


def complaint_enrollment_id(
    execution_namespace: str = DEFAULT_EXECUTION_NAMESPACE,
) -> int:
    context = FactoryContext(SEED, execution_namespace, FROZEN_AT)
    return context.physical_int(
        "adopted_courses.enrollment",
        "minimal_valid.row",
    )


def complaint_path(
    execution_namespace: str = DEFAULT_EXECUTION_NAMESPACE,
) -> str:
    enrollment_id = complaint_enrollment_id(execution_namespace)
    return f"/courses/data-reliability-zoomcamp/2026/leaderboard/{enrollment_id}/report"


__all__ = [
    "DEFAULT_EXECUTION_NAMESPACE",
    "FROZEN_AT",
    "SEED",
    "complaint_enrollment_id",
    "complaint_path",
]
