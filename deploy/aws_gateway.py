from __future__ import annotations

import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import boto3  # type: ignore[import-untyped]

from deploy.contracts import (
    IMAGE_DIGEST_PATTERN,
    MAX_RECOVERY_PHASE_TIMEOUT_SECONDS,
    MAX_WEB_RECOVERY_TIMEOUT_SECONDS,
    MAX_WORKER_RECOVERY_TIMEOUT_SECONDS,
    RECOVERY_PHASE_TIMEOUT_SECONDS,
    SOURCE_SHA_PATTERN,
    WEB_RECOVERY_TIMEOUT_SECONDS,
    WORKER_RECOVERY_TIMEOUT_SECONDS,
    ActiveServicePair,
    ReleaseContractError,
    ReleaseIdentity,
    ReleaseRecord,
    ServicePredecessor,
    ServiceSnapshot,
    ServiceTarget,
    ServiceUpdateReceipt,
    WebRuntimeBinding,
    validate_image_digest,
    validate_rfc1918_ipv4,
    validate_source_sha,
    validate_version,
)
from deploy.legacy_development_compatibility import (
    ECR_REPOSITORY_NAME,
    ECR_REPOSITORY_URI,
    RESOURCE_ENVIRONMENT_TAG,
)
from deploy.smoke import run_http_smoke, verify_health
from deploy.task_definitions import (
    TaskDefinitionConfig,
    assert_normalized_service_pair,
    assert_normalized_task_definitions,
)

CONTROLLED_MIGRATION_FAILURE_COMMAND = ["__dtc_controlled_migration_failure__"]
MIGRATION_SHUTDOWN_TIMEOUT_SECONDS = 120
DEPLOYED_BROWSER_TIMEOUT_SECONDS = 180
MAX_STAGE_TIMEOUT_SECONDS = 180
WEB_STABILIZATION_TIMEOUT_SECONDS = 240
MAX_WEB_STABILIZATION_TIMEOUT_SECONDS = 240
WORKER_STABILIZATION_TIMEOUT_SECONDS = 420
MAX_WORKER_STABILIZATION_TIMEOUT_SECONDS = 420


@dataclass(frozen=True)
class AwsReleaseConfig:
    region: str
    cluster_arn: str
    web_target_group_arn: str
    service_names: dict[str, str]
    task_families: dict[str, str]
    container_names: dict[str, str]
    task_role_arn: str
    execution_role_arn: str
    subnet_ids: list[str]
    security_group_ids: list[str]
    assign_public_ip: bool
    base_url: str
    screenshot_directory: Path
    timeout_seconds: int = MAX_STAGE_TIMEOUT_SECONDS
    web_stabilization_timeout_seconds: int = WEB_STABILIZATION_TIMEOUT_SECONDS
    worker_stabilization_timeout_seconds: int = WORKER_STABILIZATION_TIMEOUT_SECONDS
    web_recovery_timeout_seconds: int = WEB_RECOVERY_TIMEOUT_SECONDS
    worker_recovery_timeout_seconds: int = WORKER_RECOVERY_TIMEOUT_SECONDS
    recovery_phase_timeout_seconds: int = RECOVERY_PHASE_TIMEOUT_SECONDS
    poll_seconds: int = 10

    def __post_init__(self) -> None:
        integer_timeouts = {
            "stage": self.timeout_seconds,
            "web stabilization": self.web_stabilization_timeout_seconds,
            "worker stabilization": self.worker_stabilization_timeout_seconds,
            "web recovery": self.web_recovery_timeout_seconds,
            "worker recovery": self.worker_recovery_timeout_seconds,
            "recovery phase": self.recovery_phase_timeout_seconds,
            "poll": self.poll_seconds,
        }
        if any(type(value) is not int or value < 1 for value in integer_timeouts.values()):
            raise ReleaseContractError("timeouts must be positive integers")
        if self.timeout_seconds > MAX_STAGE_TIMEOUT_SECONDS:
            raise ReleaseContractError(
                "development stage timeout exceeds the recovery-safe maximum"
            )
        if self.web_stabilization_timeout_seconds > MAX_WEB_STABILIZATION_TIMEOUT_SECONDS:
            raise ReleaseContractError(
                "web stabilization timeout exceeds the recovery-safe maximum"
            )
        if self.worker_stabilization_timeout_seconds > MAX_WORKER_STABILIZATION_TIMEOUT_SECONDS:
            raise ReleaseContractError(
                "worker stabilization timeout exceeds the recovery-safe maximum"
            )
        if self.web_recovery_timeout_seconds > MAX_WEB_RECOVERY_TIMEOUT_SECONDS:
            raise ReleaseContractError("web recovery timeout exceeds the recovery-safe maximum")
        if self.worker_recovery_timeout_seconds > MAX_WORKER_RECOVERY_TIMEOUT_SECONDS:
            raise ReleaseContractError("worker recovery timeout exceeds the recovery-safe maximum")
        if self.recovery_phase_timeout_seconds > MAX_RECOVERY_PHASE_TIMEOUT_SECONDS:
            raise ReleaseContractError("recovery phase timeout exceeds the recovery-safe maximum")
        if self.recovery_phase_timeout_seconds < max(
            self.web_recovery_timeout_seconds,
            self.worker_recovery_timeout_seconds,
        ):
            raise ReleaseContractError("recovery phase cannot contain the workload budgets")
        if self.poll_seconds > self.timeout_seconds:
            raise ReleaseContractError("poll interval must not exceed the stage timeout")
        if self.poll_seconds > self.web_stabilization_timeout_seconds:
            raise ReleaseContractError(
                "poll interval must not exceed the web stabilization timeout"
            )
        if self.poll_seconds > self.worker_stabilization_timeout_seconds:
            raise ReleaseContractError(
                "poll interval must not exceed the worker stabilization timeout"
            )
        if self.poll_seconds > self.web_recovery_timeout_seconds:
            raise ReleaseContractError("poll interval must not exceed the web recovery timeout")
        if self.poll_seconds > self.worker_recovery_timeout_seconds:
            raise ReleaseContractError("poll interval must not exceed the worker recovery timeout")
        if self.poll_seconds > self.recovery_phase_timeout_seconds:
            raise ReleaseContractError("poll interval must not exceed the recovery phase timeout")


@dataclass(frozen=True)
class _DeploymentPhaseProof:
    predecessors_have_zero_work: bool
    candidate_is_exact_terminal_primary: bool


