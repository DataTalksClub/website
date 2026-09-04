"""Swap the image reference in a describe-task-definition response, in place.

Deliberately minimal, mirroring course-management-platform's
deploy/update_task_def.py: read the task definition the register-task-definition
API returned when it was last created, replace only the image and the VERSION
environment entry, strip the fields ECS forbids on RegisterTaskDefinition, and
write the result back out for `aws ecs register-task-definition
--cli-input-json` to consume directly.

Unlike course-management-platform, the image reference here is a digest
(`repo@sha256:...`), not a mutable tag: this deploys the same immutable-release
discipline the rest of this repository's Terraform and task-definition code
uses elsewhere. The caller supplies the exact `repo@sha256:...` reference; this
script never constructs one itself.
"""

from __future__ import annotations

import json
import sys

REGISTER_TASK_DEFINITION_EXCLUDED_FIELDS = (
    "status",
    "revision",
    "taskDefinitionArn",
    "requiresAttributes",
    "compatibilities",
    "registeredAt",
    "registeredBy",
)


def update_task_definition(input_file: str, image: str, version: str, output_file: str) -> None:
    with open(input_file, encoding="utf-8") as handle:
        task_definition = json.load(handle)["taskDefinition"]

    for container in task_definition["containerDefinitions"]:
        container["image"] = image
        environment = container.setdefault("environment", [])
        for entry in environment:
            if entry["name"] == "VERSION":
                entry["value"] = version
                break
        else:
            environment.append({"name": "VERSION", "value": version})

    for field in REGISTER_TASK_DEFINITION_EXCLUDED_FIELDS:
        task_definition.pop(field, None)

    with open(output_file, "w", encoding="utf-8") as handle:
        json.dump(task_definition, handle, indent=2)


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print(
            "Usage: python update_task_definition_image.py "
            "<input_file> <image_ref> <version> <output_file>",
            file=sys.stderr,
        )
        raise SystemExit(1)
    update_task_definition(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
