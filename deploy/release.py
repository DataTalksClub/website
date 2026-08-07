from __future__ import annotations

import datetime
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from deploy.contracts import (
    RELEASE_MANAGER,
    ActiveServicePair,
    ReleaseContractError,
    ReleaseIdentity,
    ReleaseRecord,
    ServiceSnapshot,
    validate_image_digest,
    validate_prior_pair,
    validate_source_sha,
    validate_task_definition_arn,
)
from deploy.task_definitions import TaskDefinitionConfig, build_task_definitions

FAILURE_INJECTIONS = {"none", "migration", "post_mutation_smoke"}
RELEASE_A_SHA = "0f0ae208526fa2e76848cf4f5a87bd4aa26687ec"
RELEASE_B_SHA = "e2b93beb1544170b6177ba55ea8fd6530b2e57a3"
SANDBOX_REPOSITORY_URI = "817685572750.dkr.ecr.eu-west-1.amazonaws.com/website-sandbox"


def _record_evidence(
    path: Path | None,
    stage: str,
    result: str,
    proof: dict[str, Any] | None = None,
) -> None:
    if path is None:
        return
    payload: dict[str, Any] = {"stages": []}
    if path.exists():
        payload = json.loads(path.read_text())
    payload["stages"].append(
        {
            "stage": stage,
            "result": result,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            "proof": proof or {},
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".new")
    temporary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary_path.replace(path)


def _record_failed_stage(path: Path | None, stage: str, error: Exception) -> None:
    try:
        _record_evidence(path, stage, "failed", {"error_class": type(error).__name__})
    except Exception:
        # Evidence must never mask the release error or prevent exact-pair recovery.
        pass


def _record_recovery_evidence(
    path: Path | None,
    stage: str,
    result: str,
    proof: dict[str, Any] | None = None,
) -> None:
    try:
        _record_evidence(path, stage, result, proof)
    except Exception:
        # Recovery must never depend on an auxiliary evidence write.
        pass


class ReleaseGateway(Protocol):
    def capture_service(self, workload: str) -> ServiceSnapshot: ...

    def source_task_definition(self, workload: str) -> dict[str, Any]: ...

    def verify_release_record(self, record: ReleaseRecord, identity: ReleaseIdentity) -> None: ...

    def verify_active_service_pair(
        self, pair: ActiveServicePair, identity: ReleaseIdentity
    ) -> None: ...

    def verify_image_digest_exists(
        self, repository_uri: str, source_sha: str, image_digest: str
    ) -> None: ...

    def register_task_definition(
        self, workload: str, task_definition: dict[str, Any], tags: dict[str, str]
    ) -> str: ...

    def run_migration(
        self, task_definition_arn: str, *, inject_controlled_failure: bool = False
    ) -> None: ...

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
    expected_prior_release: ReleaseRecord | ActiveServicePair | None = None
    failure_injection: str = "none"
    evidence_path: Path | None = None
    recovery_context_path: Path | None = None

    def __post_init__(self) -> None:
        if (
            type(self.web_desired_count) is not int
            or type(self.worker_desired_count) is not int
            or self.web_desired_count != 1
            or self.worker_desired_count != 1
        ):
            raise ReleaseContractError("sandbox promotion requires web and worker exactly 1")
        if not self.project_tag or not self.environment_tag:
            raise ReleaseContractError("Project and Environment registration tags are required")
        if self.failure_injection not in FAILURE_INJECTIONS:
            raise ReleaseContractError("unsupported release failure injection")
        if self.failure_injection != "none" and self.expected_prior_release is None:
            raise ReleaseContractError(
                "controlled failure injection requires an existing prior release"
            )
        if self.failure_injection != "none":
            if self.identity.source_sha != RELEASE_B_SHA:
                raise ReleaseContractError(
                    "controlled failure injection requires exact accepted release B"
                )
            assert self.expected_prior_release is not None
            if self.expected_prior_release.source_sha != RELEASE_A_SHA:
                raise ReleaseContractError(
                    "controlled failure injection requires exact accepted release A as prior"
                )


class CompensationError(ReleaseContractError):
    """Raised when a release fails and exact-state compensation also fails."""


@dataclass(frozen=True)
class RecoveryContext:
    repository_uri: str
    source_sha: str | None
    image_digest: str | None
    web_task_definition_arn: str
    worker_task_definition_arn: str
    web_desired_count: int
    worker_desired_count: int

    def __post_init__(self) -> None:
        if self.repository_uri != SANDBOX_REPOSITORY_URI:
            raise ReleaseContractError("recovery context repository is not exact sandbox ECR")
        validate_task_definition_arn(self.web_task_definition_arn)
        validate_task_definition_arn(self.worker_task_definition_arn)
        if not self.web_task_definition_arn.startswith(
            "arn:aws:ecs:eu-west-1:817685572750:task-definition/website-sandbox-web:"
        ):
            raise ReleaseContractError("recovery context web task family differs")
        if not self.worker_task_definition_arn.startswith(
            "arn:aws:ecs:eu-west-1:817685572750:task-definition/website-sandbox-worker:"
        ):
            raise ReleaseContractError("recovery context worker task family differs")
        counts = (self.web_desired_count, self.worker_desired_count)
        if (
            type(self.web_desired_count) is not int
            or type(self.worker_desired_count) is not int
            or counts not in {(0, 0), (1, 1)}
        ):
            raise ReleaseContractError("recovery context contains mixed service counts")
        if counts == (0, 0):
            if self.source_sha is not None or self.image_digest is not None:
                raise ReleaseContractError(
                    "bootstrap recovery context must have no release identity"
                )
        else:
            if self.source_sha is None or self.image_digest is None:
                raise ReleaseContractError("enabled recovery context requires a release identity")
            validate_source_sha(self.source_sha)
            validate_image_digest(self.image_digest)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(path.suffix + ".new")
        temporary_path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n")
        temporary_path.replace(path)

    @classmethod
    def read(cls, path: Path) -> RecoveryContext:
        expected_keys = {
            "repository_uri",
            "source_sha",
            "image_digest",
            "web_task_definition_arn",
            "worker_task_definition_arn",
            "web_desired_count",
            "worker_desired_count",
        }
        try:
            payload = json.loads(path.read_text())
            if not isinstance(payload, dict) or set(payload) != expected_keys:
                raise ReleaseContractError("recovery context fields differ")
            return cls(**payload)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ReleaseContractError("invalid recovery context") from error


def _write_recovery_context(
    path: Path | None,
    repository_uri: str,
    web: ServiceSnapshot,
    worker: ServiceSnapshot,
) -> None:
    if path is None:
        return
    context = RecoveryContext(
        repository_uri=repository_uri,
        source_sha=web.source_sha if web.desired_count > 0 else None,
        image_digest=web.image_digest if web.desired_count > 0 else None,
        web_task_definition_arn=web.task_definition_arn,
        worker_task_definition_arn=worker.task_definition_arn,
        web_desired_count=web.desired_count,
        worker_desired_count=worker.desired_count,
    )
    context.write(path)


def capture_current_service_pair(
    gateway: ReleaseGateway,
    repository_uri: str,
    pair_path: Path,
    *,
    expected_web_count: int,
    expected_worker_count: int,
) -> ActiveServicePair:
    """Synthesize a prior record only from one stable, normalized managed release."""
    web = gateway.capture_service("web")
    worker = gateway.capture_service("worker")
    for workload, snapshot in (("web", web), ("worker", worker)):
        if snapshot.desired_count < 1:
            raise ReleaseContractError(
                "automatic deployment cannot capture bootstrap-disabled services"
            )
        if snapshot.running_count != snapshot.desired_count or snapshot.pending_count != 0:
            raise ReleaseContractError(
                f"automatic deployment requires stable {workload} running/pending counts"
            )
    if worker.desired_count != 1:
        raise ReleaseContractError("automatic deployment requires exactly one worker")
    if web.desired_count != expected_web_count or worker.desired_count != expected_worker_count:
        raise ReleaseContractError("automatic deployment service counts differ from configuration")
    if (
        web.source_sha is None
        or web.image_digest is None
        or web.source_sha != worker.source_sha
        or web.image_digest != worker.image_digest
    ):
        raise ReleaseContractError("automatic deployment found mixed or missing release identity")

    identity = ReleaseIdentity(
        source_sha=web.source_sha,
        image_digest=web.image_digest,
        repository_uri=repository_uri,
    )
    pair = ActiveServicePair(
        source_sha=identity.source_sha,
        image_digest=identity.image_digest,
        web_task_definition_arn=web.task_definition_arn,
        worker_task_definition_arn=worker.task_definition_arn,
        web_desired_count=web.desired_count,
        worker_desired_count=worker.desired_count,
    )
    gateway.verify_active_service_pair(pair, identity)
    gateway.verify_image_digest_exists(
        repository_uri,
        identity.source_sha,
        identity.image_digest,
    )
    gateway.verify_terminal(
        {"web": web.task_definition_arn, "worker": worker.task_definition_arn},
        {"web": web.desired_count, "worker": worker.desired_count},
        identity,
    )
    pair.write(pair_path)
    return pair


def capture_recovery_context(
    gateway: ReleaseGateway,
    repository_uri: str,
    path: Path,
    expected: ReleaseRecord | ActiveServicePair | None,
) -> RecoveryContext:
    """Persist a strict pre-mutation checkpoint for human abrupt-runner recovery."""
    web = gateway.capture_service("web")
    worker = gateway.capture_service("worker")
    bootstrap = validate_prior_pair(web, worker, expected)
    identity: ReleaseIdentity | None = None
    if not bootstrap:
        assert web.source_sha is not None
        assert web.image_digest is not None
        if expected is None:
            raise ReleaseContractError(
                "an enabled recovery checkpoint requires the accepted prior release"
            )
        identity = ReleaseIdentity(web.source_sha, web.image_digest, repository_uri)
        _verify_expected_prior(gateway, expected, identity)
    gateway.verify_terminal(
        {"web": web.task_definition_arn, "worker": worker.task_definition_arn},
        {"web": web.desired_count, "worker": worker.desired_count},
        identity,
    )
    _write_recovery_context(path, repository_uri, web, worker)
    return RecoveryContext.read(path)


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
            errors.append(f"update {workload}: {type(error).__name__}")
    for workload in ("web", "worker"):
        try:
            gateway.wait_service_stable(workload, worker_singleton=workload == "worker")
        except Exception as error:
            errors.append(f"wait {workload}: {type(error).__name__}")
    try:
        gateway.verify_terminal(
            {"web": web.task_definition_arn, "worker": worker.task_definition_arn},
            {"web": web.desired_count, "worker": worker.desired_count},
            prior_identity,
        )
    except Exception as error:
        errors.append(f"terminal verification: {type(error).__name__}")
    if prior_identity is not None:
        try:
            gateway.verify_public_web(prior_identity.source_sha)
        except Exception as error:
            errors.append(f"public health: {type(error).__name__}")
    if errors:
        raise CompensationError(
            "automatic compensation failed; recover with exact non-secret identifiers: "
            f"{recovery}; "
            f"causes: {'; '.join(errors)}"
        )


def _recapture_prior_before_mutation(
    gateway: ReleaseGateway,
    config: PromotionConfig,
    *,
    initial_bootstrap: bool,
    prior_identity: ReleaseIdentity | None,
) -> tuple[ServiceSnapshot, ServiceSnapshot]:
    web = gateway.capture_service("web")
    worker = gateway.capture_service("worker")
    observed_bootstrap = validate_prior_pair(web, worker, config.expected_prior_release)
    if observed_bootstrap != initial_bootstrap:
        raise ReleaseContractError("active release changed before service mutation")
    if not observed_bootstrap:
        assert config.expected_prior_release is not None
        assert prior_identity is not None
        _verify_expected_prior(
            gateway,
            config.expected_prior_release,
            prior_identity,
        )
    gateway.verify_terminal(
        {"web": web.task_definition_arn, "worker": worker.task_definition_arn},
        {"web": web.desired_count, "worker": worker.desired_count},
        prior_identity,
    )
    return web, worker


def _verify_expected_prior(
    gateway: ReleaseGateway,
    expected: ReleaseRecord | ActiveServicePair,
    identity: ReleaseIdentity,
) -> None:
    if isinstance(expected, ActiveServicePair):
        gateway.verify_active_service_pair(expected, identity)
    else:
        gateway.verify_release_record(expected, identity)
    gateway.verify_image_digest_exists(
        identity.repository_uri,
        identity.source_sha,
        identity.image_digest,
    )


def promote(gateway: ReleaseGateway, config: PromotionConfig) -> ReleaseRecord:
    prior_web = gateway.capture_service("web")
    prior_worker = gateway.capture_service("worker")
    bootstrap = validate_prior_pair(
        prior_web,
        prior_worker,
        config.expected_prior_release,
    )
    initial_bootstrap = bootstrap
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
        _verify_expected_prior(gateway, config.expected_prior_release, prior_identity)
    gateway.verify_terminal(
        {
            "web": prior_web.task_definition_arn,
            "worker": prior_worker.task_definition_arn,
        },
        {"web": prior_web.desired_count, "worker": prior_worker.desired_count},
        prior_identity,
    )
    _record_evidence(
        config.evidence_path,
        "prior_gate",
        "passed",
        {
            "web_task_definition_arn": prior_web.task_definition_arn,
            "worker_task_definition_arn": prior_worker.task_definition_arn,
            "web_desired_count": prior_web.desired_count,
            "web_running_count": prior_web.running_count,
            "web_pending_count": prior_web.pending_count,
            "worker_desired_count": prior_worker.desired_count,
            "worker_running_count": prior_worker.running_count,
            "worker_pending_count": prior_worker.pending_count,
        },
    )

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
    _record_evidence(
        config.evidence_path,
        "registration",
        "passed",
        {f"{workload}_task_definition_arn": value for workload, value in registered.items()},
    )

    # Catch a raced active release after registration and before any migration side effect.
    prior_web, prior_worker = _recapture_prior_before_mutation(
        gateway,
        config,
        initial_bootstrap=initial_bootstrap,
        prior_identity=prior_identity,
    )
    _record_evidence(
        config.evidence_path,
        "migration",
        "started",
        {"task_definition_arn": registered["migration"]},
    )
    try:
        gateway.run_migration(
            registered["migration"],
            inject_controlled_failure=config.failure_injection == "migration",
        )
    except Exception as error:
        _record_failed_stage(config.evidence_path, "migration", error)
        raise
    if config.failure_injection == "migration":
        migration_error = ReleaseContractError(
            "controlled migration failure unexpectedly returned success"
        )
        _record_failed_stage(config.evidence_path, "migration", migration_error)
        raise migration_error
    _record_evidence(
        config.evidence_path,
        "migration",
        "passed",
        {"task_definition_arn": registered["migration"], "exit_code": 0},
    )

    # Migration can take long enough for an operator to race the active pair. Refresh the
    # compensation snapshots and stop before the first service update if anything changed.
    prior_web, prior_worker = _recapture_prior_before_mutation(
        gateway,
        config,
        initial_bootstrap=initial_bootstrap,
        prior_identity=prior_identity,
    )
    _write_recovery_context(
        config.recovery_context_path,
        config.identity.repository_uri,
        prior_web,
        prior_worker,
    )
    _record_evidence(config.evidence_path, "pre_update_gate", "passed")
    mutated = False
    active_stage = "web"
    try:
        _record_evidence(config.evidence_path, "web", "started")
        mutated = True
        gateway.update_service("web", registered["web"], config.web_desired_count)
        gateway.wait_service_stable("web")
        gateway.verify_public_web(config.identity.source_sha)
        _record_evidence(
            config.evidence_path,
            "web",
            "passed",
            {
                "task_definition_arn": registered["web"],
                "desired_count": config.web_desired_count,
                "source_sha": config.identity.source_sha,
                "ecs_stable": True,
                "alb_ready": True,
                "readiness": True,
                "liveness": True,
            },
        )

        active_stage = "worker"
        _record_evidence(config.evidence_path, "worker", "started")
        gateway.update_service("worker", registered["worker"], config.worker_desired_count)
        gateway.wait_service_stable("worker", worker_singleton=True)
        _record_evidence(
            config.evidence_path,
            "worker",
            "passed",
            {
                "task_definition_arn": registered["worker"],
                "desired_count": config.worker_desired_count,
                "singleton": True,
            },
        )
        active_stage = "smoke"
        _record_evidence(config.evidence_path, "smoke", "started")
        if config.failure_injection == "post_mutation_smoke":
            raise ReleaseContractError("controlled post-mutation smoke failure")
        gateway.run_deployed_smoke(config.identity.source_sha)
        _record_evidence(
            config.evidence_path,
            "smoke",
            "passed",
            {"source_sha": config.identity.source_sha, "mode": "read_only"},
        )
        active_stage = "terminal"
        _record_evidence(config.evidence_path, "terminal", "started")
        gateway.verify_terminal(
            {"web": registered["web"], "worker": registered["worker"]},
            {
                "web": config.web_desired_count,
                "worker": config.worker_desired_count,
            },
            config.identity,
        )
        _record_evidence(
            config.evidence_path,
            "terminal",
            "passed",
            {
                "web_task_definition_arn": registered["web"],
                "worker_task_definition_arn": registered["worker"],
                "web_desired_count": config.web_desired_count,
                "web_running_count": config.web_desired_count,
                "web_pending_count": 0,
                "worker_desired_count": config.worker_desired_count,
                "worker_running_count": config.worker_desired_count,
                "worker_pending_count": 0,
                "worker_singleton": True,
                "source_sha": config.identity.source_sha,
                "image_digest": config.identity.image_digest,
            },
        )
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
        active_stage = "release_record"
        record.write(config.release_record_path)
        _record_evidence(
            config.evidence_path,
            "release_record",
            "passed",
            {"rollback_eligible": True, "source_sha": config.identity.source_sha},
        )
    except Exception as error:
        _record_failed_stage(config.evidence_path, active_stage, error)
        record_cleanup_error: Exception | None = None
        if active_stage == "release_record":
            try:
                config.release_record_path.unlink(missing_ok=True)
            except Exception as cleanup_error:
                record_cleanup_error = cleanup_error
        if mutated:
            try:
                _record_recovery_evidence(
                    config.evidence_path,
                    "compensation",
                    "started",
                )
                _compensate(gateway, prior_web, prior_worker, prior_identity)
                _record_recovery_evidence(
                    config.evidence_path,
                    "compensation",
                    "passed",
                    {
                        "web_task_definition_arn": prior_web.task_definition_arn,
                        "worker_task_definition_arn": prior_worker.task_definition_arn,
                        "web_desired_count": prior_web.desired_count,
                        "worker_desired_count": prior_worker.desired_count,
                        "source_sha": (
                            prior_identity.source_sha if prior_identity is not None else None
                        ),
                        "image_digest": (
                            prior_identity.image_digest if prior_identity is not None else None
                        ),
                        "terminal": True,
                        "public_health": prior_identity is not None,
                        "worker_singleton": True,
                    },
                )
            except Exception as compensation_error:
                _record_failed_stage(
                    config.evidence_path,
                    "compensation",
                    compensation_error,
                )
                raise
        if record_cleanup_error is not None:
            raise CompensationError(
                "release record cleanup failed after exact-pair recovery "
                f"({type(record_cleanup_error).__name__})"
            ) from record_cleanup_error
        raise

    return record


def rollback(
    gateway: ReleaseGateway,
    target: ReleaseRecord,
    current: ReleaseRecord,
    repository_uri: str,
    release_record_path: Path,
    evidence_path: Path | None = None,
    recovery_context_path: Path | None = None,
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
    gateway.verify_image_digest_exists(
        repository_uri,
        current_identity.source_sha,
        current_identity.image_digest,
    )
    gateway.verify_image_digest_exists(
        repository_uri,
        target_identity.source_sha,
        target_identity.image_digest,
    )
    current_web = gateway.capture_service("web")
    current_worker = gateway.capture_service("worker")
    validate_prior_pair(current_web, current_worker, current)
    gateway.verify_terminal(
        {
            "web": current_web.task_definition_arn,
            "worker": current_worker.task_definition_arn,
        },
        {
            "web": current_web.desired_count,
            "worker": current_worker.desired_count,
        },
        current_identity,
    )
    _write_recovery_context(
        recovery_context_path,
        repository_uri,
        current_web,
        current_worker,
    )
    _record_evidence(
        evidence_path,
        "prior_gate",
        "passed",
        {
            "web_task_definition_arn": current_web.task_definition_arn,
            "worker_task_definition_arn": current_worker.task_definition_arn,
            "web_desired_count": current_web.desired_count,
            "web_running_count": current_web.running_count,
            "web_pending_count": current_web.pending_count,
            "worker_desired_count": current_worker.desired_count,
            "worker_running_count": current_worker.running_count,
            "worker_pending_count": current_worker.pending_count,
        },
    )
    _record_evidence(evidence_path, "migration", "skipped", {"operation": "rollback"})
    mutated = False
    active_stage = "web"
    try:
        _record_evidence(evidence_path, "web", "started")
        mutated = True
        gateway.update_service("web", target.web_task_definition_arn, target.web_desired_count)
        gateway.wait_service_stable("web")
        gateway.verify_public_web(target.source_sha)
        _record_evidence(
            evidence_path,
            "web",
            "passed",
            {
                "task_definition_arn": target.web_task_definition_arn,
                "desired_count": target.web_desired_count,
                "source_sha": target.source_sha,
                "ecs_stable": True,
                "alb_ready": True,
                "readiness": True,
                "liveness": True,
            },
        )
        active_stage = "worker"
        _record_evidence(evidence_path, "worker", "started")
        gateway.update_service(
            "worker", target.worker_task_definition_arn, target.worker_desired_count
        )
        gateway.wait_service_stable("worker", worker_singleton=True)
        _record_evidence(
            evidence_path,
            "worker",
            "passed",
            {
                "task_definition_arn": target.worker_task_definition_arn,
                "desired_count": target.worker_desired_count,
                "singleton": True,
            },
        )
        active_stage = "smoke"
        _record_evidence(evidence_path, "smoke", "started")
        gateway.run_deployed_smoke(target.source_sha)
        _record_evidence(
            evidence_path,
            "smoke",
            "passed",
            {"source_sha": target.source_sha, "mode": "read_only"},
        )
        active_stage = "terminal"
        _record_evidence(evidence_path, "terminal", "started")
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
        _record_evidence(
            evidence_path,
            "terminal",
            "passed",
            {
                "web_task_definition_arn": target.web_task_definition_arn,
                "worker_task_definition_arn": target.worker_task_definition_arn,
                "web_desired_count": target.web_desired_count,
                "web_running_count": target.web_desired_count,
                "web_pending_count": 0,
                "worker_desired_count": target.worker_desired_count,
                "worker_running_count": target.worker_desired_count,
                "worker_pending_count": 0,
                "worker_singleton": True,
                "source_sha": target.source_sha,
                "image_digest": target.image_digest,
            },
        )
        active_stage = "release_record"
        target.write(release_record_path)
        _record_evidence(
            evidence_path,
            "release_record",
            "passed",
            {"rollback_eligible": True, "source_sha": target.source_sha},
        )
    except Exception as error:
        _record_failed_stage(evidence_path, active_stage, error)
        record_cleanup_error: Exception | None = None
        if active_stage == "release_record":
            try:
                release_record_path.unlink(missing_ok=True)
            except Exception as cleanup_error:
                record_cleanup_error = cleanup_error
        if mutated:
            try:
                _record_recovery_evidence(evidence_path, "compensation", "started")
                _compensate(gateway, current_web, current_worker, current_identity)
                _record_recovery_evidence(
                    evidence_path,
                    "compensation",
                    "passed",
                    {
                        "web_task_definition_arn": current_web.task_definition_arn,
                        "worker_task_definition_arn": current_worker.task_definition_arn,
                        "web_desired_count": current_web.desired_count,
                        "worker_desired_count": current_worker.desired_count,
                        "source_sha": current_identity.source_sha,
                        "image_digest": current_identity.image_digest,
                        "terminal": True,
                        "public_health": True,
                        "worker_singleton": True,
                    },
                )
            except Exception as compensation_error:
                _record_failed_stage(evidence_path, "compensation", compensation_error)
                raise
        if record_cleanup_error is not None:
            raise CompensationError(
                "release record cleanup failed after exact-pair recovery "
                f"({type(record_cleanup_error).__name__})"
            ) from record_cleanup_error
        raise

    return target


def restore_after_finalization_failure(
    gateway: ReleaseGateway,
    context: RecoveryContext,
    failed_release: ReleaseRecord,
) -> None:
    """Restore the exact pre-release pair after a detectable artifact finalization failure."""
    failed_identity = ReleaseIdentity(
        source_sha=failed_release.source_sha,
        image_digest=failed_release.image_digest,
        repository_uri=context.repository_uri,
    )
    current_web = gateway.capture_service("web")
    current_worker = gateway.capture_service("worker")
    validate_prior_pair(current_web, current_worker, failed_release)
    gateway.verify_release_record(failed_release, failed_identity)
    gateway.verify_terminal(
        {
            "web": current_web.task_definition_arn,
            "worker": current_worker.task_definition_arn,
        },
        {
            "web": current_web.desired_count,
            "worker": current_worker.desired_count,
        },
        failed_identity,
    )

    prior_identity: ReleaseIdentity | None = None
    if context.source_sha is not None and context.image_digest is not None:
        prior_identity = ReleaseIdentity(
            source_sha=context.source_sha,
            image_digest=context.image_digest,
            repository_uri=context.repository_uri,
        )
        pair = ActiveServicePair(
            source_sha=context.source_sha,
            image_digest=context.image_digest,
            web_task_definition_arn=context.web_task_definition_arn,
            worker_task_definition_arn=context.worker_task_definition_arn,
            web_desired_count=context.web_desired_count,
            worker_desired_count=context.worker_desired_count,
        )
        gateway.verify_active_service_pair(pair, prior_identity)
        gateway.verify_image_digest_exists(
            context.repository_uri,
            prior_identity.source_sha,
            prior_identity.image_digest,
        )

    prior_web = ServiceSnapshot(
        service_name=current_web.service_name,
        task_definition_arn=context.web_task_definition_arn,
        desired_count=context.web_desired_count,
        running_count=context.web_desired_count,
        pending_count=0,
        source_sha=context.source_sha,
        image_digest=context.image_digest,
    )
    prior_worker = ServiceSnapshot(
        service_name=current_worker.service_name,
        task_definition_arn=context.worker_task_definition_arn,
        desired_count=context.worker_desired_count,
        running_count=context.worker_desired_count,
        pending_count=0,
        source_sha=context.source_sha,
        image_digest=context.image_digest,
    )
    _compensate(gateway, prior_web, prior_worker, prior_identity)
