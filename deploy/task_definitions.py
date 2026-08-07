from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from deploy.contracts import ReleaseContractError, ReleaseIdentity

WORKLOADS = ("web", "worker", "migration")
REGISTERABLE_FIELDS = {
    "containerDefinitions",
    "cpu",
    "ephemeralStorage",
    "executionRoleArn",
    "family",
    "ipcMode",
    "memory",
    "networkMode",
    "pidMode",
    "placementConstraints",
    "proxyConfiguration",
    "requiresCompatibilities",
    "runtimePlatform",
    "taskRoleArn",
    "volumes",
}
REQUIRED_SECRET_NAMES = {"DATABASE_URL", "DJANGO_SECRET_KEY"}
SAFETY_ENVIRONMENT = {
    "DATAMAILER_SYNC_ON_USER_CREATE": "0",
    "DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY": "0",
    "DATAMAILER_TRANSACTIONAL_DRY_RUN": "1",
    "DATAMAILER_URL": "",
    "DATAMAILER_API_KEY": "",
}
COMMANDS = {
    "web": {"command": ["web"]},
    "worker": {"command": ["worker"]},
    "migration": {
        "entryPoint": ["uv", "run", "--no-sync", "python", "manage.py"],
        "command": ["migrate", "--noinput"],
    },
}


@dataclass(frozen=True)
class TaskDefinitionConfig:
    families: dict[str, str]
    container_names: dict[str, str]
    task_role_arn: str
    execution_role_arn: str

    def __post_init__(self) -> None:
        for name, values in (
            ("families", self.families),
            ("container_names", self.container_names),
        ):
            if set(values) != set(WORKLOADS) or any(not value for value in values.values()):
                raise ReleaseContractError(f"{name} must define web, worker, and migration")


def _only_container(task: dict[str, Any], expected_name: str) -> dict[str, Any]:
    containers = task.get("containerDefinitions")
    if not isinstance(containers, list) or len(containers) != 1:
        raise ReleaseContractError(
            "each workload task definition must contain exactly one container"
        )
    container = containers[0]
    if container.get("name") != expected_name:
        raise ReleaseContractError(f"expected container {expected_name!r}")
    return container


def _environment(container: dict[str, Any]) -> dict[str, str]:
    if container.get("environmentFiles") not in (None, []):
        raise ReleaseContractError("environment files are not permitted in normalized tasks")
    items = container.get("environment", [])
    if not isinstance(items, list):
        raise ReleaseContractError("container environment must be a list")
    result: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {"name", "value"}:
            raise ReleaseContractError("container environment entries must contain name and value")
        name, value = item["name"], item["value"]
        if not isinstance(name, str) or not isinstance(value, str) or name in result:
            raise ReleaseContractError("container environment names must be unique strings")
        result[name] = value
    return result


def _secrets(container: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    items = container.get("secrets", [])
    if not isinstance(items, list):
        raise ReleaseContractError("container secrets must be a list")
    result: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"name", "valueFrom"}:
            raise ReleaseContractError("secret entries must contain name and valueFrom")
        name, value = item["name"], item["valueFrom"]
        if not isinstance(name, str) or not isinstance(value, str) or not value:
            raise ReleaseContractError("secret names and references must be non-empty strings")
        result.append((name, value))
    if len(result) != len(set(result)):
        raise ReleaseContractError("secret references must be unique")
    names = {name for name, _ in result}
    if not REQUIRED_SECRET_NAMES.issubset(names):
        raise ReleaseContractError(
            "DATABASE_URL and DJANGO_SECRET_KEY secret references are required"
        )
    return tuple(sorted(result))


