from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from deploy.contracts import ReleaseContractError, ReleaseIdentity
from deploy.legacy_development_compatibility import (
    DATABASE_SECRET_ARN_PATTERN,
    DJANGO_SECRET_ARN_PATTERN,
)

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
SECRET_ARN_PATTERNS = {
    "DATABASE_URL": DATABASE_SECRET_ARN_PATTERN,
    "DJANGO_SECRET_KEY": DJANGO_SECRET_ARN_PATTERN,
}
SAFETY_ENVIRONMENT = {
    "DATAMAILER_SYNC_ON_USER_CREATE": "0",
    "DATAMAILER_OUTBOX_DISPATCH_IMMEDIATELY": "0",
    "DATAMAILER_TRANSACTIONAL_DRY_RUN": "1",
    "DATAMAILER_URL": "",
    "DATAMAILER_API_KEY": "",
}
# The release image deliberately excludes content/public_projection/media (see
# .dockerignore), so a deployed workload cannot serve the projection images from its own
# filesystem.  It reads them from the published object store instead, at unchanged public
# /images/... URLs.  The CloudFront distribution in front of the bucket is an origin/edge
# detail: Django is the origin for /images/..., so no public URL moves to a CDN hostname.
PUBLIC_MEDIA_ENVIRONMENT = {
    "PUBLIC_MEDIA_STORE_BACKEND": "s3",
    "PUBLIC_MEDIA_S3_BUCKET": "dtc-website-media",
    "PUBLIC_MEDIA_S3_REGION": "eu-west-1",
}
FIXED_NONSECRET_ENVIRONMENT = {
    "CANONICAL_ORIGIN": "https://datatalks.club",
    "DJANGO_ALLOWED_HOSTS": "web.dtcdev.click",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://web.dtcdev.click",
    "DJANGO_SETTINGS_MODULE": "website.settings.development",
    "DTC_ENVIRONMENT": "development",
    "OBSERVABILITY_EVENT_BACKENDS": "log",
    "WEB_CONCURRENCY": "2",
    "AWS_DEFAULT_REGION": "eu-west-1",
    "AWS_REGION": "eu-west-1",
    **PUBLIC_MEDIA_ENVIRONMENT,
    **SAFETY_ENVIRONMENT,
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
    names = [name for name, _ in result]
    if len(names) != len(set(names)):
        raise ReleaseContractError("secret names must be unique")
    if set(names) != REQUIRED_SECRET_NAMES or len(result) != len(REQUIRED_SECRET_NAMES):
        raise ReleaseContractError(
            "exactly DATABASE_URL and DJANGO_SECRET_KEY secret references are required"
        )
    for name, value in result:
        if not SECRET_ARN_PATTERNS[name].fullmatch(value):
            raise ReleaseContractError(
                f"{name} secret reference is outside the development boundary"
            )
    return tuple(sorted(result))


def build_task_definitions(
    source_tasks: dict[str, dict[str, Any]],
    identity: ReleaseIdentity,
    config: TaskDefinitionConfig,
) -> dict[str, dict[str, Any]]:
    if identity.identity_schema != 2:
        raise ReleaseContractError("new task definitions require a schema-2 release identity")
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
        for identity_name in ("APP_VERSION", "VERSION", "SOURCE_SHA", "IMAGE_DIGEST"):
            source_environment.pop(identity_name, None)
        # The media-store variables are introduced by this release, so the currently
        # deployed task definition is allowed to omit them exactly once.  It is never
        # allowed to *contradict* them: a wrong backend, bucket, or region in the source
        # is a hard failure rather than something the normalizer silently overwrites.
        for name, value in PUBLIC_MEDIA_ENVIRONMENT.items():
            if source_environment.setdefault(name, value) != value:
                raise ReleaseContractError(
                    f"{workload} source environment sets an unexpected {name}"
                )
        if source_environment != FIXED_NONSECRET_ENVIRONMENT:
            raise ReleaseContractError(
                f"{workload} source environment differs from the development contract"
            )
        source_environments.append(source_environment)
        source_secrets.append(_secrets(container))

    if any(environment != source_environments[0] for environment in source_environments[1:]):
        raise ReleaseContractError("source workload non-secret environments differ")
    if any(secrets != source_secrets[0] for secrets in source_secrets[1:]):
        raise ReleaseContractError("source workload secret references differ")

    common_environment = {
        **FIXED_NONSECRET_ENVIRONMENT,
        "VERSION": identity.version,
        "SOURCE_SHA": identity.source_sha,
        "IMAGE_DIGEST": identity.image_digest,
    }
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


def _assert_normalized_workloads(
    tasks: dict[str, dict[str, Any]],
    identity: ReleaseIdentity,
    config: TaskDefinitionConfig,
    workloads: tuple[str, ...],
) -> None:
    if set(tasks) != set(workloads):
        raise ReleaseContractError("normalized task set differs from the expected workloads")
    environments: list[dict[str, str]] = []
    secrets: list[tuple[tuple[str, str], ...]] = []
    for workload in workloads:
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
        expected_identity = (
            {
                "VERSION": identity.version,
                "SOURCE_SHA": identity.source_sha,
                "IMAGE_DIGEST": identity.image_digest,
            }
            if identity.identity_schema == 2
            else {"APP_VERSION": identity.source_sha}
        )
        if {name: environment.get(name) for name in expected_identity} != expected_identity:
            raise ReleaseContractError(f"{workload} release identity differs")
        if identity.identity_schema == 2 and "APP_VERSION" in environment:
            raise ReleaseContractError(f"{workload} must not deploy APP_VERSION")
        if identity.identity_schema == 1 and any(
            name in environment for name in ("VERSION", "SOURCE_SHA", "IMAGE_DIGEST")
        ):
            raise ReleaseContractError(f"{workload} legacy release identity is mixed")
        for name, value in SAFETY_ENVIRONMENT.items():
            if environment.get(name) != value:
                raise ReleaseContractError(f"{workload} has unsafe {name}")
        expected_environment = {
            **FIXED_NONSECRET_ENVIRONMENT,
            **expected_identity,
        }
        if environment != expected_environment:
            raise ReleaseContractError(f"{workload} non-secret environment is not exact")
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


def assert_normalized_task_definitions(
    tasks: dict[str, dict[str, Any]],
    identity: ReleaseIdentity,
    config: TaskDefinitionConfig,
) -> None:
    _assert_normalized_workloads(tasks, identity, config, WORKLOADS)


def assert_normalized_service_pair(
    tasks: dict[str, dict[str, Any]],
    identity: ReleaseIdentity,
    config: TaskDefinitionConfig,
) -> None:
    _assert_normalized_workloads(tasks, identity, config, ("web", "worker"))
