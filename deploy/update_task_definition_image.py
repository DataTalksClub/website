"""Swap the release identity in a describe-task-definition response.

Deliberately minimal, mirroring course-management-platform's
deploy/update_task_def.py: read the task definition the register-task-definition
API returned when it was last created, replace only the image and the complete
release identity, strip the fields ECS forbids on RegisterTaskDefinition, and
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


IDENTITY_NAMES = frozenset({"APP_VERSION", "VERSION", "SOURCE_SHA", "IMAGE_DIGEST"})


def update_task_definition(
    input_file: str,
    image: str,
    version: str,
    source_sha: str,
    image_digest: str,
    output_file: str,
) -> None:
    with open(input_file, encoding="utf-8") as handle:
        task_definition = json.load(handle)["taskDefinition"]

    for container in task_definition["containerDefinitions"]:
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
            ]
        )
        container["environment"] = sorted(environment, key=lambda entry: entry["name"])

    for field in REGISTER_TASK_DEFINITION_EXCLUDED_FIELDS:
        task_definition.pop(field, None)

    with open(output_file, "w", encoding="utf-8") as handle:
        json.dump(task_definition, handle, indent=2)


if __name__ == "__main__":
    if len(sys.argv) != 7:
        print(
            "Usage: python update_task_definition_image.py "
            "<input_file> <image_ref> <version> <source_sha> "
            "<image_digest> <output_file>",
            file=sys.stderr,
        )
        raise SystemExit(1)
    update_task_definition(
        sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]
    )
