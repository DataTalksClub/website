from __future__ import annotations

import datetime
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from deploy.contracts import (
    RECOVERY_PHASE_TIMEOUT_SECONDS,
    RELEASE_MANAGER,
    SERVICE_RECEIPT_BINDING_REASONS,
    WEB_RECOVERY_TIMEOUT_SECONDS,
    WORKER_RECOVERY_TIMEOUT_SECONDS,
    ActiveServicePair,
    ReleaseContractError,
    ReleaseFailureReason,
    ReleaseIdentity,
    ReleaseRecord,
    ServicePredecessor,
    ServiceSnapshot,
    ServiceTarget,
    ServiceUpdateReceipt,
    WebRuntimeBinding,
    validate_image_digest,
    validate_prior_pair,
    validate_source_sha,
    validate_task_definition_arn,
    validate_version,
)
from deploy.legacy_development_compatibility import (
    ECR_REPOSITORY_URI as DEVELOPMENT_REPOSITORY_URI,
)
from deploy.legacy_development_compatibility import (
    WEB_TASK_FAMILY,
    WORKER_TASK_FAMILY,
    task_definition_arn_prefix,
)
from deploy.task_definitions import TaskDefinitionConfig, build_task_definitions

FAILURE_INJECTIONS = {"none", "migration", "post_mutation_smoke"}
RELEASE_A_SHA = "0f0ae208526fa2e76848cf4f5a87bd4aa26687ec"
RELEASE_B_SHA = "e2b93beb1544170b6177ba55ea8fd6530b2e57a3"


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


def _identity_evidence(identity: ReleaseIdentity | None) -> dict[str, str | int | None]:
    if identity is None:
        return {
            "identity_schema": None,
            "version": None,
            "source_sha": None,
            "image_digest": None,
        }
    return {
        "identity_schema": identity.identity_schema,
        "version": identity.version,
        "source_sha": identity.source_sha,
        "image_digest": identity.image_digest,
    }


