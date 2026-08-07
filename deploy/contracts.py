from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SOURCE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
TASK_DEFINITION_ARN_PATTERN = re.compile(
    r"^arn:aws:ecs:[a-z0-9-]+:[0-9]{12}:task-definition/[A-Za-z0-9_-]+:[1-9][0-9]*$"
)
PLACEHOLDER_DIGEST = f"sha256:{'1' * 64}"
RELEASE_MANAGER = "DataTalksClub/website"


class ReleaseContractError(RuntimeError):
    """Raised when a release input or observed runtime state is unsafe."""


def validate_source_sha(value: str) -> str:
    if not SOURCE_SHA_PATTERN.fullmatch(value):
        raise ReleaseContractError("source SHA must be 40 lowercase hexadecimal characters")
    return value


def validate_image_digest(value: str) -> str:
    if not IMAGE_DIGEST_PATTERN.fullmatch(value):
        raise ReleaseContractError(
            "image digest must be sha256 plus 64 lowercase hexadecimal characters"
        )
    if value == PLACEHOLDER_DIGEST:
        raise ReleaseContractError("the Terraform bootstrap placeholder is not a release")
    return value


def validate_task_definition_arn(value: str) -> str:
    if not TASK_DEFINITION_ARN_PATTERN.fullmatch(value):
        raise ReleaseContractError(f"invalid task-definition ARN: {value}")
    return value


@dataclass(frozen=True)
class ReleaseIdentity:
    source_sha: str
    image_digest: str
    repository_uri: str

    def __post_init__(self) -> None:
        validate_source_sha(self.source_sha)
        validate_image_digest(self.image_digest)
        if not self.repository_uri or "@" in self.repository_uri or "://" in self.repository_uri:
            raise ReleaseContractError("repository URI must be an untagged ECR repository URI")

    @property
    def image(self) -> str:
        return f"{self.repository_uri}@{self.image_digest}"


@dataclass(frozen=True)
class ServiceSnapshot:
    service_name: str
    task_definition_arn: str
    desired_count: int
    running_count: int
    pending_count: int
    source_sha: str | None
    image_digest: str | None

    def __post_init__(self) -> None:
        validate_task_definition_arn(self.task_definition_arn)
        if min(self.desired_count, self.running_count, self.pending_count) < 0:
            raise ReleaseContractError("ECS service counts cannot be negative")


@dataclass(frozen=True)
class ReleaseRecord:
    source_sha: str
    image_digest: str
    web_task_definition_arn: str
    worker_task_definition_arn: str
    migration_task_definition_arn: str
    web_desired_count: int
    worker_desired_count: int
    rollback_eligible: bool

    def __post_init__(self) -> None:
        validate_source_sha(self.source_sha)
        validate_image_digest(self.image_digest)
        validate_task_definition_arn(self.web_task_definition_arn)
        validate_task_definition_arn(self.worker_task_definition_arn)
        validate_task_definition_arn(self.migration_task_definition_arn)
        if self.web_desired_count < 1 or self.worker_desired_count < 1:
            raise ReleaseContractError("a successful release must run both services")
        if not self.rollback_eligible:
            raise ReleaseContractError("only a fully successful release may be recorded")

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n")

    @classmethod
    def read(cls, path: Path) -> ReleaseRecord:
        try:
            payload: dict[str, Any] = json.loads(path.read_text())
            return cls(**payload)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ReleaseContractError(f"invalid release record {path}: {error}") from error


def validate_prior_pair(
    web: ServiceSnapshot,
    worker: ServiceSnapshot,
    expected: ReleaseRecord | None,
) -> bool:
    """Validate the captured prior state and return whether this is first bootstrap."""
    if web.desired_count == 0 and worker.desired_count == 0:
        if expected is not None:
            raise ReleaseContractError("bootstrap cannot claim an existing rollback release")
        return True
    if web.desired_count < 1 or worker.desired_count < 1:
        raise ReleaseContractError("prior web and worker desired counts are mixed")
    if web.source_sha != worker.source_sha or web.image_digest != worker.image_digest:
        raise ReleaseContractError("prior web and worker release identities are mixed")
    if web.source_sha is None or web.image_digest is None:
        raise ReleaseContractError("prior release identity is missing")
    validate_source_sha(web.source_sha)
    validate_image_digest(web.image_digest)
    if expected is not None:
        comparisons = {
            "source SHA": (web.source_sha, expected.source_sha),
            "image digest": (web.image_digest, expected.image_digest),
            "web task definition": (web.task_definition_arn, expected.web_task_definition_arn),
            "worker task definition": (
                worker.task_definition_arn,
                expected.worker_task_definition_arn,
            ),
            "web desired count": (web.desired_count, expected.web_desired_count),
            "worker desired count": (worker.desired_count, expected.worker_desired_count),
        }
        mismatches = [name for name, values in comparisons.items() if values[0] != values[1]]
        if mismatches:
            raise ReleaseContractError(
                f"active services differ from the last successful release: {', '.join(mismatches)}"
            )
    return False
