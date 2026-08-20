"""Validated GitHub push data for registered course repositories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

COURSE_REPOSITORY_ADAPTER_TYPE = "course_repository_v1"
COURSE_REPOSITORY_WEBHOOK_NAMESPACE = "github.course_repository"
COURSE_REPOSITORY_JOB_HANDLER = "content_sync.course_repository_sync.import_commit"
COURSE_REPOSITORY_PARSER_VERSION = "course-repository-v1"
SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")


class CourseRepositoryWebhookError(ValueError):
    """A source-safe validation error for an untrusted GitHub push."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class GitHubCoursePush:
    owner: str
    repository: str
    branch: str
    commit_sha: str
    deleted: bool


def _required_text(value: Any, *, code: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise CourseRepositoryWebhookError(code)
    return value


def parse_github_course_push(payload: object, *, event_type: str) -> GitHubCoursePush:
    """Parse only the authenticated, branch-specific fields needed for a sync job."""

    if event_type != "push":
        raise CourseRepositoryWebhookError("github_event_not_push")
    if not isinstance(payload, dict):
        raise CourseRepositoryWebhookError("github_payload_invalid")
    repository = payload.get("repository")
    if not isinstance(repository, dict):
        raise CourseRepositoryWebhookError("github_repository_invalid")

    full_name = repository.get("full_name")
    if not isinstance(full_name, str) or full_name.count("/") != 1:
        raise CourseRepositoryWebhookError("github_repository_invalid")
    owner, repository_name = full_name.split("/", 1)
    owner = _required_text(
        owner,
        code="github_repository_invalid",
        pattern=REPOSITORY_COMPONENT_PATTERN,
    )
    repository_name = _required_text(
        repository_name,
        code="github_repository_invalid",
        pattern=REPOSITORY_COMPONENT_PATTERN,
    )

    nested_owner = repository.get("owner")
    nested_owner_name = nested_owner.get("login") if isinstance(nested_owner, dict) else None
    if nested_owner_name is not None and nested_owner_name != owner:
        raise CourseRepositoryWebhookError("github_repository_invalid")
    if repository.get("name") is not None and repository.get("name") != repository_name:
        raise CourseRepositoryWebhookError("github_repository_invalid")

    ref = payload.get("ref")
    if not isinstance(ref, str) or not ref.startswith("refs/heads/"):
        raise CourseRepositoryWebhookError("github_ref_invalid")
    branch = _required_text(
        ref.removeprefix("refs/heads/"),
        code="github_ref_invalid",
        pattern=BRANCH_PATTERN,
    )
    if ".." in branch or branch.endswith("/") or "//" in branch:
        raise CourseRepositoryWebhookError("github_ref_invalid")

    commit_sha = _required_text(
        payload.get("after"),
        code="github_commit_invalid",
        pattern=SHA1_PATTERN,
    )
    if payload.get("deleted") is not False:
        raise CourseRepositoryWebhookError("github_push_deleted")

    return GitHubCoursePush(
        owner=owner,
        repository=repository_name,
        branch=branch,
        commit_sha=commit_sha,
        deleted=False,
    )


__all__ = (
    "COURSE_REPOSITORY_ADAPTER_TYPE",
    "COURSE_REPOSITORY_JOB_HANDLER",
    "COURSE_REPOSITORY_PARSER_VERSION",
    "COURSE_REPOSITORY_WEBHOOK_NAMESPACE",
    "CourseRepositoryWebhookError",
    "GitHubCoursePush",
    "parse_github_course_push",
)
