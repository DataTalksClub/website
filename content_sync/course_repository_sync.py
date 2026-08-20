"""Fetch and project one exact commit from a registered course repository."""

from __future__ import annotations

import io
import re
import tarfile
from pathlib import PurePosixPath
from uuid import UUID

import requests

from content.models import ContentSource
from content_sync.course_repository import (
    CourseRepositoryLimits,
    CourseRepositoryValidationError,
    parse_course_repository,
)
from content_sync.course_repository_webhook import (
    COURSE_REPOSITORY_ADAPTER_TYPE,
    COURSE_REPOSITORY_JOB_HANDLER,
)
from courses.services.curriculum_import import (
    CurriculumImportCommand,
    CurriculumImportError,
    import_course_repository_curriculum,
)
from jobs.execution import PermanentJobError, RetryableJobError
from jobs.registry import JobContext, JobPayload, register_handler

COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
MAX_ARCHIVE_BYTES = 200_000_000
MAX_FILE_BYTES = 8_000_000
REQUEST_TIMEOUT_SECONDS = 30


class CourseRepositoryFetchError(RuntimeError):
    """A bounded fetch/archive failure with retry classification."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


def _validate_commit(commit_sha: str) -> str:
    if not isinstance(commit_sha, str) or COMMIT_PATTERN.fullmatch(commit_sha) is None:
        raise CourseRepositoryFetchError("course_repository_commit_invalid", retryable=False)
    return commit_sha


def _archive_path(name: str) -> str | None:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise CourseRepositoryFetchError("course_repository_archive_path_invalid", retryable=False)
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise CourseRepositoryFetchError("course_repository_archive_path_invalid", retryable=False)
    parts = pure.parts
    if len(parts) < 2:
        return None
    relative = PurePosixPath(*parts[1:]).as_posix()
    if relative == ".":
        return None
    return relative


def _response_bytes(response: requests.Response) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_ARCHIVE_BYTES:
                raise CourseRepositoryFetchError(
                    "course_repository_archive_too_large", retryable=False
                )
        except ValueError as error:
            raise CourseRepositoryFetchError(
                "course_repository_response_invalid", retryable=False
            ) from error
    chunks: list[bytes] = []
    size = 0
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_ARCHIVE_BYTES:
                raise CourseRepositoryFetchError(
                    "course_repository_archive_too_large", retryable=False
                )
            chunks.append(chunk)
    except CourseRepositoryFetchError:
        raise
    except requests.RequestException as error:
        raise CourseRepositoryFetchError(
            "course_repository_fetch_failed", retryable=True
        ) from error
    return b"".join(chunks)


def fetch_course_repository_snapshot(
    *,
    owner: str,
    repository: str,
    commit_sha: str,
    max_files: int,
    max_bytes: int,
) -> dict[str, bytes]:
    """Download GitHub's immutable codeload archive and return a safe snapshot."""

    commit_sha = _validate_commit(commit_sha)
    if (
        not isinstance(max_files, int)
        or max_files < 1
        or not isinstance(max_bytes, int)
        or max_bytes < 1
    ):
        raise CourseRepositoryFetchError("course_repository_limits_invalid", retryable=False)
    url = f"https://codeload.github.com/{owner}/{repository}/tar.gz/{commit_sha}"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS, stream=True)
    except requests.RequestException as error:
        raise CourseRepositoryFetchError(
            "course_repository_fetch_failed", retryable=True
        ) from error
    try:
        if response.status_code == 404:
            raise CourseRepositoryFetchError("course_repository_commit_not_found", retryable=False)
        if response.status_code >= 500:
            raise CourseRepositoryFetchError(
                "course_repository_provider_unavailable", retryable=True
            )
        if response.status_code != 200:
            raise CourseRepositoryFetchError("course_repository_fetch_rejected", retryable=False)
        archive_bytes = _response_bytes(response)
    finally:
        response.close()

    snapshot: dict[str, bytes] = {}
    total_bytes = 0
    try:
        archive = tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz")
    except (tarfile.TarError, OSError) as error:
        raise CourseRepositoryFetchError(
            "course_repository_archive_invalid", retryable=False
        ) from error
    with archive:
        for member in archive:
            path = _archive_path(member.name)
            if path is None:
                continue
            if not member.isreg() or member.issym() or member.islnk():
                raise CourseRepositoryFetchError(
                    "course_repository_archive_entry_invalid", retryable=False
                )
            if member.size < 0 or member.size > MAX_FILE_BYTES:
                raise CourseRepositoryFetchError(
                    "course_repository_file_too_large", retryable=False
                )
            if len(snapshot) >= max_files or total_bytes + member.size > max_bytes:
                raise CourseRepositoryFetchError(
                    "course_repository_source_limit_exceeded", retryable=False
                )
            if path in snapshot:
                raise CourseRepositoryFetchError(
                    "course_repository_duplicate_path", retryable=False
                )
            extracted = archive.extractfile(member)
            if extracted is None:
                raise CourseRepositoryFetchError(
                    "course_repository_archive_entry_invalid", retryable=False
                )
            content = extracted.read(member.size + 1)
            if len(content) != member.size:
                raise CourseRepositoryFetchError(
                    "course_repository_archive_entry_invalid", retryable=False
                )
            snapshot[path] = content
            total_bytes += len(content)
    return snapshot


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
        snapshot = fetch_course_repository_snapshot(
            owner=source.repository_owner,
            repository=source.repository_name,
            commit_sha=commit_sha,
            max_files=min(source.max_files, 5_000),
            max_bytes=min(source.max_bytes, 100_000_000),
        )
    except CourseRepositoryFetchError as error:
        if error.retryable:
            raise RetryableJobError(error.code) from error
        raise PermanentJobError(error.code) from error

    try:
        parsed = parse_course_repository(
            snapshot,
            commit_sha=commit_sha,
            limits=CourseRepositoryLimits(
                max_files=min(source.max_files, 5_000),
                max_total_bytes=min(source.max_bytes, 100_000_000),
            ),
        )
    except CourseRepositoryValidationError as error:
        raise PermanentJobError(f"course_repository_{error.diagnostics[0].code}") from error

    try:
        import_course_repository_curriculum(
            CurriculumImportCommand(
                source=parsed,
                source_uuid=source.id,
                source_stable_id=source.stable_id,
                repository_owner=source.repository_owner,
                repository_name=source.repository_name,
                repository_branch=source.branch,
                commit_sha=commit_sha,
            )
        )
    except CurriculumImportError as error:
        raise PermanentJobError(f"course_repository_{error.code}") from error


__all__ = (
    "CourseRepositoryFetchError",
    "fetch_course_repository_snapshot",
    "import_course_repository_commit",
)
