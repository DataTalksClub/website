from __future__ import annotations

import argparse
import io
import json
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from django.test import SimpleTestCase

from deploy.cli import main as release_cli_main
from deploy.contracts import (
    PLACEHOLDER_DIGEST,
    ActiveServicePair,
    ReleaseContractError,
    ReleaseIdentity,
    ReleaseRecord,
    ServicePredecessor,
    ServiceSnapshot,
    ServiceTarget,
    ServiceUpdateReceipt,
    WebRuntimeBinding,
)
from deploy.release import (
    RELEASE_A_SHA,
    RELEASE_B_SHA,
    CompensationError,
    PromotionConfig,
    RecoveryContext,
    _compensate,
    capture_current_service_pair,
    capture_recovery_context,
    promote,
    restore_after_finalization_failure,
    rollback,
)
from deploy.smoke import ROBOTS_VALUE, Response, run_http_smoke, validate_origin
from deploy.task_definitions import (
    FIXED_NONSECRET_ENVIRONMENT,
    SAFETY_ENVIRONMENT,
    TaskDefinitionConfig,
    build_task_definitions,
)

SHA_A = RELEASE_A_SHA
SHA_B = RELEASE_B_SHA
DIGEST_A = f"sha256:{'a' * 64}"
DIGEST_B = f"sha256:{'b' * 64}"
REPOSITORY = "817685572750.dkr.ecr.eu-west-1.amazonaws.com/website-sandbox"
TASK_ROLE = "arn:aws:iam::817685572750:role/website-task"
EXECUTION_ROLE = "arn:aws:iam::817685572750:role/website-execution"
FAMILIES = {name: f"website-sandbox-{name}" for name in ("web", "worker", "migration")}
CONTAINERS = {name: name for name in ("web", "worker", "migration")}
DATABASE_SECRET_ARN = (
    "arn:aws:secretsmanager:eu-west-1:817685572750:secret:website-sandbox/database-url-Ab12Cd"
)
DJANGO_SECRET_ARN = (
    "arn:aws:secretsmanager:eu-west-1:817685572750:secret:website-sandbox/django-secret-key-Ef34Gh"
)


def version_for(source_sha: str) -> str:
    return f"20260809-143205-{source_sha[:7]}"


VERSION_A = version_for(SHA_A)
VERSION_B = version_for(SHA_B)


def arn(workload: str, revision: int) -> str:
    return f"arn:aws:ecs:eu-west-1:817685572750:task-definition/{FAMILIES[workload]}:{revision}"


def deployment_id(workload: str, revision: int) -> str:
    return f"ecs-svc/{workload}-{revision}"


def task_document(workload: str, source_sha: str = "bootstrap-disabled") -> dict[str, Any]:
    return {
        "family": FAMILIES[workload],
        "taskRoleArn": TASK_ROLE,
        "executionRoleArn": EXECUTION_ROLE,
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "256",
        "memory": "512",
        "containerDefinitions": [
            {
                "name": CONTAINERS[workload],
                "image": f"{REPOSITORY}@{PLACEHOLDER_DIGEST}",
                "essential": True,
                "environment": [
                    {"name": "APP_VERSION", "value": source_sha},
                    *[
                        {"name": name, "value": value}
                        for name, value in FIXED_NONSECRET_ENVIRONMENT.items()
                    ],
                ],
                "secrets": [
                    {
                        "name": "DATABASE_URL",
                        "valueFrom": DATABASE_SECRET_ARN,
                    },
                    {
                        "name": "DJANGO_SECRET_KEY",
                        "valueFrom": DJANGO_SECRET_ARN,
                    },
                ],
            }
        ],
    }


def successful_record(source_sha: str = SHA_A, digest: str = DIGEST_A) -> ReleaseRecord:
    return ReleaseRecord(
        source_sha=source_sha,
        image_digest=digest,
        web_task_definition_arn=arn("web", 1),
        worker_task_definition_arn=arn("worker", 1),
        migration_task_definition_arn=arn("migration", 1),
        web_desired_count=1,
        worker_desired_count=1,
        rollback_eligible=True,
        version=version_for(source_sha),
    )


def active_pair(source_sha: str = SHA_A, digest: str = DIGEST_A) -> ActiveServicePair:
    return ActiveServicePair(
        source_sha=source_sha,
        image_digest=digest,
        web_task_definition_arn=arn("web", 1),
        worker_task_definition_arn=arn("worker", 1),
        web_desired_count=1,
        worker_desired_count=1,
        version=version_for(source_sha),
    )


class FakeGateway:
    web_coherence_timeout_seconds = 180
    web_stabilization_timeout_seconds = 240
    worker_stabilization_timeout_seconds = 420
    web_recovery_timeout_seconds = 240
    worker_recovery_timeout_seconds = 420
    recovery_phase_timeout_seconds = 720

    def __init__(self, *, bootstrap: bool, fail_once: str | None = None) -> None:
        desired = 0 if bootstrap else 1
        source = None if bootstrap else SHA_A
        version = None if bootstrap else VERSION_A
        digest = PLACEHOLDER_DIGEST if bootstrap else DIGEST_A
        self.snapshots = {
            workload: ServiceSnapshot(
                service_name=f"service-{workload}",
                task_definition_arn=arn(workload, 1),
                desired_count=desired,
                running_count=desired,
                pending_count=0,
                source_sha=source,
                image_digest=digest,
                primary_deployment_id=deployment_id(workload, 1),
                version=version,
                identity_schema=None if bootstrap else 2,
            )
            for workload in ("web", "worker")
        }
        self.fail_once = fail_once
        self.operations: list[str] = []
        self.update_number = 1
        self.terminal_allowed_predecessors: dict[str, tuple[ServicePredecessor, ...]] | None = None

    def _fail(self, point: str) -> None:
        if self.fail_once == point:
            self.fail_once = None
            raise ReleaseContractError(f"injected {point}")

    def capture_service(self, workload: str) -> ServiceSnapshot:
        self.operations.append(f"capture:{workload}")
        return self.snapshots[workload]

    def source_task_definition(self, workload: str) -> dict[str, Any]:
        self.operations.append(f"source:{workload}")
        task = task_document(workload)
        task["taskDefinitionArn"] = arn(workload, 1)
        return task

    def verify_release_record(self, record: ReleaseRecord, identity: ReleaseIdentity) -> None:
        self.operations.append(f"verify-record:{record.source_sha}:{identity.image_digest}")

    def verify_active_service_pair(
        self, pair: ActiveServicePair, identity: ReleaseIdentity
    ) -> None:
        self.operations.append(f"verify-pair:{pair.source_sha}:{identity.image_digest}")

    def verify_image_digest_exists(self, identity: ReleaseIdentity) -> None:
        self.operations.append(
            f"verify-image:{identity.repository_uri}:{identity.version}:"
            f"{identity.source_sha}@{identity.image_digest}"
        )
        self._fail("verify-image")

    def register_task_definition(
        self, workload: str, task_definition: dict[str, Any], tags: dict[str, str]
    ) -> str:
        self.operations.append(f"register:{workload}:{','.join(sorted(tags))}")
        self._fail(f"register:{workload}")
        return arn(workload, 2)

    def run_migration(
        self, task_definition_arn: str, *, inject_controlled_failure: bool = False
    ) -> None:
        self.operations.append(
            f"migrate:{task_definition_arn}:injected={inject_controlled_failure}"
        )
        if inject_controlled_failure:
            raise ReleaseContractError("controlled migration exited nonzero (97)")
        self._fail("migration")

    def update_service(
        self,
        workload: str,
        target: ServiceTarget,
        predecessors: tuple[ServicePredecessor, ...],
        *,
        deadline: float | None = None,
        timeout_seconds: int | None = None,
        web_runtime_binding: WebRuntimeBinding | None = None,
    ) -> ServiceUpdateReceipt:
        del timeout_seconds
        if web_runtime_binding is not None:
            self.operations.append(f"guard:update:{web_runtime_binding.fingerprint}")
        self.operations.append(
            f"update:{workload}:{target.task_definition_arn}:{target.desired_count}"
        )
        self.operations.append(
            f"update-predecessors:{workload}:"
            f"{','.join(item.primary_deployment_id for item in predecessors)}"
        )
        if self.fail_once == "compensation" and target.task_definition_arn.endswith(":1"):
            self._fail("compensation")
        lose_response = self.fail_once == f"update:{workload}"
        self.update_number += 1
        receipt_id = deployment_id(workload, self.update_number)
        self.snapshots[workload] = ServiceSnapshot(
            service_name=f"service-{workload}",
            task_definition_arn=target.task_definition_arn,
            desired_count=target.desired_count,
            running_count=target.desired_count,
            pending_count=0,
            source_sha=SHA_B if target.task_definition_arn.endswith(":2") else SHA_A,
            image_digest=DIGEST_B if target.task_definition_arn.endswith(":2") else DIGEST_A,
            primary_deployment_id=receipt_id,
            version=VERSION_B if target.task_definition_arn.endswith(":2") else VERSION_A,
            identity_schema=2,
        )
        if lose_response:
            self._fail(f"update:{workload}")
        if web_runtime_binding is not None:
            self.revalidate_web_runtime_binding(
                web_runtime_binding,
                deadline=float(420 if deadline is None else deadline),
            )
        return ServiceUpdateReceipt(
            workload,
            f"service-{workload}",
            target,
            receipt_id,
            predecessors,
        )

    def capture_attempted_predecessor(
        self,
        workload: str,
        attempted_target: ServiceTarget,
        terminal_predecessor: ServicePredecessor,
        deadline: float,
    ) -> ServicePredecessor:
        del deadline
        self.operations.append(f"capture-attempted:{workload}")
        snapshot = self.snapshots[workload]
        if (
            snapshot.task_definition_arn == attempted_target.task_definition_arn
            and snapshot.desired_count == attempted_target.desired_count
            and snapshot.primary_deployment_id != terminal_predecessor.primary_deployment_id
        ):
            return ServicePredecessor(
                attempted_target,
                snapshot.primary_deployment_id,
                "attempted",
            )
        raise ReleaseContractError("attempted deployment was not observed")

    def service_stabilization_deadline(self, timeout_seconds: int | None = None) -> float:
        return float(180 if timeout_seconds is None else timeout_seconds)

    def web_coherence_deadline(self) -> float:
        self.operations.append("coherence-deadline:180")
        return 180.0

    def recovery_phase_deadline(self) -> float:
        self.operations.append("recovery-deadline:phase=720")
        return 720.0

    def recovery_workload_deadline(self, workload: str, phase_deadline: float) -> float:
        budget = 240 if workload == "web" else 420
        deadline = min(float(budget), phase_deadline)
        self.operations.append(f"recovery-deadline:{workload}={int(deadline)}")
        return deadline

    def ensure_recovery_phase(self, phase_deadline: float) -> None:
        self.operations.append(f"recovery-phase:inside={int(phase_deadline)}")

    def wait_service_stable(
        self,
        receipt: ServiceUpdateReceipt,
        *,
        worker_singleton: bool = False,
        timeout_seconds: int | None = None,
        deadline: float | None = None,
        web_runtime_binding: WebRuntimeBinding | None = None,
    ) -> None:
        workload = receipt.workload
        self.operations.append(
            f"wait:{workload}:singleton={worker_singleton}:timeout={timeout_seconds}:"
            f"task={receipt.target.task_definition_arn}:"
            f"desired={receipt.target.desired_count}"
        )
        self.operations.append(f"wait-receipt:{workload}:{receipt.primary_deployment_id}")
        if web_runtime_binding is not None:
            self.operations.append(f"guard:wait:{web_runtime_binding.fingerprint}")
            self.revalidate_web_runtime_binding(
                web_runtime_binding,
                deadline=float(420 if deadline is None else deadline),
            )
        self._fail(f"wait:{workload}")

    def establish_web_runtime_binding(
        self,
        receipt: ServiceUpdateReceipt,
        identity: ReleaseIdentity,
        *,
        deadline: float,
    ) -> WebRuntimeBinding:
        self.operations.append(f"coherence:establish:{int(deadline)}")
        self._fail("private-address")
        self.operations.append(f"health:{identity.source_sha}")
        self._fail("health")
        self._fail("coherence")
        return WebRuntimeBinding(
            configured_service_identity=receipt.configured_service_identity,
            primary_deployment_id=receipt.primary_deployment_id,
            task_definition_arn=receipt.target.task_definition_arn,
            predecessors=receipt.predecessors,
            source_sha=identity.source_sha,
            image_digest=identity.image_digest,
            task_arn=("arn:aws:ecs:eu-west-1:817685572750:task/website-sandbox/" + "a" * 32),
            network_attachment_id="attachment-web",
            network_interface_id="eni-0123456789abcdef0",
            private_ipv4_address="10.0.1.17",
            container_port=8000,
            target_port=8000,
            version=identity.version,
            identity_schema=identity.identity_schema,
        )

    def revalidate_web_runtime_binding(
        self,
        binding: WebRuntimeBinding,
        *,
        deadline: float,
    ) -> bool:
        self.operations.append(f"coherence:revalidate:{int(deadline)}:{binding.fingerprint}")
        self._fail("web-binding")
        return True

    def observe_recovery_receipt(
        self,
        receipt: ServiceUpdateReceipt,
        *,
        workload_deadline: float,
        phase_deadline: float,
    ) -> bool:
        self.operations.append(
            f"observe:{receipt.workload}:deadline={int(workload_deadline)}:"
            f"phase={int(phase_deadline)}"
        )
        self.wait_service_stable(
            receipt,
            worker_singleton=receipt.workload == "worker",
            deadline=workload_deadline,
        )
        return True

    def sleep_recovery_round(
        self,
        workload_deadlines: dict[str, float],
        phase_deadline: float,
    ) -> None:
        self.operations.append(
            "recovery-sleep:"
            + ",".join(sorted(workload_deadlines))
            + f":phase={int(phase_deadline)}"
        )

    def verify_public_web(
        self, identity: ReleaseIdentity, *, phase_deadline: float | None = None
    ) -> None:
        del phase_deadline
        self.operations.append(f"health:{identity.version}:{identity.source_sha}")
        self._fail("health")

    def run_deployed_smoke(self, identity: ReleaseIdentity) -> None:
        self.operations.append(f"smoke:{identity.version}:{identity.source_sha}")
        self._fail("smoke")

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
    ) -> None:
        del phase_deadline
        if web_runtime_binding is not None:
            self.operations.append(
                f"guard:terminal:{int(web_runtime_deadline or 0)}:{web_runtime_binding.fingerprint}"
            )
            self.revalidate_web_runtime_binding(
                web_runtime_binding,
                deadline=float(180 if web_runtime_deadline is None else web_runtime_deadline),
            )
        self.terminal_allowed_predecessors = allowed_predecessors
        self.operations.append("terminal")
        for workload, snapshot in self.snapshots.items():
            if (
                snapshot.task_definition_arn != expected_task_definitions[workload]
                or snapshot.desired_count != expected_desired_counts[workload]
                or snapshot.running_count != expected_desired_counts[workload]
                or snapshot.pending_count != 0
            ):
                raise ReleaseContractError(f"terminal {workload} target differs")
            if (
                expected_primary_deployment_ids is not None
                and snapshot.primary_deployment_id != expected_primary_deployment_ids[workload]
            ):
                raise ReleaseContractError(f"terminal {workload} receipt ID differs")
            if expected_identity is not None and (
                snapshot.version != expected_identity.version
                or snapshot.source_sha != expected_identity.source_sha
                or snapshot.image_digest != expected_identity.image_digest
                or snapshot.identity_schema != expected_identity.identity_schema
            ):
                raise ReleaseContractError(f"terminal {workload} identity differs")
        if any(value.endswith(":2") for value in expected_task_definitions.values()):
            self._fail("terminal")


