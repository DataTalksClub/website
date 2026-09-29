"""Synthetic source definitions for the active deployment contract tests."""

from deploy.deployment_targets import DeploymentTarget

RELAY_URL = "http://relay.example.invalid:8080/private-origin"


def secret_references(target: DeploymentTarget) -> list[dict[str, str]]:
    prefix = (
        f"arn:aws:secretsmanager:{target.aws_region}:{target.aws_account_id}"
        f":secret:{target.resource_namespace}/"
    )
    references = [
        {"name": "DATABASE_URL", "valueFrom": prefix + "database-url-abc123"},
        {"name": "DJANGO_SECRET_KEY", "valueFrom": prefix + "django-secret-key-abc123"},
    ]
    if target.name == "website-development":
        for name in ("RELAY_API_KEY", "RELAY_WEBHOOK_SECRET"):
            references.append({"name": name, "valueFrom": prefix + f"integrations-abc123:{name}::"})
    return references


def source_container(target: DeploymentTarget, workload: str) -> dict:
    container = {
        "name": workload,
        "image": "old-image:tag",
        "command": [workload],
        "environment": [{"name": "RELAY_BASE_URL", "value": RELAY_URL}],
        "secrets": secret_references(target),
        "portMappings": [{"containerPort": 8000}],
    }
    if workload == "migration":
        container["entryPoint"] = ["uv", "run", "--no-sync", "python", "manage.py"]
        container["command"] = ["migrate", "--noinput"]
    return container


def source_document(target: DeploymentTarget, workload: str, revision: int = 7) -> dict:
    family = f"{target.resource_namespace}-{workload}"
    return {
        "taskDefinition": {
            "family": family,
            "revision": revision,
            "status": "ACTIVE",
            "taskDefinitionArn": target.task_definition_arn_prefix(family) + str(revision),
            "cpu": "512",
            "memory": "1024",
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "runtimePlatform": {
                "cpuArchitecture": target.task_cpu_architecture,
                "operatingSystemFamily": "LINUX",
            },
            "executionRoleArn": target.execution_role_arn,
            "taskRoleArn": target.task_role_arn,
            "containerDefinitions": [source_container(target, workload)],
        }
    }
