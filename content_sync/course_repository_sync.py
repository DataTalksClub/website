"""Push entry point: project one exact commit announced by a signed GitHub push.

This module is deliberately thin.  It resolves the registered source, hands the
commit to :func:`content_sync.course_repository_ingest.ingest_course_repository`,
and translates that service's refusals into durable-job outcomes.  Every rule
about what a course repository may contain lives in the shared service, which
the offline pull entry point (``scripts/prod/sync_course_repositories.py``) calls too.

The archive download is a network side effect and stays where it belongs: inside
the durable job the webhook enqueues after commit, never in the request.
"""

from __future__ import annotations

import logging
from uuid import UUID

from content.models import ContentSource
from content_sync.course_repository_ingest import (
    COMMIT_PATTERN,
    MAX_ARCHIVE_BYTES,
    REQUEST_TIMEOUT_SECONDS,
    CourseRepositoryFetchError,
    CourseRepositoryIngestError,
    course_repository_limits,
    fetch_course_repository_snapshot,
    ingest_course_repository,
)
from content_sync.course_repository_webhook import (
    COURSE_REPOSITORY_ADAPTER_TYPE,
    COURSE_REPOSITORY_JOB_HANDLER,
)
from jobs.execution import PermanentJobError, RetryableJobError
from jobs.registry import JobContext, JobPayload, register_handler

logger = logging.getLogger(__name__)


def _payload_uuid(payload: JobPayload, key: str) -> UUID:
    value = payload.get(key)
    try:
        parsed = UUID(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None:
        raise PermanentJobError("course_repository_job_payload_invalid")
    return parsed


def _payload_commit(payload: JobPayload) -> str:
    value = payload.get("commit_sha")
    if not isinstance(value, str) or COMMIT_PATTERN.fullmatch(value) is None:
        raise PermanentJobError("course_repository_job_payload_invalid")
    return value


@register_handler(COURSE_REPOSITORY_JOB_HANDLER)
def import_course_repository_commit(_context: JobContext, payload: JobPayload) -> None:
    source_id = _payload_uuid(payload, "source_uuid")
    _payload_uuid(payload, "delivery_record_id")
    commit_sha = _payload_commit(payload)
    try:
        source = ContentSource.objects.get(id=source_id)
    except ContentSource.DoesNotExist as error:
        raise PermanentJobError("course_repository_source_missing") from error
    if not source.enabled or source.adapter_type != COURSE_REPOSITORY_ADAPTER_TYPE:
        raise PermanentJobError("course_repository_source_disabled")

    try:
        ingest_course_repository(source=source, commit_sha=commit_sha)
    except CourseRepositoryIngestError as error:
        # ``last_error_code`` on the durable job is a bounded identifier, so the
        # part that actually explains the failure -- which file, how many bytes,
        # which limit -- would be dropped there.  Log it once, redacted to paths
        # and numbers, so an operator is not left to reverse-engineer a code.
        logger.warning(
            "course_repository_ingest_refused",
            extra={
                "content_source": source.stable_id,
                "commit_sha": commit_sha,
                "error_code": error.code,
                "error_detail": error.detail,
            },
        )
        if error.retryable:
            raise RetryableJobError(error.code) from error
        raise PermanentJobError(error.code) from error


__all__ = (
    "COMMIT_PATTERN",
    "MAX_ARCHIVE_BYTES",
    "REQUEST_TIMEOUT_SECONDS",
    "CourseRepositoryFetchError",
    "course_repository_limits",
    "fetch_course_repository_snapshot",
    "import_course_repository_commit",
)