class CooperativeRecoveryGateway(FakeGateway):
    def __init__(
        self,
        *,
        complete_at: dict[str, float],
        fail_bind: str | None = None,
    ) -> None:
        super().__init__(bootstrap=False)
        self.current = 0.0
        self.complete_at = complete_at
        self.fail_bind = fail_bind
        self.observation_times: dict[str, list[float]] = {"web": [], "worker": []}

    def recovery_phase_deadline(self) -> float:
        deadline = self.current + self.recovery_phase_timeout_seconds
        self.operations.append(f"phase-deadline:{deadline:g}")
        return deadline

    def recovery_workload_deadline(self, workload: str, phase_deadline: float) -> float:
        budget = (
            self.web_recovery_timeout_seconds
            if workload == "web"
            else self.worker_recovery_timeout_seconds
        )
        deadline = min(self.current + budget, phase_deadline)
        self.operations.append(f"deadline:{workload}:{deadline:g}")
        return deadline

    def ensure_recovery_phase(self, phase_deadline: float) -> None:
        self.operations.append(f"phase-proof:{self.current:g}")
        if self.current > phase_deadline:
            raise ReleaseContractError(
                "recovery phase deadline expired",
                reason_code="receipt_deadline_expired",
            )

    def update_service(
        self,
        workload: str,
        target: ServiceTarget,
        predecessors: tuple[ServicePredecessor, ...],
        *,
        deadline: float | None = None,
        timeout_seconds: int | None = None,
        web_runtime_binding: WebRuntimeBinding | None = None,
    ) -> ServiceUpdateReceipt:
        self.operations.append(f"bind-at:{workload}:{self.current:g}")
        if self.fail_bind == workload:
            raise ReleaseContractError(f"injected {workload} binding contradiction")
        return super().update_service(
            workload,
            target,
            predecessors,
            deadline=deadline,
            timeout_seconds=timeout_seconds,
            web_runtime_binding=web_runtime_binding,
        )

    def observe_recovery_receipt(
        self,
        receipt: ServiceUpdateReceipt,
        *,
        workload_deadline: float,
        phase_deadline: float,
    ) -> bool:
        workload = receipt.workload
        self.observation_times[workload].append(self.current)
        self.operations.append(f"observe-at:{workload}:{self.current:g}")
        if self.current > workload_deadline or self.current > phase_deadline:
            raise ReleaseContractError(
                f"{workload} response after deadline",
                reason_code="receipt_deadline_expired",
            )
        if self.current >= self.complete_at[workload]:
            return True
        if self.current >= workload_deadline:
            raise ReleaseContractError(
                f"{workload} receipt deadline expired",
                reason_code="receipt_deadline_expired",
            )
        return False

    def sleep_recovery_round(
        self,
        workload_deadlines: dict[str, float],
        phase_deadline: float,
    ) -> None:
        next_time = min(self.current + 10, phase_deadline, *workload_deadlines.values())
        self.operations.append(f"sleep:{self.current:g}->{next_time:g}")
        self.current = next_time

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
    ) -> None:
        self.operations.append(f"terminal-at:{self.current:g}")
        if phase_deadline is not None:
            self.ensure_recovery_phase(phase_deadline)
        super().verify_terminal(
            expected_task_definitions,
            expected_desired_counts,
            expected_identity,
            expected_primary_deployment_ids,
            allowed_predecessors,
            web_runtime_binding=web_runtime_binding,
            web_runtime_deadline=web_runtime_deadline,
        )

    def verify_public_web(
        self, identity: ReleaseIdentity, *, phase_deadline: float | None = None
    ) -> None:
        self.operations.append(f"health-at:{self.current:g}")
        if phase_deadline is not None:
            self.ensure_recovery_phase(phase_deadline)
        super().verify_public_web(identity)


