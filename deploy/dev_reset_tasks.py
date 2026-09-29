"""Capture and await only the reviewed development website service tasks."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from deploy.deployment_targets import registered_target

TARGET = registered_target("website-development")
AwsCall = Callable[..., dict[str, Any]]
STOP_TIMEOUT_SECONDS = 300
POLL_SECONDS = 6
CLUSTER_ARN = (
    f"arn:aws:ecs:{TARGET.aws_region}:{TARGET.aws_account_id}:cluster/{TARGET.ecs_cluster_name}"
)
TASK_ARN_PATTERN = re.compile(
    f"arn:aws:ecs:{TARGET.aws_region}:{TARGET.aws_account_id}:task/"
    + re.escape(TARGET.ecs_cluster_name)
    + r"/[0-9a-f]{32}"
)
SERVICE_FAMILIES = {
    TARGET.web_service_name: TARGET.web_task_family,
    TARGET.worker_service_name: TARGET.worker_task_family,
}
TASK_STATUSES = {
    "PROVISIONING",
    "PENDING",
    "ACTIVATING",
    "RUNNING",
    "DEACTIVATING",
    "STOPPING",
    "DEPROVISIONING",
    "STOPPED",
}


class ResetRefused(RuntimeError):
    """A fixed, safe explanation of an unproven development reset boundary."""


def _service_task_arns(aws: AwsCall, service: str, status: str) -> list[str]:
    response = aws(
        "ecs",
        "list-tasks",
        "--cluster",
        TARGET.ecs_cluster_name,
        "--service-name",
        service,
        "--desired-status",
        status,
    )
    arns = response.get("taskArns")
    if response.get("nextToken") or not isinstance(arns, list) or len(arns) > 100:
        raise ResetRefused("drain task observation incomplete")
    for arn in arns:
        if not isinstance(arn, str) or not TASK_ARN_PATTERN.fullmatch(arn):
            raise ResetRefused("drain task identity mismatch")
    if len(set(arns)) != len(arns):
        raise ResetRefused("drain task observation incomplete")
    return arns


def _validate_task(task: dict[str, Any], service: str) -> None:
    family = SERVICE_FAMILIES[service]
    definition = task.get("taskDefinitionArn", "")
    pattern = re.escape(TARGET.task_definition_arn_prefix(family)) + r"[1-9][0-9]*"
    if (
        task.get("clusterArn") != CLUSTER_ARN
        or task.get("group") != f"service:{service}"
        or not isinstance(definition, str)
        or not re.fullmatch(pattern, definition)
    ):
        raise ResetRefused("drain task identity mismatch")
    if not isinstance(task.get("lastStatus"), str) or task["lastStatus"] not in TASK_STATUSES:
        raise ResetRefused("drain task observation incomplete")


def _observed_tasks(aws: AwsCall, captured: dict[str, str]) -> list[dict[str, Any]]:
    response = aws(
        "ecs",
        "describe-tasks",
        "--cluster",
        TARGET.ecs_cluster_name,
        "--tasks",
        *captured,
    )
    tasks = response.get("tasks")
    if response.get("failures") or not isinstance(tasks, list) or len(tasks) != len(captured):
        raise ResetRefused("drain task observation incomplete")
    seen = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ResetRefused("drain task observation incomplete")
        arn = task.get("taskArn")
        if not isinstance(arn, str) or arn not in captured or arn in seen:
            raise ResetRefused("drain task identity mismatch")
        _validate_task(task, captured[arn])
        seen.add(arn)
    return tasks


def capture_service_tasks(aws: AwsCall) -> dict[str, str]:
    captured: dict[str, str] = {}
    for service in SERVICE_FAMILIES:
        for status in ("RUNNING", "STOPPED"):
            for arn in _service_task_arns(aws, service, status):
                if arn in captured and captured[arn] != service:
                    raise ResetRefused("drain task identity mismatch")
                captured[arn] = service
    if len(captured) > 100:
        raise ResetRefused("drain task observation exceeds bounded task limit")
    if captured:
        _observed_tasks(aws, captured)
    return captured


def wait_for_service_tasks(aws: AwsCall, captured: dict[str, str]) -> None:
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while captured:
        tasks = _observed_tasks(aws, captured)
        if all(task["lastStatus"] == "STOPPED" for task in tasks):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ResetRefused("drain task termination timed out; captured task still active")
        time.sleep(min(POLL_SECONDS, remaining))