def _record_failed_stage(path: Path | None, stage: str, error: Exception) -> None:
    try:
        proof = {"error_class": type(error).__name__}
        if isinstance(error, ReleaseContractError):
            proof["reason_code"] = error.reason_code
        _record_evidence(path, stage, "failed", proof)
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

    def verify_image_digest_exists(self, identity: ReleaseIdentity) -> None: ...

    def register_task_definition(
        self, workload: str, task_definition: dict[str, Any], tags: dict[str, str]
    ) -> str: ...

    def run_migration(
        self, task_definition_arn: str, *, inject_controlled_failure: bool = False
    ) -> None: ...

    def update_service(
        self,
        workload: str,
        target: ServiceTarget,
        predecessors: tuple[ServicePredecessor, ...],
        *,
        deadline: float | None = None,
        timeout_seconds: int | None = None,
        web_runtime_binding: WebRuntimeBinding | None = None,
    ) -> ServiceUpdateReceipt: ...

    def capture_attempted_predecessor(
        self,
        workload: str,
        attempted_target: ServiceTarget,
        terminal_predecessor: ServicePredecessor,
        deadline: float,
    ) -> ServicePredecessor: ...

    def service_stabilization_deadline(self, timeout_seconds: int | None = None) -> float: ...

    def web_coherence_deadline(self) -> float: ...

    def recovery_phase_deadline(self) -> float: ...

    def recovery_workload_deadline(self, workload: str, phase_deadline: float) -> float: ...

    def ensure_recovery_phase(self, phase_deadline: float) -> None: ...

    @property
    def web_stabilization_timeout_seconds(self) -> int: ...

    @property
    def web_coherence_timeout_seconds(self) -> int: ...

    @property
    def worker_stabilization_timeout_seconds(self) -> int: ...

    @property
    def web_recovery_timeout_seconds(self) -> int: ...

    @property
    def worker_recovery_timeout_seconds(self) -> int: ...

    @property
    def recovery_phase_timeout_seconds(self) -> int: ...

    def wait_service_stable(
        self,
        receipt: ServiceUpdateReceipt,
        *,
        worker_singleton: bool = False,
        timeout_seconds: int | None = None,
        deadline: float | None = None,
        web_runtime_binding: WebRuntimeBinding | None = None,
    ) -> None: ...

    def establish_web_runtime_binding(
        self,
        receipt: ServiceUpdateReceipt,
        identity: ReleaseIdentity,
        *,
        deadline: float,
    ) -> WebRuntimeBinding: ...

    def revalidate_web_runtime_binding(
        self,
        binding: WebRuntimeBinding,
        *,
        deadline: float,
    ) -> bool: ...

    def observe_recovery_receipt(
        self,
        receipt: ServiceUpdateReceipt,
        *,
        workload_deadline: float,
        phase_deadline: float,
    ) -> bool: ...

    def sleep_recovery_round(
        self,
        workload_deadlines: dict[str, float],
        phase_deadline: float,
    ) -> None: ...

    def verify_public_web(
        self,
        identity: ReleaseIdentity,
        *,
        phase_deadline: float | None = None,
    ) -> None: ...

    def run_deployed_smoke(self, identity: ReleaseIdentity) -> None: ...

    def verify_terminal(
        self,
        expected_task_definitions: dict[str, str],
        expected_desired_counts: dict[str, int],
        expected_identity: ReleaseIdentity | None,
        expected_primary_deployment_ids: dict[str, str] | None = None,
        allowed_predecessors: dict[str, tuple[ServicePredecessor, ...]] | None = None,
        *,
        phase_deadline: float | None = None,
        web_runtime_binding: WebRuntimeBinding | None = None,
        web_runtime_deadline: float | None = None,
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
            raise ReleaseContractError("development promotion requires web and worker exactly 1")
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


@dataclass(frozen=True)
class RestorativeReceiptSummary:
    workload: str
    receipt_id: str
    receipt_binding: str
    carried_terminal: bool

    def __post_init__(self) -> None:
        if self.workload not in {"web", "worker"}:
            raise ReleaseContractError("restorative receipt workload differs")
        if not isinstance(self.receipt_id, str) or not self.receipt_id.strip():
            raise ReleaseContractError("restorative receipt ID differs")
        if self.receipt_binding not in SERVICE_RECEIPT_BINDING_REASONS:
            raise ReleaseContractError("restorative receipt binding reason differs")
        if type(self.carried_terminal) is not bool:
            raise ReleaseContractError("restorative receipt terminal observation differs")

    @classmethod
    def from_receipt(cls, receipt: ServiceUpdateReceipt) -> RestorativeReceiptSummary:
        return cls(
            workload=receipt.workload,
            receipt_id=receipt.primary_deployment_id,
            receipt_binding=receipt.binding_reason,
            carried_terminal=receipt.terminal_observed,
        )

    def as_evidence(self) -> dict[str, object]:
        return asdict(self)


class CompensationError(ReleaseContractError):
    """Raised when a release fails and exact-state compensation also fails."""

    def __init__(
        self,
        message: str,
        *,
        receipt_summaries: tuple[RestorativeReceiptSummary, ...] = (),
        reason_code: ReleaseFailureReason = "contract_contradiction",
    ) -> None:
        self.receipt_summaries = receipt_summaries
        message = f"{message}; reason_code={reason_code}"
        if receipt_summaries:
            safe_receipts = [item.as_evidence() for item in receipt_summaries]
            message = f"{message}; restorative_receipts={json.dumps(safe_receipts, sort_keys=True)}"
        super().__init__(message, reason_code=reason_code)


def _restorative_error_reason(error: Exception) -> ReleaseFailureReason:
    if isinstance(error, ReleaseContractError):
        if error.reason_code == "receipt_deadline_expired":
            return "receipt_deadline_expired"
        if error.reason_code == "contract_contradiction":
            return "contract_contradiction"
    return "contract_contradiction"


def _combined_restorative_reason(
    reasons: list[ReleaseFailureReason],
) -> ReleaseFailureReason:
    if "contract_contradiction" in reasons:
        return "contract_contradiction"
    if "receipt_deadline_expired" in reasons:
        return "receipt_deadline_expired"
    return "contract_contradiction"


@dataclass(frozen=True)
class RecoveryContext:
    repository_uri: str
    source_sha: str | None
    image_digest: str | None
    web_task_definition_arn: str
    worker_task_definition_arn: str
    web_desired_count: int
    worker_desired_count: int
    version: str | None = None
    identity_schema: int = 2

    def __post_init__(self) -> None:
        if self.repository_uri != DEVELOPMENT_REPOSITORY_URI:
            raise ReleaseContractError(
                "recovery context repository is outside the development boundary"
            )
        validate_task_definition_arn(self.web_task_definition_arn)
        validate_task_definition_arn(self.worker_task_definition_arn)
        if not self.web_task_definition_arn.startswith(task_definition_arn_prefix(WEB_TASK_FAMILY)):
            raise ReleaseContractError("recovery context web task family differs")
        if not self.worker_task_definition_arn.startswith(
            task_definition_arn_prefix(WORKER_TASK_FAMILY)
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
            if (
                self.source_sha is not None
                or self.image_digest is not None
                or self.version is not None
            ):
                raise ReleaseContractError(
                    "bootstrap recovery context must have no release identity"
                )
        else:
            if self.source_sha is None or self.image_digest is None or self.version is None:
                raise ReleaseContractError("enabled recovery context requires a release identity")
            validate_source_sha(self.source_sha)
            validate_image_digest(self.image_digest)
            validate_version(self.version, self.source_sha, identity_schema=self.identity_schema)

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
            "version",
            "identity_schema",
        }
        legacy_keys = expected_keys - {"version", "identity_schema"}
        try:
            payload = json.loads(path.read_text())
            if isinstance(payload, dict) and set(payload) == legacy_keys:
                if payload.get("source_sha") is not None:
                    payload["version"] = payload["source_sha"]
                    payload["identity_schema"] = 1
                else:
                    payload["version"] = None
                    payload["identity_schema"] = 2
            elif not isinstance(payload, dict) or set(payload) != expected_keys:
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
        version=web.version if web.desired_count > 0 else None,
        identity_schema=web.identity_schema or 2,
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
        or web.version is None
        or web.identity_schema is None
        or web.version != worker.version
        or web.identity_schema != worker.identity_schema
        or web.source_sha != worker.source_sha
        or web.image_digest != worker.image_digest
    ):
        raise ReleaseContractError("automatic deployment found mixed or missing release identity")

    identity = ReleaseIdentity(
        source_sha=web.source_sha,
        image_digest=web.image_digest,
        repository_uri=repository_uri,
        version=web.version,
        identity_schema=web.identity_schema,
    )
    pair = ActiveServicePair(
        source_sha=identity.source_sha,
        image_digest=identity.image_digest,
        version=identity.version,
        identity_schema=identity.identity_schema,
        web_task_definition_arn=web.task_definition_arn,
        worker_task_definition_arn=worker.task_definition_arn,
        web_desired_count=web.desired_count,
        worker_desired_count=worker.desired_count,
    )
    gateway.verify_active_service_pair(pair, identity)
    gateway.verify_image_digest_exists(identity)
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
        assert web.version is not None and web.identity_schema is not None
        identity = ReleaseIdentity(
            web.source_sha,
            web.image_digest,
            repository_uri,
            web.version,
            web.identity_schema,
        )
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
    restore_targets: dict[str, ServiceTarget],
    terminal_predecessors: dict[str, ServicePredecessor],
    prior_identity: ReleaseIdentity | None,
    attempted: dict[str, ServiceTarget | ServicePredecessor],
    *,
    restore_workloads: frozenset[str] | None = None,
    evidence_path: Path | None = None,
    evidence_stage: str = "compensation_receipt",
) -> tuple[RestorativeReceiptSummary, ...]:
    recovery = (
        f"web={restore_targets['web'].task_definition_arn} "
        f"desired={restore_targets['web'].desired_count}; "
        f"worker={restore_targets['worker'].task_definition_arn} "
        f"desired={restore_targets['worker'].desired_count}"
    )
    workloads = frozenset(attempted) if restore_workloads is None else restore_workloads
    if type(workloads) is not frozenset or not workloads or not workloads <= {"web", "worker"}:
        raise ReleaseContractError("compensation workload allowlist differs")
    if set(restore_targets) != {"web", "worker"} or any(
        type(target) is not ServiceTarget for target in restore_targets.values()
    ):
        raise ReleaseContractError("compensation restore target pair differs")
    if set(terminal_predecessors) != {"web", "worker"} or any(
        type(predecessor) is not ServicePredecessor
        for predecessor in terminal_predecessors.values()
    ):
        raise ReleaseContractError("compensation terminal predecessor pair differs")
    if not set(attempted) <= {"web", "worker"} or any(
        type(state) not in {ServiceTarget, ServicePredecessor} for state in attempted.values()
    ):
        raise ReleaseContractError("compensation attempted workload state differs")
    if any(terminal_predecessors[workload].role != "terminal" for workload in ("web", "worker")):
        raise ReleaseContractError("compensation captured predecessor role differs")
    recovery_budgets = {
        "web": gateway.web_recovery_timeout_seconds,
        "worker": gateway.worker_recovery_timeout_seconds,
        "phase": gateway.recovery_phase_timeout_seconds,
    }
    if any(type(value) is not int for value in recovery_budgets.values()) or recovery_budgets != {
        "web": WEB_RECOVERY_TIMEOUT_SECONDS,
        "worker": WORKER_RECOVERY_TIMEOUT_SECONDS,
        "phase": RECOVERY_PHASE_TIMEOUT_SECONDS,
    }:
        raise ReleaseContractError("compensation recovery budgets differ")

    errors: list[str] = []
    error_reasons: list[ReleaseFailureReason] = []
    workload_outcomes: dict[str, str] = {
        workload: "passed" if workload not in workloads else "pending"
        for workload in ("web", "worker")
    }
    intentionally_untouched = {
        workload: workload not in workloads for workload in ("web", "worker")
    }

    def retain_error(label: str, error: Exception, *, workload: str | None = None) -> None:
        reason = _restorative_error_reason(error)
        errors.append(f"{label}: {type(error).__name__}")
        error_reasons.append(reason)
        if workload is not None:
            workload_outcomes[workload] = reason

    phase_deadline = gateway.recovery_phase_deadline()
    _record_recovery_evidence(
        evidence_path,
        "recovery_plan",
        "accepted",
        {
            "mode": (
                "artifact_finalization"
                if evidence_stage == "finalization_receipt"
                else "automatic_compensation"
            ),
            "web_recovery_seconds": WEB_RECOVERY_TIMEOUT_SECONDS,
            "worker_recovery_seconds": WORKER_RECOVERY_TIMEOUT_SECONDS,
            "phase_recovery_seconds": RECOVERY_PHASE_TIMEOUT_SECONDS,
            "restore_initiation_order": ["web", "worker"],
            "cooperative_observation_order": ["web", "worker"],
            "eligible_workloads": [
                workload for workload in ("web", "worker") if workload in workloads
            ],
            "intentionally_untouched": intentionally_untouched,
            **_identity_evidence(prior_identity),
        },
    )

    expected_primary_deployment_ids = {
        workload: predecessor.primary_deployment_id
        for workload, predecessor in terminal_predecessors.items()
    }
    allowed_terminal_predecessors: dict[str, tuple[ServicePredecessor, ...]] = {
        "web": (),
        "worker": (),
    }
    receipt_summaries: list[RestorativeReceiptSummary] = []
    receipts: dict[str, ServiceUpdateReceipt] = {}
    workload_deadlines: dict[str, float] = {}
    phase_predecessors_by_workload: dict[str, tuple[ServicePredecessor, ...]] = {}

    # Reconcile every ambiguous attempted mutation before restorative updates begin. This keeps
    # the subsequent web -> worker restorative receipts back-to-back even when a lost provider
    # acknowledgement requires bounded observation.
    for workload in ("web", "worker"):
        if workload not in workloads:
            continue
        terminal = terminal_predecessors[workload]
        phase_predecessors = [terminal]
        try:
            deadline = gateway.recovery_workload_deadline(workload, phase_deadline)
            workload_deadlines[workload] = deadline
            attempted_state = attempted.get(workload)
            if type(attempted_state) is ServiceTarget:
                captured = gateway.capture_attempted_predecessor(
                    workload,
                    attempted_state,
                    terminal,
                    deadline,
                )
                phase_predecessors.append(captured)
            elif type(attempted_state) is ServicePredecessor:
                phase_predecessors.append(attempted_state)
            phase_predecessors_by_workload[workload] = tuple(phase_predecessors)
        except Exception as error:
            retain_error(f"prepare {workload}", error, workload=workload)

    # Do not add evidence writes, waits, health checks, terminal proof, or deliberate sleeps
    # between these successfully bound restorative receipts.
    for workload in ("web", "worker"):
        if workload not in workloads or workload_outcomes[workload] != "pending":
            continue
        try:
            deadline = workload_deadlines[workload]
            restore_target = restore_targets[workload]
            receipt = gateway.update_service(
                workload,
                restore_target,
                phase_predecessors_by_workload[workload],
                deadline=deadline,
                timeout_seconds=recovery_budgets[workload],
            )
            summary = RestorativeReceiptSummary.from_receipt(receipt)
            receipt_summaries.append(summary)
            receipts[workload] = receipt
            expected_primary_deployment_ids[workload] = receipt.primary_deployment_id
            allowed_terminal_predecessors[workload] = receipt.predecessors
        except Exception as error:
            retain_error(f"bind {workload}", error, workload=workload)

    for summary in receipt_summaries:
        _record_recovery_evidence(
            evidence_path,
            evidence_stage,
            "bound",
            summary.as_evidence(),
        )
    _record_recovery_evidence(
        evidence_path,
        "recovery_initiation",
        "completed",
        {
            "attempted_before_observation": True,
            "bound_workloads": [workload for workload in ("web", "worker") if workload in receipts],
        },
    )

    pending = {workload for workload in ("web", "worker") if workload in receipts}
    while pending:
        for workload in ("web", "worker"):
            if workload not in pending:
                continue
            try:
                if gateway.observe_recovery_receipt(
                    receipts[workload],
                    workload_deadline=workload_deadlines[workload],
                    phase_deadline=phase_deadline,
                ):
                    workload_outcomes[workload] = "passed"
                    pending.remove(workload)
            except Exception as error:
                retain_error(f"observe {workload}", error, workload=workload)
                pending.remove(workload)
        if pending:
            try:
                gateway.sleep_recovery_round(
                    {workload: workload_deadlines[workload] for workload in pending},
                    phase_deadline,
                )
            except Exception as error:
                for workload in ("web", "worker"):
                    if workload in pending:
                        retain_error("cooperative recovery poll", error, workload=workload)
                pending.clear()

    for workload in ("web", "worker"):
        _record_recovery_evidence(
            evidence_path,
            "recovery_workload",
            workload_outcomes[workload],
            {
                "workload": workload,
                "outcome": workload_outcomes[workload],
                "intentionally_untouched": intentionally_untouched[workload],
            },
        )

    terminal_passed = False
    try:
        gateway.verify_terminal(
            {workload: target.task_definition_arn for workload, target in restore_targets.items()},
            {workload: target.desired_count for workload, target in restore_targets.items()},
            prior_identity,
            expected_primary_deployment_ids,
            allowed_terminal_predecessors,
            phase_deadline=phase_deadline,
        )
        terminal_passed = True
    except Exception as error:
        retain_error("terminal verification", error)
    _record_recovery_evidence(
        evidence_path,
        "recovery_terminal_pair",
        "passed" if terminal_passed else "contract_contradiction",
        {
            "exact_pair": terminal_passed,
            "worker_singleton": terminal_passed,
            **_identity_evidence(prior_identity),
        },
    )

    public_applicable = prior_identity is not None
    public_attempted = False
    public_passed = not public_applicable
    if public_applicable and terminal_passed and not errors:
        public_attempted = True
        try:
            assert prior_identity is not None
            gateway.verify_public_web(
                prior_identity,
                phase_deadline=phase_deadline,
            )
            public_passed = True
        except Exception as error:
            retain_error("public health", error)
    if not public_applicable:
        public_result = "skipped"
    elif not public_attempted:
        public_result = "not_attempted"
    elif public_passed:
        public_result = "passed"
    else:
        public_result = "failed"
    _record_recovery_evidence(
        evidence_path,
        "recovery_public_health",
        public_result,
        {
            "applicable": public_applicable,
            "attempted": public_attempted,
            "exact_prior_sha_ready": public_passed if public_applicable else None,
            **_identity_evidence(prior_identity),
        },
    )
    try:
        gateway.ensure_recovery_phase(phase_deadline)
    except Exception as error:
        retain_error("recovery phase", error)

    total_result = "passed" if not errors else _combined_restorative_reason(error_reasons)
    _record_recovery_evidence(
        evidence_path,
        "recovery_total",
        total_result,
        {
            "terminal_pair": terminal_passed,
            "public_health": public_passed,
            "worker_singleton": terminal_passed,
            "intentionally_untouched": intentionally_untouched,
            **_identity_evidence(prior_identity),
        },
    )
    if errors:
        raise CompensationError(
            "automatic compensation failed; recover with exact non-secret identifiers: "
            f"{recovery}; "
            f"causes: {'; '.join(errors)}",
            receipt_summaries=tuple(receipt_summaries),
            reason_code=_combined_restorative_reason(error_reasons),
        )
    return tuple(receipt_summaries)


