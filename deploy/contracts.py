from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

WEB_RECOVERY_TIMEOUT_SECONDS = 240
MAX_WEB_RECOVERY_TIMEOUT_SECONDS = 240
WORKER_RECOVERY_TIMEOUT_SECONDS = 420
MAX_WORKER_RECOVERY_TIMEOUT_SECONDS = 420
RECOVERY_PHASE_TIMEOUT_SECONDS = 720
MAX_RECOVERY_PHASE_TIMEOUT_SECONDS = 720

SOURCE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
TASK_DEFINITION_ARN_PATTERN = re.compile(
    r"^arn:aws:ecs:[a-z0-9-]+:[0-9]{12}:task-definition/[A-Za-z0-9_-]+:[1-9][0-9]*$"
)
PLACEHOLDER_DIGEST = f"sha256:{'1' * 64}"
RELEASE_MANAGER = "DataTalksClub/website"


RELEASE_FAILURE_REASON_CODES = {
    "contract_contradiction",
    "receipt_deadline_expired",
}
ReleaseFailureReason = Literal[
    "contract_contradiction",
    "receipt_deadline_expired",
]
SERVICE_RECEIPT_BINDING_REASONS = {
    "complete_receipt",
    "zero_count_initialization",
    "partial_acknowledgement_reconciled",
    "partial_acknowledgement_zero_count_initialization",
}


class ReleaseContractError(RuntimeError):
    """Raised when a release input or observed runtime state is unsafe."""

    def __init__(
        self,
        message: str,
        *,
        reason_code: ReleaseFailureReason = "contract_contradiction",
    ) -> None:
        if reason_code not in RELEASE_FAILURE_REASON_CODES:
            raise ValueError("release failure reason code is not allowlisted")
        super().__init__(message)
        self.reason_code = reason_code


def _validate_count(value: object, *, context: str) -> int:
    if type(value) is not int or value < 0:
        raise ReleaseContractError(f"{context} must be a nonnegative integer")
    return value


def _validate_deployment_id(value: object, *, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseContractError(f"{context} must be a non-empty string")
    return value


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
    primary_deployment_id: str

    def __post_init__(self) -> None:
        validate_task_definition_arn(self.task_definition_arn)
        for field, value in (
            ("desired count", self.desired_count),
            ("running count", self.running_count),
            ("pending count", self.pending_count),
        ):
            _validate_count(value, context=f"ECS service {field}")
        _validate_deployment_id(
            self.primary_deployment_id,
            context="ECS PRIMARY deployment ID",
        )


@dataclass(frozen=True)
class ServiceTarget:
    task_definition_arn: str
    desired_count: int

    def __post_init__(self) -> None:
        validate_task_definition_arn(self.task_definition_arn)
        _validate_count(self.desired_count, context="service target desired count")


@dataclass(frozen=True)
class ServicePredecessor:
    target: ServiceTarget
    primary_deployment_id: str
    role: Literal["terminal", "attempted"]

    def __post_init__(self) -> None:
        if type(self.target) is not ServiceTarget:
            raise ReleaseContractError("service predecessor target differs")
        _validate_deployment_id(
            self.primary_deployment_id,
            context="predecessor PRIMARY deployment ID",
        )
        if self.role not in {"terminal", "attempted"}:
            raise ReleaseContractError("service predecessor role differs")


@dataclass(frozen=True)
class ServiceUpdateReceipt:
    workload: str
    configured_service_identity: str
    target: ServiceTarget
    primary_deployment_id: str
    predecessors: tuple[ServicePredecessor, ...]
    binding_reason: Literal[
        "complete_receipt",
        "zero_count_initialization",
        "partial_acknowledgement_reconciled",
        "partial_acknowledgement_zero_count_initialization",
    ] = "complete_receipt"
    terminal_observed: bool = False

    def __post_init__(self) -> None:
        if self.workload not in {"web", "worker"}:
            raise ReleaseContractError("service update receipt workload differs")
        if (
            not isinstance(self.configured_service_identity, str)
            or not self.configured_service_identity.strip()
        ):
            raise ReleaseContractError("service update receipt identity is empty")
        if type(self.target) is not ServiceTarget:
            raise ReleaseContractError("service update receipt target differs")
        if type(self.predecessors) is not tuple or any(
            type(item) is not ServicePredecessor for item in self.predecessors
        ):
            raise ReleaseContractError("service update receipt predecessors differ")
        terminal_count = sum(item.role == "terminal" for item in self.predecessors)
        attempted_count = sum(item.role == "attempted" for item in self.predecessors)
        if terminal_count != 1 or attempted_count > 1:
            raise ReleaseContractError("service update receipt predecessor phase differs")
        _validate_deployment_id(
            self.primary_deployment_id,
            context="receipt PRIMARY deployment ID",
        )
        predecessor_ids = [item.primary_deployment_id for item in self.predecessors]
        if len(predecessor_ids) != len(set(predecessor_ids)):
            raise ReleaseContractError("service update receipt has duplicate predecessor IDs")
        if self.primary_deployment_id in predecessor_ids:
            raise ReleaseContractError("service update receipt reused a predecessor ID")
        if self.binding_reason not in SERVICE_RECEIPT_BINDING_REASONS:
            raise ReleaseContractError("service update receipt binding reason differs")
        if type(self.terminal_observed) is not bool:
            raise ReleaseContractError("service update receipt terminal observation differs")
        if self.terminal_observed and self.binding_reason != "partial_acknowledgement_reconciled":
            raise ReleaseContractError("service update receipt terminal attribution differs")


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
        if (
            type(self.web_desired_count) is not int
            or type(self.worker_desired_count) is not int
            or self.web_desired_count != 1
            or self.worker_desired_count != 1
        ):
            raise ReleaseContractError(
                "a successful development release requires web and worker exactly 1"
            )
        if self.rollback_eligible is not True:
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


@dataclass(frozen=True)
class ActiveServicePair:
    """A read-only auto-deploy prior without an unnecessary migration identifier."""

    source_sha: str
    image_digest: str
    web_task_definition_arn: str
    worker_task_definition_arn: str
    web_desired_count: int
    worker_desired_count: int

    def __post_init__(self) -> None:
        validate_source_sha(self.source_sha)
        validate_image_digest(self.image_digest)
        validate_task_definition_arn(self.web_task_definition_arn)
        validate_task_definition_arn(self.worker_task_definition_arn)
        if (
            type(self.web_desired_count) is not int
            or type(self.worker_desired_count) is not int
            or self.web_desired_count != 1
            or self.worker_desired_count != 1
        ):
            raise ReleaseContractError(
                "an active development service pair requires web and worker exactly 1"
            )

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n")

    @classmethod
    def read(cls, path: Path) -> ActiveServicePair:
        try:
            payload: dict[str, Any] = json.loads(path.read_text())
            return cls(**payload)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ReleaseContractError(f"invalid active service pair {path}: {error}") from error


def validate_prior_pair(
    web: ServiceSnapshot,
    worker: ServiceSnapshot,
    expected: ReleaseRecord | ActiveServicePair | None,
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
