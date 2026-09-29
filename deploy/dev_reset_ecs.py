"""Fixed AWS boundary for the authorized development website schema rebuild."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

from deploy.dev_reset_tasks import (
    TARGET,
    ResetRefused,
    capture_service_tasks,
    wait_for_service_tasks,
)


def _aws(*arguments: str) -> dict[str, Any]:
    cli = "aws"
    if os.environ.get("DTC_TEST_NETWORK_DENY") == "1":
        cli = "awscli-fake"
    prefix = [cli, arguments[0], arguments[1]]
    remainder = arguments[2:]
    if arguments[1] == "wait":
        prefix.append(arguments[2])
        remainder = arguments[3:]
    command = [*prefix, "--region", TARGET.aws_region, "--output", "json", *remainder]
    timeout = 60
    if len(arguments) > 1 and arguments[1] == "wait":
        timeout = 900
    completed = subprocess.run(command, capture_output=True, check=False, timeout=timeout)
    if completed.returncode:
        raise ResetRefused("AWS target check or service operation failed")
    if not completed.stdout.strip():
        return {}
    try:
        return json.loads(completed.stdout)
    except ValueError as error:
        raise ResetRefused("AWS response was not JSON") from error


def _service_pair() -> dict[str, dict[str, Any]]:
    response = _aws(
        "ecs",
        "describe-services",
        "--cluster",
        TARGET.ecs_cluster_name,
        "--services",
        TARGET.web_service_name,
        TARGET.worker_service_name,
    )
    if response.get("failures") or len(response.get("services", [])) != 2:
        raise ResetRefused("development website services are unavailable")
    services: dict[str, dict[str, Any]] = {}
    for service in response["services"]:
        services[service.get("serviceName", "")] = service
    if set(services) != {TARGET.web_service_name, TARGET.worker_service_name}:
        raise ResetRefused("service names differ from the reviewed target")
    for name, family in (
        (TARGET.web_service_name, TARGET.web_task_family),
        (TARGET.worker_service_name, TARGET.worker_task_family),
    ):
        service = services[name]
        expected = TARGET.task_definition_arn_prefix(family)
        if service.get("serviceArn") != (
            f"arn:aws:ecs:{TARGET.aws_region}:{TARGET.aws_account_id}:service/"
            f"{TARGET.ecs_cluster_name}/{name}"
        ) or not service.get("taskDefinition", "").startswith(expected):
            raise ResetRefused("service identity differs from reviewed target")
    return services


def _assert_migration_secret() -> None:
    response = _aws(
        "ecs",
        "describe-task-definition",
        "--task-definition",
        TARGET.migration_task_family,
    )
    task = response.get("taskDefinition", {})
    if task.get("family") != TARGET.migration_task_family:
        raise ResetRefused("migration task family mismatch")
    container = task.get("containerDefinitions", [])
    if len(container) != 1 or container[0].get("name") != "migration":
        raise ResetRefused("migration container mismatch")
    database: list[str] = []
    for secret in container[0].get("secrets", []):
        if secret.get("name") == "DATABASE_URL":
            database.append(secret.get("valueFrom", ""))
    if len(database) != 1 or not TARGET.database_secret_arn_pattern.fullmatch(database[0]):
        raise ResetRefused("website development database secret mismatch")


def preflight() -> None:
    if os.environ.get("DTC_DEPLOYMENT_TARGET") != TARGET.name:
        raise ResetRefused("development target selector mismatch")
    if os.environ.get("AWS_REGION") != TARGET.aws_region:
        raise ResetRefused("AWS region mismatch")
    caller = _aws("sts", "get-caller-identity")
    if caller.get("Account") != TARGET.aws_account_id:
        raise ResetRefused("AWS account mismatch")
    if not caller.get("Arn", "").startswith(
        f"arn:aws:sts::{TARGET.aws_account_id}:assumed-role/website-dev-github-deployer/"
    ):
        raise ResetRefused("AWS deployer role mismatch")
    clusters = _aws("ecs", "describe-clusters", "--clusters", TARGET.ecs_cluster_name)
    expected_cluster = (
        f"arn:aws:ecs:{TARGET.aws_region}:{TARGET.aws_account_id}:cluster/{TARGET.ecs_cluster_name}"
    )
    if clusters.get("failures") or len(clusters.get("clusters", [])) != 1:
        raise ResetRefused("ECS cluster unavailable")
    if clusters["clusters"][0].get("clusterArn") != expected_cluster:
        raise ResetRefused("ECS cluster identity mismatch")
    _service_pair()
    _assert_migration_secret()


def _website_tasks(status: str) -> list[str]:
    response = _aws(
        "ecs",
        "list-tasks",
        "--cluster",
        TARGET.ecs_cluster_name,
        "--desired-status",
        status,
    )
    arns = response.get("taskArns", [])
    if not isinstance(arns, list):
        raise ResetRefused("ECS task listing malformed")
    found: list[str] = []
    for offset in range(0, len(arns), 100):
        batch = arns[offset : offset + 100]
        details = _aws(
            "ecs",
            "describe-tasks",
            "--cluster",
            TARGET.ecs_cluster_name,
            "--tasks",
            *batch,
        )
        if details.get("failures") or len(details.get("tasks", [])) != len(batch):
            raise ResetRefused("ECS task descriptions incomplete")
        for task in details["tasks"]:
            family = task.get("taskDefinitionArn", "").split("/")[-1].split(":")[0]
            if family.startswith("website-dev-") and task.get("lastStatus") != "STOPPED":
                found.append(task.get("taskArn", ""))
    return found


def assert_quiescent() -> None:
    services = _service_pair()
    for service in services.values():
        if any(service.get(key) != 0 for key in ("desiredCount", "runningCount", "pendingCount")):
            raise ResetRefused("a development website service is not drained")
    if _website_tasks("RUNNING") or _website_tasks("PENDING") or _website_tasks("STOPPED"):
        raise ResetRefused("a development website writer task remains active")


def observed_counts() -> dict[str, dict[str, int]]:
    services = _service_pair()
    observed: dict[str, dict[str, int]] = {}
    for name in (TARGET.web_service_name, TARGET.worker_service_name):
        service = services[name]
        counts: dict[str, int] = {}
        for field in ("desiredCount", "runningCount", "pendingCount"):
            count = service.get(field)
            if not isinstance(count, int) or count < 0:
                raise ResetRefused("service count is unavailable")
            counts[field] = count
        observed[name] = counts
    return observed


def _wait_until_quiescent(captured: dict[str, str] | None = None) -> None:
    _aws(
        "ecs",
        "wait",
        "services-stable",
        "--cluster",
        TARGET.ecs_cluster_name,
        "--services",
        TARGET.web_service_name,
        TARGET.worker_service_name,
    )
    if captured is not None:
        wait_for_service_tasks(_aws, captured)
    assert_quiescent()


def drain() -> None:
    services = _service_pair()
    captured = capture_service_tasks(_aws)
    for name in (TARGET.web_service_name, TARGET.worker_service_name):
        _aws(
            "ecs",
            "update-service",
            "--cluster",
            TARGET.ecs_cluster_name,
            "--service",
            name,
            "--task-definition",
            services[name]["taskDefinition"],
            "--desired-count",
            "0",
        )
    _wait_until_quiescent(captured)


def stop() -> None:
    services = _service_pair()
    failed = False
    for name in (TARGET.web_service_name, TARGET.worker_service_name):
        try:
            _aws(
                "ecs",
                "update-service",
                "--cluster",
                TARGET.ecs_cluster_name,
                "--service",
                name,
                "--task-definition",
                services[name]["taskDefinition"],
                "--desired-count",
                "0",
            )
        except (ResetRefused, OSError, subprocess.TimeoutExpired):
            failed = True
    if failed:
        raise ResetRefused("one or more development website services could not be stopped")
    _wait_until_quiescent()


def main() -> int:
    action = ""
    if len(sys.argv) > 1:
        action = sys.argv[1]
    try:
        if action == "preflight" and len(sys.argv) == 2:
            preflight()
        elif action == "drain" and len(sys.argv) == 2:
            drain()
        elif action == "quiescent" and len(sys.argv) == 2:
            assert_quiescent()
        elif action == "stop" and len(sys.argv) == 2:
            stop()
        elif action == "state" and len(sys.argv) == 2:
            print(json.dumps(observed_counts(), sort_keys=True))
        else:
            raise ResetRefused("unsupported dev reset operation")
    except ResetRefused as error:
        print(f"Development reset {action} refused: {error}", file=sys.stderr)
        return 1
    except (OSError, KeyError, ValueError, subprocess.TimeoutExpired):
        print(f"Development reset {action} refused or failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
