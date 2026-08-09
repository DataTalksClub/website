"""One explicit boundary for legacy physical names used by development.

The deployment at ``web.dtcdev.click`` is the development environment. Its
physical AWS, Terraform, GitHub-environment, and OIDC identifiers predate that
terminology and remain unchanged until issue #94 performs a state-safe rename.
Current callers should use development-named symbols from this module rather
than repeating those physical strings.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping
from typing import Any

from deploy.contracts import ReleaseContractError

AWS_ACCOUNT_ID = "817685572750"
AWS_REGION = "eu-west-1"
ORIGIN = "https://web.dtcdev.click"

# These values are physical compatibility identifiers, not environment labels.
GITHUB_ENVIRONMENT_NAME = "sandbox"
TERRAFORM_ROOT = "sandbox/website"
STATE_BUCKET = "datamailer-sandbox-817685572750-us-east-1-tfstate"
STATE_KEY = f"{TERRAFORM_ROOT}/terraform.tfstate"
RESOURCE_NAMESPACE = "website-sandbox"
ECR_REPOSITORY_NAME = RESOURCE_NAMESPACE
ECR_REPOSITORY_URI = f"{AWS_ACCOUNT_ID}.dkr.ecr.{AWS_REGION}.amazonaws.com/{ECR_REPOSITORY_NAME}"
ECS_CLUSTER_ARN = f"arn:aws:ecs:{AWS_REGION}:{AWS_ACCOUNT_ID}:cluster/{RESOURCE_NAMESPACE}"
WEB_SERVICE_NAME = f"{RESOURCE_NAMESPACE}-web"
WORKER_SERVICE_NAME = f"{RESOURCE_NAMESPACE}-worker"
WEB_TASK_FAMILY = WEB_SERVICE_NAME
WORKER_TASK_FAMILY = WORKER_SERVICE_NAME
MIGRATION_TASK_FAMILY = f"{RESOURCE_NAMESPACE}-migration"
PUBLISHER_ROLE_ARN = f"arn:aws:iam::{AWS_ACCOUNT_ID}:role/{RESOURCE_NAMESPACE}-github-publisher"
DEPLOYER_ROLE_ARN = f"arn:aws:iam::{AWS_ACCOUNT_ID}:role/{RESOURCE_NAMESPACE}-github-deployer"
TASK_ROLE_ARN = f"arn:aws:iam::{AWS_ACCOUNT_ID}:role/{RESOURCE_NAMESPACE}-task-application"
EXECUTION_ROLE_ARN = f"arn:aws:iam::{AWS_ACCOUNT_ID}:role/{RESOURCE_NAMESPACE}-task-execution"
TARGET_GROUP_ARN_PATTERN = re.compile(
    rf"^arn:aws:elasticloadbalancing:{AWS_REGION}:{AWS_ACCOUNT_ID}:"
    rf"targetgroup/{RESOURCE_NAMESPACE}-web/[0-9a-f]{{16}}$"
)
DATABASE_SECRET_ARN_PATTERN = re.compile(
    rf"^arn:aws:secretsmanager:{AWS_REGION}:{AWS_ACCOUNT_ID}:"
    rf"secret:{RESOURCE_NAMESPACE}/database-url-[A-Za-z0-9]{{6}}$"
)
DJANGO_SECRET_ARN_PATTERN = re.compile(
    rf"^arn:aws:secretsmanager:{AWS_REGION}:{AWS_ACCOUNT_ID}:"
    rf"secret:{RESOURCE_NAMESPACE}/django-secret-key-[A-Za-z0-9]{{6}}$"
)
ROUTE53_HOSTED_ZONE_ID = "Z05963572WVWFHDQZH5NE"
KMS_KEY_ARN = f"arn:aws:kms:{AWS_REGION}:{AWS_ACCOUNT_ID}:key/b9181223-d870-4bae-92d2-fc28b7813887"
RESOURCE_PROJECT_TAG = "website"
RESOURCE_ENVIRONMENT_TAG = "sandbox"
CONTAINER_NAMES = '{"migration":"migration","web":"web","worker":"worker"}'
SUBNET_IDS = '["subnet-0614747082352aecf","subnet-001e1960dcfc4fa60"]'
SECURITY_GROUP_IDS = '["sg-0641397bd1c2083f0"]'

_COMMON_EXPECTED = {
    "AWS_REGION": AWS_REGION,
    "ECR_REPOSITORY_URI": ECR_REPOSITORY_URI,
}
_PROFILES: dict[str, dict[str, str]] = {
    "publisher": {
        **_COMMON_EXPECTED,
        "ECR_REPOSITORY_NAME": ECR_REPOSITORY_NAME,
        "PUBLISHER_ROLE_ARN": PUBLISHER_ROLE_ARN,
    },
    "publisher-probe": {
        "AWS_REGION": AWS_REGION,
        "ECR_REPOSITORY_NAME": ECR_REPOSITORY_NAME,
        "HOSTED_ZONE_ID": ROUTE53_HOSTED_ZONE_ID,
        "KMS_KEY_ARN": KMS_KEY_ARN,
        "PUBLISHER_ROLE_ARN": PUBLISHER_ROLE_ARN,
    },
    "deployer-probe": {
        "AWS_REGION": AWS_REGION,
        "DEPLOYER_ROLE_ARN": DEPLOYER_ROLE_ARN,
        "ECR_REPOSITORY_NAME": ECR_REPOSITORY_NAME,
        "ECS_CLUSTER_ARN": ECS_CLUSTER_ARN,
        "HOSTED_ZONE_ID": ROUTE53_HOSTED_ZONE_ID,
        "KMS_KEY_ARN": KMS_KEY_ARN,
        "WEB_SERVICE_NAME": WEB_SERVICE_NAME,
        "WORKER_SERVICE_NAME": WORKER_SERVICE_NAME,
        "WEB_FAMILY": WEB_TASK_FAMILY,
        "WORKER_FAMILY": WORKER_TASK_FAMILY,
        "MIGRATION_FAMILY": MIGRATION_TASK_FAMILY,
    },
    "main-claim-probe": {
        "AWS_REGION": AWS_REGION,
        "DEPLOYER_ROLE_ARN": DEPLOYER_ROLE_ARN,
        "PUBLISHER_ROLE_ARN": PUBLISHER_ROLE_ARN,
    },
    "environment-claim-probe": {
        "AWS_REGION": AWS_REGION,
        "PUBLISHER_ROLE_ARN": PUBLISHER_ROLE_ARN,
    },
    "prior-capture": {
        **_COMMON_EXPECTED,
        "DEPLOYER_ROLE_ARN": DEPLOYER_ROLE_ARN,
        "ECS_CLUSTER_ARN": ECS_CLUSTER_ARN,
        "WEB_SERVICE_NAME": WEB_SERVICE_NAME,
        "WORKER_SERVICE_NAME": WORKER_SERVICE_NAME,
        "WEB_FAMILY": WEB_TASK_FAMILY,
        "WORKER_FAMILY": WORKER_TASK_FAMILY,
        "MIGRATION_FAMILY": MIGRATION_TASK_FAMILY,
        "CONTAINER_NAMES": CONTAINER_NAMES,
        "TASK_ROLE_ARN": TASK_ROLE_ARN,
        "EXECUTION_ROLE_ARN": EXECUTION_ROLE_ARN,
        "ECS_SUBNET_IDS": SUBNET_IDS,
        "ECS_SECURITY_GROUP_IDS": SECURITY_GROUP_IDS,
        "ASSIGN_PUBLIC_IP": "true",
        "WEB_DESIRED_COUNT": "1",
        "WORKER_DESIRED_COUNT": "1",
    },
    "deployer": {
        **_COMMON_EXPECTED,
        "DEPLOYER_ROLE_ARN": DEPLOYER_ROLE_ARN,
        "ECS_CLUSTER_ARN": ECS_CLUSTER_ARN,
        "WEB_SERVICE_NAME": WEB_SERVICE_NAME,
        "WORKER_SERVICE_NAME": WORKER_SERVICE_NAME,
        "WEB_FAMILY": WEB_TASK_FAMILY,
        "WORKER_FAMILY": WORKER_TASK_FAMILY,
        "MIGRATION_FAMILY": MIGRATION_TASK_FAMILY,
        "CONTAINER_NAMES": CONTAINER_NAMES,
        "TASK_ROLE_ARN": TASK_ROLE_ARN,
        "EXECUTION_ROLE_ARN": EXECUTION_ROLE_ARN,
        "ASSIGN_PUBLIC_IP": "true",
        "WEB_DESIRED_COUNT": "1",
        "WORKER_DESIRED_COUNT": "1",
        "PROJECT_TAG": RESOURCE_PROJECT_TAG,
        "ENVIRONMENT_TAG": RESOURCE_ENVIRONMENT_TAG,
    },
}


def _normalize_json(name: str, value: str) -> str:
    if name not in {
        "CONTAINER_NAMES",
        "ECS_SUBNET_IDS",
        "ECS_SECURITY_GROUP_IDS",
    }:
        return value
    try:
        return json.dumps(json.loads(value), separators=(",", ":"), sort_keys=True)
    except json.JSONDecodeError as error:
        raise ReleaseContractError(f"development variable {name} is not valid JSON") from error


def validate_environment(profile: str, values: Mapping[str, str]) -> None:
    """Fail closed when development variables select a different physical deployment."""

    try:
        expected = dict(_PROFILES[profile])
    except KeyError as error:
        raise ReleaseContractError("unknown development compatibility profile") from error
    if profile == "deployer":
        expected["ECS_SUBNET_IDS"] = SUBNET_IDS
        expected["ECS_SECURITY_GROUP_IDS"] = SECURITY_GROUP_IDS
    missing = sorted(name for name in expected if not values.get(name))
    if missing:
        raise ReleaseContractError(
            "development configuration is missing required values: " + ", ".join(missing)
        )
    mismatched = sorted(
        name
        for name, expected_value in expected.items()
        if _normalize_json(name, values[name]) != _normalize_json(name, expected_value)
    )
    target_group = values.get("WEB_TARGET_GROUP_ARN")
    if profile in {"prior-capture", "deployer", "deployer-probe"} and (
        target_group is None or TARGET_GROUP_ARN_PATTERN.fullmatch(target_group) is None
    ):
        mismatched.append("WEB_TARGET_GROUP_ARN")
    if mismatched:
        raise ReleaseContractError(
            "development configuration selects unexpected physical values: "
            + ", ".join(sorted(set(mismatched)))
        )


def task_definition_arn_prefix(family: str) -> str:
    if family not in {WEB_TASK_FAMILY, WORKER_TASK_FAMILY, MIGRATION_TASK_FAMILY}:
        raise ReleaseContractError("task family is outside development compatibility boundary")
    return f"arn:aws:ecs:{AWS_REGION}:{AWS_ACCOUNT_ID}:task-definition/{family}:"


def validate_release_record(payload: Any) -> None:
    """Validate an immutable release record against exact physical task families."""

    legacy_keys = {
        "image_digest",
        "migration_task_definition_arn",
        "rollback_eligible",
        "source_sha",
        "web_desired_count",
        "web_task_definition_arn",
        "worker_desired_count",
        "worker_task_definition_arn",
    }
    schema2_keys = legacy_keys | {"identity_schema", "version"}
    if not isinstance(payload, dict):
        raise ReleaseContractError("development release record fields differ")
    payload_keys = frozenset(payload)
    if payload_keys not in {frozenset(legacy_keys), frozenset(schema2_keys)}:
        raise ReleaseContractError("development release record fields differ")
    source_sha = payload["source_sha"]
    image_digest = payload["image_digest"]
    if not isinstance(source_sha, str) or re.fullmatch(r"[0-9a-f]{40}", source_sha) is None:
        raise ReleaseContractError("development release record source SHA differs")
    if payload_keys == frozenset(schema2_keys):
        if payload["identity_schema"] != 2 or not isinstance(payload["version"], str):
            raise ReleaseContractError("development release record identity schema differs")
        if re.fullmatch(
            r"[0-9]{8}-[0-9]{6}-[0-9a-f]{7}", payload["version"]
        ) is None or not payload["version"].endswith(f"-{source_sha[:7]}"):
            raise ReleaseContractError("development release record version differs")
    if (
        not isinstance(image_digest, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest) is None
        or image_digest
        in {
            "sha256:" + "0" * 64,
            "sha256:" + "1" * 64,
        }
    ):
        raise ReleaseContractError("development release record image digest differs")
    family_fields = {
        "web_task_definition_arn": WEB_TASK_FAMILY,
        "worker_task_definition_arn": WORKER_TASK_FAMILY,
        "migration_task_definition_arn": MIGRATION_TASK_FAMILY,
    }
    for field, family in family_fields.items():
        value = payload[field]
        prefix = task_definition_arn_prefix(family)
        if (
            not isinstance(value, str)
            or re.fullmatch(re.escape(prefix) + r"[1-9][0-9]*", value) is None
        ):
            raise ReleaseContractError(f"development release record {field} differs")
    if (
        payload["web_desired_count"] != 1
        or type(payload["web_desired_count"]) is not int
        or payload["worker_desired_count"] != 1
        or type(payload["worker_desired_count"]) is not int
        or payload["rollback_eligible"] is not True
    ):
        raise ReleaseContractError("development release record safety fields differ")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate development variables against legacy physical identifiers"
    )
    parser.add_argument("command", choices=[*sorted(_PROFILES), "release-record"])
    arguments = parser.parse_args()
    try:
        if arguments.command == "release-record":
            validate_release_record(json.load(sys.stdin))
        else:
            validate_environment(arguments.command, os.environ)
    except (json.JSONDecodeError, ReleaseContractError) as error:
        print(f"Development compatibility validation failed safely: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
