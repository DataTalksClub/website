from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from deploy.contracts import (
    RELEASE_MANAGER,
    ReleaseContractError,
    ReleaseIdentity,
    ReleaseRecord,
    ServiceSnapshot,
    validate_prior_pair,
)
from deploy.task_definitions import TaskDefinitionConfig, build_task_definitions


class ReleaseGateway(Protocol):
    def capture_service(self, workload: str) -> ServiceSnapshot: ...

    def source_task_definition(self, workload: str) -> dict[str, Any]: ...

    def verify_release_record(self, record: ReleaseRecord, identity: ReleaseIdentity) -> None: ...

    def register_task_definition(
        self, workload: str, task_definition: dict[str, Any], tags: dict[str, str]
    ) -> str: ...

    def run_migration(self, task_definition_arn: str) -> None: ...

    def update_service(
        self, workload: str, task_definition_arn: str, desired_count: int
    ) -> None: ...

    def wait_service_stable(self, workload: str, *, worker_singleton: bool = False) -> None: ...

    def verify_public_web(self, source_sha: str) -> None: ...

    def run_deployed_smoke(self, source_sha: str) -> None: ...

    def verify_terminal(
        self,
        expected_task_definitions: dict[str, str],
        expected_desired_counts: dict[str, int],
        expected_identity: ReleaseIdentity | None,
    ) -> None: ...


@dataclass(frozen=True)
class PromotionConfig:
    identity: ReleaseIdentity
    task_definitions: TaskDefinitionConfig
    web_desired_count: int
    worker_desired_count: int
    project_tag: str
    environment_tag: str
    release_record_path: Path
    expected_prior_release: ReleaseRecord | None = None

    def __post_init__(self) -> None:
        if self.web_desired_count < 1 or self.worker_desired_count != 1:
            raise ReleaseContractError("sandbox promotion requires web >= 1 and worker exactly 1")
        if not self.project_tag or not self.environment_tag:
            raise ReleaseContractError("Project and Environment registration tags are required")


class CompensationError(ReleaseContractError):
    """Raised when a release fails and exact-state compensation also fails."""


def _compensate(
    gateway: ReleaseGateway,
    web: ServiceSnapshot,
    worker: ServiceSnapshot,
    prior_identity: ReleaseIdentity | None,
) -> None:
    recovery = (
        f"web={web.task_definition_arn} desired={web.desired_count}; "
        f"worker={worker.task_definition_arn} desired={worker.desired_count}"
    )
    errors: list[str] = []
    for workload, snapshot in (("web", web), ("worker", worker)):
        try:
            gateway.update_service(workload, snapshot.task_definition_arn, snapshot.desired_count)
        except Exception as error:
            errors.append(f"update {workload}: {error}")
    for workload in ("web", "worker"):
        try:
            gateway.wait_service_stable(workload, worker_singleton=workload == "worker")
        except Exception as error:
            errors.append(f"wait {workload}: {error}")
    try:
        gateway.verify_terminal(
            {"web": web.task_definition_arn, "worker": worker.task_definition_arn},
            {"web": web.desired_count, "worker": worker.desired_count},
            prior_identity,
        )
    except Exception as error:
        errors.append(f"terminal verification: {error}")
    if prior_identity is not None:
        try:
            gateway.verify_public_web(prior_identity.source_sha)
        except Exception as error:
            errors.append(f"public health: {error}")
    if errors:
        raise CompensationError(
            "automatic compensation failed; recover with exact non-secret identifiers: "
            f"{recovery}; "
            f"causes: {'; '.join(errors)}"
        )


