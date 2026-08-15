from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from django.test import SimpleTestCase

from core.tests.test_deployment_release import (
    CONTAINERS,
    DIGEST_B,
    EXECUTION_ROLE,
    FAMILIES,
    REPOSITORY,
    SHA_B,
    TASK_ROLE,
    FakeGateway,
    successful_record,
    version_for,
)
from deploy.contracts import (
    CAPTURE_REASON_SCHEMA_VERSION,
    CaptureContractError,
    ReleaseContractError,
    ReleaseIdentity,
    ServicePredecessor,
    WebRuntimeBinding,
)
from deploy.release import (
    PromotionConfig,
    _recapture_prior_before_mutation,
    capture_current_service_pair,
    capture_recovery_context,
)
from deploy.task_definitions import TaskDefinitionConfig


class LateTerminalGateway(FakeGateway):
    def __init__(self, error: Exception) -> None:
        super().__init__(bootstrap=False)
        self.late_error = error

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
        del (
            expected_task_definitions,
            expected_desired_counts,
            expected_identity,
            expected_primary_deployment_ids,
            allowed_predecessors,
            phase_deadline,
            web_runtime_binding,
            web_runtime_deadline,
        )
        self.operations.append("terminal")
        raise self.late_error


class LateTerminalCaptureEvidenceTests(SimpleTestCase):
    def _config(self, directory: str) -> PromotionConfig:
        return PromotionConfig(
            identity=ReleaseIdentity(SHA_B, DIGEST_B, REPOSITORY, version_for(SHA_B)),
            task_definitions=TaskDefinitionConfig(
                families=FAMILIES,
                container_names=CONTAINERS,
                task_role_arn=TASK_ROLE,
                execution_role_arn=EXECUTION_ROLE,
            ),
            web_desired_count=1,
            worker_desired_count=1,
            project_tag="website",
            environment_tag="test",
            release_record_path=Path(directory) / "release.json",
            expected_prior_release=successful_record(),
            evidence_path=Path(directory) / "evidence.json",
            recovery_context_path=Path(directory) / "recovery.json",
        )

    def _invoke(
        self,
        entry_point: str,
        gateway: LateTerminalGateway,
        directory: str,
    ) -> None:
        evidence_path = Path(directory) / "evidence.json"
        if entry_point == "current":
            pair_path = Path(directory) / "pair.json"
            capture_current_service_pair(
                gateway,
                REPOSITORY,
                pair_path,
                expected_web_count=1,
                expected_worker_count=1,
                evidence_path=evidence_path,
            )
            return
        if entry_point == "recovery":
            recovery_path = Path(directory) / "recovery.json"
            capture_recovery_context(
                gateway,
                REPOSITORY,
                recovery_path,
                successful_record(),
                evidence_path=evidence_path,
            )
            return
        if entry_point == "recapture":
            config = self._config(directory)
            _recapture_prior_before_mutation(
                gateway,
                config,
                initial_bootstrap=False,
                prior_identity=ReleaseIdentity(SHA_B, DIGEST_B, REPOSITORY, version_for(SHA_B)),
            )
            return
        raise AssertionError(f"unknown entry point: {entry_point}")

    def _protected_paths(self, entry_point: str, directory: str) -> tuple[Path, ...]:
        if entry_point == "current":
            return (Path(directory) / "pair.json",)
        if entry_point == "recovery":
            return (Path(directory) / "recovery.json",)
        if entry_point == "recapture":
            return (Path(directory) / "release.json", Path(directory) / "recovery.json")
        raise AssertionError(f"unknown entry point: {entry_point}")

    def test_late_terminal_failures_are_redacted_and_preserve_original_errors(self) -> None:
        failure_specs: tuple[tuple[str, type[Exception], dict[str, Any]], ...] = (
            (
                "capture",
                CaptureContractError,
                {
                    "reason_schema_version": CAPTURE_REASON_SCHEMA_VERSION,
                    "reason_code": "service_running_mismatch",
                    "workload": "web",
                },
            ),
            (
                "release",
                ReleaseContractError,
                {
                    "error_class": "ReleaseContractError",
                    "reason_code": "contract_contradiction",
                },
            ),
            (
                "unexpected",
                RuntimeError,
                {"error_class": "RuntimeError"},
            ),
        )
        for entry_point in ("current", "recovery", "recapture"):
            for failure_kind, error_type, expected_proof in failure_specs:
                with self.subTest(entry_point=entry_point, failure_kind=failure_kind):
                    error: Exception
                    if failure_kind == "capture":
                        error = CaptureContractError(
                            "sentinel-provider-payload",
                            workload="web",
                            reason_code="service_running_mismatch",
                        )
                    elif failure_kind == "release":
                        error = ReleaseContractError("sentinel-provider-payload")
                    else:
                        error = RuntimeError("sentinel-provider-payload")
                    gateway = LateTerminalGateway(error)
                    Path(".tmp").mkdir(exist_ok=True)
                    with tempfile.TemporaryDirectory(dir=".tmp") as directory:
                        evidence_path = Path(directory) / "evidence.json"
                        protected_paths = self._protected_paths(entry_point, directory)
                        with self.assertRaises(error_type) as caught:
                            self._invoke(
                                entry_point,
                                gateway,
                                directory,
                            )
                        self.assertIs(caught.exception, error)
                        self.assertEqual(gateway.operations.count("terminal"), 1)
                        self.assertFalse(
                            any(
                                operation.startswith(("register:", "migrate:", "update:"))
                                for operation in gateway.operations
                            )
                        )
                        self.assertTrue(all(not path.exists() for path in protected_paths))
                        evidence_text = evidence_path.read_text()
                        evidence = json.loads(evidence_text)
                    self.assertEqual(len(evidence["stages"]), 1)
                    self.assertEqual(
                        evidence["stages"][0],
                        {
                            "stage": "capture:terminal",
                            "result": "failed",
                            "proof": expected_proof,
                            "timestamp": evidence["stages"][0]["timestamp"],
                        },
                    )
                    self.assertNotIn("sentinel-provider-payload", evidence_text)