class CooperativeRecoveryCoordinatorTests(SimpleTestCase):
    @staticmethod
    def restore_phase() -> tuple[
        dict[str, ServiceTarget],
        dict[str, ServicePredecessor],
        dict[str, ServicePredecessor],
    ]:
        targets = {workload: ServiceTarget(arn(workload, 1), 1) for workload in ("web", "worker")}
        terminal = {
            workload: ServicePredecessor(
                targets[workload],
                deployment_id(workload, 1),
                "terminal",
            )
            for workload in ("web", "worker")
        }
        attempted = {
            workload: ServicePredecessor(
                ServiceTarget(arn(workload, 2), 1),
                f"ecs-svc/{workload}-attempted",
                "attempted",
            )
            for workload in ("web", "worker")
        }
        return targets, terminal, attempted

    def compensate(
        self,
        gateway: CooperativeRecoveryGateway,
        *,
        attempted_workloads: tuple[str, ...] = ("web", "worker"),
        evidence_path: Path | None = None,
    ) -> tuple[Any, ...]:
        targets, terminal, attempted = self.restore_phase()
        return _compensate(
            gateway,
            targets,
            terminal,
            ReleaseIdentity(SHA_A, DIGEST_A, REPOSITORY, VERSION_A),
            {workload: attempted[workload] for workload in attempted_workloads},
            evidence_path=evidence_path,
        )

    def test_recorded_incident_binds_both_receipts_before_fair_160_280_observation(self) -> None:
        gateway = CooperativeRecoveryGateway(complete_at={"web": 160, "worker": 280})
        evidence_events: list[str] = []

        def record(*_args: object, **_kwargs: object) -> None:
            evidence_events.append(f"evidence-at:{gateway.current:g}")
            gateway.operations.append(evidence_events[-1])

        with patch("deploy.release._record_recovery_evidence", side_effect=record):
            summaries = self.compensate(gateway)

        self.assertEqual([summary.workload for summary in summaries], ["web", "worker"])
        web_bind = gateway.operations.index("bind-at:web:0")
        worker_bind = gateway.operations.index("bind-at:worker:0")
        first_observe = next(
            index
            for index, operation in enumerate(gateway.operations)
            if operation.startswith("observe-at:")
        )
        self.assertLess(web_bind, worker_bind)
        self.assertLess(worker_bind, first_observe)
        self.assertFalse(
            any(
                operation.startswith(
                    ("observe-at:", "sleep:", "terminal-at:", "health-at:", "evidence-at:")
                )
                for operation in gateway.operations[web_bind + 1 : worker_bind]
            )
        )
        self.assertEqual(gateway.observation_times["web"][-1], 160)
        self.assertEqual(gateway.observation_times["worker"][-1], 280)
        self.assertEqual(gateway.current, 280)

    def test_independent_inclusive_240_420_deadlines_have_one_final_read(self) -> None:
        gateway = CooperativeRecoveryGateway(complete_at={"web": 240, "worker": 420})

        self.compensate(gateway)

        self.assertEqual(gateway.observation_times["web"].count(240), 1)
        self.assertEqual(gateway.observation_times["worker"].count(420), 1)
        self.assertFalse(any(value > 240 for value in gateway.observation_times["web"]))
        self.assertFalse(any(value > 420 for value in gateway.observation_times["worker"]))
        self.assertEqual(gateway.current, 420)

    def test_schema1_recovery_proves_terminal_receipts_before_legacy_public_health(self) -> None:
        gateway = CooperativeRecoveryGateway(complete_at={"web": 0, "worker": 0})
        legacy_identity = ReleaseIdentity.legacy(SHA_A, DIGEST_A, REPOSITORY)
        original_update = gateway.update_service

        def update_legacy(*args, **kwargs):  # type: ignore[no-untyped-def]
            receipt = original_update(*args, **kwargs)
            workload = receipt.workload
            snapshot = gateway.snapshots[workload]
            gateway.snapshots[workload] = ServiceSnapshot(
                service_name=snapshot.service_name,
                task_definition_arn=snapshot.task_definition_arn,
                desired_count=snapshot.desired_count,
                running_count=snapshot.running_count,
                pending_count=snapshot.pending_count,
                source_sha=legacy_identity.source_sha,
                image_digest=legacy_identity.image_digest,
                primary_deployment_id=snapshot.primary_deployment_id,
                version=legacy_identity.version,
                identity_schema=legacy_identity.identity_schema,
            )
            return receipt

        gateway.update_service = update_legacy  # type: ignore[method-assign]
        targets, terminal, attempted = self.restore_phase()
        attempted_states: dict[str, ServiceTarget | ServicePredecessor] = dict(attempted)

        _compensate(
            gateway,
            targets,
            terminal,
            legacy_identity,
            attempted_states,
        )

        terminal_index = gateway.operations.index("terminal-at:0")
        health_index = gateway.operations.index("health-at:0")
        self.assertLess(terminal_index, health_index)
        self.assertIn(f"health:{SHA_A}:{SHA_A}", gateway.operations)

    def test_schema1_mixed_worker_never_attempts_or_passes_public_health(self) -> None:
        gateway = CooperativeRecoveryGateway(complete_at={"web": 0, "worker": 0})
        legacy_identity = ReleaseIdentity.legacy(SHA_A, DIGEST_A, REPOSITORY)
        original_update = gateway.update_service

        def update_mixed_worker(*args, **kwargs):  # type: ignore[no-untyped-def]
            receipt = original_update(*args, **kwargs)
            workload = receipt.workload
            snapshot = gateway.snapshots[workload]
            source_sha = "f" * 40 if workload == "worker" else legacy_identity.source_sha
            gateway.snapshots[workload] = ServiceSnapshot(
                service_name=snapshot.service_name,
                task_definition_arn=snapshot.task_definition_arn,
                desired_count=snapshot.desired_count,
                running_count=snapshot.running_count,
                pending_count=snapshot.pending_count,
                source_sha=source_sha,
                image_digest=legacy_identity.image_digest,
                primary_deployment_id=snapshot.primary_deployment_id,
                version=source_sha,
                identity_schema=legacy_identity.identity_schema,
            )
            return receipt

        gateway.update_service = update_mixed_worker  # type: ignore[method-assign]
        targets, terminal, attempted = self.restore_phase()
        attempted_states: dict[str, ServiceTarget | ServicePredecessor] = dict(attempted)
        Path(".tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=".tmp") as directory:
            evidence_path = Path(directory) / "recovery-evidence.json"
            with self.assertRaises(CompensationError):
                _compensate(
                    gateway,
                    targets,
                    terminal,
                    legacy_identity,
                    attempted_states,
                    evidence_path=evidence_path,
                )
            stages = json.loads(evidence_path.read_text())["stages"]

        self.assertFalse(any(operation.startswith("health") for operation in gateway.operations))
        terminal_evidence = next(
            item for item in stages if item["stage"] == "recovery_terminal_pair"
        )
        public_evidence = next(item for item in stages if item["stage"] == "recovery_public_health")
        total_evidence = next(item for item in stages if item["stage"] == "recovery_total")
        self.assertEqual(terminal_evidence["result"], "contract_contradiction")
        self.assertEqual(public_evidence["result"], "not_attempted")
        self.assertEqual(public_evidence["proof"]["attempted"], False)
        self.assertEqual(public_evidence["proof"]["exact_prior_sha_ready"], False)
        self.assertEqual(total_evidence["result"], "contract_contradiction")
        self.assertEqual(total_evidence["proof"]["terminal_pair"], False)
        self.assertEqual(total_evidence["proof"]["public_health"], False)

    def test_retained_receipt_error_blocks_schema2_public_health_after_terminal_read(self) -> None:
        gateway = CooperativeRecoveryGateway(complete_at={"web": 0, "worker": 0})
        original_observe = gateway.observe_recovery_receipt

        def observe_with_worker_error(
            receipt: ServiceUpdateReceipt,
            *,
            workload_deadline: float,
            phase_deadline: float,
        ) -> bool:
            if receipt.workload == "worker":
                raise ReleaseContractError("injected worker receipt contradiction")
            return original_observe(
                receipt,
                workload_deadline=workload_deadline,
                phase_deadline=phase_deadline,
            )

        gateway.observe_recovery_receipt = observe_with_worker_error  # type: ignore[method-assign]
        Path(".tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=".tmp") as directory:
            evidence_path = Path(directory) / "recovery-evidence.json"
            with self.assertRaises(CompensationError):
                self.compensate(gateway, evidence_path=evidence_path)
            stages = json.loads(evidence_path.read_text())["stages"]

        self.assertFalse(any(operation.startswith("health") for operation in gateway.operations))
        terminal_evidence = next(
            item for item in stages if item["stage"] == "recovery_terminal_pair"
        )
        public_evidence = next(item for item in stages if item["stage"] == "recovery_public_health")
        total_evidence = next(item for item in stages if item["stage"] == "recovery_total")
        self.assertEqual(terminal_evidence["result"], "passed")
        self.assertEqual(public_evidence["result"], "not_attempted")
        self.assertEqual(public_evidence["proof"]["attempted"], False)
        self.assertEqual(public_evidence["proof"]["exact_prior_sha_ready"], False)
        self.assertEqual(total_evidence["result"], "contract_contradiction")
        self.assertEqual(total_evidence["proof"]["public_health"], False)

    def test_worker_deadline_cannot_be_rescued_by_a_later_terminal_fixture(self) -> None:
        gateway = CooperativeRecoveryGateway(complete_at={"web": 10, "worker": 430})

        with self.assertRaises(CompensationError) as caught:
            self.compensate(gateway)

        self.assertEqual(caught.exception.reason_code, "receipt_deadline_expired")
        self.assertEqual(gateway.observation_times["worker"].count(420), 1)
        self.assertFalse(any(value > 420 for value in gateway.observation_times["worker"]))
        self.assertNotIn(430, gateway.observation_times["worker"])

    def test_isolated_web_binding_failure_still_restores_and_observes_worker_once(self) -> None:
        gateway = CooperativeRecoveryGateway(
            complete_at={"web": 0, "worker": 20},
            fail_bind="web",
        )

        with self.assertRaises(CompensationError) as caught:
            self.compensate(gateway)

        self.assertEqual(caught.exception.reason_code, "contract_contradiction")
        self.assertEqual(gateway.operations.count("bind-at:web:0"), 1)
        self.assertEqual(gateway.operations.count("bind-at:worker:0"), 1)
        self.assertEqual(gateway.observation_times["worker"][-1], 20)

    def test_web_only_failure_never_mutates_worker_and_proves_empty_allowance(self) -> None:
        gateway = CooperativeRecoveryGateway(complete_at={"web": 10, "worker": 0})

        self.compensate(gateway, attempted_workloads=("web",))

        self.assertFalse(
            any(operation.startswith("bind-at:worker") for operation in gateway.operations)
        )
        assert gateway.terminal_allowed_predecessors is not None
        self.assertEqual(gateway.terminal_allowed_predecessors["worker"], ())

    def test_invalid_recovery_budget_fails_before_any_mutation(self) -> None:
        gateway = CooperativeRecoveryGateway(complete_at={"web": 0, "worker": 0})
        gateway.web_recovery_timeout_seconds = True  # type: ignore[misc]

        with self.assertRaisesMessage(ReleaseContractError, "recovery budgets differ"):
            self.compensate(gateway)

        self.assertFalse(any(operation.startswith("bind-at:") for operation in gateway.operations))

    def test_evidence_has_only_fixed_plan_safe_receipts_outcomes_and_terminal_proofs(self) -> None:
        gateway = CooperativeRecoveryGateway(complete_at={"web": 10, "worker": 20})
        Path(".tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=".tmp") as directory:
            evidence_path = Path(directory) / "recovery-evidence.json"
            self.compensate(gateway, evidence_path=evidence_path)
            payload = __import__("json").loads(evidence_path.read_text())

        stages = payload["stages"]
        plan = next(item for item in stages if item["stage"] == "recovery_plan")
        self.assertEqual(
            plan["proof"],
            {
                "mode": "automatic_compensation",
                "web_recovery_seconds": 240,
                "worker_recovery_seconds": 420,
                "phase_recovery_seconds": 720,
                "restore_initiation_order": ["web", "worker"],
                "cooperative_observation_order": ["web", "worker"],
                "eligible_workloads": ["web", "worker"],
                "intentionally_untouched": {"web": False, "worker": False},
                "identity_schema": 2,
                "version": VERSION_A,
                "source_sha": SHA_A,
                "image_digest": DIGEST_A,
            },
        )
        receipts = [item["proof"] for item in stages if item["stage"] == "compensation_receipt"]
        self.assertEqual([item["workload"] for item in receipts], ["web", "worker"])
        self.assertTrue(
            all(
                set(item) == {"workload", "receipt_id", "receipt_binding", "carried_terminal"}
                for item in receipts
            )
        )
        outcomes = [item["proof"] for item in stages if item["stage"] == "recovery_workload"]
        self.assertEqual([item["outcome"] for item in outcomes], ["passed", "passed"])
        total = next(item for item in stages if item["stage"] == "recovery_total")
        self.assertEqual(total["result"], "passed")
        self.assertEqual(total["proof"]["terminal_pair"], True)
        self.assertEqual(total["proof"]["public_health"], True)
        self.assertEqual(total["proof"]["worker_singleton"], True)
        serialized = __import__("json").dumps(payload).lower()
        for forbidden in (
            "authorization",
            "cookie",
            "credential",
            "environment",
            "provider response",
            "query string",
            "recovery-context",
            "request body",
            "task log",
            "token",
        ):
            self.assertNotIn(forbidden, serialized)


class TaskDefinitionBuilderTests(SimpleTestCase):
    def setUp(self) -> None:
        self.config = TaskDefinitionConfig(
            families=FAMILIES,
            container_names=CONTAINERS,
            task_role_arn=TASK_ROLE,
            execution_role_arn=EXECUTION_ROLE,
        )
        self.identity = ReleaseIdentity(SHA_B, DIGEST_B, REPOSITORY, VERSION_B)

    def test_builder_normalizes_all_workloads_from_one_contract(self) -> None:
        tasks = build_task_definitions(
            {workload: task_document(workload) for workload in CONTAINERS},
            self.identity,
            self.config,
        )

        environments = []
        secrets = []
        for workload, task in tasks.items():
            container = task["containerDefinitions"][0]
            self.assertEqual(container["image"], f"{REPOSITORY}@{DIGEST_B}")
            self.assertEqual(container["user"], "10001:10001")
            environment = {item["name"]: item["value"] for item in container["environment"]}
            self.assertEqual(environment["VERSION"], VERSION_B)
            self.assertEqual(environment["SOURCE_SHA"], SHA_B)
            self.assertEqual(environment["IMAGE_DIGEST"], DIGEST_B)
            self.assertNotIn("APP_VERSION", environment)
            self.assertLessEqual(SAFETY_ENVIRONMENT.items(), environment.items())
            environments.append(environment)
            secrets.append(container["secrets"])
            if workload == "migration":
                self.assertEqual(
                    container["entryPoint"],
                    ["uv", "run", "--no-sync", "python", "manage.py"],
                )
                self.assertEqual(container["command"], ["migrate", "--noinput"])
            else:
                self.assertNotIn("entryPoint", container)
                self.assertEqual(container["command"], [workload])
        self.assertEqual(environments[0], environments[1])
        self.assertEqual(environments[1], environments[2])
        self.assertEqual(secrets[0], secrets[1])
        self.assertEqual(secrets[1], secrets[2])

    def test_builder_rejects_role_environment_and_secret_mismatches(self) -> None:
        mutations = ("role", "environment", "secret")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                tasks = {workload: task_document(workload) for workload in CONTAINERS}
                worker = tasks["worker"]
                if mutation == "role":
                    worker["taskRoleArn"] = f"{TASK_ROLE}-other"
                elif mutation == "environment":
                    worker["containerDefinitions"][0]["environment"].append(
                        {"name": "UNSAFE_DIFFERENCE", "value": "1"}
                    )
                else:
                    worker["containerDefinitions"][0]["secrets"][0]["valueFrom"] += "-other"
                with self.assertRaises(ReleaseContractError):
                    build_task_definitions(tasks, self.identity, self.config)

    def test_builder_replaces_prior_release_metadata_before_comparing_sources(self) -> None:
        tasks = {workload: task_document(workload) for workload in CONTAINERS}
        for index, workload in enumerate(CONTAINERS):
            container = tasks[workload]["containerDefinitions"][0]
            container["environment"][0]["value"] = f"{index:x}" * 40

        normalized = build_task_definitions(tasks, self.identity, self.config)

        for task in normalized.values():
            environment = {
                item["name"]: item["value"]
                for item in task["containerDefinitions"][0]["environment"]
            }
            self.assertEqual(environment["VERSION"], VERSION_B)
            self.assertEqual(environment["SOURCE_SHA"], SHA_B)
            self.assertEqual(environment["IMAGE_DIGEST"], DIGEST_B)
            self.assertEqual(environment["DATAMAILER_TRANSACTIONAL_DRY_RUN"], "1")

    def test_builder_rejects_extra_environment_duplicate_secret_names_and_wrong_arns(self) -> None:
        mutations = ("extra_environment", "duplicate_name", "wrong_arn")
        for mutation in mutations:
            tasks = {workload: task_document(workload) for workload in CONTAINERS}
            for task in tasks.values():
                container = task["containerDefinitions"][0]
                if mutation == "extra_environment":
                    container["environment"].append({"name": "UNREVIEWED", "value": "1"})
                elif mutation == "duplicate_name":
                    container["secrets"][1]["name"] = "DATABASE_URL"
                else:
                    container["secrets"][0]["valueFrom"] = (
                        "arn:aws:secretsmanager:eu-west-1:817685572750:"
                        "secret:another/database-url-Ab12Cd"
                    )
            with self.subTest(mutation=mutation), self.assertRaises(ReleaseContractError):
                build_task_definitions(tasks, self.identity, self.config)

    def test_release_identity_rejects_short_sha_mutable_digest_and_placeholder(self) -> None:
        for source, digest in (
            ("abc123", DIGEST_B),
            (SHA_B, "latest"),
            (SHA_B, PLACEHOLDER_DIGEST),
        ):
            with (
                self.subTest(source=source, digest=digest),
                self.assertRaises(ReleaseContractError),
            ):
                ReleaseIdentity(source, digest, REPOSITORY, version_for(source))


class PromotionTests(SimpleTestCase):
    def _config(
        self,
        directory: str,
        *,
        prior: ReleaseRecord | ActiveServicePair | None,
        failure_injection: str = "none",
        source_sha: str = SHA_B,
    ) -> PromotionConfig:
        return PromotionConfig(
            identity=ReleaseIdentity(source_sha, DIGEST_B, REPOSITORY, version_for(source_sha)),
            task_definitions=TaskDefinitionConfig(
                families=FAMILIES,
                container_names=CONTAINERS,
                task_role_arn=TASK_ROLE,
                execution_role_arn=EXECUTION_ROLE,
            ),
            web_desired_count=1,
            worker_desired_count=1,
            project_tag="website",
            environment_tag="sandbox",
            release_record_path=Path(directory) / "release.json",
            expected_prior_release=prior,
            failure_injection=failure_injection,
            evidence_path=Path(directory) / "evidence.json",
            recovery_context_path=Path(directory) / "recovery.json",
        )

    def _temporary_directory(self):  # type: ignore[no-untyped-def]
        Path(".tmp").mkdir(exist_ok=True)
        return tempfile.TemporaryDirectory(dir=".tmp")

    def test_bootstrap_migrates_then_updates_web_then_worker_and_records_success(self) -> None:
        gateway = FakeGateway(bootstrap=True)
        Path(".tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=".tmp") as directory:
            config = self._config(directory, prior=None)
            record = promote(gateway, config)
            self.assertEqual(ReleaseRecord.read(Path(directory) / "release.json"), record)
            assert config.evidence_path is not None
            stages = __import__("json").loads(config.evidence_path.read_text())["stages"]
            web_proofs = [item["proof"] for item in stages if item["stage"] == "web"]
            self.assertEqual(
                [proof["stabilization_timeout_seconds"] for proof in web_proofs],
                [240, 240],
            )
            self.assertEqual(web_proofs[-1]["receipt_binding"], "complete_receipt")
            worker_proofs = [item["proof"] for item in stages if item["stage"] == "worker"]
            self.assertEqual(worker_proofs[-1]["receipt_binding"], "complete_receipt")
            expected_identity = {
                "identity_schema": 2,
                "version": VERSION_B,
                "source_sha": SHA_B,
                "image_digest": DIGEST_B,
            }
            for stage_name in (
                "registration",
                "migration",
                "pre_update_gate",
                "web",
                "worker",
                "smoke",
                "terminal",
                "release_record",
            ):
                with self.subTest(stage=stage_name):
                    proof = next(
                        item["proof"]
                        for item in reversed(stages)
                        if item["stage"] == stage_name and item["result"] == "passed"
                    )
                    self.assertLessEqual(expected_identity.items(), proof.items())
        migration = next(
            i for i, value in enumerate(gateway.operations) if value.startswith("migrate:")
        )
        web = next(
            i for i, value in enumerate(gateway.operations) if value.startswith("update:web:")
        )
        worker = next(
            i for i, value in enumerate(gateway.operations) if value.startswith("update:worker:")
        )
        pre_mutation_web_capture = max(
            i for i, value in enumerate(gateway.operations) if value == "capture:web"
        )
        pre_mutation_worker_capture = max(
            i for i, value in enumerate(gateway.operations) if value == "capture:worker"
        )
        self.assertLess(migration, web)
        self.assertLess(migration, pre_mutation_web_capture)
        self.assertLess(pre_mutation_web_capture, pre_mutation_worker_capture)
        self.assertLess(pre_mutation_worker_capture, web)
        self.assertLess(web, worker)
        web_wait = f"wait:web:singleton=False:timeout=240:task={arn('web', 2)}:desired=1"
        health = f"health:{SHA_B}"
        worker_wait = f"wait:worker:singleton=True:timeout=420:task={arn('worker', 2)}:desired=1"
        self.assertIn(web_wait, gateway.operations)
        self.assertIn(worker_wait, gateway.operations)
        self.assertLess(gateway.operations.index(web_wait), gateway.operations.index(health))
        self.assertLess(gateway.operations.index(health), gateway.operations.index(worker_wait))
        self.assertTrue(
            any(
                value.startswith("wait:worker:singleton=True:timeout=420:")
                for value in gateway.operations
            )
        )
        establish = gateway.operations.index("coherence:establish:180")
        pre_worker = next(
            index
            for index, value in enumerate(gateway.operations)
            if value.startswith("coherence:revalidate:420:")
        )
        worker_update = gateway.operations.index(f"update:worker:{arn('worker', 2)}:1")
        smoke = gateway.operations.index(f"smoke:{VERSION_B}:{SHA_B}")
        terminal_guard = next(
            index
            for index, value in enumerate(gateway.operations)
            if value.startswith("guard:terminal:180:")
        )
        self.assertLess(establish, pre_worker)
        self.assertLess(pre_worker, worker_update)
        self.assertLess(worker_update, smoke)
        self.assertLess(smoke, terminal_guard)

    def test_promotion_carries_one_web_binding_through_worker_smoke_and_terminal(self) -> None:
        gateway = FakeGateway(bootstrap=False)
        with self._temporary_directory() as directory:
            config = self._config(directory, prior=successful_record())
            promote(gateway, config)
            assert config.evidence_path is not None
            evidence = json.loads(config.evidence_path.read_text())

        establish = gateway.operations.index("coherence:establish:180")
        pre_worker = next(
            index
            for index, value in enumerate(gateway.operations)
            if value.startswith("coherence:revalidate:420:")
        )
        worker_update = gateway.operations.index(f"update:worker:{arn('worker', 2)}:1")
        worker_guard = next(
            index
            for index, value in enumerate(gateway.operations)
            if value.startswith("guard:update:")
        )
        worker_wait = next(
            index
            for index, value in enumerate(gateway.operations)
            if value.startswith("wait:worker:singleton=True")
        )
        wait_guard = next(
            index
            for index, value in enumerate(gateway.operations)
            if value.startswith("guard:wait:")
        )
        smoke = gateway.operations.index(f"smoke:{VERSION_B}:{SHA_B}")
        terminal_guard = next(
            index
            for index, value in enumerate(gateway.operations)
            if value.startswith("guard:terminal:180:")
        )
        self.assertLess(establish, pre_worker)
        self.assertLess(pre_worker, worker_update)
        self.assertLess(worker_guard, worker_update)
        self.assertLess(worker_update, worker_wait)
        self.assertLess(worker_wait, wait_guard)
        self.assertLess(wait_guard, smoke)
        self.assertLess(smoke, terminal_guard)
        fingerprints = {
            value.rsplit(":", 1)[-1]
            for value in gateway.operations
            if value.startswith(("coherence:revalidate:", "guard:update:", "guard:wait:"))
        }
        fingerprints.add(
            next(
                value.rsplit(":", 1)[-1]
                for value in gateway.operations
                if value.startswith("guard:terminal:")
            )
        )
        self.assertEqual(len(fingerprints), 1)

        web_pass = next(
            item
            for item in evidence["stages"]
            if item["stage"] == "web" and item["result"] == "passed"
        )
        proof = web_pass["proof"]
        self.assertEqual(proof["observation_count"], 2)
        self.assertEqual(proof["deadline_budget_seconds"], 180)
        evidence_text = json.dumps(evidence, sort_keys=True)
        for forbidden in (
            "arn:aws:ecs:eu-west-1:817685572750:task/website-sandbox/",
            "eni-0123456789abcdef0",
            "10.0.1.17",
            "attachment-web",
        ):
            self.assertNotIn(forbidden, evidence_text)

    def test_preworker_binding_failure_restores_only_web_and_never_runs_smoke(self) -> None:
        gateway = FakeGateway(bootstrap=False, fail_once="web-binding")
        with self._temporary_directory() as directory:
            config = self._config(directory, prior=successful_record())
            with self.assertRaisesMessage(ReleaseContractError, "injected web-binding"):
                promote(gateway, config)
            assert config.evidence_path is not None
            evidence = json.loads(config.evidence_path.read_text())

        self.assertNotIn(f"update:worker:{arn('worker', 2)}:1", gateway.operations)
        self.assertNotIn(f"update:worker:{arn('worker', 1)}:1", gateway.operations)
        self.assertIn(f"update:web:{arn('web', 1)}:1", gateway.operations)
        self.assertNotIn(f"smoke:{SHA_B}", gateway.operations)
        self.assertFalse(any(item["stage"] == "smoke" for item in evidence["stages"]))
        self.assertFalse(config.release_record_path.exists())

    def test_non_rfc1918_preworker_failure_is_redacted_and_leaves_worker_untouched(
        self,
    ) -> None:
        gateway = FakeGateway(bootstrap=False, fail_once="private-address")
        with self._temporary_directory() as directory:
            config = self._config(directory, prior=successful_record())
            with self.assertRaisesMessage(ReleaseContractError, "injected private-address"):
                promote(gateway, config)
            assert config.evidence_path is not None
            evidence = json.loads(config.evidence_path.read_text())

        self.assertIn("coherence:establish:180", gateway.operations)
        self.assertNotIn(f"health:{SHA_B}", gateway.operations)
        self.assertNotIn(f"update:worker:{arn('worker', 2)}:1", gateway.operations)
        self.assertNotIn(f"update:worker:{arn('worker', 1)}:1", gateway.operations)
        self.assertIn(f"update:web:{arn('web', 1)}:1", gateway.operations)
        self.assertNotIn(f"smoke:{SHA_B}", gateway.operations)
        self.assertFalse(config.release_record_path.exists())
        failure = next(
            item
            for item in evidence["stages"]
            if item["stage"] == "web" and item["result"] == "failed"
        )
        self.assertEqual(
            failure["proof"],
            {
                "error_class": "ReleaseContractError",
                "reason_code": "contract_contradiction",
            },
        )
        evidence_text = json.dumps(evidence, sort_keys=True)
        self.assertNotIn("private-address", evidence_text)
        self.assertFalse(any(item["stage"] == "smoke" for item in evidence["stages"]))

    def test_worker_phase_binding_failure_compensates_pair_before_smoke(self) -> None:
        gateway = FakeGateway(bootstrap=False)
        original = gateway.revalidate_web_runtime_binding
        calls = 0

        def fail_after_worker_mutation(
            binding: WebRuntimeBinding,
            *,
            deadline: float,
        ) -> bool:
            nonlocal calls
            calls += 1
            if calls == 1:
                return original(binding, deadline=deadline)
            raise ReleaseContractError("injected worker-phase web replacement")

        gateway.revalidate_web_runtime_binding = fail_after_worker_mutation  # type: ignore[method-assign]
        with self._temporary_directory() as directory:
            config = self._config(directory, prior=successful_record())
            with self.assertRaisesMessage(ReleaseContractError, "worker-phase web replacement"):
                promote(gateway, config)

        self.assertIn(f"update:worker:{arn('worker', 2)}:1", gateway.operations)
        self.assertIn(f"update:web:{arn('web', 1)}:1", gateway.operations)
        self.assertIn(f"update:worker:{arn('worker', 1)}:1", gateway.operations)
        self.assertNotIn(f"smoke:{SHA_B}", gateway.operations)
        self.assertFalse(config.release_record_path.exists())

    def test_bootstrap_recovery_context_discards_valid_looking_placeholder_identity(self) -> None:
        gateway = FakeGateway(bootstrap=True)
        for workload in ("web", "worker"):
            snapshot = gateway.snapshots[workload]
            gateway.snapshots[workload] = ServiceSnapshot(
                service_name=snapshot.service_name,
                task_definition_arn=snapshot.task_definition_arn,
                desired_count=0,
                running_count=0,
                pending_count=0,
                source_sha="0" * 40,
                image_digest=PLACEHOLDER_DIGEST,
                primary_deployment_id=snapshot.primary_deployment_id,
            )
        with self._temporary_directory() as directory:
            config = self._config(directory, prior=None)
            promote(gateway, config)
            recovery_path = config.recovery_context_path
            assert recovery_path is not None
            context = RecoveryContext.read(recovery_path)
        self.assertIsNone(context.source_sha)
        self.assertIsNone(context.image_digest)
        self.assertEqual((context.web_desired_count, context.worker_desired_count), (0, 0))

    def test_nonbootstrap_requires_and_validates_last_successful_record(self) -> None:
        with self._temporary_directory() as directory:
            with self.assertRaisesMessage(
                ReleaseContractError, "requires the last successful release record"
            ):
                promote(FakeGateway(bootstrap=False), self._config(directory, prior=None))
            wrong = successful_record(source_sha="c" * 40, digest=f"sha256:{'c' * 64}")
            with self.assertRaisesMessage(ReleaseContractError, "differ from"):
                promote(FakeGateway(bootstrap=False), self._config(directory, prior=wrong))

    def test_auto_promotion_accepts_a_verified_web_worker_pair_without_migration_state(
        self,
    ) -> None:
        gateway = FakeGateway(bootstrap=False)
        with self._temporary_directory() as directory:
            result = promote(gateway, self._config(directory, prior=active_pair()))
        self.assertEqual(result.source_sha, RELEASE_B_SHA)
        self.assertTrue(any(value.startswith("verify-pair:") for value in gateway.operations))

    def test_pre_migration_snapshot_rejects_a_race_without_migration_or_update(self) -> None:
        gateway = FakeGateway(bootstrap=False)
        original_register = gateway.register_task_definition

        def register_then_external_change(
            workload: str, task_definition: dict[str, Any], tags: dict[str, str]
        ) -> str:
            result = original_register(workload, task_definition, tags)
            if workload == "migration":
                gateway.snapshots["worker"] = ServiceSnapshot(
                    service_name="service-worker",
                    task_definition_arn=arn("worker", 2),
                    desired_count=1,
                    running_count=1,
                    pending_count=0,
                    source_sha=SHA_B,
                    image_digest=DIGEST_B,
                    primary_deployment_id=deployment_id("worker", 2),
                    version=VERSION_B,
                    identity_schema=2,
                )
            return result

        gateway.register_task_definition = register_then_external_change  # type: ignore[method-assign]
        with (
            self._temporary_directory() as directory,
            self.assertRaisesMessage(ReleaseContractError, "identities are mixed"),
        ):
            promote(gateway, self._config(directory, prior=successful_record()))
        self.assertFalse(any(value.startswith("migrate:") for value in gateway.operations))
        self.assertFalse(any(value.startswith("update:") for value in gateway.operations))

    def test_post_migration_snapshot_rejects_an_active_release_race(self) -> None:
        gateway = FakeGateway(bootstrap=False)

        def migration_then_external_change(
            task_definition_arn: str, *, inject_controlled_failure: bool = False
        ) -> None:
            gateway.operations.append(
                f"migrate:{task_definition_arn}:injected={inject_controlled_failure}"
            )
            gateway.snapshots["worker"] = ServiceSnapshot(
                service_name="service-worker",
                task_definition_arn=arn("worker", 2),
                desired_count=1,
                running_count=1,
                pending_count=0,
                source_sha=SHA_B,
                image_digest=DIGEST_B,
                primary_deployment_id=deployment_id("worker", 2),
                version=VERSION_B,
                identity_schema=2,
            )

        gateway.run_migration = migration_then_external_change  # type: ignore[method-assign]
        with (
            self._temporary_directory() as directory,
            self.assertRaisesMessage(ReleaseContractError, "identities are mixed"),
        ):
            promote(gateway, self._config(directory, prior=successful_record()))
        self.assertTrue(any(value.startswith("migrate:") for value in gateway.operations))
        self.assertFalse(any(value.startswith("update:") for value in gateway.operations))

    def test_controlled_failure_rejects_bootstrap_unknown_and_wrong_drill_identities(self) -> None:
        with self._temporary_directory() as directory:
            with self.assertRaisesMessage(
                ReleaseContractError,
                "controlled failure injection requires an existing prior release",
            ):
                self._config(directory, prior=None, failure_injection="migration")
            with self.assertRaisesMessage(
                ReleaseContractError, "unsupported release failure injection"
            ):
                self._config(
                    directory,
                    prior=successful_record(),
                    failure_injection="unreviewed",
                )
            with self.assertRaisesMessage(
                ReleaseContractError,
                "controlled failure injection requires exact accepted release B",
            ):
                self._config(
                    directory,
                    prior=successful_record(),
                    failure_injection="migration",
                    source_sha=RELEASE_A_SHA,
                )
            with self.assertRaisesMessage(
                ReleaseContractError,
                "controlled failure injection requires exact accepted release B",
            ):
                self._config(
                    directory,
                    prior=successful_record(),
                    failure_injection="migration",
                    source_sha="c" * 40,
                )
            with self.assertRaisesMessage(
                ReleaseContractError,
                "controlled failure injection requires exact accepted release A as prior",
            ):
                self._config(
                    directory,
                    prior=successful_record(source_sha="c" * 40),
                    failure_injection="migration",
                )

    def test_migration_failure_never_updates_a_service_or_records_release(self) -> None:
        gateway = FakeGateway(bootstrap=True, fail_once="migration")
        with self._temporary_directory() as directory:
            with self.assertRaisesMessage(ReleaseContractError, "injected migration"):
                promote(gateway, self._config(directory, prior=None))
            self.assertFalse((Path(directory) / "release.json").exists())
        self.assertFalse(any(value.startswith("update:") for value in gateway.operations))

    def test_controlled_migration_failure_uses_b_task_and_leaves_a_unchanged(self) -> None:
        gateway = FakeGateway(bootstrap=False)
        with self._temporary_directory() as directory:
            release_path = Path(directory) / "release.json"
            with self.assertRaisesMessage(
                ReleaseContractError, "controlled migration exited nonzero (97)"
            ):
                promote(
                    gateway,
                    self._config(
                        directory,
                        prior=successful_record(),
                        failure_injection="migration",
                    ),
                )
            self.assertFalse(release_path.exists())
        self.assertIn(f"migrate:{arn('migration', 2)}:injected=True", gateway.operations)
        self.assertFalse(any(value.startswith("update:") for value in gateway.operations))
        self.assertEqual(gateway.snapshots["web"].task_definition_arn, arn("web", 1))
        self.assertEqual(gateway.snapshots["worker"].task_definition_arn, arn("worker", 1))

    def test_controlled_migration_success_still_fails_before_service_mutation(self) -> None:
        gateway = FakeGateway(bootstrap=False)

        def unexpected_success(
            task_definition_arn: str, *, inject_controlled_failure: bool = False
        ) -> None:
            gateway.operations.append(
                f"migrate:{task_definition_arn}:injected={inject_controlled_failure}"
            )

        gateway.run_migration = unexpected_success  # type: ignore[method-assign]
        with self._temporary_directory() as directory:
            with self.assertRaisesMessage(
                ReleaseContractError,
                "controlled migration failure unexpectedly returned success",
            ):
                promote(
                    gateway,
                    self._config(
                        directory,
                        prior=successful_record(),
                        failure_injection="migration",
                    ),
                )
            self.assertFalse((Path(directory) / "release.json").exists())
        self.assertFalse(any(value.startswith("update:") for value in gateway.operations))

    def test_post_mutation_smoke_injection_compensates_both_services_to_a(self) -> None:
        gateway = FakeGateway(bootstrap=False)
        with self._temporary_directory() as directory:
            with self.assertRaisesMessage(
                ReleaseContractError, "controlled post-mutation smoke failure"
            ):
                promote(
                    gateway,
                    self._config(
                        directory,
                        prior=successful_record(),
                        failure_injection="post_mutation_smoke",
                    ),
                )
            self.assertFalse((Path(directory) / "release.json").exists())

        worker_b_update = gateway.operations.index(f"update:worker:{arn('worker', 2)}:1")
        web_a_restore = gateway.operations.index(f"update:web:{arn('web', 1)}:1")
        self.assertLess(worker_b_update, web_a_restore)
        self.assertNotIn(f"smoke:{SHA_B}", gateway.operations)
        self.assertIn(f"update:worker:{arn('worker', 1)}:1", gateway.operations)
        self.assertEqual(gateway.snapshots["web"].task_definition_arn, arn("web", 1))
        self.assertEqual(gateway.snapshots["worker"].task_definition_arn, arn("worker", 1))

    def test_each_postmutation_failure_restores_only_attempted_services(self) -> None:
        for failure in (
            "update:web",
            "wait:web",
            "health",
            "update:worker",
            "wait:worker",
            "smoke",
            "terminal",
        ):
            with self.subTest(failure=failure), self._temporary_directory() as directory:
                gateway = FakeGateway(bootstrap=False, fail_once=failure)
                with self.assertRaises(ReleaseContractError):
                    promote(gateway, self._config(directory, prior=successful_record()))
                self.assertIn(f"update:web:{arn('web', 1)}:1", gateway.operations)
                worker_restore = f"update:worker:{arn('worker', 1)}:1"
                if failure in {"update:worker", "wait:worker", "smoke", "terminal"}:
                    self.assertIn(worker_restore, gateway.operations)
                else:
                    self.assertNotIn(worker_restore, gateway.operations)
                self.assertEqual(gateway.snapshots["web"].task_definition_arn, arn("web", 1))
                self.assertEqual(gateway.snapshots["worker"].task_definition_arn, arn("worker", 1))
                self.assertFalse((Path(directory) / "release.json").exists())
                if failure == "update:web":
                    self.assertIn("capture-attempted:web", gateway.operations)
                    self.assertIn(
                        "update-predecessors:web:ecs-svc/web-1,ecs-svc/web-2",
                        gateway.operations,
                    )

    def test_worker_timeout_uses_extended_forward_budget_and_default_compensation_budget(
        self,
    ) -> None:
        gateway = FakeGateway(bootstrap=False, fail_once="wait:worker")
        with self._temporary_directory() as directory:
            release_path = Path(directory) / "release.json"
            with self.assertRaisesMessage(ReleaseContractError, "injected wait:worker"):
                promote(gateway, self._config(directory, prior=successful_record()))
            self.assertFalse(release_path.exists())

        forward_wait = next(
            value
            for value in gateway.operations
            if value.startswith("wait:worker:singleton=True:timeout=420:")
        )
        recovery_wait = next(
            value
            for value in gateway.operations
            if value.startswith("wait:worker:singleton=True:timeout=None:")
        )
        self.assertLess(
            gateway.operations.index(forward_wait),
            gateway.operations.index(recovery_wait),
        )
        self.assertEqual(gateway.snapshots["web"].task_definition_arn, arn("web", 1))
        self.assertEqual(gateway.snapshots["worker"].task_definition_arn, arn("worker", 1))

    def test_web_timeout_uses_extended_forward_budget_and_default_compensation_budget(
        self,
    ) -> None:
        gateway = FakeGateway(bootstrap=False, fail_once="wait:web")
        with self._temporary_directory() as directory:
            release_path = Path(directory) / "release.json"
            with self.assertRaisesMessage(ReleaseContractError, "injected wait:web"):
                promote(gateway, self._config(directory, prior=successful_record()))
            self.assertFalse(release_path.exists())

        forward_wait = f"wait:web:singleton=False:timeout=240:task={arn('web', 2)}:desired=1"
        recovery_waits = [
            value
            for value in gateway.operations
            if value.startswith("wait:") and ":timeout=None:" in value
        ]
        self.assertIn(forward_wait, gateway.operations)
        self.assertEqual(len(recovery_waits), 1)
        self.assertIn(
            f"wait:web:singleton=False:timeout=None:task={arn('web', 1)}:desired=1",
            recovery_waits,
        )
        self.assertFalse(any(":timeout=240:" in value for value in recovery_waits))
        self.assertNotIn(f"health:{SHA_B}", gateway.operations)
        self.assertNotIn(f"update:worker:{arn('worker', 2)}:1", gateway.operations)
        self.assertIn(
            "update-predecessors:web:ecs-svc/web-1,ecs-svc/web-2",
            gateway.operations,
        )
        self.assertNotIn(
            "update-predecessors:worker:ecs-svc/worker-1",
            gateway.operations,
        )
        self.assertNotIn("ecs-svc/worker-2", "\n".join(gateway.operations))
        self.assertNotIn(f"update:worker:{arn('worker', 2)}:1", gateway.operations)
        self.assertLess(
            gateway.operations.index(forward_wait),
            gateway.operations.index(recovery_waits[0]),
        )
        self.assertEqual(gateway.snapshots["web"].task_definition_arn, arn("web", 1))
        self.assertEqual(gateway.snapshots["worker"].task_definition_arn, arn("worker", 1))
        self.assertEqual(gateway.snapshots["web"].primary_deployment_id, "ecs-svc/web-3")
        assert gateway.terminal_allowed_predecessors is not None
        self.assertEqual(
            tuple(
                predecessor.primary_deployment_id
                for predecessor in gateway.terminal_allowed_predecessors["web"]
            ),
            ("ecs-svc/web-1", "ecs-svc/web-2"),
        )
        self.assertEqual(gateway.terminal_allowed_predecessors["worker"], ())

    def test_web_compensation_rejects_a_changed_untouched_worker_receipt_id(self) -> None:
        gateway = FakeGateway(bootstrap=False, fail_once="wait:web")
        original_wait = gateway.wait_service_stable

        def change_untouched_worker_after_web_restore(
            receipt: ServiceUpdateReceipt,
            *,
            worker_singleton: bool = False,
            timeout_seconds: int | None = None,
            deadline: float | None = None,
            web_runtime_binding: WebRuntimeBinding | None = None,
        ) -> None:
            original_wait(
                receipt,
                worker_singleton=worker_singleton,
                timeout_seconds=timeout_seconds,
                deadline=deadline,
                web_runtime_binding=web_runtime_binding,
            )
            if receipt.workload == "web" and receipt.target.task_definition_arn.endswith(":1"):
                worker = gateway.snapshots["worker"]
                gateway.snapshots["worker"] = ServiceSnapshot(
                    service_name=worker.service_name,
                    task_definition_arn=worker.task_definition_arn,
                    desired_count=worker.desired_count,
                    running_count=worker.running_count,
                    pending_count=worker.pending_count,
                    source_sha=worker.source_sha,
                    image_digest=worker.image_digest,
                    primary_deployment_id="ecs-svc/worker-alien",
                    version=worker.version,
                    identity_schema=worker.identity_schema,
                )

        gateway.wait_service_stable = change_untouched_worker_after_web_restore  # type: ignore[method-assign]
        with (
            self._temporary_directory() as directory,
            self.assertRaisesMessage(
                CompensationError,
                "terminal verification",
            ),
        ):
            promote(gateway, self._config(directory, prior=successful_record()))

        self.assertNotIn(f"update:worker:{arn('worker', 2)}:1", gateway.operations)
        self.assertNotIn(f"update:worker:{arn('worker', 1)}:1", gateway.operations)
        self.assertIn("terminal", gateway.operations)

    def test_failed_stage_evidence_uses_only_the_allowlisted_reason_code(self) -> None:
        gateway = FakeGateway(bootstrap=False, fail_once="wait:web")
        with self._temporary_directory() as directory:
            config = self._config(directory, prior=successful_record())
            with self.assertRaises(ReleaseContractError):
                promote(gateway, config)
            assert config.evidence_path is not None
            records = __import__("json").loads(config.evidence_path.read_text())["stages"]
        failure = next(
            item for item in records if item["stage"] == "web" and item["result"] == "failed"
        )
        receipt = next(item for item in records if item["stage"] == "web_receipt")
        self.assertLess(records.index(receipt), records.index(failure))
        self.assertEqual(receipt["result"], "bound")
        self.assertEqual(
            receipt["proof"],
            {
                "primary_deployment_id": "ecs-svc/web-2",
                "receipt_binding": "complete_receipt",
                "terminal_observed": False,
            },
        )
        self.assertEqual(
            failure["proof"],
            {
                "error_class": "ReleaseContractError",
                "reason_code": "contract_contradiction",
            },
        )

    def test_release_record_write_failure_compensates_and_records_actual_stage_evidence(
        self,
    ) -> None:
        gateway = FakeGateway(bootstrap=False)
        with self._temporary_directory() as directory:
            config = self._config(directory, prior=successful_record())
            with (
                patch.object(ReleaseRecord, "write", side_effect=OSError("sentinel-secret")),
                self.assertRaises(OSError),
            ):
                promote(gateway, config)
            self.assertFalse(config.release_record_path.exists())
            evidence = config.evidence_path
            assert evidence is not None
            records = __import__("json").loads(evidence.read_text())["stages"]
            self.assertIn(
                ("release_record", "failed"),
                {(item["stage"], item["result"]) for item in records},
            )
            self.assertIn(
                ("compensation", "passed"),
                {(item["stage"], item["result"]) for item in records},
            )
            compensation_receipts = [
                item for item in records if item["stage"] == "compensation_receipt"
            ]
            self.assertEqual(
                [item["proof"] for item in compensation_receipts],
                [
                    {
                        "workload": "web",
                        "receipt_id": "ecs-svc/web-4",
                        "receipt_binding": "complete_receipt",
                        "carried_terminal": False,
                    },
                    {
                        "workload": "worker",
                        "receipt_id": "ecs-svc/worker-5",
                        "receipt_binding": "complete_receipt",
                        "carried_terminal": False,
                    },
                ],
            )
            self.assertTrue(all(item["result"] == "bound" for item in compensation_receipts))
            self.assertNotIn("sentinel-secret", evidence.read_text())
        self.assertEqual(gateway.snapshots["web"].task_definition_arn, arn("web", 1))
        self.assertEqual(gateway.snapshots["worker"].task_definition_arn, arn("worker", 1))

    def test_manual_prior_image_tag_and_digest_are_proven_before_registration(self) -> None:
        gateway = FakeGateway(bootstrap=False, fail_once="verify-image")
        with self._temporary_directory() as directory, self.assertRaises(ReleaseContractError):
            promote(gateway, self._config(directory, prior=successful_record()))
        self.assertFalse(any(item.startswith("register:") for item in gateway.operations))
        self.assertFalse(any(item.startswith("migrate:") for item in gateway.operations))
        self.assertFalse(any(item.startswith("update:") for item in gateway.operations))

    def test_compensation_failure_is_loud_and_contains_only_recovery_identifiers(self) -> None:
        gateway = FakeGateway(bootstrap=False, fail_once="wait:web")
        original_wait = gateway.wait_service_stable

        def fail_then_break_compensation(
            receipt: ServiceUpdateReceipt,
            *,
            worker_singleton: bool = False,
            timeout_seconds: int | None = None,
            deadline: float | None = None,
            web_runtime_binding: WebRuntimeBinding | None = None,
        ) -> None:
            try:
                original_wait(
                    receipt,
                    worker_singleton=worker_singleton,
                    timeout_seconds=timeout_seconds,
                    deadline=deadline,
                    web_runtime_binding=web_runtime_binding,
                )
            except ReleaseContractError:
                gateway.fail_once = "compensation"
                raise

        gateway.wait_service_stable = fail_then_break_compensation  # type: ignore[method-assign]
        with (
            self._temporary_directory() as directory,
            self.assertRaises(CompensationError) as caught,
        ):
            promote(gateway, self._config(directory, prior=successful_record()))
        self.assertIn(arn("web", 1), str(caught.exception))
        self.assertIn(arn("worker", 1), str(caught.exception))

    def test_compensation_never_leaks_raw_exception_messages(self) -> None:
        def instrument_secret_failure(gateway: FakeGateway, selected_failure: str) -> None:
            restoring = {"value": False}
            original_update = gateway.update_service
            original_wait = gateway.wait_service_stable
            original_terminal = gateway.verify_terminal
            original_health = gateway.verify_public_web

            def update_with_secret_failure(
                workload: str,
                target: ServiceTarget,
                predecessors: tuple[ServicePredecessor, ...],
                *,
                deadline: float | None = None,
                timeout_seconds: int | None = None,
                web_runtime_binding: WebRuntimeBinding | None = None,
            ) -> ServiceUpdateReceipt:
                if target.task_definition_arn.endswith(":1"):
                    restoring["value"] = True
                    if selected_failure == "update":
                        raise RuntimeError("sentinel-secret-must-not-leak")
                return original_update(
                    workload,
                    target,
                    predecessors,
                    deadline=deadline,
                    timeout_seconds=timeout_seconds,
                    web_runtime_binding=web_runtime_binding,
                )

            def wait_with_secret_failure(
                receipt: ServiceUpdateReceipt,
                *,
                worker_singleton: bool = False,
                timeout_seconds: int | None = None,
                deadline: float | None = None,
                web_runtime_binding: WebRuntimeBinding | None = None,
            ) -> None:
                if restoring["value"] and selected_failure == "wait":
                    raise RuntimeError("sentinel-secret-must-not-leak")
                original_wait(
                    receipt,
                    worker_singleton=worker_singleton,
                    timeout_seconds=timeout_seconds,
                    deadline=deadline,
                    web_runtime_binding=web_runtime_binding,
                )

            def terminal_with_secret_failure(*args, **kwargs):  # type: ignore[no-untyped-def]
                if restoring["value"] and selected_failure == "terminal":
                    raise RuntimeError("sentinel-secret-must-not-leak")
                return original_terminal(*args, **kwargs)

            def health_with_secret_failure(
                identity: ReleaseIdentity,
                *,
                phase_deadline: float | None = None,
            ) -> None:
                if (
                    restoring["value"]
                    and identity.source_sha == SHA_A
                    and selected_failure == "health"
                ):
                    raise RuntimeError("sentinel-secret-must-not-leak")
                original_health(identity, phase_deadline=phase_deadline)

            gateway.update_service = update_with_secret_failure  # type: ignore[method-assign]
            gateway.wait_service_stable = wait_with_secret_failure  # type: ignore[method-assign]
            gateway.verify_terminal = terminal_with_secret_failure  # type: ignore[method-assign]
            gateway.verify_public_web = health_with_secret_failure  # type: ignore[method-assign]

        for failure_point in ("update", "wait", "terminal", "health"):
            gateway = FakeGateway(bootstrap=False, fail_once="smoke")
            instrument_secret_failure(gateway, failure_point)
            with (
                self.subTest(failure_point=failure_point),
                self._temporary_directory() as directory,
            ):
                config = self._config(directory, prior=successful_record())
                with self.assertRaises(CompensationError) as caught:
                    promote(gateway, config)
                assert config.evidence_path is not None
                evidence_text = config.evidence_path.read_text()
                evidence = __import__("json").loads(evidence_text)
            self.assertNotIn("sentinel-secret-must-not-leak", str(caught.exception))
            self.assertIn("RuntimeError", str(caught.exception))
            self.assertEqual(caught.exception.reason_code, "contract_contradiction")
            self.assertNotIn("sentinel-secret-must-not-leak", evidence_text)
            recorded_receipts = [
                item["proof"]
                for item in evidence["stages"]
                if item["stage"] == "compensation_receipt"
            ]
            self.assertEqual(
                recorded_receipts,
                [item.as_evidence() for item in caught.exception.receipt_summaries],
            )
            self.assertTrue(
                all(
                    set(item) == {"workload", "receipt_id", "receipt_binding", "carried_terminal"}
                    for item in recorded_receipts
                )
            )

            parser = argparse.ArgumentParser()
            parser.parse_args = lambda: SimpleNamespace(  # type: ignore[assignment]
                handler=lambda _arguments: (_ for _ in ()).throw(caught.exception),
                command="promote",
            )
            stderr = io.StringIO()
            with (
                patch("deploy.cli.build_parser", return_value=parser),
                redirect_stderr(stderr),
                self.assertRaises(SystemExit),
            ):
                release_cli_main()
            self.assertNotIn("sentinel-secret-must-not-leak", stderr.getvalue())
            self.assertIn("RuntimeError", stderr.getvalue())

    def test_compensation_preserves_deadline_reason_without_raw_failure_text(self) -> None:
        gateway = FakeGateway(bootstrap=False, fail_once="smoke")
        original_wait = gateway.wait_service_stable

        def expire_restorative_wait(
            receipt: ServiceUpdateReceipt,
            *,
            worker_singleton: bool = False,
            timeout_seconds: int | None = None,
            deadline: float | None = None,
            web_runtime_binding: WebRuntimeBinding | None = None,
        ) -> None:
            if receipt.target.task_definition_arn.endswith(":1"):
                raise ReleaseContractError(
                    "sentinel-provider-deadline-payload-must-not-leak",
                    reason_code="receipt_deadline_expired",
                )
            original_wait(
                receipt,
                worker_singleton=worker_singleton,
                timeout_seconds=timeout_seconds,
                deadline=deadline,
                web_runtime_binding=web_runtime_binding,
            )

        gateway.wait_service_stable = expire_restorative_wait  # type: ignore[method-assign]
        with self._temporary_directory() as directory:
            config = self._config(directory, prior=successful_record())
            with self.assertRaises(CompensationError) as caught:
                promote(gateway, config)
            assert config.evidence_path is not None
            evidence_text = config.evidence_path.read_text()
            stages = __import__("json").loads(evidence_text)["stages"]
        self.assertEqual(caught.exception.reason_code, "receipt_deadline_expired")
        self.assertIn("reason_code=receipt_deadline_expired", str(caught.exception))
        self.assertNotIn("sentinel-provider-deadline-payload", str(caught.exception))
        self.assertNotIn("sentinel-provider-deadline-payload", evidence_text)
        compensation_failure = next(
            item
            for item in stages
            if item["stage"] == "compensation" and item["result"] == "failed"
        )
        self.assertEqual(
            compensation_failure["proof"],
            {
                "error_class": "CompensationError",
                "reason_code": "receipt_deadline_expired",
            },
        )
        self.assertEqual(
            [item.workload for item in caught.exception.receipt_summaries],
            ["web", "worker"],
        )

    def test_compensation_runs_when_its_evidence_marker_cannot_be_written(self) -> None:
        import deploy.release as release_module

        gateway = FakeGateway(bootstrap=False, fail_once="wait:web")
        original = release_module._record_evidence

        def fail_recovery_marker(path, stage, result, proof=None):  # type: ignore[no-untyped-def]
            if stage == "compensation":
                raise OSError("sentinel-secret-must-not-leak")
            return original(path, stage, result, proof)

        with (
            self._temporary_directory() as directory,
            patch("deploy.release._record_evidence", side_effect=fail_recovery_marker),
            self.assertRaisesMessage(ReleaseContractError, "injected wait:web"),
        ):
            promote(gateway, self._config(directory, prior=successful_record()))
        self.assertIn(f"update:web:{arn('web', 1)}:1", gateway.operations)
        self.assertNotIn(f"update:worker:{arn('worker', 1)}:1", gateway.operations)
        self.assertEqual(gateway.snapshots["web"].task_definition_arn, arn("web", 1))
        self.assertEqual(gateway.snapshots["worker"].task_definition_arn, arn("worker", 1))

    def test_auto_deploy_capture_synthesizes_only_a_stable_managed_release(self) -> None:
        gateway = FakeGateway(bootstrap=False)
        with self._temporary_directory() as directory:
            path = Path(directory) / "captured.json"
            pair = capture_current_service_pair(
                gateway,
                REPOSITORY,
                path,
                expected_web_count=1,
                expected_worker_count=1,
            )

            self.assertEqual(ActiveServicePair.read(path), pair)
        self.assertEqual(pair.source_sha, RELEASE_A_SHA)
        self.assertFalse(hasattr(pair, "migration_task_definition_arn"))
        self.assertIn(f"verify-pair:{RELEASE_A_SHA}:{DIGEST_A}", gateway.operations)
        self.assertIn("terminal", gateway.operations)

    def test_auto_deploy_capture_rejects_bootstrap_unstable_and_mixed_state(self) -> None:
        bootstrap = FakeGateway(bootstrap=True)
        unstable = FakeGateway(bootstrap=False)
        unstable.snapshots["web"] = ServiceSnapshot(
            service_name="service-web",
            task_definition_arn=arn("web", 1),
            desired_count=1,
            running_count=0,
            pending_count=1,
            source_sha=SHA_A,
            image_digest=DIGEST_A,
            primary_deployment_id=deployment_id("web", 1),
            version=VERSION_A,
            identity_schema=2,
        )
        mixed = FakeGateway(bootstrap=False)
        mixed.snapshots["worker"] = ServiceSnapshot(
            service_name="service-worker",
            task_definition_arn=arn("worker", 1),
            desired_count=1,
            running_count=1,
            pending_count=0,
            source_sha="c" * 40,
            image_digest=DIGEST_A,
            primary_deployment_id=deployment_id("worker", 1),
            version=version_for("c" * 40),
            identity_schema=2,
        )

        cases = (
            (bootstrap, "bootstrap-disabled"),
            (unstable, "stable web"),
            (mixed, "mixed or missing"),
        )
        for gateway, message in cases:
            with (
                self.subTest(message=message),
                self._temporary_directory() as directory,
                self.assertRaisesMessage(ReleaseContractError, message),
            ):
                capture_current_service_pair(
                    gateway,
                    REPOSITORY,
                    Path(directory) / "captured.json",
                    expected_web_count=1,
                    expected_worker_count=1,
                )
            self.assertFalse(any(value.startswith("register:") for value in gateway.operations))
            self.assertFalse(any(value.startswith("update:") for value in gateway.operations))

    def test_enabled_recovery_checkpoint_requires_an_accepted_prior(self) -> None:
        with (
            self._temporary_directory() as directory,
            self.assertRaisesMessage(
                ReleaseContractError,
                "enabled recovery checkpoint requires the accepted prior release",
            ),
        ):
            capture_recovery_context(
                FakeGateway(bootstrap=False),
                REPOSITORY,
                Path(directory) / "checkpoint.json",
                None,
            )

    def test_manual_rollback_uses_no_migration_and_compensates_on_failure(self) -> None:
        gateway = FakeGateway(bootstrap=False)
        current = successful_record()
        target = ReleaseRecord(
            source_sha=SHA_B,
            image_digest=DIGEST_B,
            web_task_definition_arn=arn("web", 2),
            worker_task_definition_arn=arn("worker", 2),
            migration_task_definition_arn=arn("migration", 2),
            web_desired_count=1,
            worker_desired_count=1,
            rollback_eligible=True,
            version=VERSION_B,
        )
        with self._temporary_directory() as directory:
            rollback(
                gateway,
                target,
                current,
                REPOSITORY,
                Path(directory) / "rolled-back.json",
            )
        self.assertFalse(any(value.startswith("migrate:") for value in gateway.operations))
        self.assertLess(
            gateway.operations.index(f"update:web:{arn('web', 2)}:1"),
            gateway.operations.index(f"update:worker:{arn('worker', 2)}:1"),
        )
        self.assertIn(
            f"wait:web:singleton=False:timeout=240:task={arn('web', 2)}:desired=1",
            gateway.operations,
        )
        self.assertTrue(
            any(
                value.startswith("wait:worker:singleton=True:timeout=420:")
                for value in gateway.operations
            )
        )
        establish = gateway.operations.index("coherence:establish:180")
        pre_worker = next(
            index
            for index, value in enumerate(gateway.operations)
            if value.startswith("coherence:revalidate:420:")
        )
        worker_update = gateway.operations.index(f"update:worker:{arn('worker', 2)}:1")
        smoke = gateway.operations.index(f"smoke:{VERSION_B}:{SHA_B}")
        terminal_guard = next(
            index
            for index, value in enumerate(gateway.operations)
            if value.startswith("guard:terminal:180:")
        )
        self.assertLess(establish, pre_worker)
        self.assertLess(pre_worker, worker_update)
        self.assertLess(worker_update, smoke)
        self.assertLess(smoke, terminal_guard)

    def test_each_rollback_failure_compensates_to_current_exact_pair(self) -> None:
        current = successful_record()
        target = ReleaseRecord(
            source_sha=SHA_B,
            image_digest=DIGEST_B,
            web_task_definition_arn=arn("web", 2),
            worker_task_definition_arn=arn("worker", 2),
            migration_task_definition_arn=arn("migration", 2),
            web_desired_count=1,
            worker_desired_count=1,
            rollback_eligible=True,
            version=VERSION_B,
        )
        for failure in (
            "update:web",
            "wait:web",
            "health",
            "update:worker",
            "wait:worker",
            "smoke",
            "terminal",
        ):
            with self.subTest(failure=failure), self._temporary_directory() as directory:
                gateway = FakeGateway(bootstrap=False, fail_once=failure)
                with self.assertRaises(ReleaseContractError):
                    rollback(
                        gateway,
                        target,
                        current,
                        REPOSITORY,
                        Path(directory) / "release.json",
                    )
                self.assertEqual(gateway.snapshots["web"].task_definition_arn, arn("web", 1))
                self.assertEqual(gateway.snapshots["worker"].task_definition_arn, arn("worker", 1))

    def test_rollback_web_timeout_compensates_with_general_service_budget(self) -> None:
        current = successful_record()
        target = ReleaseRecord(
            source_sha=SHA_B,
            image_digest=DIGEST_B,
            web_task_definition_arn=arn("web", 2),
            worker_task_definition_arn=arn("worker", 2),
            migration_task_definition_arn=arn("migration", 2),
            web_desired_count=1,
            worker_desired_count=1,
            rollback_eligible=True,
            version=VERSION_B,
        )
        gateway = FakeGateway(bootstrap=False, fail_once="wait:web")
        with self._temporary_directory() as directory:
            release_path = Path(directory) / "release.json"
            with self.assertRaisesMessage(ReleaseContractError, "injected wait:web"):
                rollback(gateway, target, current, REPOSITORY, release_path)
            self.assertFalse(release_path.exists())

        self.assertIn(
            f"wait:web:singleton=False:timeout=240:task={arn('web', 2)}:desired=1",
            gateway.operations,
        )
        recovery_waits = [
            value
            for value in gateway.operations
            if value.startswith("wait:") and ":timeout=None:" in value
        ]
        self.assertEqual(len(recovery_waits), 1)
        self.assertIn(
            f"wait:web:singleton=False:timeout=None:task={arn('web', 1)}:desired=1",
            recovery_waits,
        )
        self.assertNotIn(f"update:worker:{arn('worker', 2)}:1", gateway.operations)
        self.assertNotIn(f"update:worker:{arn('worker', 1)}:1", gateway.operations)
        self.assertNotIn(f"health:{SHA_B}", gateway.operations)
        self.assertEqual(gateway.snapshots["web"].task_definition_arn, arn("web", 1))
        self.assertEqual(gateway.snapshots["worker"].task_definition_arn, arn("worker", 1))

    def test_finalization_recovery_restores_enabled_and_bootstrap_pairs(self) -> None:
        failed = ReleaseRecord(
            source_sha=SHA_B,
            image_digest=DIGEST_B,
            web_task_definition_arn=arn("web", 2),
            worker_task_definition_arn=arn("worker", 2),
            migration_task_definition_arn=arn("migration", 2),
            web_desired_count=1,
            worker_desired_count=1,
            rollback_eligible=True,
            version=VERSION_B,
        )
        contexts = (
            RecoveryContext(
                REPOSITORY,
                SHA_A,
                DIGEST_A,
                arn("web", 1),
                arn("worker", 1),
                1,
                1,
                VERSION_A,
            ),
            RecoveryContext(REPOSITORY, None, None, arn("web", 1), arn("worker", 1), 0, 0),
        )
        for context in contexts:
            gateway = FakeGateway(bootstrap=False)
            for workload in ("web", "worker"):
                snapshot = gateway.snapshots[workload]
                gateway.update_service(
                    workload,
                    ServiceTarget(arn(workload, 2), 1),
                    (
                        ServicePredecessor(
                            ServiceTarget(snapshot.task_definition_arn, snapshot.desired_count),
                            snapshot.primary_deployment_id,
                            "terminal",
                        ),
                    ),
                )
            with self.subTest(count=context.web_desired_count):
                summaries = restore_after_finalization_failure(gateway, context, failed)
                self.assertEqual(
                    [item.as_evidence() for item in summaries],
                    [
                        {
                            "workload": "web",
                            "receipt_id": "ecs-svc/web-4",
                            "receipt_binding": "complete_receipt",
                            "carried_terminal": False,
                        },
                        {
                            "workload": "worker",
                            "receipt_id": "ecs-svc/worker-5",
                            "receipt_binding": "complete_receipt",
                            "carried_terminal": False,
                        },
                    ],
                )
                self.assertEqual(
                    gateway.snapshots["web"].task_definition_arn,
                    context.web_task_definition_arn,
                )
                self.assertEqual(
                    gateway.snapshots["worker"].desired_count,
                    context.worker_desired_count,
                )
                self.assertFalse(any(":timeout=240:" in value for value in gateway.operations))
                self.assertTrue(
                    all(
                        ":timeout=None:" in value
                        for value in gateway.operations
                        if value.startswith("wait:")
                    )
                )
                restore_web_update = (
                    f"update:web:{context.web_task_definition_arn}:{context.web_desired_count}"
                )
                restore_worker_update = (
                    f"update:worker:{context.worker_task_definition_arn}:"
                    f"{context.worker_desired_count}"
                )
                self.assertIn(restore_web_update, gateway.operations)
                self.assertIn(restore_worker_update, gateway.operations)
                self.assertIn(
                    "update-predecessors:web:ecs-svc/web-2",
                    gateway.operations,
                )
                self.assertIn(
                    "update-predecessors:worker:ecs-svc/worker-3",
                    gateway.operations,
                )
                self.assertIn("wait-receipt:web:ecs-svc/web-4", gateway.operations)
                self.assertIn("wait-receipt:worker:ecs-svc/worker-5", gateway.operations)

    def test_finalization_postbinding_failure_exposes_only_safe_receipt_summaries(self) -> None:
        failed = ReleaseRecord(
            source_sha=SHA_B,
            image_digest=DIGEST_B,
            web_task_definition_arn=arn("web", 2),
            worker_task_definition_arn=arn("worker", 2),
            migration_task_definition_arn=arn("migration", 2),
            web_desired_count=1,
            worker_desired_count=1,
            rollback_eligible=True,
            version=VERSION_B,
        )
        context = RecoveryContext(
            REPOSITORY,
            SHA_A,
            DIGEST_A,
            arn("web", 1),
            arn("worker", 1),
            1,
            1,
            VERSION_A,
        )
        gateway = FakeGateway(bootstrap=False)
        for workload in ("web", "worker"):
            snapshot = gateway.snapshots[workload]
            gateway.update_service(
                workload,
                ServiceTarget(arn(workload, 2), 1),
                (
                    ServicePredecessor(
                        ServiceTarget(snapshot.task_definition_arn, snapshot.desired_count),
                        snapshot.primary_deployment_id,
                        "terminal",
                    ),
                ),
            )
        original_wait = gateway.wait_service_stable

        def fail_restorative_web_wait(
            receipt: ServiceUpdateReceipt,
            *,
            worker_singleton: bool = False,
            timeout_seconds: int | None = None,
            deadline: float | None = None,
            web_runtime_binding: WebRuntimeBinding | None = None,
        ) -> None:
            if receipt.workload == "web" and receipt.target.task_definition_arn.endswith(":1"):
                raise RuntimeError("sentinel-provider-payload-must-not-leak")
            original_wait(
                receipt,
                worker_singleton=worker_singleton,
                timeout_seconds=timeout_seconds,
                deadline=deadline,
                web_runtime_binding=web_runtime_binding,
            )

        gateway.wait_service_stable = fail_restorative_web_wait  # type: ignore[method-assign]
        with self.assertRaises(CompensationError) as caught:
            restore_after_finalization_failure(gateway, context, failed)
        self.assertNotIn("sentinel-provider-payload-must-not-leak", str(caught.exception))
        self.assertEqual(
            [item.as_evidence() for item in caught.exception.receipt_summaries],
            [
                {
                    "workload": "web",
                    "receipt_id": "ecs-svc/web-4",
                    "receipt_binding": "complete_receipt",
                    "carried_terminal": False,
                },
                {
                    "workload": "worker",
                    "receipt_id": "ecs-svc/worker-5",
                    "receipt_binding": "complete_receipt",
                    "carried_terminal": False,
                },
            ],
        )

    def test_finalization_recovery_uses_the_same_bounded_coordinator_and_evidence(self) -> None:
        failed = ReleaseRecord(
            source_sha=SHA_B,
            image_digest=DIGEST_B,
            web_task_definition_arn=arn("web", 2),
            worker_task_definition_arn=arn("worker", 2),
            migration_task_definition_arn=arn("migration", 2),
            web_desired_count=1,
            worker_desired_count=1,
            rollback_eligible=True,
            version=VERSION_B,
        )
        context = RecoveryContext(
            REPOSITORY,
            SHA_A,
            DIGEST_A,
            arn("web", 1),
            arn("worker", 1),
            1,
            1,
            VERSION_A,
        )
        gateway = FakeGateway(bootstrap=False)
        for workload in ("web", "worker"):
            snapshot = gateway.snapshots[workload]
            gateway.update_service(
                workload,
                ServiceTarget(arn(workload, 2), 1),
                (
                    ServicePredecessor(
                        ServiceTarget(snapshot.task_definition_arn, snapshot.desired_count),
                        snapshot.primary_deployment_id,
                        "terminal",
                    ),
                ),
            )
        with self._temporary_directory() as directory:
            evidence_path = Path(directory) / "finalization-evidence.json"
            restore_after_finalization_failure(gateway, context, failed, evidence_path)
            stages = __import__("json").loads(evidence_path.read_text())["stages"]

        plan = next(item for item in stages if item["stage"] == "recovery_plan")
        self.assertEqual(plan["proof"]["mode"], "artifact_finalization")
        self.assertEqual(plan["proof"]["eligible_workloads"], ["web", "worker"])
        self.assertEqual(
            [
                item["proof"]["workload"]
                for item in stages
                if item["stage"] == "finalization_receipt"
            ],
            ["web", "worker"],
        )
        total = next(item for item in stages if item["stage"] == "recovery_total")
        self.assertEqual(total["result"], "passed")

    def test_finalization_failure_uses_safe_deterministic_restorative_reason(self) -> None:
        failed = ReleaseRecord(
            source_sha=SHA_B,
            image_digest=DIGEST_B,
            web_task_definition_arn=arn("web", 2),
            worker_task_definition_arn=arn("worker", 2),
            migration_task_definition_arn=arn("migration", 2),
            web_desired_count=1,
            worker_desired_count=1,
            rollback_eligible=True,
            version=VERSION_B,
        )
        context = RecoveryContext(
            REPOSITORY,
            SHA_A,
            DIGEST_A,
            arn("web", 1),
            arn("worker", 1),
            1,
            1,
            VERSION_A,
        )
        cases = (
            ("deadline", "receipt_deadline_expired"),
            ("mixed", "contract_contradiction"),
            ("unknown", "contract_contradiction"),
        )
        for failure_kind, expected_reason in cases:
            gateway = FakeGateway(bootstrap=False)
            for workload in ("web", "worker"):
                snapshot = gateway.snapshots[workload]
                gateway.update_service(
                    workload,
                    ServiceTarget(arn(workload, 2), 1),
                    (
                        ServicePredecessor(
                            ServiceTarget(snapshot.task_definition_arn, snapshot.desired_count),
                            snapshot.primary_deployment_id,
                            "terminal",
                        ),
                    ),
                )
            original_wait = gateway.wait_service_stable

            def fail_restorative_wait(
                receipt: ServiceUpdateReceipt,
                *,
                worker_singleton: bool = False,
                timeout_seconds: int | None = None,
                deadline: float | None = None,
                web_runtime_binding: WebRuntimeBinding | None = None,
                selected_failure_kind: str = failure_kind,
                selected_original_wait: Any = original_wait,
            ) -> None:
                if receipt.target.task_definition_arn.endswith(":1"):
                    if selected_failure_kind == "mixed" and receipt.workload == "worker":
                        raise ReleaseContractError(
                            "sentinel-provider-contradiction-payload-must-not-leak"
                        )
                    error = ReleaseContractError(
                        "sentinel-provider-deadline-payload-must-not-leak",
                        reason_code="receipt_deadline_expired",
                    )
                    if selected_failure_kind == "unknown" and receipt.workload == "worker":
                        error.reason_code = "unknown_provider_reason"  # type: ignore[assignment]
                    raise error
                selected_original_wait(
                    receipt,
                    worker_singleton=worker_singleton,
                    timeout_seconds=timeout_seconds,
                    deadline=deadline,
                    web_runtime_binding=web_runtime_binding,
                )

            gateway.wait_service_stable = fail_restorative_wait  # type: ignore[method-assign]
            with (
                self.subTest(failure_kind=failure_kind),
                self.assertRaises(CompensationError) as caught,
            ):
                restore_after_finalization_failure(gateway, context, failed)
            self.assertEqual(caught.exception.reason_code, expected_reason)
            self.assertIn(f"reason_code={expected_reason}", str(caught.exception))
            self.assertNotIn("sentinel-provider", str(caught.exception))
            self.assertEqual(
                [item.workload for item in caught.exception.receipt_summaries],
                ["web", "worker"],
            )

            parser = argparse.ArgumentParser()
            parser.parse_args = lambda: SimpleNamespace(  # type: ignore[assignment]
                handler=lambda _arguments: (_ for _ in ()).throw(caught.exception),
                command="restore-finalization",
            )
            stderr = io.StringIO()
            with (
                patch("deploy.cli.build_parser", return_value=parser),
                redirect_stderr(stderr),
                self.assertRaises(SystemExit),
            ):
                release_cli_main()
            self.assertIn(f"reason_code={expected_reason}", stderr.getvalue())
            self.assertNotIn("sentinel-provider", stderr.getvalue())

    def test_finalization_recovery_rejects_a_stale_live_pair_before_update(self) -> None:
        gateway = FakeGateway(bootstrap=False)
        context = RecoveryContext(
            REPOSITORY,
            SHA_A,
            DIGEST_A,
            arn("web", 1),
            arn("worker", 1),
            1,
            1,
            VERSION_A,
        )
        failed = ReleaseRecord(
            source_sha=SHA_B,
            image_digest=DIGEST_B,
            web_task_definition_arn=arn("web", 2),
            worker_task_definition_arn=arn("worker", 2),
            migration_task_definition_arn=arn("migration", 2),
            web_desired_count=1,
            worker_desired_count=1,
            rollback_eligible=True,
            version=VERSION_B,
        )
        with self.assertRaisesMessage(ReleaseContractError, "differ from"):
            restore_after_finalization_failure(gateway, context, failed)
        self.assertFalse(any(operation.startswith("update:") for operation in gateway.operations))

    def test_cli_generic_exception_prints_only_the_exception_class(self) -> None:
        parser = argparse.ArgumentParser()
        parser.parse_args = lambda: SimpleNamespace(  # type: ignore[assignment]
            handler=lambda _arguments: (_ for _ in ()).throw(
                RuntimeError("sentinel-secret-must-not-leak")
            ),
            command="promote",
        )
        stderr = io.StringIO()
        with (
            patch("deploy.cli.build_parser", return_value=parser),
            redirect_stderr(stderr),
            self.assertRaises(SystemExit),
        ):
            release_cli_main()
        self.assertIn("RuntimeError", stderr.getvalue())
        self.assertNotIn("sentinel-secret-must-not-leak", stderr.getvalue())

    def test_cli_finalization_success_exposes_only_safe_receipt_summaries(self) -> None:
        parser = argparse.ArgumentParser()
        payload = {
            "status": "restored_prior",
            "identity_schema": 2,
            "version": VERSION_A,
            "source_sha": SHA_A,
            "image_digest": DIGEST_A,
            "restorative_receipts": [
                {
                    "workload": "web",
                    "receipt_id": "ecs-svc/web-restore",
                    "receipt_binding": "partial_acknowledgement_reconciled",
                    "carried_terminal": True,
                }
            ],
        }
        parser.parse_args = lambda: SimpleNamespace(  # type: ignore[assignment]
            handler=lambda _arguments: payload,
            command="restore-finalization",
        )
        stdout = io.StringIO()
        with patch("deploy.cli.build_parser", return_value=parser), redirect_stdout(stdout):
            release_cli_main()
        self.assertEqual(__import__("json").loads(stdout.getvalue()), payload)
        self.assertNotIn("sentinel-provider-payload", stdout.getvalue())

    def test_cli_success_reports_the_complete_release_identity(self) -> None:
        parser = argparse.ArgumentParser()
        parser.parse_args = lambda: SimpleNamespace(  # type: ignore[assignment]
            handler=lambda _arguments: successful_record(),
            command="promote",
        )
        stdout = io.StringIO()
        with patch("deploy.cli.build_parser", return_value=parser), redirect_stdout(stdout):
            release_cli_main()

        self.assertEqual(
            __import__("json").loads(stdout.getvalue()),
            {
                "status": "successful",
                "identity_schema": 2,
                "version": VERSION_A,
                "source_sha": SHA_A,
                "image_digest": DIGEST_A,
            },
        )


class RemoteSmokeSafetyTests(SimpleTestCase):
    def test_remote_smoke_is_restricted_to_the_exact_development_origin(self) -> None:
        self.assertEqual(validate_origin("https://web.dtcdev.click/"), "https://web.dtcdev.click")
        for origin in ("http://web.dtcdev.click", "https://example.com"):
            with self.subTest(origin=origin), self.assertRaises(ReleaseContractError):
                validate_origin(origin)

    def test_http_smoke_checks_safe_404_and_writes_only_redacted_evidence(self) -> None:
        from content.public_views import production_sitemap

        noindex = {"x-robots-tag": ROBOTS_VALUE}
        private = noindex | {"cache-control": "private, no-store"}
        responses = [
            Response(
                200,
                noindex,
                json.dumps(
                    {
                        "status": "ok",
                        "version": VERSION_A,
                        "source_sha": SHA_A,
                        "image_digest": DIGEST_A,
                    }
                ).encode(),
            ),
            Response(
                200,
                noindex,
                json.dumps(
                    {
                        "status": "ready",
                        "version": VERSION_A,
                        "source_sha": SHA_A,
                        "image_digest": DIGEST_A,
                        "checks": {
                            "configuration": {"status": "ok"},
                            "database": {"status": "ok"},
                            "migrations": {"status": "ok"},
                        },
                    }
                ).encode(),
            ),
            Response(
                200,
                noindex,
                b"<title>DataTalks.Club \xe2\x80\x94 free courses for data and AI engineers</title>"
                b"Ship data pipelines and AI systems that run in production."
                b"Free, project-based courses where you build the real thing"
                + f"Version {VERSION_A}".encode()
                + b'<link rel="canonical" href="https://datatalks.club/">'
                + b'<link rel="stylesheet" href="/static/core.fixture.css">',
            ),
            Response(
                200,
                noindex,
                b"Ship data pipelines and AI systems that run in production."
                b'<link rel="canonical" href="https://datatalks.club/">',
            ),
            Response(
                200,
                noindex,
                b"Learn data skills. For free. Together."
                + f"Version {VERSION_A}".encode()
                + b'<link rel="canonical" href="https://datatalks.club/courses">'
                + b'<link rel="stylesheet" href="/static/courses.fixture.css">',
            ),
            Response(
                302,
                private | {"location": "/accounts/login/?next=%2Fstudio%2F"},
                b"",
            ),
            Response(200, private, b"Sign In"),
            Response(
                200,
                noindex,
                json.dumps(
                    {
                        "status": "ok",
                        "version": VERSION_A,
                        "source_sha": SHA_A,
                        "image_digest": DIGEST_A,
                    }
                ).encode(),
            ),
            Response(
                401,
                private
                | {
                    "www-authenticate": "Bearer",
                    "x-request-id": "request-smoke",
                },
                b'{"error":{"code":"authentication_required",'
                b'"message":"Valid Bearer authentication is required.",'
                b'"request_id":"request-smoke"}}',
            ),
            Response(404, noindex, b"Page not found"),
            Response(
                200,
                noindex | {"content-type": "text/plain; charset=utf-8"},
                b"User-agent: *\nDisallow: /\n",
            ),
            Response(
                200,
                noindex | {"content-type": "application/xml; charset=utf-8"},
                production_sitemap().encode(),
            ),
            Response(200, noindex | {"content-type": "text/css"}, b"body{}"),
        ]
        Path(".tmp").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=".tmp") as directory:
            path = Path(directory) / "http-evidence.json"
            with patch("deploy.smoke._request", side_effect=responses) as request:
                evidence = run_http_smoke(
                    "https://web.dtcdev.click", VERSION_A, SHA_A, DIGEST_A, path
                )
            self.assertEqual(
                [call.args[1] for call in request.call_args_list][-4:],
                [
                    "/__dtc_deployed_smoke_missing__",
                    "/robots.txt",
                    "/sitemap.xml",
                    "/static/core.fixture.css",
                ],
            )
            persisted = path.read_text()
            self.assertEqual(__import__("json").loads(persisted), evidence)
            courses_check = next(
                check for check in evidence["checks"] if check.get("path") == "/courses"
            )
            self.assertTrue(courses_check["exact_canonical"])
            self.assertNotIn("canonical_absent", courses_check)
            sitemap_check = next(
                check for check in evidence["checks"] if check.get("path") == "/sitemap.xml"
            )
            self.assertFalse(sitemap_check["empty"])
            self.assertEqual(sitemap_check["kind"], "sitemap_index")
            self.assertEqual(sitemap_check["section_count"], 10)
            self.assertTrue(sitemap_check["canonical_production_locations"])
            self.assertTrue(sitemap_check["unique_locations"])
            self.assertEqual(evidence["checks"][-1]["runtime_group"], "analytics")
            for forbidden in ("cookie", "authorization", "set-cookie", "response_body"):
                self.assertNotIn(forbidden, persisted.lower())

        invalid_surface_responses = (
            (
                "home identity",
                2,
                Response(
                    200,
                    noindex,
                    b"Learn data skills. For free. Together."
                    + f"Version {VERSION_A}".encode()
                    + b'<link rel="stylesheet" href="/static/core.fixture.css">',
                ),
                "home page lacks expected content",
            ),
            (
                "course identity",
                4,
                Response(
                    200,
                    noindex,
                    b"Ship data pipelines and AI systems that run in production."
                    + f"Version {VERSION_A}".encode()
                    + b'<link rel="stylesheet" href="/static/courses.fixture.css">',
                ),
                "course discovery lacks expected content",
            ),
        )
        for case, response_index, invalid_response, error_message in invalid_surface_responses:
            with self.subTest(case=case):
                invalid_responses = [*responses]
                invalid_responses[response_index] = invalid_response
                with (
                    patch("deploy.smoke._request", side_effect=invalid_responses),
                    self.assertRaisesMessage(ReleaseContractError, error_message),
                ):
                    run_http_smoke("https://web.dtcdev.click", VERSION_A, SHA_A, DIGEST_A)

        exact_courses_canonical = b'<link rel="canonical" href="https://datatalks.club/courses">'
        invalid_courses_canonicals = (
            ("missing", b""),
            ("duplicate", exact_courses_canonical * 2),
            (
                "wrong path",
                b'<link rel="canonical" href="https://datatalks.club/courses/wrong/">',
            ),
            (
                "external",
                b'<link rel="canonical" href="https://example.com/courses/">',
            ),
        )
        for case, rendered_canonical in invalid_courses_canonicals:
            invalid_responses = [*responses]
            invalid_responses[4] = Response(
                200,
                noindex,
                b"Learn data skills. For free. Together."
                + f"Version {VERSION_A}".encode()
                + rendered_canonical
                + b'<link rel="stylesheet" href="/static/courses.fixture.css">',
            )
            with (
                self.subTest(case=case),
                patch("deploy.smoke._request", side_effect=invalid_responses),
                self.assertRaisesMessage(
                    ReleaseContractError,
                    "course discovery production canonical differs",
                ),
            ):
                run_http_smoke("https://web.dtcdev.click", VERSION_A, SHA_A, DIGEST_A)

        invalid_admin_responses = (
            (
                Response(
                    401,
                    private | {"x-request-id": "request-smoke"},
                    b'{"error":{"code":"authentication_required",'
                    b'"message":"Valid Bearer authentication is required.",'
                    b'"request_id":"request-smoke"}}',
                ),
                "lacks the Bearer challenge",
            ),
            (
                Response(
                    401,
                    private
                    | {
                        "www-authenticate": "Bearer",
                        "x-request-id": "request-smoke",
                    },
                    b'{"error":{"code":"authentication_required",'
                    b'"message":"Valid Bearer authentication is required.",'
                    b'"request_id":"different-request"}}',
                ),
                "payload differs",
            ),
        )
        for admin_response, error_message in invalid_admin_responses:
            with self.subTest(error_message=error_message):
                invalid_responses = [*responses]
                invalid_responses[8] = admin_response
                with (
                    patch("deploy.smoke._request", side_effect=invalid_responses),
                    self.assertRaisesMessage(ReleaseContractError, error_message),
                ):
                    run_http_smoke("https://web.dtcdev.click", VERSION_A, SHA_A, DIGEST_A)
