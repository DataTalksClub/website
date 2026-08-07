from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3  # type: ignore[import-untyped]

from deploy.contracts import (
    IMAGE_DIGEST_PATTERN,
    SOURCE_SHA_PATTERN,
    ReleaseContractError,
    ReleaseIdentity,
    ReleaseRecord,
    ServiceSnapshot,
)
from deploy.smoke import run_http_smoke, verify_health
from deploy.task_definitions import TaskDefinitionConfig, assert_normalized_task_definitions


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
    timeout_seconds: int = 900
    poll_seconds: int = 10


class AwsReleaseGateway:
    def __init__(self, config: AwsReleaseConfig) -> None:
        self.config = config
        self.ecs = boto3.client("ecs", region_name=config.region)
        self.elbv2 = boto3.client("elbv2", region_name=config.region)

    def _service(self, workload: str) -> dict[str, Any]:
        response = self.ecs.describe_services(
            cluster=self.config.cluster_arn,
            services=[self.config.service_names[workload]],
        )
        failures = response.get("failures", [])
        services = response.get("services", [])
        if failures or len(services) != 1:
            raise ReleaseContractError(
                f"cannot describe exact {workload} service: {failures or 'missing service'}"
            )
        return services[0]

    def _task_definition(self, reference: str) -> dict[str, Any]:
        response = self.ecs.describe_task_definition(taskDefinition=reference, include=["TAGS"])
        task = response.get("taskDefinition")
        if not isinstance(task, dict):
            raise ReleaseContractError(f"task definition {reference} is missing")
        return task

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
        task_definition_arn = service.get("taskDefinition")
        if not isinstance(task_definition_arn, str):
            raise ReleaseContractError(f"{workload} service has no task definition")
        source_sha, image_digest = self._identity(task_definition_arn, workload)
        return ServiceSnapshot(
            service_name=self.config.service_names[workload],
            task_definition_arn=task_definition_arn,
            desired_count=int(service.get("desiredCount", 0)),
            running_count=int(service.get("runningCount", 0)),
            pending_count=int(service.get("pendingCount", 0)),
            source_sha=source_sha,
            image_digest=image_digest,
        )

    def source_task_definition(self, workload: str) -> dict[str, Any]:
        return self._task_definition(self.config.task_families[workload])

    def verify_release_record(self, record: ReleaseRecord, identity: ReleaseIdentity) -> None:
        references = {
            "web": record.web_task_definition_arn,
            "worker": record.worker_task_definition_arn,
            "migration": record.migration_task_definition_arn,
        }
        tasks: dict[str, dict[str, Any]] = {}
        for workload, reference in references.items():
            cluster_arn_parts = self.config.cluster_arn.split(":")
            family_prefix = (
                ":".join(cluster_arn_parts[:5])
                + f":task-definition/{self.config.task_families[workload]}:"
            )
            if not reference.startswith(family_prefix):
                raise ReleaseContractError(f"release record {workload} family differs")
            tasks[workload] = self._task_definition(reference)
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

    def run_migration(self, task_definition_arn: str) -> None:
        response = self.ecs.run_task(
            cluster=self.config.cluster_arn,
            taskDefinition=task_definition_arn,
            count=1,
            launchType="FARGATE",
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": self.config.subnet_ids,
                    "securityGroups": self.config.security_group_ids,
                    "assignPublicIp": "ENABLED" if self.config.assign_public_ip else "DISABLED",
                }
            },
        )
        failures = response.get("failures", [])
        tasks = response.get("tasks", [])
        if failures or len(tasks) != 1 or not tasks[0].get("taskArn"):
            raise ReleaseContractError(
                f"migration task launch failed: {failures or 'missing task ARN'}"
            )
        task_arn = tasks[0]["taskArn"]
        deadline = time.monotonic() + self.config.timeout_seconds
        while time.monotonic() < deadline:
            observed = self.ecs.describe_tasks(cluster=self.config.cluster_arn, tasks=[task_arn])
            if observed.get("failures") or len(observed.get("tasks", [])) != 1:
                raise ReleaseContractError("migration task disappeared while waiting")
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
        self.ecs.stop_task(
            cluster=self.config.cluster_arn,
            task=task_arn,
            reason="immutable release migration timeout",
        )
        raise ReleaseContractError(f"migration timed out and was stopped: {task_arn}")

    def update_service(self, workload: str, task_definition_arn: str, desired_count: int) -> None:
        self.ecs.update_service(
            cluster=self.config.cluster_arn,
            service=self.config.service_names[workload],
            taskDefinition=task_definition_arn,
            desiredCount=desired_count,
            forceNewDeployment=True,
        )

    def wait_service_stable(self, workload: str, *, worker_singleton: bool = False) -> None:
        deadline = time.monotonic() + self.config.timeout_seconds
        while time.monotonic() < deadline:
            service = self._service(workload)
            running = int(service.get("runningCount", 0))
            pending = int(service.get("pendingCount", 0))
            desired = int(service.get("desiredCount", 0))
            if worker_singleton and running + pending > 1:
                raise ReleaseContractError("worker rollout exceeded one running/pending task")
            primary = [
                item for item in service.get("deployments", []) if item.get("status") == "PRIMARY"
            ]
            if len(primary) != 1:
                raise ReleaseContractError(f"{workload} service has no unique primary deployment")
            if primary[0].get("rolloutState") == "FAILED":
                raise ReleaseContractError(f"{workload} ECS deployment failed")
            if (
                running == desired
                and pending == 0
                and primary[0].get("runningCount") == desired
                and primary[0].get("pendingCount", 0) == 0
                and primary[0].get("rolloutState") == "COMPLETED"
            ):
                return
            time.sleep(self.config.poll_seconds)
        raise ReleaseContractError(f"{workload} service did not reach steady state")

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
            run_http_smoke(self.config.base_url, source_sha)
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
            )
        except subprocess.CalledProcessError as error:
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
