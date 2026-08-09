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
    SOURCE_SHA_PATTERN,
    ActiveServicePair,
    ReleaseContractError,
    ReleaseIdentity,
    ReleaseRecord,
    ServicePredecessor,
    ServiceSnapshot,
    ServiceTarget,
    ServiceUpdateReceipt,
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
    poll_seconds: int = 10

    def __post_init__(self) -> None:
        integer_timeouts = {
            "stage": self.timeout_seconds,
            "web stabilization": self.web_stabilization_timeout_seconds,
            "worker stabilization": self.worker_stabilization_timeout_seconds,
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
    def worker_stabilization_timeout_seconds(self) -> int:
        return self.config.worker_stabilization_timeout_seconds

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

    def _validate_recovery_deadline(self, deadline: object) -> float:
        return self._validate_stabilization_deadline(
            deadline,
            maximum_seconds=self.config.timeout_seconds,
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

    def _identity(self, task_definition_arn: str, workload: str) -> tuple[str | None, str | None]:
        task = self._task_definition(task_definition_arn)
        containers = task.get("containerDefinitions", [])
        matches = [
            item for item in containers if item.get("name") == self.config.container_names[workload]
        ]
        if len(matches) != 1:
            raise ReleaseContractError(f"{workload} task does not have its exact container")
        container = matches[0]
        environment = {
            item.get("name"): item.get("value") for item in container.get("environment", [])
        }
        source_sha = environment.get("APP_VERSION")
        if not isinstance(source_sha, str) or not SOURCE_SHA_PATTERN.fullmatch(source_sha):
            source_sha = None
        image = container.get("image", "")
        digest = image.rsplit("@", 1)[-1] if "@" in image else None
        if not isinstance(digest, str) or not IMAGE_DIGEST_PATTERN.fullmatch(digest):
            digest = None
        return source_sha, digest

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
        source_sha, image_digest = self._identity(task_definition_arn, workload)
        return ServiceSnapshot(
            service_name=self.config.service_names[workload],
            task_definition_arn=task_definition_arn,
            desired_count=target.desired_count,
            running_count=running_count,
            pending_count=pending_count,
            source_sha=source_sha,
            image_digest=image_digest,
            primary_deployment_id=primary_id,
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

    def verify_image_digest_exists(
        self,
        repository_uri: str,
        source_sha: str,
        image_digest: str,
    ) -> None:
        if repository_uri != ECR_REPOSITORY_URI:
            raise ReleaseContractError(
                "active image repository is outside the development boundary"
            )
        tagged = self.ecr.describe_images(
            repositoryName=ECR_REPOSITORY_NAME,
            imageIds=[{"imageTag": source_sha}],
        )
        tagged_details = tagged.get("imageDetails", [])
        if len(tagged_details) != 1 or tagged_details[0].get("imageDigest") != image_digest:
            raise ReleaseContractError(
                "active source SHA tag does not resolve to the exact development image digest"
            )
        described = self.ecr.describe_images(
            repositoryName=ECR_REPOSITORY_NAME,
            imageIds=[{"imageDigest": image_digest}],
        )
        details = described.get("imageDetails", [])
        if len(details) != 1 or details[0].get("imageDigest") != image_digest:
            raise ReleaseContractError("active image digest is missing from development ECR")
        manifest = self.ecr.batch_get_image(
            repositoryName=ECR_REPOSITORY_NAME,
            imageIds=[{"imageDigest": image_digest}],
        )
        images = manifest.get("images", [])
        if (
            manifest.get("failures")
            or len(images) != 1
            or images[0].get("imageId", {}).get("imageDigest") != image_digest
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
            if receipt is not None:
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
            if receipt is not None:
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
            maximum = self.config.web_stabilization_timeout_seconds
        elif workload == "worker":
            maximum = self.config.worker_stabilization_timeout_seconds
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
            if "status" in deployment and status not in {"PRIMARY", "ACTIVE"}:
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
            candidate_matches = candidate_is_possible and self._partial_deployment_matches_target(
                task_definition,
                desired,
                running,
                pending,
                failed,
                rollout_state,
                target,
            )
            matching_predecessors = tuple(
                item
                for item in possible_predecessors
                if self._partial_deployment_matches_predecessor(
                    task_definition,
                    desired,
                    running,
                    pending,
                    failed,
                    rollout_state,
                    item,
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
        terminal_observed = bool(
            reconciled
            and candidate.get("status") == "PRIMARY"
            and service_target == target
            and service_running == target.desired_count
            and service_pending == 0
            and candidate_target == target
            and candidate_running == target.desired_count
            and candidate_pending == 0
            and candidate_failed == 0
            and candidate_state == "COMPLETED"
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
        self._validate_deployment_allowlist(workload, deployments, receipt)
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

    def _validate_deployment_allowlist(
        self,
        workload: str,
        deployments: list[dict[str, Any]],
        receipt: ServiceUpdateReceipt,
    ) -> None:
        predecessor_by_id = {
            predecessor.primary_deployment_id: predecessor for predecessor in receipt.predecessors
        }
        seen_deployment_ids: set[str] = set()
        worker_tasks = 0
        for deployment in deployments:
            if deployment.get("status") not in {"PRIMARY", "ACTIVE"}:
                raise ReleaseContractError(f"{workload} deployment status differs")
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
            target_initialization = (
                deployment_id == receipt.primary_deployment_id
                and self._is_receipt_initialization(
                    deployment_target,
                    running,
                    pending,
                    failed,
                    rollout_state,
                    receipt.target,
                )
            )
            is_candidate = deployment_id == receipt.primary_deployment_id
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
                if deployment_target != receipt.target and not target_initialization:
                    raise ReleaseContractError(
                        f"{workload} deployment identity is outside the phase allowlist"
                    )
            elif not predecessor_retirement:
                raise ReleaseContractError(
                    f"{workload} deployment identity is outside the phase allowlist"
                )
            if is_candidate:
                if rollout_state == "FAILED" or failed > 0:
                    raise ReleaseContractError(f"{workload} receipt deployment failed")
                if rollout_state == "COMPLETED" and (
                    running != receipt.target.desired_count or pending != 0
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
            worker_tasks += running + pending
            if workload == "worker" and running + pending > 1:
                raise ReleaseContractError("worker deployment exceeded one running/pending task")
        if workload == "worker" and worker_tasks > 1:
            raise ReleaseContractError("worker deployments exceeded singleton bounds")

    def capture_attempted_predecessor(
        self,
        workload: str,
        attempted_target: ServiceTarget,
        terminal_predecessor: ServicePredecessor,
        deadline: float,
    ) -> ServicePredecessor:
        deadline = self._validate_recovery_deadline(deadline)
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
                if item.get("status") not in {"PRIMARY", "ACTIVE"} or item_state not in {
                    "IN_PROGRESS",
                    "COMPLETED",
                    "FAILED",
                }:
                    raise ReleaseContractError(
                        f"{workload} recovery capture deployment state differs"
                    )
                worker_tasks += item_running + item_pending
                if item_id == terminal_predecessor.primary_deployment_id:
                    if not self._partial_deployment_matches_predecessor(
                        item_target.task_definition_arn,
                        item_target.desired_count,
                        item_running,
                        item_pending,
                        item_failed,
                        item_state,
                        terminal_predecessor,
                    ):
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
        if receipt.terminal_observed:
            self._require_not_after_deadline(
                deadline,
                context=f"{workload} service stabilization",
            )
            return
        while True:
            self._require_not_after_deadline(
                deadline,
                context=f"{workload} service stabilization",
            )
            service = self._service(workload)
            self._require_not_after_deadline(
                deadline,
                context=f"{workload} service stabilization",
            )
            self._validate_service_identity(workload, service)
            service_target, running, pending = self._service_target_and_counts(workload, service)
            recognized_targets = {receipt.target} | {item.target for item in receipt.predecessors}
            if service_target not in recognized_targets:
                raise ReleaseContractError(
                    f"{workload} service target is outside the phase allowlist"
                )
            if worker_singleton and running + pending > 1:
                raise ReleaseContractError("worker rollout exceeded one running/pending task")
            deployments = self._deployments(workload, service)
            self._validate_deployment_allowlist(workload, deployments, receipt)
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
                    if (
                        running != receipt.target.desired_count
                        or pending != 0
                        or primary_target != receipt.target
                    ):
                        raise ReleaseContractError(f"{workload} completed with inexact counts")
                    self._require_not_after_deadline(
                        deadline,
                        context=f"{workload} service stabilization",
                    )
                    return
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
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(self.config.poll_seconds, remaining))
        self._require_deadline_remaining(
            deadline,
            context=f"{workload} service stabilization",
        )
        raise AssertionError("unreachable service stabilization deadline state")

    def verify_public_web(self, source_sha: str) -> None:
        deadline = time.monotonic() + self.config.timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                desired = int(self._service("web").get("desiredCount", 0))
                target_health = self.elbv2.describe_target_health(
                    TargetGroupArn=self.config.web_target_group_arn
                )
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
            time.sleep(self.config.poll_seconds)
        else:
            error_name = type(last_error).__name__ if last_error is not None else "unknown"
            raise ReleaseContractError(
                f"ALB target readiness did not become healthy ({error_name})"
            )

        while time.monotonic() < deadline:
            try:
                verify_health(self.config.base_url, source_sha)
                return
            except Exception as error:
                last_error = error
                time.sleep(self.config.poll_seconds)
        error_name = type(last_error).__name__ if last_error is not None else "unknown"
        raise ReleaseContractError(
            f"public readiness/liveness did not reach the exact source SHA ({error_name})"
        )

    def run_deployed_smoke(self, source_sha: str) -> None:
        try:
            run_http_smoke(
                self.config.base_url,
                source_sha,
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
                    "DTC_TEST_BASE_URL": self.config.base_url,
                    "DTC_EXPECTED_APP_VERSION": source_sha,
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
    ) -> None:
        for workload in ("web", "worker"):
            snapshot = self.capture_service(workload)
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
            deployments = self._deployments(workload, service)
            if any(item.get("status") not in {"PRIMARY", "ACTIVE"} for item in deployments):
                raise ReleaseContractError(f"terminal {workload} deployment status differs")
            primary = [item for item in deployments if item.get("status") == "PRIMARY"]
            if len(primary) != 1:
                raise ReleaseContractError(f"terminal {workload} has no unique PRIMARY deployment")
            deployment = primary[0]
            primary_id = self._deployment_id(workload, deployment)
            primary_target, primary_running, primary_pending, failed_tasks = (
                self._deployment_target_and_counts(workload, deployment)
            )
            if (
                primary_target
                != ServiceTarget(
                    expected_task_definitions[workload],
                    expected_desired_counts[workload],
                )
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
                snapshot.source_sha != expected_identity.source_sha
                or snapshot.image_digest != expected_identity.image_digest
            ):
                raise ReleaseContractError(f"terminal {workload} release identity differs")

            if expected_identity is not None:
                task_definition = self._task_definition(snapshot.task_definition_arn)
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


def die(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)
