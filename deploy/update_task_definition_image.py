"""Promote one described task definition to a release identity (REL-08).

The deploy orchestrator hands this module the task definition each service is
running *right now* -- its active ``taskDefinition`` ARN from
``describe-services``, never whichever revision of the family was registered
most recently.  The source document is first validated against the reviewed
deployment target (exact roles, the target's runtime platform, a single
application container with the workload's own name -- the architecture forbids
sidecars --, target-scoped secret references, and the reviewed command for the
long-running services), so a malformed or foreign response fails closed
before anything is registered.

Only then is the release identity rewritten: the immutable image digest
reference and the complete identity environment.  Everything the release
pipeline does not own (Terraform-managed sizing, networking, and the rest of
the container environment) is carried over unchanged.

The deployment target is selected the same closed way as everywhere else in
``deploy/``: ``DTC_DEPLOYMENT_TARGET`` names a member of
``deploy.deployment_targets.DEPLOYMENT_TARGETS``, resolved once at import.

Run as a module so the ``deploy`` package imports::

    python3 -m deploy.update_task_definition_image <input> <image> <version>
        <source-sha> <image-digest> <workload> <output>
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from deploy.contracts import ReleaseContractError
from deploy.deployment_targets import SELECTED_TARGET, DeploymentTarget
from deploy.task_definitions import config_for_target, validate_source_workload

REGISTER_TASK_DEFINITION_EXCLUDED_FIELDS = (
    "status",
    "revision",
    "taskDefinitionArn",
    "requiresAttributes",
    "compatibilities",
    "registeredAt",
    "registeredBy",
)


IDENTITY_NAMES = frozenset(
    {
        "APP_VERSION",
        "VERSION",
        "SOURCE_SHA",
        "IMAGE_DIGEST",
        "DTC_ENVIRONMENT",
        "DTC_DEVELOPMENT_HOSTNAME",
        "DJANGO_SETTINGS_MODULE",
        "PUBLIC_MEDIA_S3_BUCKET",
        "PUBLIC_MEDIA_S3_REGION",
        "PUBLIC_MEDIA_STORE_BACKEND",
    }
)

#: The digest reference the pipeline publishes and promotes; nothing else may
#: be written into a task definition as the release image.
IMAGE_REFERENCE_PATTERN = re.compile(r"^.+@sha256:[0-9a-f]{64}$")


def _development_hostname(target: DeploymentTarget) -> str:
    """The pinned development host for development-settings targets, else ''."""

    if target.settings_module == "website.settings.development":
        return target.hostname
    return ""


def update_task_definition(
    input_file: str,
    image: str,
    version: str,
    source_sha: str,
    image_digest: str,
    workload: str,
    output_file: str,
) -> None:
    if IMAGE_REFERENCE_PATTERN.fullmatch(image) is None:
        raise ReleaseContractError("image must be an immutable repo@sha256:... digest reference")
    if image != f"{SELECTED_TARGET.ecr_repository_uri}@{image_digest}":
        raise ReleaseContractError(
            f"image is not a digest in the {SELECTED_TARGET.name} repository"
        )
    with open(input_file, encoding="utf-8") as handle:
        document = json.load(handle)
    task = document.get("taskDefinition", document)

    config = config_for_target(SELECTED_TARGET)
    validate_source_workload(task, workload, config, SELECTED_TARGET)

    containers = task["containerDefinitions"]
    container = containers[0]
    container["image"] = image
    environment = [
        entry
        for entry in container.setdefault("environment", [])
        if entry.get("name") not in IDENTITY_NAMES
    ]
    environment.extend(
        [
            {"name": "IMAGE_DIGEST", "value": image_digest},
            {"name": "SOURCE_SHA", "value": source_sha},
            {"name": "VERSION", "value": version},
            {"name": "DTC_ENVIRONMENT", "value": SELECTED_TARGET.dtc_environment},
            {"name": "DJANGO_SETTINGS_MODULE", "value": SELECTED_TARGET.settings_module},
            *(
                {"name": name, "value": value}
                for name, value in SELECTED_TARGET.public_media_environment.items()
            ),
        ]
    )
    development_hostname = _development_hostname(SELECTED_TARGET)
    if development_hostname:
        environment.append({"name": "DTC_DEVELOPMENT_HOSTNAME", "value": development_hostname})
    container["environment"] = sorted(environment, key=lambda entry: entry["name"])

    for field in REGISTER_TASK_DEFINITION_EXCLUDED_FIELDS:
        task.pop(field, None)

    written_environment = {entry["name"]: entry["value"] for entry in container["environment"]}
    expected_identity = {
        "IMAGE_DIGEST": image_digest,
        "SOURCE_SHA": source_sha,
        "VERSION": version,
        "DTC_ENVIRONMENT": SELECTED_TARGET.dtc_environment,
        "DJANGO_SETTINGS_MODULE": SELECTED_TARGET.settings_module,
        **SELECTED_TARGET.public_media_environment,
    }
    if development_hostname:
        expected_identity["DTC_DEVELOPMENT_HOSTNAME"] = development_hostname
    for name, value in expected_identity.items():
        if written_environment.get(name) != value:
            raise ReleaseContractError(f"{workload} written {name} differs from the target")
    if "APP_VERSION" in written_environment:
        raise ReleaseContractError(f"{workload} must not deploy APP_VERSION")

    with open(output_file, "w", encoding="utf-8") as handle:
        json.dump(task, handle, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a described task definition against the selected deployment "
            "target and rewrite its release identity"
        )
    )
    parser.add_argument("input_file")
    parser.add_argument("image")
    parser.add_argument("version")
    parser.add_argument("source_sha")
    parser.add_argument("image_digest")
    parser.add_argument("workload", choices=("web", "worker", "migration"))
    parser.add_argument("output_file")
    arguments = parser.parse_args()
    try:
        update_task_definition(
            arguments.input_file,
            arguments.image,
            arguments.version,
            arguments.source_sha,
            arguments.image_digest,
            arguments.workload,
            arguments.output_file,
        )
    except ReleaseContractError as error:
        print(f"Task definition promotion refused: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
