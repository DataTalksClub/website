"""Async scoring and score-notification handlers on the durable jobs worker.

Studio and admin scoring used to run whole-cohort transactions inline, which
blocked the sync Gunicorn workers for more than 2 s on large cohorts. These
handlers run the same pure scoring functions inside the leased worker; the
request path only enqueues a durable intent and returns immediately.

Idempotency: every intent uses a stable deduplication key
(homework-score:<id>[:force], project-score:<id>, ...). Retried leases
re-run the same overwrite-style scoring (bulk_update overwrites totals;
notifications use replay-safe per-recipient idempotency keys), so a retry
never double-applies.
"""

from __future__ import annotations

from community_base.jobs.dispatch import dispatch_after_commit
from community_base.jobs.registry import JobContext, JobPayload, register_handler
from community_base.jobs.runner import PermanentJobError

HOMEWORK_SCORE_HANDLER = "courses.scoring.homework"
PROJECT_SCORE_HANDLER = "courses.scoring.project"
HOMEWORK_NOTIFY_HANDLER = "courses.scoring.homework_notify"
PROJECT_NOTIFY_HANDLER = "courses.scoring.project_notify"


def _payload_int(payload: JobPayload, key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PermanentJobError(f"invalid_{key}")
    return value


def _payload_bool(payload: JobPayload, key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise PermanentJobError(f"invalid_{key}")
    return value


@register_handler(HOMEWORK_SCORE_HANDLER)
def score_homework_job(context: JobContext, payload: JobPayload) -> None:
    del context
    from courses.scoring import score_homework_submissions

    homework_id = _payload_int(payload, "homework_id")
    force = payload.get("force", False)
    if not isinstance(force, bool):
        raise PermanentJobError("invalid_force")
    # Idempotent: already-scored without force returns FAIL inside, scored
    # state persists; retries overwrite the same totals.
    score_homework_submissions(str(homework_id), force=force)


@register_handler(PROJECT_SCORE_HANDLER)
def score_project_job(context: JobContext, payload: JobPayload) -> None:
    del context
    from courses.models.project import Project
    from courses.project_scoring import score_project

    project_id = _payload_int(payload, "project_id")
    try:
        project = Project.objects.get(pk=project_id)
    except Project.DoesNotExist as error:
        raise PermanentJobError("project_not_found") from error
    # Idempotent: non-PEER_REVIEWING state returns FAIL without writes;
    # completed scoring overwrites the same totals on retry.
    score_project(project)


@register_handler(HOMEWORK_NOTIFY_HANDLER)
def notify_homework_scores_job(context: JobContext, payload: JobPayload) -> None:
    del context
    from courses.models.homework import Homework
    from courses.package_notifications import send_homework_score_notification

    homework_id = _payload_int(payload, "homework_id")
    try:
        homework = Homework.objects.get(pk=homework_id)
    except Homework.DoesNotExist as error:
        raise PermanentJobError("homework_not_found") from error
    # Idempotent per recipient via send_package_mail idempotency keys.
    send_homework_score_notification(homework)


@register_handler(PROJECT_NOTIFY_HANDLER)
def notify_project_scores_job(context: JobContext, payload: JobPayload) -> None:
    del context
    from courses.models.project import Project
    from courses.package_notifications import send_project_score_notification

    project_id = _payload_int(payload, "project_id")
    try:
        project = Project.objects.get(pk=project_id)
    except Project.DoesNotExist as error:
        raise PermanentJobError("project_not_found") from error
    send_project_score_notification(project)


def enqueue_homework_score(homework_id: int, *, force: bool = False):
    """Enqueue homework scoring; must be called inside a transaction."""
    suffix = ":force" if force else ""
    key = f"homework-score:{homework_id}{suffix}"
    return dispatch_after_commit(
        HOMEWORK_SCORE_HANDLER,
        key,
        {"homework_id": homework_id, "force": force},
    )


def enqueue_project_score(project_id: int):
    key = f"project-score:{project_id}"
    return dispatch_after_commit(
        PROJECT_SCORE_HANDLER,
        key,
        {"project_id": project_id},
    )


def enqueue_homework_notify(homework_id: int):
    key = f"homework-notify:{homework_id}"
    return dispatch_after_commit(
        HOMEWORK_NOTIFY_HANDLER,
        key,
        {"homework_id": homework_id},
    )


def enqueue_project_notify(project_id: int):
    key = f"project-notify:{project_id}"
    return dispatch_after_commit(
        PROJECT_NOTIFY_HANDLER,
        key,
        {"project_id": project_id},
    )


__all__ = [
    "HOMEWORK_SCORE_HANDLER",
    "PROJECT_SCORE_HANDLER",
    "HOMEWORK_NOTIFY_HANDLER",
    "PROJECT_NOTIFY_HANDLER",
    "enqueue_homework_notify",
    "enqueue_homework_score",
    "enqueue_project_notify",
    "enqueue_project_score",
    "notify_homework_scores_job",
    "notify_project_scores_job",
    "score_homework_job",
    "score_project_job",
]