def promote(gateway: ReleaseGateway, config: PromotionConfig) -> ReleaseRecord:
    prior_web = gateway.capture_service("web")
    prior_worker = gateway.capture_service("worker")
    bootstrap = validate_prior_pair(
        prior_web,
        prior_worker,
        config.expected_prior_release,
    )
    if not bootstrap and config.expected_prior_release is None:
        raise ReleaseContractError(
            "a non-bootstrap promotion requires the last successful release record"
        )

    prior_identity = None
    if not bootstrap:
        assert prior_web.source_sha is not None
        assert prior_web.image_digest is not None
        prior_identity = ReleaseIdentity(
            source_sha=prior_web.source_sha,
            image_digest=prior_web.image_digest,
            repository_uri=config.identity.repository_uri,
        )
        assert config.expected_prior_release is not None
        gateway.verify_release_record(config.expected_prior_release, prior_identity)

    source_tasks = {
        workload: gateway.source_task_definition(workload)
        for workload in ("web", "worker", "migration")
    }
    task_documents = build_task_definitions(
        source_tasks,
        config.identity,
        config.task_definitions,
    )
    tags = {
        "ReleaseManager": RELEASE_MANAGER,
        "Project": config.project_tag,
        "Environment": config.environment_tag,
    }
    registered = {
        workload: gateway.register_task_definition(workload, task_documents[workload], tags)
        for workload in ("web", "worker", "migration")
    }

    gateway.run_migration(registered["migration"])
    mutated = False
    try:
        gateway.update_service("web", registered["web"], config.web_desired_count)
        mutated = True
        gateway.wait_service_stable("web")
        gateway.verify_public_web(config.identity.source_sha)

        gateway.update_service("worker", registered["worker"], config.worker_desired_count)
        gateway.wait_service_stable("worker", worker_singleton=True)
        gateway.run_deployed_smoke(config.identity.source_sha)
        gateway.verify_terminal(
            {"web": registered["web"], "worker": registered["worker"]},
            {
                "web": config.web_desired_count,
                "worker": config.worker_desired_count,
            },
            config.identity,
        )
    except Exception:
        if mutated:
            _compensate(gateway, prior_web, prior_worker, prior_identity)
        raise

    record = ReleaseRecord(
        source_sha=config.identity.source_sha,
        image_digest=config.identity.image_digest,
        web_task_definition_arn=registered["web"],
        worker_task_definition_arn=registered["worker"],
        migration_task_definition_arn=registered["migration"],
        web_desired_count=config.web_desired_count,
        worker_desired_count=config.worker_desired_count,
        rollback_eligible=True,
    )
    record.write(config.release_record_path)
    return record


def rollback(
    gateway: ReleaseGateway,
    target: ReleaseRecord,
    current: ReleaseRecord,
    repository_uri: str,
    release_record_path: Path,
) -> ReleaseRecord:
    current_web = gateway.capture_service("web")
    current_worker = gateway.capture_service("worker")
    validate_prior_pair(current_web, current_worker, current)
    current_identity = ReleaseIdentity(
        source_sha=current.source_sha,
        image_digest=current.image_digest,
        repository_uri=repository_uri,
    )
    target_identity = ReleaseIdentity(
        source_sha=target.source_sha,
        image_digest=target.image_digest,
        repository_uri=repository_uri,
    )
    gateway.verify_release_record(current, current_identity)
    gateway.verify_release_record(target, target_identity)
    mutated = False
    try:
        gateway.update_service("web", target.web_task_definition_arn, target.web_desired_count)
        mutated = True
        gateway.wait_service_stable("web")
        gateway.verify_public_web(target.source_sha)
        gateway.update_service(
            "worker", target.worker_task_definition_arn, target.worker_desired_count
        )
        gateway.wait_service_stable("worker", worker_singleton=True)
        gateway.run_deployed_smoke(target.source_sha)
        gateway.verify_terminal(
            {
                "web": target.web_task_definition_arn,
                "worker": target.worker_task_definition_arn,
            },
            {
                "web": target.web_desired_count,
                "worker": target.worker_desired_count,
            },
            target_identity,
        )
    except Exception:
        if mutated:
            _compensate(gateway, current_web, current_worker, current_identity)
        raise

    target.write(release_record_path)
    return target