def _restore_phase_from_snapshots(
    web: ServiceSnapshot,
    worker: ServiceSnapshot,
) -> tuple[dict[str, ServiceTarget], dict[str, ServicePredecessor]]:
    snapshots = {"web": web, "worker": worker}
    targets = {
        workload: ServiceTarget(snapshot.task_definition_arn, snapshot.desired_count)
        for workload, snapshot in snapshots.items()
    }
    predecessors = {
        workload: ServicePredecessor(
            targets[workload],
            snapshot.primary_deployment_id,
            "terminal",
        )
        for workload, snapshot in snapshots.items()
    }
    return targets, predecessors


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
    gateway.verify_image_digest_exists(identity)


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
        assert prior_web.version is not None
        assert prior_web.identity_schema is not None
        prior_identity = ReleaseIdentity(
            source_sha=prior_web.source_sha,
            image_digest=prior_web.image_digest,
            repository_uri=config.identity.repository_uri,
            version=prior_web.version,
            identity_schema=prior_web.identity_schema,
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
            **_identity_evidence(prior_identity),
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
        {
            **{f"{workload}_task_definition_arn": value for workload, value in registered.items()},
            **_identity_evidence(config.identity),
        },
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
        {
            "task_definition_arn": registered["migration"],
            **_identity_evidence(config.identity),
        },
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
        {
            "task_definition_arn": registered["migration"],
            "exit_code": 0,
            **_identity_evidence(config.identity),
        },
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
    _record_evidence(
        config.evidence_path,
        "pre_update_gate",
        "passed",
        _identity_evidence(config.identity),
    )
    mutated = False
    attempted: dict[str, ServiceTarget | ServicePredecessor] = {}
    active_stage = "web"
    try:
        _record_evidence(
            config.evidence_path,
            "web",
            "started",
            {"stabilization_timeout_seconds": gateway.web_stabilization_timeout_seconds},
        )
        mutated = True
        web_target = ServiceTarget(registered["web"], config.web_desired_count)
        attempted["web"] = web_target
        web_deadline = gateway.service_stabilization_deadline(
            gateway.web_stabilization_timeout_seconds
        )
        web_receipt = gateway.update_service(
            "web",
            web_target,
            (
                ServicePredecessor(
                    ServiceTarget(
                        prior_web.task_definition_arn,
                        prior_web.desired_count,
                    ),
                    prior_web.primary_deployment_id,
                    "terminal",
                ),
            ),
            deadline=web_deadline,
            timeout_seconds=gateway.web_stabilization_timeout_seconds,
        )
        _record_evidence(
            config.evidence_path,
            "web_receipt",
            "bound",
            {
                "primary_deployment_id": web_receipt.primary_deployment_id,
                "receipt_binding": web_receipt.binding_reason,
                "terminal_observed": web_receipt.terminal_observed,
            },
        )
        attempted["web"] = ServicePredecessor(
            web_receipt.target,
            web_receipt.primary_deployment_id,
            "attempted",
        )
        gateway.wait_service_stable(
            web_receipt,
            timeout_seconds=gateway.web_stabilization_timeout_seconds,
            deadline=web_deadline,
        )
        coherence_deadline = gateway.web_coherence_deadline()
        web_runtime_binding = gateway.establish_web_runtime_binding(
            web_receipt,
            config.identity,
            deadline=coherence_deadline,
        )
        _record_evidence(
            config.evidence_path,
            "web",
            "passed",
            {
                "task_definition_arn": registered["web"],
                "desired_count": config.web_desired_count,
                **_identity_evidence(config.identity),
                "primary_deployment_id": web_receipt.primary_deployment_id,
                "receipt_binding": web_receipt.binding_reason,
                "ecs_stable": True,
                "readiness": True,
                "liveness": True,
                "stabilization_timeout_seconds": gateway.web_stabilization_timeout_seconds,
                **web_runtime_binding.safe_evidence(
                    observation_count=2,
                    deadline_budget_seconds=gateway.web_coherence_timeout_seconds,
                ),
            },
        )

        active_stage = "worker"
        _record_evidence(
            config.evidence_path,
            "worker",
            "started",
            {"stabilization_timeout_seconds": gateway.worker_stabilization_timeout_seconds},
        )
        worker_target = ServiceTarget(registered["worker"], config.worker_desired_count)
        worker_deadline = gateway.service_stabilization_deadline(
            gateway.worker_stabilization_timeout_seconds
        )
        if not gateway.revalidate_web_runtime_binding(
            web_runtime_binding,
            deadline=worker_deadline,
        ):
            raise ReleaseContractError("bound web runtime is temporarily absent")
        attempted["worker"] = worker_target
        worker_receipt = gateway.update_service(
            "worker",
            worker_target,
            (
                ServicePredecessor(
                    ServiceTarget(
                        prior_worker.task_definition_arn,
                        prior_worker.desired_count,
                    ),
                    prior_worker.primary_deployment_id,
                    "terminal",
                ),
            ),
            deadline=worker_deadline,
            timeout_seconds=gateway.worker_stabilization_timeout_seconds,
            web_runtime_binding=web_runtime_binding,
        )
        _record_evidence(
            config.evidence_path,
            "worker_receipt",
            "bound",
            {
                "primary_deployment_id": worker_receipt.primary_deployment_id,
                "receipt_binding": worker_receipt.binding_reason,
                "terminal_observed": worker_receipt.terminal_observed,
            },
        )
        attempted["worker"] = ServicePredecessor(
            worker_receipt.target,
            worker_receipt.primary_deployment_id,
            "attempted",
        )
        gateway.wait_service_stable(
            worker_receipt,
            worker_singleton=True,
            timeout_seconds=gateway.worker_stabilization_timeout_seconds,
            deadline=worker_deadline,
            web_runtime_binding=web_runtime_binding,
        )
        _record_evidence(
            config.evidence_path,
            "worker",
            "passed",
            {
                "task_definition_arn": registered["worker"],
                "desired_count": config.worker_desired_count,
                "primary_deployment_id": worker_receipt.primary_deployment_id,
                "receipt_binding": worker_receipt.binding_reason,
                "singleton": True,
                "stabilization_timeout_seconds": (gateway.worker_stabilization_timeout_seconds),
                **_identity_evidence(config.identity),
            },
        )
        active_stage = "smoke"
        _record_evidence(config.evidence_path, "smoke", "started")
        if config.failure_injection == "post_mutation_smoke":
            raise ReleaseContractError("controlled post-mutation smoke failure")
        gateway.run_deployed_smoke(config.identity)
        _record_evidence(
            config.evidence_path,
            "smoke",
            "passed",
            {"mode": "read_only", **_identity_evidence(config.identity)},
        )
        active_stage = "terminal"
        _record_evidence(config.evidence_path, "terminal", "started")
        terminal_deadline = gateway.service_stabilization_deadline()
        gateway.verify_terminal(
            {"web": registered["web"], "worker": registered["worker"]},
            {
                "web": config.web_desired_count,
                "worker": config.worker_desired_count,
            },
            config.identity,
            {
                "web": web_receipt.primary_deployment_id,
                "worker": worker_receipt.primary_deployment_id,
            },
            {
                "web": web_receipt.predecessors,
                "worker": worker_receipt.predecessors,
            },
            web_runtime_binding=web_runtime_binding,
            web_runtime_deadline=terminal_deadline,
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
                **_identity_evidence(config.identity),
                "binding_fingerprint": web_runtime_binding.fingerprint,
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
            version=config.identity.version,
            identity_schema=config.identity.identity_schema,
        )
        active_stage = "release_record"
        record.write(config.release_record_path)
        _record_evidence(
            config.evidence_path,
            "release_record",
            "passed",
            {
                "rollback_eligible": True,
                "version": config.identity.version,
                "source_sha": config.identity.source_sha,
                "image_digest": config.identity.image_digest,
                "identity_schema": config.identity.identity_schema,
            },
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
                restore_targets, terminal_predecessors = _restore_phase_from_snapshots(
                    prior_web,
                    prior_worker,
                )
                _compensate(
                    gateway,
                    restore_targets,
                    terminal_predecessors,
                    prior_identity,
                    attempted,
                    evidence_path=config.evidence_path,
                )
                _record_recovery_evidence(
                    config.evidence_path,
                    "compensation",
                    "passed",
                    {
                        "web_task_definition_arn": prior_web.task_definition_arn,
                        "worker_task_definition_arn": prior_worker.task_definition_arn,
                        "web_desired_count": prior_web.desired_count,
                        "worker_desired_count": prior_worker.desired_count,
                        **_identity_evidence(prior_identity),
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
        version=current.version,
        identity_schema=current.identity_schema,
    )
    target_identity = ReleaseIdentity(
        source_sha=target.source_sha,
        image_digest=target.image_digest,
        repository_uri=repository_uri,
        version=target.version,
        identity_schema=target.identity_schema,
    )
    gateway.verify_release_record(current, current_identity)
    gateway.verify_release_record(target, target_identity)
    gateway.verify_image_digest_exists(current_identity)
    gateway.verify_image_digest_exists(target_identity)
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
            **_identity_evidence(current_identity),
        },
    )
    _record_evidence(
        evidence_path,
        "migration",
        "skipped",
        {"operation": "rollback", **_identity_evidence(target_identity)},
    )
    mutated = False
    attempted: dict[str, ServiceTarget | ServicePredecessor] = {}
    active_stage = "web"
    try:
        _record_evidence(
            evidence_path,
            "web",
            "started",
            {"stabilization_timeout_seconds": gateway.web_stabilization_timeout_seconds},
        )
        mutated = True
        web_target = ServiceTarget(target.web_task_definition_arn, target.web_desired_count)
        attempted["web"] = web_target
        web_deadline = gateway.service_stabilization_deadline(
            gateway.web_stabilization_timeout_seconds
        )
        web_receipt = gateway.update_service(
            "web",
            web_target,
            (
                ServicePredecessor(
                    ServiceTarget(
                        current_web.task_definition_arn,
                        current_web.desired_count,
                    ),
                    current_web.primary_deployment_id,
                    "terminal",
                ),
            ),
            deadline=web_deadline,
            timeout_seconds=gateway.web_stabilization_timeout_seconds,
        )
        _record_evidence(
            evidence_path,
            "web_receipt",
            "bound",
            {
                "primary_deployment_id": web_receipt.primary_deployment_id,
                "receipt_binding": web_receipt.binding_reason,
                "terminal_observed": web_receipt.terminal_observed,
            },
        )
        attempted["web"] = ServicePredecessor(
            web_receipt.target,
            web_receipt.primary_deployment_id,
            "attempted",
        )
        gateway.wait_service_stable(
            web_receipt,
            timeout_seconds=gateway.web_stabilization_timeout_seconds,
            deadline=web_deadline,
        )
        coherence_deadline = gateway.web_coherence_deadline()
        web_runtime_binding = gateway.establish_web_runtime_binding(
            web_receipt,
            target_identity,
            deadline=coherence_deadline,
        )
        _record_evidence(
            evidence_path,
            "web",
            "passed",
            {
                "task_definition_arn": target.web_task_definition_arn,
                "desired_count": target.web_desired_count,
                **_identity_evidence(target_identity),
                "primary_deployment_id": web_receipt.primary_deployment_id,
                "receipt_binding": web_receipt.binding_reason,
                "ecs_stable": True,
                "readiness": True,
                "liveness": True,
                "stabilization_timeout_seconds": gateway.web_stabilization_timeout_seconds,
                **web_runtime_binding.safe_evidence(
                    observation_count=2,
                    deadline_budget_seconds=gateway.web_coherence_timeout_seconds,
                ),
            },
        )
        active_stage = "worker"
        _record_evidence(
            evidence_path,
            "worker",
            "started",
            {"stabilization_timeout_seconds": gateway.worker_stabilization_timeout_seconds},
        )
        worker_target = ServiceTarget(
            target.worker_task_definition_arn,
            target.worker_desired_count,
        )
        worker_deadline = gateway.service_stabilization_deadline(
            gateway.worker_stabilization_timeout_seconds
        )
        if not gateway.revalidate_web_runtime_binding(
            web_runtime_binding,
            deadline=worker_deadline,
        ):
            raise ReleaseContractError("bound web runtime is temporarily absent")
        attempted["worker"] = worker_target
        worker_receipt = gateway.update_service(
            "worker",
            worker_target,
            (
                ServicePredecessor(
                    ServiceTarget(
                        current_worker.task_definition_arn,
                        current_worker.desired_count,
                    ),
                    current_worker.primary_deployment_id,
                    "terminal",
                ),
            ),
            deadline=worker_deadline,
            timeout_seconds=gateway.worker_stabilization_timeout_seconds,
            web_runtime_binding=web_runtime_binding,
        )
        _record_evidence(
            evidence_path,
            "worker_receipt",
            "bound",
            {
                "primary_deployment_id": worker_receipt.primary_deployment_id,
                "receipt_binding": worker_receipt.binding_reason,
                "terminal_observed": worker_receipt.terminal_observed,
            },
        )
        attempted["worker"] = ServicePredecessor(
            worker_receipt.target,
            worker_receipt.primary_deployment_id,
            "attempted",
        )
        gateway.wait_service_stable(
            worker_receipt,
            worker_singleton=True,
            timeout_seconds=gateway.worker_stabilization_timeout_seconds,
            deadline=worker_deadline,
            web_runtime_binding=web_runtime_binding,
        )
        _record_evidence(
            evidence_path,
            "worker",
            "passed",
            {
                "task_definition_arn": target.worker_task_definition_arn,
                "desired_count": target.worker_desired_count,
                "primary_deployment_id": worker_receipt.primary_deployment_id,
                "receipt_binding": worker_receipt.binding_reason,
                "singleton": True,
                "stabilization_timeout_seconds": (gateway.worker_stabilization_timeout_seconds),
                **_identity_evidence(target_identity),
            },
        )
        active_stage = "smoke"
        _record_evidence(evidence_path, "smoke", "started")
        gateway.run_deployed_smoke(target_identity)
        _record_evidence(
            evidence_path,
            "smoke",
            "passed",
            {"mode": "read_only", **_identity_evidence(target_identity)},
        )
        active_stage = "terminal"
        _record_evidence(evidence_path, "terminal", "started")
        terminal_deadline = gateway.service_stabilization_deadline()
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
            {
                "web": web_receipt.primary_deployment_id,
                "worker": worker_receipt.primary_deployment_id,
            },
            {
                "web": web_receipt.predecessors,
                "worker": worker_receipt.predecessors,
            },
            web_runtime_binding=web_runtime_binding,
            web_runtime_deadline=terminal_deadline,
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
                **_identity_evidence(target_identity),
                "binding_fingerprint": web_runtime_binding.fingerprint,
            },
        )
        active_stage = "release_record"
        target.write(release_record_path)
        _record_evidence(
            evidence_path,
            "release_record",
            "passed",
            {"rollback_eligible": True, **_identity_evidence(target_identity)},
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
                restore_targets, terminal_predecessors = _restore_phase_from_snapshots(
                    current_web,
                    current_worker,
                )
                _compensate(
                    gateway,
                    restore_targets,
                    terminal_predecessors,
                    current_identity,
                    attempted,
                    evidence_path=evidence_path,
                )
                _record_recovery_evidence(
                    evidence_path,
                    "compensation",
                    "passed",
                    {
                        "web_task_definition_arn": current_web.task_definition_arn,
                        "worker_task_definition_arn": current_worker.task_definition_arn,
                        "web_desired_count": current_web.desired_count,
                        "worker_desired_count": current_worker.desired_count,
                        **_identity_evidence(current_identity),
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
    evidence_path: Path | None = None,
) -> tuple[RestorativeReceiptSummary, ...]:
    """Restore the exact pre-release pair after a detectable artifact finalization failure."""
    failed_identity = ReleaseIdentity(
        source_sha=failed_release.source_sha,
        image_digest=failed_release.image_digest,
        repository_uri=context.repository_uri,
        version=failed_release.version,
        identity_schema=failed_release.identity_schema,
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
    if (
        context.source_sha is not None
        and context.image_digest is not None
        and context.version is not None
    ):
        prior_identity = ReleaseIdentity(
            source_sha=context.source_sha,
            image_digest=context.image_digest,
            repository_uri=context.repository_uri,
            version=context.version,
            identity_schema=context.identity_schema,
        )
        pair = ActiveServicePair(
            source_sha=context.source_sha,
            image_digest=context.image_digest,
            web_task_definition_arn=context.web_task_definition_arn,
            worker_task_definition_arn=context.worker_task_definition_arn,
            web_desired_count=context.web_desired_count,
            worker_desired_count=context.worker_desired_count,
            version=context.version,
            identity_schema=context.identity_schema,
        )
        gateway.verify_active_service_pair(pair, prior_identity)
        gateway.verify_image_digest_exists(prior_identity)

    restore_targets = {
        "web": ServiceTarget(
            context.web_task_definition_arn,
            context.web_desired_count,
        ),
        "worker": ServiceTarget(
            context.worker_task_definition_arn,
            context.worker_desired_count,
        ),
    }
    terminal_predecessors = {
        "web": ServicePredecessor(
            ServiceTarget(current_web.task_definition_arn, current_web.desired_count),
            current_web.primary_deployment_id,
            "terminal",
        ),
        "worker": ServicePredecessor(
            ServiceTarget(current_worker.task_definition_arn, current_worker.desired_count),
            current_worker.primary_deployment_id,
            "terminal",
        ),
    }
    return _compensate(
        gateway,
        restore_targets,
        terminal_predecessors,
        prior_identity,
        {},
        restore_workloads=frozenset({"web", "worker"}),
        evidence_path=evidence_path,
        evidence_stage="finalization_receipt",
    )