class AwsReleaseGateway:
    def __init__(self, config: AwsReleaseConfig) -> None:
        self.config = config
        self.ecs = boto3.client("ecs", region_name=config.region)
        self.ecr = boto3.client("ecr", region_name=config.region)
        self.elbv2 = boto3.client("elbv2", region_name=config.region)

    @property
    def web_stabilization_timeout_seconds(self) -> int:
        return self.config.web_stabilization_timeout_seconds

    @property
    def web_coherence_timeout_seconds(self) -> int:
        return self.config.timeout_seconds

    @property
    def worker_stabilization_timeout_seconds(self) -> int:
        return self.config.worker_stabilization_timeout_seconds

    @property
    def web_recovery_timeout_seconds(self) -> int:
        return self.config.web_recovery_timeout_seconds

    @property
    def worker_recovery_timeout_seconds(self) -> int:
        return self.config.worker_recovery_timeout_seconds

    @property
    def recovery_phase_timeout_seconds(self) -> int:
        return self.config.recovery_phase_timeout_seconds

    def service_stabilization_deadline(self, timeout_seconds: int | None = None) -> float:
        selected = self.config.timeout_seconds if timeout_seconds is None else timeout_seconds
        allowed = {
            self.config.timeout_seconds,
            self.config.web_stabilization_timeout_seconds,
            self.config.worker_stabilization_timeout_seconds,
        }
        if type(selected) is not int or selected not in allowed:
            raise ReleaseContractError("service stabilization deadline budget differs")
        return time.monotonic() + selected

    def recovery_phase_deadline(self) -> float:
        return time.monotonic() + self.config.recovery_phase_timeout_seconds

    def recovery_workload_deadline(self, workload: str, phase_deadline: float) -> float:
        phase_deadline = self._validate_stabilization_deadline(
            phase_deadline,
            maximum_seconds=self.config.recovery_phase_timeout_seconds,
        )
        self._require_deadline_remaining(phase_deadline, context="recovery phase")
        if workload == "web":
            budget = self.config.web_recovery_timeout_seconds
        elif workload == "worker":
            budget = self.config.worker_recovery_timeout_seconds
        else:
            raise ReleaseContractError("recovery workload differs")
        return min(time.monotonic() + budget, phase_deadline)

    def ensure_recovery_phase(self, phase_deadline: float) -> None:
        phase_deadline = self._validate_stabilization_deadline(
            phase_deadline,
            maximum_seconds=self.config.recovery_phase_timeout_seconds,
        )
        self._require_not_after_deadline(phase_deadline, context="recovery phase")

    def _validate_recovery_deadline(self, workload: str, deadline: object) -> float:
        if workload == "web":
            maximum = self.config.web_recovery_timeout_seconds
        elif workload == "worker":
            maximum = self.config.worker_recovery_timeout_seconds
        else:
            raise ReleaseContractError("recovery workload differs")
        return self._validate_stabilization_deadline(
            deadline,
            maximum_seconds=maximum,
        )

    @staticmethod
    def _validate_stabilization_deadline(
        deadline: object,
        *,
        maximum_seconds: int,
    ) -> float:
        now = time.monotonic()
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(deadline)
            or deadline > now + maximum_seconds
        ):
            raise ReleaseContractError("service stabilization deadline differs")
        return float(deadline)

    @staticmethod
    def _require_deadline_remaining(deadline: float, *, context: str) -> None:
        if time.monotonic() >= deadline:
            raise ReleaseContractError(
                f"{context} deadline expired",
                reason_code="receipt_deadline_expired",
            )

    @staticmethod
    def _require_not_after_deadline(deadline: float, *, context: str) -> None:
        if time.monotonic() > deadline:
            raise ReleaseContractError(
                f"{context} deadline expired",
                reason_code="receipt_deadline_expired",
            )

    def _service(self, workload: str) -> dict[str, Any]:
        response = self.ecs.describe_services(
            cluster=self.config.cluster_arn,
            services=[self.config.service_names[workload]],
        )
        failures = response.get("failures", [])
        services = response.get("services", [])
        if failures or len(services) != 1:
            raise ReleaseContractError(
                f"cannot describe exact {workload} service "
                f"(failure_count={len(failures)}, service_count={len(services)})"
            )
        return services[0]

    def _task_definition(self, reference: str) -> dict[str, Any]:
        task, _tags = self._task_definition_with_tags(reference)
        return task

    def _task_definition_with_tags(
        self, reference: str
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        response = self.ecs.describe_task_definition(taskDefinition=reference, include=["TAGS"])
        task = response.get("taskDefinition")
        if not isinstance(task, dict):
            raise ReleaseContractError(f"task definition {reference} is missing")
        tags = response.get("tags")
        if not isinstance(tags, list):
            raise ReleaseContractError(f"task definition {reference} has no readable tags")
        return task, tags

    def _identity(
        self, task_definition_arn: str, workload: str
    ) -> tuple[str | None, str | None, str | None, int | None]:
        task = self._task_definition(task_definition_arn)
        containers = task.get("containerDefinitions", [])
        matches = [
            item for item in containers if item.get("name") == self.config.container_names[workload]
        ]
        if len(matches) != 1:
            raise ReleaseContractError(f"{workload} task does not have its exact container")
        container = matches[0]
        raw_environment = container.get("environment", [])
        identity_names = {"VERSION", "SOURCE_SHA", "IMAGE_DIGEST", "APP_VERSION"}
        identity_items = [item for item in raw_environment if item.get("name") in identity_names]
        names = [item.get("name") for item in identity_items]
        if len(names) != len(set(names)):
            raise ReleaseContractError(f"{workload} task has duplicate release identity variables")
        environment = {item.get("name"): item.get("value") for item in identity_items}
        image = container.get("image", "")
        digest = image.rsplit("@", 1)[-1] if "@" in image else None
        if not isinstance(digest, str) or not IMAGE_DIGEST_PATTERN.fullmatch(digest):
            digest = None
        schema2_names = {"VERSION", "SOURCE_SHA", "IMAGE_DIGEST"}
        if schema2_names & set(environment):
            if set(environment) != schema2_names or digest is None:
                raise ReleaseContractError(f"{workload} task release identity is incomplete")
            version = environment["VERSION"]
            source_sha = environment["SOURCE_SHA"]
            environment_digest = environment["IMAGE_DIGEST"]
            if not all(
                isinstance(value, str) for value in (version, source_sha, environment_digest)
            ):
                raise ReleaseContractError(f"{workload} task release identity is malformed")
            validate_source_sha(source_sha)
            validate_image_digest(environment_digest)
            validate_version(version, source_sha)
            if environment_digest != digest:
                raise ReleaseContractError(f"{workload} task image digest identity differs")
            return version, source_sha, digest, 2
        legacy_sha = environment.get("APP_VERSION")
        if isinstance(legacy_sha, str) and SOURCE_SHA_PATTERN.fullmatch(legacy_sha) and digest:
            return legacy_sha, legacy_sha, digest, 1
        if environment:
            raise ReleaseContractError(f"{workload} task release identity is malformed")
        return None, None, digest, None

    def capture_service(self, workload: str) -> ServiceSnapshot:
        service = self._service(workload)
        self._validate_service_identity(workload, service)
        target, running_count, pending_count = self._service_target_and_counts(workload, service)
        deployments = self._deployments(workload, service)
        primary = [item for item in deployments if item.get("status") == "PRIMARY"]
        if len(primary) != 1:
            raise ReleaseContractError(f"{workload} service has no unique primary deployment")
        deployment = primary[0]
        primary_target, primary_running, primary_pending, failed_tasks = (
            self._deployment_target_and_counts(workload, deployment)
        )
        primary_id = self._deployment_id(workload, deployment)
        if primary_target != target:
            raise ReleaseContractError(f"{workload} captured service and PRIMARY targets differ")
        if deployment.get("rolloutState") != "COMPLETED" or failed_tasks:
            raise ReleaseContractError(f"{workload} captured PRIMARY is not terminal")
        if (
            running_count != target.desired_count
            or pending_count != 0
            or primary_running != target.desired_count
            or primary_pending != 0
        ):
            raise ReleaseContractError(f"{workload} captured terminal counts differ")
        task_definition_arn = target.task_definition_arn
        version, source_sha, image_digest, identity_schema = self._identity(
            task_definition_arn, workload
        )
        return ServiceSnapshot(
            service_name=self.config.service_names[workload],
            task_definition_arn=task_definition_arn,
            desired_count=target.desired_count,
            running_count=running_count,
            pending_count=pending_count,
            source_sha=source_sha,
            image_digest=image_digest,
            primary_deployment_id=primary_id,
            version=version,
            identity_schema=identity_schema,
        )

    def source_task_definition(self, workload: str) -> dict[str, Any]:
        return self._task_definition(self.config.task_families[workload])

    def _managed_task_definitions(self, references: dict[str, str]) -> dict[str, dict[str, Any]]:
        tasks: dict[str, dict[str, Any]] = {}
        expected_tags = {
            "ReleaseManager": "DataTalksClub/website",
            "Project": "website",
            "Environment": RESOURCE_ENVIRONMENT_TAG,
        }
        for workload, reference in references.items():
            cluster_arn_parts = self.config.cluster_arn.split(":")
            family_prefix = (
                ":".join(cluster_arn_parts[:5])
                + f":task-definition/{self.config.task_families[workload]}:"
            )
            if not reference.startswith(family_prefix):
                raise ReleaseContractError(f"release record {workload} family differs")
            task, raw_tags = self._task_definition_with_tags(reference)
            if task.get("status") != "ACTIVE":
                raise ReleaseContractError(
                    f"release record {workload} task definition is not ACTIVE"
                )
            tags = {
                item.get("key"): item.get("value") for item in raw_tags if isinstance(item, dict)
            }
            if tags != expected_tags or len(raw_tags) != len(expected_tags):
                raise ReleaseContractError(f"release record {workload} management tags differ")
            tasks[workload] = task
        return tasks

    def verify_release_record(self, record: ReleaseRecord, identity: ReleaseIdentity) -> None:
        if (
            record.version,
            record.source_sha,
            record.image_digest,
            record.identity_schema,
        ) != (
            identity.version,
            identity.source_sha,
            identity.image_digest,
            identity.identity_schema,
        ):
            raise ReleaseContractError("release record identity differs")
        tasks = self._managed_task_definitions(
            {
                "web": record.web_task_definition_arn,
                "worker": record.worker_task_definition_arn,
                "migration": record.migration_task_definition_arn,
            }
        )
        assert_normalized_task_definitions(
            tasks,
            identity,
            TaskDefinitionConfig(
                families=self.config.task_families,
                container_names=self.config.container_names,
                task_role_arn=self.config.task_role_arn,
                execution_role_arn=self.config.execution_role_arn,
            ),
        )

    def verify_active_service_pair(
        self, pair: ActiveServicePair, identity: ReleaseIdentity
    ) -> None:
        if (
            pair.version,
            pair.source_sha,
            pair.image_digest,
            pair.identity_schema,
        ) != (
            identity.version,
            identity.source_sha,
            identity.image_digest,
            identity.identity_schema,
        ):
            raise ReleaseContractError("active service pair identity differs")
        tasks = self._managed_task_definitions(
            {
                "web": pair.web_task_definition_arn,
                "worker": pair.worker_task_definition_arn,
            }
        )
        assert_normalized_service_pair(
            tasks,
            identity,
            TaskDefinitionConfig(
                families=self.config.task_families,
                container_names=self.config.container_names,
                task_role_arn=self.config.task_role_arn,
                execution_role_arn=self.config.execution_role_arn,
            ),
        )

    def verify_image_digest_exists(self, identity: ReleaseIdentity) -> None:
        if identity.repository_uri != ECR_REPOSITORY_URI:
            raise ReleaseContractError(
                "active image repository is outside the development boundary"
            )
        tagged = self.ecr.describe_images(
            repositoryName=ECR_REPOSITORY_NAME,
            imageIds=[{"imageTag": identity.source_sha}],
        )
        tagged_details = tagged.get("imageDetails", [])
        if (
            len(tagged_details) != 1
            or tagged_details[0].get("imageDigest") != identity.image_digest
        ):
            raise ReleaseContractError(
                "active source SHA tag does not resolve to the exact development image digest"
            )
        described = self.ecr.describe_images(
            repositoryName=ECR_REPOSITORY_NAME,
            imageIds=[{"imageDigest": identity.image_digest}],
        )
        details = described.get("imageDetails", [])
        if len(details) != 1 or details[0].get("imageDigest") != identity.image_digest:
            raise ReleaseContractError("active image digest is missing from development ECR")
        if identity.identity_schema == 2:
            versioned = self.ecr.describe_images(
                repositoryName=ECR_REPOSITORY_NAME,
                imageIds=[{"imageTag": identity.version}],
            )
            versioned_details = versioned.get("imageDetails", [])
            if (
                len(versioned_details) != 1
                or versioned_details[0].get("imageDigest") != identity.image_digest
            ):
                raise ReleaseContractError(
                    "active VERSION tag does not resolve to the exact development image digest"
                )
        manifest = self.ecr.batch_get_image(
            repositoryName=ECR_REPOSITORY_NAME,
            imageIds=[{"imageDigest": identity.image_digest}],
        )
        images = manifest.get("images", [])
        if (
            manifest.get("failures")
            or len(images) != 1
            or images[0].get("imageId", {}).get("imageDigest") != identity.image_digest
            or not images[0].get("imageManifest")
        ):
            raise ReleaseContractError("active image manifest is missing from development ECR")

    def _stop_migration_and_prove_terminal(self, task_arn: str, reason: str) -> None:
        stop_error: Exception | None = None
        try:
            self.ecs.stop_task(
                cluster=self.config.cluster_arn,
                task=task_arn,
                reason="immutable release migration controller stop",
            )
        except Exception as error:
            # A lost response is ambiguous: the stop may have reached ECS. Keep polling the
            # exact task instead of abandoning a potentially running database migration.
            stop_error = error

        shutdown_deadline = time.monotonic() + MIGRATION_SHUTDOWN_TIMEOUT_SECONDS
        while time.monotonic() < shutdown_deadline:
            try:
                observed = self.ecs.describe_tasks(
                    cluster=self.config.cluster_arn,
                    tasks=[task_arn],
                )
            except Exception:
                time.sleep(self.config.poll_seconds)
                continue
            tasks = observed.get("tasks", [])
            if not observed.get("failures") and len(tasks) == 1:
                if tasks[0].get("taskArn") == task_arn and tasks[0].get("lastStatus") == "STOPPED":
                    raise ReleaseContractError(f"migration {reason}; exact task is STOPPED")
            time.sleep(self.config.poll_seconds)
        raise ReleaseContractError(
            f"migration {reason}; exact task terminal state could not be proven"
            + (f" after stop error ({type(stop_error).__name__})" if stop_error is not None else "")
        )

    def register_task_definition(
        self, workload: str, task_definition: dict[str, Any], tags: dict[str, str]
    ) -> str:
        if set(tags) != {"ReleaseManager", "Project", "Environment"}:
            raise ReleaseContractError("task registration tags differ from the IAM contract")
        response = self.ecs.register_task_definition(
            **task_definition,
            tags=[{"key": key, "value": value} for key, value in sorted(tags.items())],
        )
        arn = response.get("taskDefinition", {}).get("taskDefinitionArn")
        if not isinstance(arn, str):
            raise ReleaseContractError(f"registration returned no {workload} task-definition ARN")
        return arn

    def run_migration(
        self, task_definition_arn: str, *, inject_controlled_failure: bool = False
    ) -> None:
        run_arguments: dict[str, Any] = {
            "cluster": self.config.cluster_arn,
            "taskDefinition": task_definition_arn,
            "count": 1,
            "launchType": "FARGATE",
            "networkConfiguration": {
                "awsvpcConfiguration": {
                    "subnets": self.config.subnet_ids,
                    "securityGroups": self.config.security_group_ids,
                    "assignPublicIp": "ENABLED" if self.config.assign_public_ip else "DISABLED",
                }
            },
        }
        if inject_controlled_failure:
            run_arguments["overrides"] = {
                "containerOverrides": [
                    {
                        "name": self.config.container_names["migration"],
                        "command": CONTROLLED_MIGRATION_FAILURE_COMMAND,
                    }
                ]
            }
        response = self.ecs.run_task(
            **run_arguments,
        )
        failures = response.get("failures", [])
        tasks = response.get("tasks", [])
        if failures or len(tasks) != 1 or not tasks[0].get("taskArn"):
            raise ReleaseContractError(
                "migration task launch failed "
                f"(failure_count={len(failures)}, task_count={len(tasks)})"
            )
        task_arn = tasks[0]["taskArn"]
        deadline = time.monotonic() + self.config.timeout_seconds
        while time.monotonic() < deadline:
            try:
                observed = self.ecs.describe_tasks(
                    cluster=self.config.cluster_arn,
                    tasks=[task_arn],
                )
            except Exception:
                self._stop_migration_and_prove_terminal(task_arn, "task observation failed")
            if observed.get("failures") or len(observed.get("tasks", [])) != 1:
                self._stop_migration_and_prove_terminal(task_arn, "task observation failed")
            task = observed["tasks"][0]
            if task.get("lastStatus") == "STOPPED":
                containers = [
                    item
                    for item in task.get("containers", [])
                    if item.get("name") == self.config.container_names["migration"]
                ]
                if len(containers) != 1 or "exitCode" not in containers[0]:
                    raise ReleaseContractError("migration stopped without an essential exit code")
                if containers[0]["exitCode"] != 0:
                    raise ReleaseContractError(
                        f"migration exited nonzero ({containers[0]['exitCode']})"
                    )
                return
            time.sleep(self.config.poll_seconds)
        self._stop_migration_and_prove_terminal(task_arn, "timed out")

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
        if type(target) is not ServiceTarget:
            raise ReleaseContractError("service update target differs")
        if type(predecessors) is not tuple or any(
            type(item) is not ServicePredecessor for item in predecessors
        ):
            raise ReleaseContractError("service update predecessors differ")
        if (
            sum(item.role == "terminal" for item in predecessors) != 1
            or sum(item.role == "attempted" for item in predecessors) > 1
        ):
            raise ReleaseContractError("service update predecessor phase differs")
        maximum = self._stabilization_maximum(workload, timeout_seconds)
        if deadline is None:
            deadline = self.service_stabilization_deadline(timeout_seconds)
        else:
            deadline = self._validate_stabilization_deadline(
                deadline,
                maximum_seconds=maximum,
            )
        self._require_deadline_remaining(
            deadline,
            context=f"{workload} update receipt",
        )
        if web_runtime_binding is not None:
            if workload != "worker" or type(web_runtime_binding) is not WebRuntimeBinding:
                raise ReleaseContractError("web runtime guard is restricted to worker rollout")
        arguments: dict[str, Any] = {
            "cluster": self.config.cluster_arn,
            "service": self.config.service_names[workload],
            "taskDefinition": target.task_definition_arn,
            "desiredCount": target.desired_count,
            "forceNewDeployment": True,
        }
        if workload == "worker":
            arguments["deploymentConfiguration"] = {
                "minimumHealthyPercent": 0,
                "maximumPercent": 100,
            }
        response = self.ecs.update_service(
            **arguments,
        )
        self._require_not_after_deadline(
            deadline,
            context=f"{workload} update receipt",
        )
        service = response.get("service")
        if service is not None and not isinstance(service, dict):
            raise ReleaseContractError(f"{workload} update service is malformed")
        if isinstance(service, dict):
            receipt = self._receipt_from_acknowledgement(
                workload,
                service,
                target,
                predecessors,
                reconciled=False,
            )
            web_coherent = web_runtime_binding is None or self.revalidate_web_runtime_binding(
                web_runtime_binding, deadline=deadline
            )
            if receipt is not None and web_coherent:
                self._require_not_after_deadline(
                    deadline,
                    context=f"{workload} update receipt",
                )
                return receipt

        # A successful ECS mutation can return a structurally partial service document.
        # Reconcile immediately, without a preliminary sleep, and keep the phase's original
        # absolute deadline.  Missing identity is never synthesized from the request.
        while True:
            self._require_not_after_deadline(
                deadline,
                context=f"{workload} update reconciliation",
            )
            observed = self._service(workload)
            self._require_not_after_deadline(
                deadline,
                context=f"{workload} update reconciliation",
            )
            receipt = self._receipt_from_acknowledgement(
                workload,
                observed,
                target,
                predecessors,
                reconciled=True,
            )
            web_coherent = web_runtime_binding is None or self.revalidate_web_runtime_binding(
                web_runtime_binding, deadline=deadline
            )
            if receipt is not None and web_coherent:
                if receipt.terminal_observed:
                    self._require_not_after_deadline(
                        deadline,
                        context=f"{workload} update reconciliation",
                    )
                else:
                    self._require_deadline_remaining(
                        deadline,
                        context=f"{workload} update reconciliation",
                    )
                return receipt
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._require_deadline_remaining(
                    deadline,
                    context=f"{workload} update reconciliation",
                )
            time.sleep(min(self.config.poll_seconds, remaining))

    def _stabilization_maximum(self, workload: str, timeout_seconds: int | None) -> int:
        if timeout_seconds is None:
            return self.config.timeout_seconds
        if workload == "web":
            maximum = max(
                self.config.web_stabilization_timeout_seconds,
                self.config.web_recovery_timeout_seconds,
            )
        elif workload == "worker":
            maximum = max(
                self.config.worker_stabilization_timeout_seconds,
                self.config.worker_recovery_timeout_seconds,
            )
        else:
            raise ReleaseContractError("service update workload differs")
        if type(timeout_seconds) is not int or timeout_seconds < 1 or timeout_seconds > maximum:
            raise ReleaseContractError(f"invalid {workload} stabilization timeout override")
        return timeout_seconds

    @staticmethod
    def _required_service_count(document: dict[str, Any], field: str, *, context: str) -> int:
        value = document.get(field)
        if type(value) is not int or value < 0:
            raise ReleaseContractError(f"{context} {field} is not a nonnegative integer")
        return value

    @staticmethod
    def _optional_service_count(
        document: dict[str, Any],
        field: str,
        *,
        context: str,
    ) -> int | None:
        if field not in document:
            return None
        return AwsReleaseGateway._required_service_count(document, field, context=context)

    def _validate_service_identity(self, workload: str, service: dict[str, Any]) -> None:
        configured = self.config.service_names[workload]
        field = "serviceArn" if configured.startswith("arn:") else "serviceName"
        if service.get(field) != configured:
            raise ReleaseContractError(f"{workload} service identity differs")

    @staticmethod
    def _deployment_id(workload: str, deployment: dict[str, Any]) -> str:
        deployment_id = deployment.get("id")
        if not isinstance(deployment_id, str) or not deployment_id.strip():
            raise ReleaseContractError(f"{workload} deployment ID is missing")
        return deployment_id

    @staticmethod
    def _deployments(workload: str, service: dict[str, Any]) -> list[dict[str, Any]]:
        deployments = service.get("deployments")
        if not isinstance(deployments, list) or any(
            not isinstance(item, dict) for item in deployments
        ):
            raise ReleaseContractError(f"{workload} service deployments are malformed")
        return deployments

    def _service_target_and_counts(
        self, workload: str, service: dict[str, Any]
    ) -> tuple[ServiceTarget, int, int]:
        task_definition_arn = service.get("taskDefinition")
        if not isinstance(task_definition_arn, str):
            raise ReleaseContractError(f"{workload} service has no task definition")
        desired = self._required_service_count(
            service, "desiredCount", context=f"{workload} service"
        )
        running = self._required_service_count(
            service, "runningCount", context=f"{workload} service"
        )
        pending = self._required_service_count(
            service, "pendingCount", context=f"{workload} service"
        )
        return ServiceTarget(task_definition_arn, desired), running, pending

    def _deployment_target_and_counts(
        self, workload: str, deployment: dict[str, Any]
    ) -> tuple[ServiceTarget, int, int, int]:
        task_definition_arn = deployment.get("taskDefinition")
        if not isinstance(task_definition_arn, str):
            raise ReleaseContractError(f"{workload} deployment has no task definition")
        desired = self._required_service_count(
            deployment, "desiredCount", context=f"{workload} deployment"
        )
        running = self._required_service_count(
            deployment, "runningCount", context=f"{workload} deployment"
        )
        pending = self._required_service_count(
            deployment, "pendingCount", context=f"{workload} deployment"
        )
        failed_tasks = self._required_service_count(
            deployment,
            "failedTasks",
            context=f"{workload} deployment",
        )
        return ServiceTarget(task_definition_arn, desired), running, pending, failed_tasks

    def _receipt_from_acknowledgement(
        self,
        workload: str,
        service: dict[str, Any],
        target: ServiceTarget,
        predecessors: tuple[ServicePredecessor, ...],
        *,
        reconciled: bool,
    ) -> ServiceUpdateReceipt | None:
        configured = self.config.service_names[workload]
        identity_field = "serviceArn" if configured.startswith("arn:") else "serviceName"
        identity = service.get(identity_field)
        if identity_field in service and identity != configured:
            raise ReleaseContractError(f"{workload} update service identity differs")

        recognized_targets = {target} | {item.target for item in predecessors}
        recognized_task_definitions = {item.task_definition_arn for item in recognized_targets}
        recognized_desired_counts = {item.desired_count for item in recognized_targets}
        service_task_definition = service.get("taskDefinition")
        if "taskDefinition" in service and not isinstance(service_task_definition, str):
            raise ReleaseContractError(f"{workload} update service task definition is malformed")
        if (
            isinstance(service_task_definition, str)
            and service_task_definition not in recognized_task_definitions
        ):
            raise ReleaseContractError(f"{workload} update service task definition is alien")
        service_desired = self._optional_service_count(
            service,
            "desiredCount",
            context=f"{workload} update service",
        )
        service_running = self._optional_service_count(
            service,
            "runningCount",
            context=f"{workload} update service",
        )
        service_pending = self._optional_service_count(
            service,
            "pendingCount",
            context=f"{workload} update service",
        )
        if service_desired is not None and service_desired not in recognized_desired_counts:
            raise ReleaseContractError(f"{workload} update service desired count differs")
        service_target: ServiceTarget | None = None
        if service_task_definition is not None and service_desired is not None:
            service_target = ServiceTarget(service_task_definition, service_desired)
            if service_target not in recognized_targets:
                raise ReleaseContractError(f"{workload} update service target differs")
        if workload == "worker":
            present_counts = [
                value for value in (service_running, service_pending) if value is not None
            ]
            if any(value > 1 for value in present_counts) or (
                len(present_counts) == 2 and sum(present_counts) > 1
            ):
                raise ReleaseContractError("worker update exceeded singleton bounds")

        if "deployments" not in service:
            return None
        deployments = self._deployments(workload, service)
        predecessor_by_id = {item.primary_deployment_id: item for item in predecessors}
        predecessor_slot_by_id = {
            item.primary_deployment_id: index for index, item in enumerate(predecessors, start=1)
        }
        new_deployments: list[dict[str, Any]] = []
        deployment_projection_slots: list[tuple[int, ...]] = []
        complete_deployments = True
        primary_count = 0
        worker_tasks = 0

        for deployment in deployments:
            deployment_id: str | None = None
            if "id" in deployment:
                deployment_id = self._deployment_id(workload, deployment)
            status = deployment.get("status")
            if "status" in deployment and status not in {
                "PRIMARY",
                "ACTIVE",
                "DRAINING",
            }:
                raise ReleaseContractError(f"{workload} update deployment status differs")
            if status == "PRIMARY":
                primary_count += 1
            task_definition = deployment.get("taskDefinition")
            if "taskDefinition" in deployment and not isinstance(task_definition, str):
                raise ReleaseContractError(
                    f"{workload} update deployment task definition is malformed"
                )
            if (
                isinstance(task_definition, str)
                and task_definition not in recognized_task_definitions
            ):
                raise ReleaseContractError(f"{workload} update deployment task definition is alien")
            desired = self._optional_service_count(
                deployment,
                "desiredCount",
                context=f"{workload} update deployment",
            )
            running = self._optional_service_count(
                deployment,
                "runningCount",
                context=f"{workload} update deployment",
            )
            pending = self._optional_service_count(
                deployment,
                "pendingCount",
                context=f"{workload} update deployment",
            )
            failed = self._optional_service_count(
                deployment,
                "failedTasks",
                context=f"{workload} update deployment",
            )
            rollout_state = deployment.get("rolloutState")
            if "rolloutState" in deployment and rollout_state not in {
                "IN_PROGRESS",
                "COMPLETED",
                "FAILED",
            }:
                raise ReleaseContractError(f"{workload} update deployment rollout state differs")

            predecessor = predecessor_by_id.get(deployment_id or "")
            possible_predecessors = (predecessor,) if predecessor is not None else predecessors
            candidate_is_possible = predecessor is None
            candidate_matches = (
                status != "DRAINING"
                and candidate_is_possible
                and self._partial_deployment_matches_target(
                    task_definition,
                    desired,
                    running,
                    pending,
                    failed,
                    rollout_state,
                    target,
                )
            )
            matching_predecessors = tuple(
                item
                for item in possible_predecessors
                if (
                    self._partial_deployment_matches_draining_predecessor(
                        deployment_id,
                        task_definition,
                        desired,
                        running,
                        pending,
                        failed,
                        rollout_state,
                        item,
                    )
                    if status == "DRAINING"
                    else self._partial_deployment_matches_predecessor(
                        task_definition,
                        desired,
                        running,
                        pending,
                        failed,
                        rollout_state,
                        item,
                    )
                )
            )
            if not (candidate_matches or matching_predecessors):
                raise ReleaseContractError(
                    f"{workload} update deployment members contradict the phase"
                )
            if predecessor is not None:
                deployment_projection_slots.append(
                    (predecessor_slot_by_id[predecessor.primary_deployment_id],)
                )
            elif deployment_id is not None:
                deployment_projection_slots.append((0,) if candidate_matches else ())
            else:
                deployment_projection_slots.append(
                    ((0,) if candidate_matches else ())
                    + tuple(
                        predecessor_slot_by_id[item.primary_deployment_id]
                        for item in matching_predecessors
                    )
                )
            if predecessor is not None:
                if (
                    task_definition is not None
                    and task_definition != predecessor.target.task_definition_arn
                ):
                    raise ReleaseContractError(
                        f"{workload} update cross-paired predecessor identity"
                    )
                if predecessor.role == "terminal" and (
                    rollout_state == "FAILED" or (failed is not None and failed > 0)
                ):
                    raise ReleaseContractError(f"{workload} terminal predecessor failed")
            elif deployment_id is not None:
                new_deployments.append(deployment)
                if task_definition is not None and task_definition != target.task_definition_arn:
                    raise ReleaseContractError(
                        f"{workload} update found an alien deployment identity"
                    )
                if rollout_state == "FAILED" or (failed is not None and failed > 0):
                    raise ReleaseContractError(f"{workload} update candidate failed")
                if desired is not None:
                    if desired == target.desired_count:
                        pass
                    elif desired == 0 and target.desired_count > 0:
                        if rollout_state is not None and rollout_state != "IN_PROGRESS":
                            raise ReleaseContractError(
                                f"{workload} update candidate initialization state differs"
                            )
                        if any(
                            value is not None and value != 0 for value in (running, pending, failed)
                        ):
                            raise ReleaseContractError(
                                f"{workload} update candidate initialization counts differ"
                            )
                    else:
                        raise ReleaseContractError(
                            f"{workload} update candidate desired count differs"
                        )

            if workload == "worker":
                present_deployment_counts = [
                    value for value in (running, pending) if value is not None
                ]
                if any(value > 1 for value in present_deployment_counts) or (
                    len(present_deployment_counts) == 2 and sum(present_deployment_counts) > 1
                ):
                    raise ReleaseContractError("worker update deployment exceeded singleton bounds")
                worker_tasks += sum(present_deployment_counts)

            if any(
                value is None
                for value in (
                    deployment_id,
                    status,
                    task_definition,
                    desired,
                    running,
                    pending,
                    failed,
                    rollout_state,
                )
            ):
                complete_deployments = False

        if not self._partial_deployments_fit_phase(deployment_projection_slots):
            raise ReleaseContractError(
                f"{workload} update deployments cannot fit the phase cardinality"
            )
        if primary_count > 1:
            raise ReleaseContractError(f"{workload} update has multiple PRIMARY deployments")
        if complete_deployments and primary_count != 1:
            raise ReleaseContractError(f"{workload} update has no unique PRIMARY deployment")
        if len(new_deployments) > 1:
            raise ReleaseContractError(f"{workload} update found multiple new deployments")
        if workload == "worker" and worker_tasks > 1:
            raise ReleaseContractError("worker update deployments exceeded singleton bounds")
        if not new_deployments or not complete_deployments:
            return None

        candidate = new_deployments[0]
        candidate_id = self._deployment_id(workload, candidate)
        candidate_target, candidate_running, candidate_pending, candidate_failed = (
            self._deployment_target_and_counts(workload, candidate)
        )
        candidate_state = candidate.get("rolloutState")
        initialization = self._is_receipt_initialization(
            candidate_target,
            candidate_running,
            candidate_pending,
            candidate_failed,
            candidate_state,
            target,
        )
        if candidate_target != target and not initialization:
            raise ReleaseContractError(f"{workload} update candidate target differs")
        if candidate_state == "FAILED" or candidate_failed > 0:
            raise ReleaseContractError(f"{workload} update candidate failed")
        if candidate_state == "COMPLETED" and (
            candidate_target != target
            or candidate_running != target.desired_count
            or candidate_pending != 0
        ):
            raise ReleaseContractError(f"{workload} update candidate completed inexactly")

        service_complete = all(
            value is not None
            for value in (
                identity,
                service_task_definition,
                service_desired,
                service_running,
                service_pending,
            )
        )
        if not service_complete:
            return None
        assert service_task_definition is not None
        assert service_desired is not None
        if not reconciled and service_target != target:
            # A crossed acknowledgement must enter reconciliation. A subsequent complete
            # DescribeServices observation may bind the exact new deployment identity even while
            # the recognized service-level predecessor remains visible; stabilization is still
            # receipt-bound and poll-only until the service target converges.
            return None
        if not reconciled and candidate.get("status") != "PRIMARY":
            return None

        reason: Literal[
            "complete_receipt",
            "zero_count_initialization",
            "partial_acknowledgement_reconciled",
            "partial_acknowledgement_zero_count_initialization",
        ]
        if initialization:
            reason = (
                "partial_acknowledgement_zero_count_initialization"
                if reconciled
                else "zero_count_initialization"
            )
        else:
            reason = "partial_acknowledgement_reconciled" if reconciled else "complete_receipt"
        phase_proof = self._validate_deployment_phase(
            workload,
            deployments,
            target,
            candidate_id,
            predecessors,
        )
        terminal_observed = bool(
            reconciled
            and service_target == target
            and service_running == target.desired_count
            and service_pending == 0
            and phase_proof.predecessors_have_zero_work
            and phase_proof.candidate_is_exact_terminal_primary
        )
        receipt = ServiceUpdateReceipt(
            workload=workload,
            configured_service_identity=configured,
            target=target,
            primary_deployment_id=candidate_id,
            predecessors=predecessors,
            binding_reason=reason,
            terminal_observed=terminal_observed,
        )
        return receipt

    @staticmethod
    def _partial_member_matches(observed: object | None, expected: object) -> bool:
        return observed is None or observed == expected

    @staticmethod
    def _partial_deployments_fit_phase(possible_slots: list[tuple[int, ...]]) -> bool:
        """Return whether entries can map injectively to one candidate and each predecessor."""
        deployment_by_slot: dict[int, int] = {}

        def assign(deployment_index: int, visited_slots: set[int]) -> bool:
            for slot in possible_slots[deployment_index]:
                if slot in visited_slots:
                    continue
                visited_slots.add(slot)
                assigned = deployment_by_slot.get(slot)
                if assigned is None or assign(assigned, visited_slots):
                    deployment_by_slot[slot] = deployment_index
                    return True
            return False

        return all(
            assign(deployment_index, set()) for deployment_index in range(len(possible_slots))
        )

    @classmethod
    def _partial_deployment_matches_target(
        cls,
        task_definition: str | None,
        desired: int | None,
        running: int | None,
        pending: int | None,
        failed: int | None,
        rollout_state: object | None,
        target: ServiceTarget,
    ) -> bool:
        task_matches = cls._partial_member_matches(
            task_definition,
            target.task_definition_arn,
        )
        ordinary = (
            task_matches
            and cls._partial_member_matches(desired, target.desired_count)
            and cls._partial_member_matches(failed, 0)
            and rollout_state != "FAILED"
            and (
                rollout_state != "COMPLETED"
                or (
                    cls._partial_member_matches(running, target.desired_count)
                    and cls._partial_member_matches(pending, 0)
                )
            )
        )
        initialization = (
            target.desired_count > 0
            and task_matches
            and cls._partial_member_matches(desired, 0)
            and cls._partial_member_matches(running, 0)
            and cls._partial_member_matches(pending, 0)
            and cls._partial_member_matches(failed, 0)
            and cls._partial_member_matches(rollout_state, "IN_PROGRESS")
        )
        return ordinary or initialization

    @classmethod
    def _partial_deployment_matches_predecessor(
        cls,
        task_definition: str | None,
        desired: int | None,
        running: int | None,
        pending: int | None,
        failed: int | None,
        rollout_state: object | None,
        predecessor: ServicePredecessor,
    ) -> bool:
        target = predecessor.target
        task_matches = cls._partial_member_matches(
            task_definition,
            target.task_definition_arn,
        )
        desired_within_bound = desired is None or desired <= target.desired_count
        present_task_counts = [value for value in (running, pending) if value is not None]
        tasks_within_bound = sum(present_task_counts) <= target.desired_count
        retirement = task_matches and desired_within_bound and tasks_within_bound
        if predecessor.role == "terminal":
            retirement = (
                retirement and cls._partial_member_matches(failed, 0) and rollout_state != "FAILED"
            )
        return retirement

    @staticmethod
    def _is_zero_work_predecessor_remnant(
        deployment_id: str | None,
        status: object | None,
        task_definition: str | None,
        desired: int | None,
        running: int | None,
        pending: int | None,
        failed: int | None,
        rollout_state: object | None,
        predecessor: ServicePredecessor,
    ) -> bool:
        return (
            status in {"ACTIVE", "DRAINING"}
            and deployment_id == predecessor.primary_deployment_id
            and task_definition == predecessor.target.task_definition_arn
            and desired == 0
            and running == 0
            and pending == 0
            and failed == 0
            and rollout_state in {"IN_PROGRESS", "COMPLETED"}
        )

    @classmethod
    def _partial_deployment_matches_draining_predecessor(
        cls,
        deployment_id: str | None,
        task_definition: str | None,
        desired: int | None,
        running: int | None,
        pending: int | None,
        failed: int | None,
        rollout_state: object | None,
        predecessor: ServicePredecessor,
    ) -> bool:
        return (
            cls._partial_member_matches(
                deployment_id,
                predecessor.primary_deployment_id,
            )
            and cls._partial_member_matches(
                task_definition,
                predecessor.target.task_definition_arn,
            )
            and cls._partial_member_matches(desired, 0)
            and cls._partial_member_matches(running, 0)
            and cls._partial_member_matches(pending, 0)
            and cls._partial_member_matches(failed, 0)
            and (rollout_state is None or rollout_state in {"IN_PROGRESS", "COMPLETED"})
        )

    @staticmethod
    def _is_receipt_initialization(
        observed_target: ServiceTarget,
        running: int,
        pending: int,
        failed: int,
        rollout_state: object,
        requested_target: ServiceTarget,
    ) -> bool:
        return (
            requested_target.desired_count > 0
            and observed_target.task_definition_arn == requested_target.task_definition_arn
            and observed_target.desired_count == 0
            and running == 0
            and pending == 0
            and failed == 0
            and rollout_state == "IN_PROGRESS"
        )

    def _validate_deployment_phase(
        self,
        workload: str,
        deployments: list[dict[str, Any]],
        target: ServiceTarget,
        candidate_id: str,
        predecessors: tuple[ServicePredecessor, ...],
        *,
        allow_candidate_initialization: bool = True,
        terminal_predecessors_must_have_zero_work: bool = False,
    ) -> _DeploymentPhaseProof:
        predecessor_by_id = {
            predecessor.primary_deployment_id: predecessor for predecessor in predecessors
        }
        seen_deployment_ids: set[str] = set()
        worker_tasks = 0
        predecessors_have_zero_work = True
        primary_count = 0
        candidate_is_exact_terminal_primary = False
        for deployment in deployments:
            status = deployment.get("status")
            if status not in {"PRIMARY", "ACTIVE", "DRAINING"}:
                raise ReleaseContractError(f"{workload} deployment status differs")
            if status == "PRIMARY":
                primary_count += 1
            deployment_id = self._deployment_id(workload, deployment)
            if deployment_id in seen_deployment_ids:
                raise ReleaseContractError(f"{workload} deployment ID is duplicated")
            seen_deployment_ids.add(deployment_id)
            deployment_target, running, pending, failed = self._deployment_target_and_counts(
                workload, deployment
            )
            rollout_state = deployment.get("rolloutState")
            predecessor = predecessor_by_id.get(deployment_id)
            if rollout_state not in {"IN_PROGRESS", "COMPLETED", "FAILED"}:
                raise ReleaseContractError(f"{workload} deployment rollout state differs")
            zero_work_predecessor = predecessor is not None and (
                self._is_zero_work_predecessor_remnant(
                    deployment_id,
                    status,
                    deployment_target.task_definition_arn,
                    deployment_target.desired_count,
                    running,
                    pending,
                    failed,
                    rollout_state,
                    predecessor,
                )
            )
            if status == "DRAINING" and not zero_work_predecessor:
                raise ReleaseContractError(
                    f"{workload} DRAINING deployment is not a recognized predecessor"
                )
            target_initialization = (
                allow_candidate_initialization
                and deployment_id == candidate_id
                and self._is_receipt_initialization(
                    deployment_target,
                    running,
                    pending,
                    failed,
                    rollout_state,
                    target,
                )
            )
            is_candidate = deployment_id == candidate_id
            predecessor_retirement = predecessor is not None and (
                self._partial_deployment_matches_predecessor(
                    deployment_target.task_definition_arn,
                    deployment_target.desired_count,
                    running,
                    pending,
                    failed,
                    rollout_state,
                    predecessor,
                )
            )
            if is_candidate:
                if status == "DRAINING":
                    raise ReleaseContractError(f"{workload} receipt deployment is DRAINING")
                if deployment_target != target and not target_initialization:
                    raise ReleaseContractError(
                        f"{workload} deployment identity is outside the phase allowlist"
                    )
                candidate_is_exact_terminal_primary = (
                    status == "PRIMARY"
                    and deployment_target == target
                    and running == target.desired_count
                    and pending == 0
                    and failed == 0
                    and rollout_state == "COMPLETED"
                )
            elif predecessor is None:
                raise ReleaseContractError(
                    f"{workload} deployment identity is outside the phase allowlist"
                )
            elif terminal_predecessors_must_have_zero_work:
                if not zero_work_predecessor:
                    raise ReleaseContractError(
                        f"terminal {workload} predecessor is not an exact zero-work remnant"
                    )
            elif not predecessor_retirement:
                raise ReleaseContractError(
                    f"{workload} deployment identity is outside the phase allowlist"
                )
            if is_candidate:
                if rollout_state == "FAILED" or failed > 0:
                    raise ReleaseContractError(f"{workload} receipt deployment failed")
                if rollout_state == "COMPLETED" and (
                    running != target.desired_count or pending != 0
                ):
                    raise ReleaseContractError(
                        f"{workload} receipt deployment completed with inexact counts"
                    )
            elif (
                predecessor is not None
                and predecessor.role == "terminal"
                and (rollout_state == "FAILED" or failed > 0)
            ):
                raise ReleaseContractError(f"{workload} terminal predecessor failed")
            if predecessor is not None and not is_candidate:
                predecessors_have_zero_work = predecessors_have_zero_work and (
                    deployment_target.desired_count == 0
                    and running == 0
                    and pending == 0
                    and failed == 0
                    and rollout_state != "FAILED"
                )
            worker_tasks += running + pending
            if workload == "worker" and running + pending > 1:
                raise ReleaseContractError("worker deployment exceeded one running/pending task")
        if workload == "worker" and worker_tasks > 1:
            raise ReleaseContractError("worker deployments exceeded singleton bounds")
        return _DeploymentPhaseProof(
            predecessors_have_zero_work=predecessors_have_zero_work,
            candidate_is_exact_terminal_primary=(
                primary_count == 1 and candidate_is_exact_terminal_primary
            ),
        )

    def capture_attempted_predecessor(
        self,
        workload: str,
        attempted_target: ServiceTarget,
        terminal_predecessor: ServicePredecessor,
        deadline: float,
    ) -> ServicePredecessor:
        deadline = self._validate_recovery_deadline(workload, deadline)
        while True:
            self._require_not_after_deadline(
                deadline,
                context=f"{workload} recovery capture",
            )
            service = self._service(workload)
            self._require_deadline_remaining(
                deadline,
                context=f"{workload} recovery capture",
            )
            self._validate_service_identity(workload, service)
            service_target, running, pending = self._service_target_and_counts(workload, service)
            if service_target not in {terminal_predecessor.target, attempted_target}:
                raise ReleaseContractError(
                    f"{workload} recovery capture service target is outside the attempted phase"
                )
            if workload == "worker" and running + pending > 1:
                raise ReleaseContractError("worker recovery capture exceeded singleton bounds")
            deployments = self._deployments(workload, service)
            primary = [item for item in deployments if item.get("status") == "PRIMARY"]
            if len(primary) != 1:
                raise ReleaseContractError(
                    f"{workload} recovery capture has no unique PRIMARY deployment"
                )
            candidate_ids: set[str] = set()
            seen_deployment_ids: set[str] = set()
            worker_tasks = 0
            for item in deployments:
                item_id = self._deployment_id(workload, item)
                if item_id in seen_deployment_ids:
                    raise ReleaseContractError(
                        f"{workload} recovery capture deployment ID is duplicated"
                    )
                seen_deployment_ids.add(item_id)
                item_target, item_running, item_pending, item_failed = (
                    self._deployment_target_and_counts(workload, item)
                )
                item_state = item.get("rolloutState")
                item_status = item.get("status")
                if item_status not in {"PRIMARY", "ACTIVE", "DRAINING"} or item_state not in {
                    "IN_PROGRESS",
                    "COMPLETED",
                    "FAILED",
                }:
                    raise ReleaseContractError(
                        f"{workload} recovery capture deployment state differs"
                    )
                worker_tasks += item_running + item_pending
                if item_id == terminal_predecessor.primary_deployment_id:
                    terminal_matches = self._partial_deployment_matches_predecessor(
                        item_target.task_definition_arn,
                        item_target.desired_count,
                        item_running,
                        item_pending,
                        item_failed,
                        item_state,
                        terminal_predecessor,
                    )
                    if item_status == "DRAINING":
                        terminal_matches = self._is_zero_work_predecessor_remnant(
                            item_id,
                            item_status,
                            item_target.task_definition_arn,
                            item_target.desired_count,
                            item_running,
                            item_pending,
                            item_failed,
                            item_state,
                            terminal_predecessor,
                        )
                    if not terminal_matches:
                        raise ReleaseContractError(
                            f"{workload} recovery capture terminal predecessor state differs"
                        )
                elif item_target == attempted_target or self._is_receipt_initialization(
                    item_target,
                    item_running,
                    item_pending,
                    item_failed,
                    item_state,
                    attempted_target,
                ):
                    if item_status == "DRAINING":
                        raise ReleaseContractError(
                            f"{workload} recovery capture candidate is DRAINING"
                        )
                    candidate_ids.add(item_id)
                else:
                    raise ReleaseContractError(
                        f"{workload} recovery capture found a third deployment identity"
                    )
            if len(candidate_ids) > 1:
                raise ReleaseContractError(
                    f"{workload} recovery capture found multiple attempted identities"
                )
            if workload == "worker" and worker_tasks > 1:
                raise ReleaseContractError("worker recovery capture exceeded singleton bounds")

            deployment = primary[0]
            primary_id = self._deployment_id(workload, deployment)
            primary_target, primary_running, primary_pending, failed_tasks = (
                self._deployment_target_and_counts(workload, deployment)
            )
            if primary_id == terminal_predecessor.primary_deployment_id:
                if not self._partial_deployment_matches_predecessor(
                    primary_target.task_definition_arn,
                    primary_target.desired_count,
                    primary_running,
                    primary_pending,
                    failed_tasks,
                    deployment.get("rolloutState"),
                    terminal_predecessor,
                ):
                    raise ReleaseContractError(
                        f"{workload} recovery capture terminal state differs"
                    )
            elif primary_target == attempted_target or self._is_receipt_initialization(
                primary_target,
                primary_running,
                primary_pending,
                failed_tasks,
                deployment.get("rolloutState"),
                attempted_target,
            ):
                self._require_deadline_remaining(
                    deadline,
                    context=f"{workload} recovery capture",
                )
                return ServicePredecessor(attempted_target, primary_id, "attempted")
            else:
                raise ReleaseContractError(
                    f"{workload} recovery capture found an unrecognized PRIMARY identity"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._require_deadline_remaining(
                    deadline,
                    context=f"{workload} recovery capture",
                )
            time.sleep(min(self.config.poll_seconds, remaining))

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
        if receipt.configured_service_identity != self.config.service_names[workload]:
            raise ReleaseContractError("service stabilization receipt identity differs")
        if timeout_seconds is not None:
            if (workload == "web" and worker_singleton) or (
                workload == "worker" and not worker_singleton
            ):
                raise ReleaseContractError(
                    "extended stabilization timeouts are restricted to forward web "
                    "or singleton worker waits"
                )
        maximum = self._stabilization_maximum(workload, timeout_seconds)
        if deadline is None:
            deadline = self.service_stabilization_deadline(timeout_seconds)
        else:
            deadline = self._validate_stabilization_deadline(
                deadline,
                maximum_seconds=maximum,
            )
        if web_runtime_binding is not None and (
            workload != "worker" or type(web_runtime_binding) is not WebRuntimeBinding
        ):
            raise ReleaseContractError("web runtime guard is restricted to worker rollout")
        if receipt.terminal_observed:
            self._require_not_after_deadline(
                deadline,
                context=f"{workload} service stabilization",
            )
            if web_runtime_binding is None or self.revalidate_web_runtime_binding(
                web_runtime_binding,
                deadline=deadline,
            ):
                return
        while True:
            stable = self._observe_service_stable_once(
                receipt,
                worker_singleton=worker_singleton,
            )
            web_coherent = web_runtime_binding is None or self.revalidate_web_runtime_binding(
                web_runtime_binding, deadline=deadline
            )
            if stable and web_coherent:
                self._require_not_after_deadline(
                    deadline,
                    context=f"{workload} service stabilization",
                )
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(self.config.poll_seconds, remaining))
        self._require_deadline_remaining(
            deadline,
            context=f"{workload} service stabilization",
        )
        raise AssertionError("unreachable service stabilization deadline state")

    def _observe_service_stable_once(
        self,
        receipt: ServiceUpdateReceipt,
        *,
        worker_singleton: bool,
    ) -> bool:
        workload = receipt.workload
        service = self._service(workload)
        self._validate_service_identity(workload, service)
        service_target, running, pending = self._service_target_and_counts(workload, service)
        recognized_targets = {receipt.target} | {item.target for item in receipt.predecessors}
        if service_target not in recognized_targets:
            raise ReleaseContractError(f"{workload} service target is outside the phase allowlist")
        if worker_singleton and running + pending > 1:
            raise ReleaseContractError("worker rollout exceeded one running/pending task")
        deployments = self._deployments(workload, service)
        phase_proof = self._validate_deployment_phase(
            workload,
            deployments,
            receipt.target,
            receipt.primary_deployment_id,
            receipt.predecessors,
        )
        primary = [item for item in deployments if item.get("status") == "PRIMARY"]
        if len(primary) != 1:
            raise ReleaseContractError(f"{workload} service has no unique primary deployment")
        deployment = primary[0]
        primary_id = self._deployment_id(workload, deployment)
        primary_target, primary_running, primary_pending, failed_tasks = (
            self._deployment_target_and_counts(workload, deployment)
        )
        rollout_state = deployment.get("rolloutState")
        if rollout_state not in {"IN_PROGRESS", "COMPLETED", "FAILED"}:
            raise ReleaseContractError(f"{workload} PRIMARY rollout state differs")
        predecessor = next(
            (item for item in receipt.predecessors if item.primary_deployment_id == primary_id),
            None,
        )
        is_target = primary_id == receipt.primary_deployment_id
        if is_target:
            if rollout_state == "FAILED":
                raise ReleaseContractError(f"{workload} receipt deployment failed")
            if failed_tasks > 0:
                raise ReleaseContractError(f"{workload} receipt deployment has failed tasks")
            if rollout_state == "COMPLETED" and (
                primary_running != receipt.target.desired_count or primary_pending != 0
            ):
                raise ReleaseContractError(
                    f"{workload} receipt deployment completed with inexact counts"
                )
            initialization = self._is_receipt_initialization(
                primary_target,
                primary_running,
                primary_pending,
                failed_tasks,
                rollout_state,
                receipt.target,
            )
            if primary_target != receipt.target and not initialization:
                raise ReleaseContractError(f"{workload} receipt deployment target differs")
            if service_target == receipt.target and rollout_state == "COMPLETED":
                if primary_target != receipt.target:
                    raise ReleaseContractError(f"{workload} completed with inexact counts")
                if (
                    running == receipt.target.desired_count
                    and pending == 0
                    and phase_proof.predecessors_have_zero_work
                    and phase_proof.candidate_is_exact_terminal_primary
                ):
                    return True
                phase_task_capacity = receipt.target.desired_count + sum(
                    item.target.desired_count for item in receipt.predecessors
                )
                if (
                    running < receipt.target.desired_count
                    or running + pending > phase_task_capacity
                ):
                    raise ReleaseContractError(
                        f"{workload} completed aggregate counts exceed the phase envelope"
                    )
        elif predecessor is None:
            raise ReleaseContractError(f"{workload} PRIMARY deployment ID is unrecognized")
        elif predecessor.role == "terminal" and not (
            self._partial_deployment_matches_predecessor(
                primary_target.task_definition_arn,
                primary_target.desired_count,
                primary_running,
                primary_pending,
                failed_tasks,
                rollout_state,
                predecessor,
            )
        ):
            raise ReleaseContractError(f"{workload} terminal predecessor state differs")
        # An actually attempted predecessor may be failed while recovery replaces it.
        return False

    def observe_recovery_receipt(
        self,
        receipt: ServiceUpdateReceipt,
        *,
        workload_deadline: float,
        phase_deadline: float,
    ) -> bool:
        workload = receipt.workload
        workload_deadline = self._validate_recovery_deadline(workload, workload_deadline)
        phase_deadline = self._validate_stabilization_deadline(
            phase_deadline,
            maximum_seconds=self.config.recovery_phase_timeout_seconds,
        )
        if workload_deadline > phase_deadline:
            raise ReleaseContractError("recovery workload deadline exceeds the phase deadline")
        self._require_not_after_deadline(phase_deadline, context="recovery phase")
        self._require_not_after_deadline(
            workload_deadline,
            context=f"{workload} recovery receipt",
        )
        if receipt.configured_service_identity != self.config.service_names[workload]:
            raise ReleaseContractError("recovery receipt identity differs")
        if receipt.terminal_observed:
            return True
        stable = self._observe_service_stable_once(
            receipt,
            worker_singleton=workload == "worker",
        )
        self._require_not_after_deadline(phase_deadline, context="recovery phase")
        self._require_not_after_deadline(
            workload_deadline,
            context=f"{workload} recovery receipt",
        )
        if stable:
            return True
        if time.monotonic() >= workload_deadline:
            raise ReleaseContractError(
                f"{workload} recovery receipt deadline expired",
                reason_code="receipt_deadline_expired",
            )
        return False

    def sleep_recovery_round(
        self,
        workload_deadlines: dict[str, float],
        phase_deadline: float,
    ) -> None:
        if not workload_deadlines or not set(workload_deadlines) <= {"web", "worker"}:
            raise ReleaseContractError("recovery polling workload set differs")
        phase_deadline = self._validate_stabilization_deadline(
            phase_deadline,
            maximum_seconds=self.config.recovery_phase_timeout_seconds,
        )
        deadlines = []
        for workload, deadline in workload_deadlines.items():
            validated = self._validate_recovery_deadline(workload, deadline)
            if validated > phase_deadline:
                raise ReleaseContractError("recovery workload deadline exceeds the phase deadline")
            deadlines.append(validated)
        remaining = min([phase_deadline, *deadlines]) - time.monotonic()
        if remaining > 0:
            time.sleep(min(self.config.poll_seconds, remaining))

    def web_coherence_deadline(self) -> float:
        """Return the existing general-stage deadline used by coherent public proof."""
        return time.monotonic() + self.config.timeout_seconds

    def _validate_web_coherence_deadline(self, deadline: object) -> float:
        return self._validate_stabilization_deadline(
            deadline,
            maximum_seconds=self.config.timeout_seconds,
        )

    def _prove_web_receipt_service(
        self,
        receipt: ServiceUpdateReceipt,
        *,
        deadline: float,
    ) -> None:
        if (
            type(receipt) is not ServiceUpdateReceipt
            or receipt.workload != "web"
            or receipt.configured_service_identity != self.config.service_names["web"]
            or receipt.target.desired_count != 1
        ):
            raise ReleaseContractError("web runtime receipt identity differs")
        try:
            service = self._service("web")
        except ReleaseContractError:
            raise
        except Exception:
            raise ReleaseContractError("web runtime service request failed") from None
        self._require_not_after_deadline(deadline, context="web runtime coherence")
        self._validate_service_identity("web", service)
        service_target, running, pending = self._service_target_and_counts("web", service)
        if service_target != receipt.target or running != 1 or pending != 0:
            raise ReleaseContractError("web runtime service target or counts differ")
        deployments = self._deployments("web", service)
        phase = self._validate_deployment_phase(
            "web",
            deployments,
            receipt.target,
            receipt.primary_deployment_id,
            receipt.predecessors,
            allow_candidate_initialization=False,
            terminal_predecessors_must_have_zero_work=True,
        )
        primary = [item for item in deployments if item.get("status") == "PRIMARY"]
        if len(primary) != 1:
            raise ReleaseContractError("web runtime has no unique PRIMARY deployment")
        target, primary_running, primary_pending, failed = self._deployment_target_and_counts(
            "web",
            primary[0],
        )
        if (
            self._deployment_id("web", primary[0]) != receipt.primary_deployment_id
            or target != receipt.target
            or primary_running != 1
            or primary_pending != 0
            or failed != 0
            or primary[0].get("rolloutState") != "COMPLETED"
            or not phase.candidate_is_exact_terminal_primary
            or not phase.predecessors_have_zero_work
        ):
            raise ReleaseContractError("web runtime receipt PRIMARY is not terminal")

    def _list_running_web_task_arns(self, *, deadline: float) -> list[str]:
        task_arns: list[str] = []
        seen_task_arns: set[str] = set()
        seen_tokens: set[str] = set()
        next_token: str | None = None
        while True:
            arguments: dict[str, Any] = {
                "cluster": self.config.cluster_arn,
                "serviceName": self.config.service_names["web"],
                "desiredStatus": "RUNNING",
            }
            if next_token is not None:
                arguments["nextToken"] = next_token
            try:
                response = self.ecs.list_tasks(**arguments)
            except Exception:
                raise ReleaseContractError("web runtime task inventory request failed") from None
            self._require_not_after_deadline(deadline, context="web runtime coherence")
            if not isinstance(response, dict):
                raise ReleaseContractError("web runtime task inventory is malformed")
            page = response.get("taskArns")
            if not isinstance(page, list) or any(
                not isinstance(task_arn, str) or not task_arn.strip() for task_arn in page
            ):
                raise ReleaseContractError("web runtime task inventory is malformed")
            for task_arn in page:
                if task_arn in seen_task_arns:
                    raise ReleaseContractError("web runtime task inventory is duplicated")
                seen_task_arns.add(task_arn)
                task_arns.append(task_arn)
            token = response.get("nextToken")
            if token is None:
                return task_arns
            if not isinstance(token, str) or not token.strip() or token in seen_tokens:
                raise ReleaseContractError("web runtime task pagination is malformed")
            seen_tokens.add(token)
            next_token = token

    def _describe_running_web_tasks(
        self,
        task_arns: list[str],
        *,
        deadline: float,
    ) -> list[dict[str, Any]]:
        described: list[dict[str, Any]] = []
        seen: set[str] = set()
        for offset in range(0, len(task_arns), 100):
            requested = task_arns[offset : offset + 100]
            try:
                response = self.ecs.describe_tasks(
                    cluster=self.config.cluster_arn,
                    tasks=requested,
                )
            except Exception:
                raise ReleaseContractError("web runtime task description request failed") from None
            self._require_not_after_deadline(deadline, context="web runtime coherence")
            if not isinstance(response, dict):
                raise ReleaseContractError("web runtime task descriptions are malformed")
            failures = response.get("failures")
            tasks = response.get("tasks")
            if (
                failures != []
                or not isinstance(tasks, list)
                or any(not isinstance(task, dict) for task in tasks)
            ):
                raise ReleaseContractError("web runtime task descriptions failed")
            returned: set[str] = set()
            for task in tasks:
                task_arn = task.get("taskArn")
                if not isinstance(task_arn, str) or task_arn in returned or task_arn in seen:
                    raise ReleaseContractError("web runtime task descriptions are malformed")
                returned.add(task_arn)
                seen.add(task_arn)
                described.append(task)
            if returned != set(requested):
                raise ReleaseContractError("web runtime task description membership differs")
        return described

    @staticmethod
    def _require_private_ipv4(value: object) -> str:
        return validate_rfc1918_ipv4(value)

    @staticmethod
    def _container_environment(container: dict[str, Any]) -> list[dict[str, str]]:
        environment = container.get("environment")
        if not isinstance(environment, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not isinstance(item.get("value"), str)
            for item in environment
        ):
            raise ReleaseContractError("web runtime task-definition environment is malformed")
        return environment

    def _describe_web_task_definition(
        self,
        task_definition_arn: str,
        identity: ReleaseIdentity,
        *,
        deadline: float,
    ) -> tuple[dict[str, Any], tuple[tuple[int, int], ...]]:
        try:
            response = self.ecs.describe_task_definition(taskDefinition=task_definition_arn)
        except Exception:
            raise ReleaseContractError("web runtime task-definition request failed") from None
        self._require_not_after_deadline(deadline, context="web runtime coherence")
        if not isinstance(response, dict) or not isinstance(response.get("taskDefinition"), dict):
            raise ReleaseContractError("web runtime task definition is malformed")
        task_definition = response["taskDefinition"]
        if (
            task_definition.get("taskDefinitionArn") != task_definition_arn
            or task_definition.get("status") != "ACTIVE"
            or task_definition.get("networkMode") != "awsvpc"
        ):
            raise ReleaseContractError("web runtime task definition identity differs")
        containers = task_definition.get("containerDefinitions")
        if not isinstance(containers, list) or any(
            not isinstance(container, dict) for container in containers
        ):
            raise ReleaseContractError("web runtime task-definition containers are malformed")
        matching = [
            container
            for container in containers
            if container.get("name") == self.config.container_names["web"]
        ]
        if len(matching) != 1:
            raise ReleaseContractError("web runtime task definition has no exact web container")
        container = matching[0]
        environment = self._container_environment(container)
        expected_identity = (
            {
                "VERSION": identity.version,
                "SOURCE_SHA": identity.source_sha,
                "IMAGE_DIGEST": identity.image_digest,
            }
            if identity.identity_schema == 2
            else {"APP_VERSION": identity.source_sha}
        )
        observed_identity = [
            (item["name"], item["value"])
            for item in environment
            if item["name"] in {"VERSION", "SOURCE_SHA", "IMAGE_DIGEST", "APP_VERSION"}
        ]
        if observed_identity != sorted(expected_identity.items()):
            raise ReleaseContractError("web runtime task-definition release identity differs")
        if container.get("image") != identity.image:
            raise ReleaseContractError("web runtime task-definition repository or digest differs")
        raw_mappings = container.get("portMappings")
        if (
            not isinstance(raw_mappings, list)
            or not raw_mappings
            or any(not isinstance(mapping, dict) for mapping in raw_mappings)
        ):
            raise ReleaseContractError("web runtime task-definition ports are malformed")
        mappings: list[tuple[int, int]] = []
        for mapping in raw_mappings:
            container_port = mapping.get("containerPort")
            host_port = mapping.get("hostPort", container_port)
            if (
                type(container_port) is not int
                or type(host_port) is not int
                or not 1 <= container_port <= 65535
                or host_port != container_port
                or mapping.get("protocol", "tcp") != "tcp"
            ):
                raise ReleaseContractError("web runtime task-definition ports are malformed")
            pair = (container_port, host_port)
            if pair in mappings:
                raise ReleaseContractError("web runtime task-definition ports are duplicated")
            mappings.append(pair)
        return container, tuple(mappings)

    @staticmethod
    def _prove_no_identity_override(task: dict[str, Any]) -> None:
        overrides = task.get("overrides")
        if not isinstance(overrides, dict):
            raise ReleaseContractError("web runtime task overrides are malformed")
        container_overrides = overrides.get("containerOverrides", [])
        if not isinstance(container_overrides, list) or any(
            not isinstance(container, dict) for container in container_overrides
        ):
            raise ReleaseContractError("web runtime task overrides are malformed")
        for container in container_overrides:
            environment = container.get("environment", [])
            if not isinstance(environment, list) or any(
                not isinstance(item, dict)
                or not isinstance(item.get("name"), str)
                or not isinstance(item.get("value"), str)
                for item in environment
            ):
                raise ReleaseContractError("web runtime task overrides are malformed")
            if any(
                item["name"] in {"VERSION", "SOURCE_SHA", "IMAGE_DIGEST", "APP_VERSION"}
                for item in environment
            ):
                raise ReleaseContractError("web runtime task overrides release identity")

    def _web_task_runtime_identity(
        self,
        task: dict[str, Any],
        receipt: ServiceUpdateReceipt,
        identity: ReleaseIdentity,
        definition_ports: tuple[tuple[int, int], ...],
    ) -> tuple[str, str, str, str, int]:
        task_arn = task.get("taskArn")
        if not isinstance(task_arn, str) or not task_arn.strip():
            raise ReleaseContractError("web runtime task identity is malformed")
        if (
            task.get("taskDefinitionArn") != receipt.target.task_definition_arn
            or task.get("desiredStatus") != "RUNNING"
            or task.get("lastStatus") != "RUNNING"
            or task.get("healthStatus") != "HEALTHY"
        ):
            raise ReleaseContractError("web runtime active task identity or health differs")
        self._prove_no_identity_override(task)
        containers = task.get("containers")
        if not isinstance(containers, list) or any(
            not isinstance(container, dict) for container in containers
        ):
            raise ReleaseContractError("web runtime task containers are malformed")
        matching = [
            container
            for container in containers
            if container.get("name") == self.config.container_names["web"]
        ]
        if len(matching) != 1:
            raise ReleaseContractError("web runtime task has no exact web container")
        container = matching[0]
        if (
            container.get("healthStatus") != "HEALTHY"
            or container.get("image") != identity.image
            or container.get("imageDigest") != identity.image_digest
        ):
            raise ReleaseContractError("web runtime container identity or health differs")

        attachments = task.get("attachments")
        if not isinstance(attachments, list) or any(
            not isinstance(attachment, dict) for attachment in attachments
        ):
            raise ReleaseContractError("web runtime task attachments are malformed")
        network_attachments = [
            attachment
            for attachment in attachments
            if attachment.get("type") == "ElasticNetworkInterface"
        ]
        if len(network_attachments) != 1 or network_attachments[0].get("status") != "ATTACHED":
            raise ReleaseContractError("web runtime attached network interface differs")
        attachment = network_attachments[0]
        attachment_id = attachment.get("id")
        details = attachment.get("details")
        if (
            not isinstance(attachment_id, str)
            or not attachment_id.strip()
            or not isinstance(details, list)
        ):
            raise ReleaseContractError("web runtime attached network interface is malformed")
        detail_values: dict[str, str] = {}
        for detail in details:
            if (
                not isinstance(detail, dict)
                or not isinstance(detail.get("name"), str)
                or not isinstance(detail.get("value"), str)
                or detail["name"] in detail_values
            ):
                raise ReleaseContractError("web runtime attached network interface is malformed")
            detail_values[detail["name"]] = detail["value"]
        network_interface_id = detail_values.get("networkInterfaceId")
        attachment_address = self._require_private_ipv4(detail_values.get("privateIPv4Address"))
        if not isinstance(network_interface_id, str):
            raise ReleaseContractError("web runtime network-interface identity is malformed")

        interfaces = container.get("networkInterfaces")
        if (
            not isinstance(interfaces, list)
            or len(interfaces) != 1
            or not isinstance(interfaces[0], dict)
        ):
            raise ReleaseContractError("web runtime container network interface is malformed")
        container_address = self._require_private_ipv4(interfaces[0].get("privateIpv4Address"))
        if (
            interfaces[0].get("attachmentId") != attachment_id
            or container_address != attachment_address
        ):
            raise ReleaseContractError("web runtime attachment and container interface differ")

        raw_bindings = container.get("networkBindings", [])
        if not isinstance(raw_bindings, list) or any(
            not isinstance(binding, dict) for binding in raw_bindings
        ):
            raise ReleaseContractError("web runtime network bindings are malformed")
        runtime_ports: list[tuple[int, int]] = []
        for binding in raw_bindings:
            container_port = binding.get("containerPort")
            host_port = binding.get("hostPort")
            if (
                type(container_port) is not int
                or type(host_port) is not int
                or (container_port, host_port) not in definition_ports
            ):
                raise ReleaseContractError("web runtime network bindings are malformed")
            runtime_ports.append((container_port, host_port))
        selected_ports = runtime_ports or list(definition_ports)
        if len(selected_ports) != 1:
            raise ReleaseContractError("web runtime network port is ambiguous")
        container_port, host_port = selected_ports[0]
        if container_port != host_port:
            raise ReleaseContractError("web runtime host and container ports differ")
        return (
            task_arn,
            attachment_id,
            network_interface_id,
            attachment_address,
            container_port,
        )

    def _prove_web_target(
        self,
        private_address: str,
        target_port: int,
        *,
        frozen: bool,
        deadline: float,
    ) -> bool:
        try:
            response = self.elbv2.describe_target_health(
                TargetGroupArn=self.config.web_target_group_arn
            )
        except Exception:
            raise ReleaseContractError("web runtime target-health request failed") from None
        self._require_not_after_deadline(deadline, context="web runtime coherence")
        if not isinstance(response, dict):
            raise ReleaseContractError("web runtime target health is malformed")
        descriptions = response.get("TargetHealthDescriptions")
        if not isinstance(descriptions, list) or any(
            not isinstance(description, dict) for description in descriptions
        ):
            raise ReleaseContractError("web runtime target health is malformed")
        seen: set[tuple[str, int]] = set()
        candidate_state: str | None = None
        for description in descriptions:
            target = description.get("Target")
            health = description.get("TargetHealth")
            if not isinstance(target, dict) or not isinstance(health, dict):
                raise ReleaseContractError("web runtime target health is malformed")
            target_id = self._require_private_ipv4(target.get("Id"))
            port = target.get("Port")
            state = health.get("State")
            if type(port) is not int or not 1 <= port <= 65535 or not isinstance(state, str):
                raise ReleaseContractError("web runtime target health is malformed")
            target_tuple = (target_id, port)
            if target_tuple in seen:
                raise ReleaseContractError("web runtime target tuple is duplicated")
            seen.add(target_tuple)
            if target_tuple == (private_address, target_port):
                candidate_state = state
            elif state != "draining":
                raise ReleaseContractError("web runtime has an alien non-draining target")
        if candidate_state == "healthy":
            return True
        if frozen:
            raise ReleaseContractError("bound web runtime target is no longer healthy")
        return False

    def _observe_web_runtime_once(
        self,
        receipt: ServiceUpdateReceipt,
        identity: ReleaseIdentity,
        *,
        deadline: float,
        frozen_binding: WebRuntimeBinding | None,
    ) -> WebRuntimeBinding | None:
        self._require_not_after_deadline(deadline, context="web runtime coherence")
        self._prove_web_receipt_service(receipt, deadline=deadline)
        task_arns = self._list_running_web_task_arns(deadline=deadline)
        if not task_arns:
            return None
        tasks = self._describe_running_web_tasks(task_arns, deadline=deadline)
        active: list[dict[str, Any]] = []
        for task in tasks:
            last_status = task.get("lastStatus")
            desired_status = task.get("desiredStatus")
            if last_status == "STOPPED" and desired_status in {"STOPPED", "RUNNING"}:
                continue
            if last_status != "RUNNING" or desired_status != "RUNNING":
                raise ReleaseContractError("web runtime task inventory contains mixed active state")
            active.append(task)
        if not active:
            return None
        if len(active) != 1:
            raise ReleaseContractError("web runtime active task count differs")
        task = active[0]
        if frozen_binding is not None and task.get("taskArn") != frozen_binding.task_arn:
            raise ReleaseContractError("bound web runtime task was replaced")
        _definition_container, definition_ports = self._describe_web_task_definition(
            receipt.target.task_definition_arn,
            identity,
            deadline=deadline,
        )
        task_arn, attachment_id, network_interface_id, private_address, port = (
            self._web_task_runtime_identity(
                task,
                receipt,
                identity,
                definition_ports,
            )
        )
        if not self._prove_web_target(
            private_address,
            port,
            frozen=frozen_binding is not None,
            deadline=deadline,
        ):
            return None
        observed = WebRuntimeBinding(
            configured_service_identity=receipt.configured_service_identity,
            primary_deployment_id=receipt.primary_deployment_id,
            task_definition_arn=receipt.target.task_definition_arn,
            predecessors=receipt.predecessors,
            source_sha=identity.source_sha,
            image_digest=identity.image_digest,
            task_arn=task_arn,
            network_attachment_id=attachment_id,
            network_interface_id=network_interface_id,
            private_ipv4_address=private_address,
            container_port=port,
            target_port=port,
            version=identity.version,
            identity_schema=identity.identity_schema,
        )
        if frozen_binding is not None and observed != frozen_binding:
            raise ReleaseContractError("bound web runtime identity changed")
        return observed

    def _sleep_web_coherence_poll(self, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReleaseContractError(
                "web runtime coherence deadline expired",
                reason_code="receipt_deadline_expired",
            )
        time.sleep(min(self.config.poll_seconds, remaining))

    def establish_web_runtime_binding(
        self,
        receipt: ServiceUpdateReceipt,
        identity: ReleaseIdentity,
        *,
        deadline: float,
    ) -> WebRuntimeBinding:
        deadline = self._validate_web_coherence_deadline(deadline)
        if type(identity) is not ReleaseIdentity or identity.repository_uri != ECR_REPOSITORY_URI:
            raise ReleaseContractError("web runtime expected repository differs")
        binding: WebRuntimeBinding | None = None
        while binding is None:
            self._require_not_after_deadline(deadline, context="web runtime coherence")
            binding = self._observe_web_runtime_once(
                receipt,
                identity,
                deadline=deadline,
                frozen_binding=None,
            )
            if binding is None:
                self._sleep_web_coherence_poll(deadline)
        self._require_deadline_remaining(deadline, context="web runtime coherence")

        while True:
            self._require_not_after_deadline(deadline, context="web runtime coherence")
            try:
                verify_health(
                    self.config.base_url,
                    identity.version,
                    identity.source_sha,
                    identity.image_digest,
                )
            except Exception:
                self._sleep_web_coherence_poll(deadline)
                continue
            self._require_not_after_deadline(deadline, context="web runtime coherence")
            break
        self._require_deadline_remaining(deadline, context="web runtime coherence")

        while True:
            observed = self._observe_web_runtime_once(
                receipt,
                identity,
                deadline=deadline,
                frozen_binding=binding,
            )
            if observed is not None:
                return binding
            self._sleep_web_coherence_poll(deadline)

    def revalidate_web_runtime_binding(
        self,
        binding: WebRuntimeBinding,
        *,
        deadline: float,
    ) -> bool:
        if type(binding) is not WebRuntimeBinding:
            raise ReleaseContractError("web runtime binding is malformed")
        deadline = self._validate_stabilization_deadline(
            deadline,
            maximum_seconds=self.config.worker_stabilization_timeout_seconds,
        )
        receipt = ServiceUpdateReceipt(
            workload="web",
            configured_service_identity=binding.configured_service_identity,
            target=ServiceTarget(binding.task_definition_arn, 1),
            primary_deployment_id=binding.primary_deployment_id,
            predecessors=binding.predecessors,
        )
        identity = ReleaseIdentity(
            source_sha=binding.source_sha,
            image_digest=binding.image_digest,
            repository_uri=ECR_REPOSITORY_URI,
            version=binding.version,
            identity_schema=binding.identity_schema,
        )
        return (
            self._observe_web_runtime_once(
                receipt,
                identity,
                deadline=deadline,
                frozen_binding=binding,
            )
            is not None
        )

    def verify_public_web(
        self, identity: ReleaseIdentity, *, phase_deadline: float | None = None
    ) -> None:
        deadline = time.monotonic() + self.config.timeout_seconds
        if phase_deadline is not None:
            phase_deadline = self._validate_stabilization_deadline(
                phase_deadline,
                maximum_seconds=self.config.recovery_phase_timeout_seconds,
            )
            self._require_not_after_deadline(phase_deadline, context="recovery phase")
            deadline = min(deadline, phase_deadline)
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                desired = int(self._service("web").get("desiredCount", 0))
                self._require_not_after_deadline(deadline, context="public web health")
                target_health = self.elbv2.describe_target_health(
                    TargetGroupArn=self.config.web_target_group_arn
                )
                self._require_not_after_deadline(deadline, context="public web health")
                states = [
                    item.get("TargetHealth", {}).get("State")
                    for item in target_health.get("TargetHealthDescriptions", [])
                ]
                healthy = sum(state == "healthy" for state in states)
                if (
                    desired > 0
                    and healthy >= desired
                    and all(state in {"healthy", "draining"} for state in states)
                ):
                    break
                last_error = ReleaseContractError("ALB targets are not all ready")
            except Exception as error:
                last_error = error
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(self.config.poll_seconds, remaining))
        else:
            error_name = type(last_error).__name__ if last_error is not None else "unknown"
            raise ReleaseContractError(
                f"ALB target readiness did not become healthy ({error_name})"
            )

        while time.monotonic() < deadline:
            try:
                verify_health(
                    self.config.base_url,
                    identity.version,
                    identity.source_sha,
                    identity.image_digest,
                )
                self._require_not_after_deadline(deadline, context="public web health")
                return
            except Exception as error:
                last_error = error
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(self.config.poll_seconds, remaining))
        error_name = type(last_error).__name__ if last_error is not None else "unknown"
        raise ReleaseContractError(
            f"public readiness/liveness did not reach the exact release identity ({error_name})"
        )

    def run_deployed_smoke(self, identity: ReleaseIdentity) -> None:
        try:
            run_http_smoke(
                self.config.base_url,
                identity.version,
                identity.source_sha,
                identity.image_digest,
                self.config.screenshot_directory / "http-evidence.json",
            )
        except ReleaseContractError:
            raise
        except Exception as error:
            raise ReleaseContractError(
                f"deployed HTTP smoke failed ({type(error).__name__})"
            ) from error
        self.config.screenshot_directory.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                [
                    "uv",
                    "run",
                    "pytest",
                    "playwright_tests/test_deployed_smoke.py",
                    "-m",
                    "core",
                    "-v",
                ],
                check=True,
                env={
                    **os.environ,
                    "VERSION": identity.version,
                    "SOURCE_SHA": identity.source_sha,
                    "IMAGE_DIGEST": identity.image_digest,
                    "DTC_TEST_BASE_URL": self.config.base_url,
                    "DTC_EXPECTED_VERSION": identity.version,
                    "DTC_EXPECTED_SOURCE_SHA": identity.source_sha,
                    "DTC_EXPECTED_IMAGE_DIGEST": identity.image_digest,
                    "DTC_SCREENSHOT_DIR": str(self.config.screenshot_directory),
                    "DJANGO_ALLOW_ASYNC_UNSAFE": "true",
                },
                timeout=DEPLOYED_BROWSER_TIMEOUT_SECONDS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise ReleaseContractError("deployed browser smoke failed") from error

    def _active_tasks(self, workload: str) -> list[dict[str, Any]]:
        arns: list[str] = []
        for desired_status in ("RUNNING", "PENDING"):
            response = self.ecs.list_tasks(
                cluster=self.config.cluster_arn,
                serviceName=self.config.service_names[workload],
                desiredStatus=desired_status,
            )
            arns.extend(response.get("taskArns", []))
        if not arns:
            return []
        response = self.ecs.describe_tasks(cluster=self.config.cluster_arn, tasks=sorted(set(arns)))
        if response.get("failures"):
            raise ReleaseContractError(f"cannot describe active {workload} tasks")
        return response.get("tasks", [])

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
        if (web_runtime_binding is None) != (web_runtime_deadline is None):
            raise ReleaseContractError("terminal web runtime proof is incomplete")
        if phase_deadline is not None:
            phase_deadline = self._validate_stabilization_deadline(
                phase_deadline,
                maximum_seconds=self.config.recovery_phase_timeout_seconds,
            )
            self._require_not_after_deadline(phase_deadline, context="recovery phase")

        def require_phase() -> None:
            if phase_deadline is not None:
                self._require_not_after_deadline(phase_deadline, context="recovery phase")

        if allowed_predecessors is not None and set(allowed_predecessors) != {"web", "worker"}:
            raise ReleaseContractError("terminal predecessor allowlist differs")
        if allowed_predecessors is not None:
            for workload in ("web", "worker"):
                predecessors = allowed_predecessors[workload]
                if type(predecessors) is not tuple or any(
                    type(predecessor) is not ServicePredecessor for predecessor in predecessors
                ):
                    raise ReleaseContractError("terminal predecessor allowlist is malformed")
                predecessor_ids = [
                    predecessor.primary_deployment_id for predecessor in predecessors
                ]
                if len(predecessor_ids) != len(set(predecessor_ids)):
                    raise ReleaseContractError("terminal predecessor allowlist is duplicated")
                if (
                    expected_primary_deployment_ids is not None
                    and expected_primary_deployment_ids[workload] in predecessor_ids
                ):
                    raise ReleaseContractError(
                        f"terminal {workload} receipt ID is reused as a predecessor"
                    )
        for workload in ("web", "worker"):
            require_phase()
            snapshot = self.capture_service(workload)
            require_phase()
            if snapshot.task_definition_arn != expected_task_definitions[workload]:
                raise ReleaseContractError(f"terminal {workload} task definition differs")
            if snapshot.desired_count != expected_desired_counts[workload]:
                raise ReleaseContractError(f"terminal {workload} desired count differs")
            if (
                snapshot.running_count != expected_desired_counts[workload]
                or snapshot.pending_count != 0
            ):
                raise ReleaseContractError(f"terminal {workload} running/pending counts differ")
            if (
                expected_primary_deployment_ids is not None
                and snapshot.primary_deployment_id != expected_primary_deployment_ids[workload]
            ):
                raise ReleaseContractError(f"terminal {workload} PRIMARY deployment ID differs")
            service = self._service(workload)
            require_phase()
            self._validate_service_identity(workload, service)
            service_target, service_running, service_pending = self._service_target_and_counts(
                workload,
                service,
            )
            expected_target = ServiceTarget(
                expected_task_definitions[workload],
                expected_desired_counts[workload],
            )
            if (
                service_target != expected_target
                or service_running != expected_desired_counts[workload]
                or service_pending != 0
            ):
                raise ReleaseContractError(f"terminal {workload} service target/counts differ")
            deployments = self._deployments(workload, service)
            predecessors = () if allowed_predecessors is None else allowed_predecessors[workload]
            candidate_id = (
                snapshot.primary_deployment_id
                if expected_primary_deployment_ids is None
                else expected_primary_deployment_ids[workload]
            )
            phase_proof = self._validate_deployment_phase(
                workload,
                deployments,
                expected_target,
                candidate_id,
                predecessors,
                allow_candidate_initialization=False,
                terminal_predecessors_must_have_zero_work=True,
            )
            if (
                not phase_proof.candidate_is_exact_terminal_primary
                or not phase_proof.predecessors_have_zero_work
            ):
                raise ReleaseContractError(f"terminal {workload} deployment phase is not stable")
            primary = [item for item in deployments if item.get("status") == "PRIMARY"]
            if len(primary) != 1:
                raise ReleaseContractError(f"terminal {workload} has no unique PRIMARY deployment")
            deployment = primary[0]
            primary_id = self._deployment_id(workload, deployment)
            primary_target, primary_running, primary_pending, failed_tasks = (
                self._deployment_target_and_counts(workload, deployment)
            )
            if (
                primary_target != expected_target
                or primary_running != expected_desired_counts[workload]
                or primary_pending != 0
                or failed_tasks != 0
                or deployment.get("rolloutState") != "COMPLETED"
            ):
                raise ReleaseContractError(f"terminal {workload} PRIMARY deployment is not stable")
            if (
                expected_primary_deployment_ids is not None
                and primary_id != expected_primary_deployment_ids[workload]
            ):
                raise ReleaseContractError(f"terminal {workload} PRIMARY deployment ID differs")
            tasks = self._active_tasks(workload)
            require_phase()
            if len(tasks) != expected_desired_counts[workload]:
                raise ReleaseContractError(f"terminal {workload} active task count differs")
            if workload == "worker" and len(tasks) > 1:
                raise ReleaseContractError("terminal worker has more than one running/pending task")
            if any(
                task.get("taskDefinitionArn") != expected_task_definitions[workload]
                for task in tasks
            ):
                raise ReleaseContractError(f"terminal {workload} tasks are mixed")
            if expected_identity is not None and (
                snapshot.version != expected_identity.version
                or snapshot.source_sha != expected_identity.source_sha
                or snapshot.image_digest != expected_identity.image_digest
                or snapshot.identity_schema != expected_identity.identity_schema
            ):
                raise ReleaseContractError(f"terminal {workload} release identity differs")

            if expected_identity is not None:
                task_definition = self._task_definition(snapshot.task_definition_arn)
                require_phase()
                matching_containers = [
                    container
                    for container in task_definition.get("containerDefinitions", [])
                    if container.get("name") == self.config.container_names[workload]
                ]
                if (
                    len(matching_containers) != 1
                    or matching_containers[0].get("image") != expected_identity.image
                ):
                    raise ReleaseContractError(f"terminal {workload} repository/digest differs")
        require_phase()
        if web_runtime_binding is not None:
            assert web_runtime_deadline is not None
            if not self.revalidate_web_runtime_binding(
                web_runtime_binding,
                deadline=web_runtime_deadline,
            ):
                raise ReleaseContractError("terminal bound web runtime is temporarily absent")


def die(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)
