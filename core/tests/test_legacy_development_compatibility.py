from __future__ import annotations

from django.test import SimpleTestCase

from deploy.contracts import ReleaseContractError
from deploy.legacy_development_compatibility import (
    AWS_REGION,
    CONTAINER_NAMES,
    DEPLOYER_ROLE_ARN,
    ECR_REPOSITORY_NAME,
    ECR_REPOSITORY_URI,
    ECS_CLUSTER_ARN,
    EXECUTION_ROLE_ARN,
    MIGRATION_TASK_FAMILY,
    RESOURCE_ENVIRONMENT_TAG,
    SECURITY_GROUP_IDS,
    SUBNET_IDS,
    TASK_ROLE_ARN,
    WEB_SERVICE_NAME,
    WEB_TASK_FAMILY,
    WORKER_SERVICE_NAME,
    WORKER_TASK_FAMILY,
    task_definition_arn_prefix,
    validate_environment,
    validate_release_record,
)


class DevelopmentCompatibilityBoundaryTests(SimpleTestCase):
    def test_prior_capture_profile_accepts_only_the_exact_physical_values(self) -> None:
        values = {
            "AWS_REGION": AWS_REGION,
            "DEPLOYER_ROLE_ARN": DEPLOYER_ROLE_ARN,
            "ECR_REPOSITORY_URI": ECR_REPOSITORY_URI,
            "ECS_CLUSTER_ARN": ECS_CLUSTER_ARN,
            "WEB_TARGET_GROUP_ARN": (
                f"arn:aws:elasticloadbalancing:eu-west-1:817685572750:"
                f"targetgroup/{WEB_SERVICE_NAME}/0123456789abcdef"
            ),
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
        }
        validate_environment("prior-capture", values)
        self.assertEqual(ECR_REPOSITORY_NAME, "website-sandbox")
        self.assertEqual(RESOURCE_ENVIRONMENT_TAG, "sandbox")

        for name, invalid in (
            ("AWS_REGION", "eu-west-2"),
            ("WEB_DESIRED_COUNT", "2"),
            ("ECR_REPOSITORY_URI", "example.invalid/repository"),
            (
                "WEB_TARGET_GROUP_ARN",
                "arn:aws:elasticloadbalancing:eu-west-1:817685572750:"
                "targetgroup/foreign-web/0123456789abcdef",
            ),
        ):
            with self.subTest(name=name), self.assertRaises(ReleaseContractError):
                validate_environment("prior-capture", {**values, name: invalid})
        missing = dict(values)
        missing.pop("DEPLOYER_ROLE_ARN")
        with self.assertRaises(ReleaseContractError):
            validate_environment("prior-capture", missing)

    def test_release_record_rejects_a_foreign_task_family(self) -> None:
        payload = {
            "source_sha": "a" * 40,
            "image_digest": "sha256:" + "a" * 64,
            "web_task_definition_arn": task_definition_arn_prefix(WEB_TASK_FAMILY) + "7",
            "worker_task_definition_arn": task_definition_arn_prefix(WORKER_TASK_FAMILY) + "8",
            "migration_task_definition_arn": (
                task_definition_arn_prefix(MIGRATION_TASK_FAMILY) + "9"
            ),
            "web_desired_count": 1,
            "worker_desired_count": 1,
            "rollback_eligible": True,
        }
        validate_release_record(payload)

        foreign = dict(payload)
        foreign["web_task_definition_arn"] = (
            "arn:aws:ecs:eu-west-1:817685572750:task-definition/foreign-web:7"
        )
        with self.assertRaises(ReleaseContractError):
            validate_release_record(foreign)
