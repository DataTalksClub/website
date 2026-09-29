"""Target-scoped ECS secret references; never read or report secret values."""

from typing import Any

from deploy.contracts import ReleaseContractError
from deploy.deployment_targets import DeploymentTarget

RELAY_SECRET_NAMES = ("RELAY_API_KEY", "RELAY_WEBHOOK_SECRET")


def _references(container: dict[str, Any]) -> dict[str, str]:
    items = container.get("secrets", [])
    if not isinstance(items, list):
        raise ReleaseContractError("container secrets must be a list")
    references: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {"name", "valueFrom"}:
            raise ReleaseContractError("secret entries must contain name and valueFrom")
        name, value = item["name"], item["valueFrom"]
        if not isinstance(name, str) or not isinstance(value, str) or not value:
            raise ReleaseContractError("secret names and references must be non-empty strings")
        if name in references:
            raise ReleaseContractError("secret names must be unique")
        references[name] = value
    return references


def _relay_container(name: str, reference: str, target: DeploymentTarget) -> str:
    suffix = f":{name}::"
    container = reference.removesuffix(suffix)
    if not reference.endswith(suffix) or not target._secret_arn_pattern("integrations").fullmatch(
        container
    ):
        raise ReleaseContractError(f"{name} secret reference is outside the {target.name} boundary")
    return container


def secret_references(
    container: dict[str, Any], target: DeploymentTarget, *, active_promotion: bool = False
) -> tuple[tuple[str, str], ...]:
    references = _references(container)
    patterns = {
        "DATABASE_URL": target.database_secret_arn_pattern,
        "DJANGO_SECRET_KEY": target.django_secret_arn_pattern,
    }
    required = set(patterns)
    needs_relay = active_promotion and target.name == "website-development"
    if needs_relay:
        required.update(RELAY_SECRET_NAMES)
    if set(references) != required:
        raise ReleaseContractError("required target secret references differ")
    for name, pattern in patterns.items():
        if not pattern.fullmatch(references[name]):
            raise ReleaseContractError(
                f"{name} secret reference is outside the {target.name} boundary"
            )
    if needs_relay:
        containers = []
        for name in RELAY_SECRET_NAMES:
            containers.append(_relay_container(name, references[name], target))
        if len(set(containers)) != 1:
            raise ReleaseContractError("Relay references must use the same integrations secret")
    return tuple(sorted(references.items()))