def build_task_definitions(
    source_tasks: dict[str, dict[str, Any]],
    identity: ReleaseIdentity,
    config: TaskDefinitionConfig,
) -> dict[str, dict[str, Any]]:
    if set(source_tasks) != set(WORKLOADS):
        raise ReleaseContractError("source tasks must define web, worker, and migration")

    source_environments: list[dict[str, str]] = []
    source_secrets: list[tuple[tuple[str, str], ...]] = []
    for workload in WORKLOADS:
        task = source_tasks[workload].get("taskDefinition", source_tasks[workload])
        if task.get("taskRoleArn") != config.task_role_arn:
            raise ReleaseContractError(f"{workload} task role differs from the expected exact ARN")
        if task.get("executionRoleArn") != config.execution_role_arn:
            raise ReleaseContractError(
                f"{workload} execution role differs from the expected exact ARN"
            )
        container = _only_container(task, config.container_names[workload])
        source_environment = _environment(container)
        for normalized_name in {"APP_VERSION", *SAFETY_ENVIRONMENT}:
            source_environment.pop(normalized_name, None)
        source_environments.append(source_environment)
        source_secrets.append(_secrets(container))

    if any(environment != source_environments[0] for environment in source_environments[1:]):
        raise ReleaseContractError("source workload non-secret environments differ")
    if any(secrets != source_secrets[0] for secrets in source_secrets[1:]):
        raise ReleaseContractError("source workload secret references differ")

    common_environment = (
        source_environments[0] | SAFETY_ENVIRONMENT | {"APP_VERSION": identity.source_sha}
    )
    normalized: dict[str, dict[str, Any]] = {}
    for workload in WORKLOADS:
        source = source_tasks[workload].get("taskDefinition", source_tasks[workload])
        task = {key: deepcopy(value) for key, value in source.items() if key in REGISTERABLE_FIELDS}
        task["family"] = config.families[workload]
        task["taskRoleArn"] = config.task_role_arn
        task["executionRoleArn"] = config.execution_role_arn
        container = _only_container(task, config.container_names[workload])
        container["image"] = identity.image
        container["user"] = "10001:10001"
        container["essential"] = True
        container["environment"] = [
            {"name": name, "value": value} for name, value in sorted(common_environment.items())
        ]
        container["secrets"] = [
            {"name": name, "valueFrom": value} for name, value in source_secrets[0]
        ]
        container.pop("entryPoint", None)
        container.pop("command", None)
        container.update(COMMANDS[workload])
        normalized[workload] = task

    assert_normalized_task_definitions(normalized, identity, config)
    return normalized


def assert_normalized_task_definitions(
    tasks: dict[str, dict[str, Any]],
    identity: ReleaseIdentity,
    config: TaskDefinitionConfig,
) -> None:
    environments: list[dict[str, str]] = []
    secrets: list[tuple[tuple[str, str], ...]] = []
    for workload in WORKLOADS:
        task = tasks[workload]
        if task.get("family") != config.families[workload]:
            raise ReleaseContractError(f"{workload} family mismatch")
        if task.get("taskRoleArn") != config.task_role_arn:
            raise ReleaseContractError(f"{workload} task role mismatch")
        if task.get("executionRoleArn") != config.execution_role_arn:
            raise ReleaseContractError(f"{workload} execution role mismatch")
        container = _only_container(task, config.container_names[workload])
        if container.get("image") != identity.image:
            raise ReleaseContractError(f"{workload} image is not the exact immutable digest")
        if container.get("user") != "10001:10001":
            raise ReleaseContractError(f"{workload} must run as 10001:10001")
        environment = _environment(container)
        if environment.get("APP_VERSION") != identity.source_sha:
            raise ReleaseContractError(f"{workload} APP_VERSION is not the source SHA")
        for name, value in SAFETY_ENVIRONMENT.items():
            if environment.get(name) != value:
                raise ReleaseContractError(f"{workload} has unsafe {name}")
        environments.append(environment)
        secrets.append(_secrets(container))
        for field, expected in COMMANDS[workload].items():
            if container.get(field) != expected:
                raise ReleaseContractError(f"{workload} {field} mismatch")
        if workload != "migration" and "entryPoint" in container:
            raise ReleaseContractError(f"{workload} must retain the image entrypoint")
    if len({tuple(sorted(item.items())) for item in environments}) != 1:
        raise ReleaseContractError("normalized non-secret environments differ")
    if len(set(secrets)) != 1:
        raise ReleaseContractError("normalized secret references differ")
