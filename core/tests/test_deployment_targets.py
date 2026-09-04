from __future__ import annotations

import json
import re

from django.test import SimpleTestCase

from deploy.contracts import ReleaseContractError
from deploy.deployment_targets import (
    CONTAINER_NAMES,
    DEFAULT_DEPLOYMENT_TARGET,
    DEPLOYMENT_TARGET_VARIABLE,
    DEPLOYMENT_TARGETS,
    ROLE_PROFILES,
    SELECTED_TARGET,
    SUPPLIED_IDENTIFIERS,
    DeploymentTarget,
    DeploymentTargetError,
    deployment_target,
    registered_target,
    role_profile_expectations,
    validate_environment,
    validate_release_record,
)
from deploy.development_target import PERMITTED_DEVELOPMENT_HOSTNAMES

PRODUCTION = registered_target("website-production")
SANDBOX = registered_target("website-sandbox")


def deployer_variables(target: DeploymentTarget) -> dict[str, str]:
    """Every variable a correct deployment environment presents for ``target``."""

    values: dict[str, str] = {}
    for profile in ROLE_PROFILES:
        values.update(role_profile_expectations(profile, target))
    values["ECS_SUBNET_IDS"] = target.pinned_identifiers.get(
        "ECS_SUBNET_IDS", '["subnet-0123456789abcdef0","subnet-0fedcba9876543210"]'
    )
    values["ECS_SECURITY_GROUP_IDS"] = target.pinned_identifiers.get(
        "ECS_SECURITY_GROUP_IDS", '["sg-0123456789abcdef0"]'
    )
    values["HOSTED_ZONE_ID"] = target.pinned_identifiers.get(
        "HOSTED_ZONE_ID", "Z0A1B2C3D4E5F6G7H8I9"
    )
    values["KMS_KEY_ARN"] = target.pinned_identifiers.get(
        "KMS_KEY_ARN",
        f"arn:aws:kms:{target.aws_region}:{target.aws_account_id}:"
        "key/0a1b2c3d-4e5f-6a7b-8c9d-0e1f2a3b4c5d",
    )
    values["WEB_TARGET_GROUP_ARN"] = (
        f"arn:aws:elasticloadbalancing:{target.aws_region}:{target.aws_account_id}:"
        f"targetgroup/{target.resource_namespace}-web/0123456789abcdef"
    )
    return values


class DeploymentTargetSelectionTests(SimpleTestCase):
    def test_unset_configuration_selects_the_reviewed_default(self) -> None:
        self.assertEqual(deployment_target({}).name, DEFAULT_DEPLOYMENT_TARGET)
        self.assertIs(deployment_target({}), SELECTED_TARGET)

    def test_an_unreviewed_target_fails_closed(self) -> None:
        for value in (
            "website-staging",
            "production",
            "WEBSITE-PRODUCTION",
            "website-production ",
            "../website-production",
        ):
            with self.subTest(value=value), self.assertRaises(DeploymentTargetError):
                deployment_target({DEPLOYMENT_TARGET_VARIABLE: value})

    def test_a_retired_target_is_readable_but_never_deployable(self) -> None:
        self.assertTrue(SANDBOX.retired)
        self.assertEqual(SANDBOX.resource_namespace, "website-sandbox")
        with self.assertRaises(DeploymentTargetError) as raised:
            deployment_target({DEPLOYMENT_TARGET_VARIABLE: "website-sandbox"})
        self.assertIn("retired", str(raised.exception))

    def test_the_destroyed_sandbox_profile_is_retained_for_recorded_evidence(self) -> None:
        self.assertEqual(SANDBOX.aws_account_id, "817685572750")
        self.assertEqual(SANDBOX.hostname, "web.dtcdev.click")
        self.assertEqual(SANDBOX.github_environment, "sandbox")
        self.assertEqual(SANDBOX.terraform_root, "sandbox/website")
        self.assertEqual(
            SANDBOX.terraform_state_bucket,
            "datamailer-sandbox-817685572750-us-east-1-tfstate",
        )
        self.assertEqual(SANDBOX.resource_environment_tag, "sandbox")


class ProductionTargetTests(SimpleTestCase):
    """The reviewed production profile, as DataTalksClub/aws-infra main/website applies it."""

    def test_production_is_the_deployable_target(self) -> None:
        self.assertIs(SELECTED_TARGET, PRODUCTION)
        self.assertFalse(PRODUCTION.retired)

    def test_production_physical_identifiers_derive_from_the_reviewed_namespace(self) -> None:
        self.assertEqual(PRODUCTION.aws_account_id, "387546586013")
        self.assertEqual(PRODUCTION.aws_region, "eu-west-1")
        self.assertEqual(PRODUCTION.resource_namespace, "website-production")
        self.assertEqual(
            PRODUCTION.ecr_repository_uri,
            "387546586013.dkr.ecr.eu-west-1.amazonaws.com/website-production",
        )
        self.assertEqual(
            PRODUCTION.ecs_cluster_arn,
            "arn:aws:ecs:eu-west-1:387546586013:cluster/website-production",
        )
        self.assertEqual(PRODUCTION.web_service_name, "website-production-web")
        self.assertEqual(PRODUCTION.worker_service_name, "website-production-worker")
        self.assertEqual(PRODUCTION.migration_task_family, "website-production-migration")
        self.assertEqual(
            PRODUCTION.task_role_arn,
            "arn:aws:iam::387546586013:role/website-production-task-application",
        )
        self.assertEqual(
            PRODUCTION.execution_role_arn,
            "arn:aws:iam::387546586013:role/website-production-task-execution",
        )
        self.assertEqual(
            PRODUCTION.publisher_role_arn,
            "arn:aws:iam::387546586013:role/website-production-github-publisher",
        )
        self.assertEqual(
            PRODUCTION.deployer_role_arn,
            "arn:aws:iam::387546586013:role/website-production-github-deployer",
        )

    def test_production_runtime_contract(self) -> None:
        self.assertEqual(PRODUCTION.hostname, "prod.datatalks.club")
        self.assertEqual(PRODUCTION.settings_module, "website.settings.production")
        self.assertEqual(PRODUCTION.dtc_environment, "production")
        self.assertEqual(PRODUCTION.web_desired_count, 2)
        self.assertEqual(PRODUCTION.worker_desired_count, 1)
        self.assertFalse(PRODUCTION.assign_public_ip)
        self.assertEqual(PRODUCTION.resource_environment_tag, "production")
        self.assertEqual(PRODUCTION.github_environment, "production")
        self.assertEqual(PRODUCTION.terraform_root, "main/website")

    def test_a_staging_host_still_declares_the_apex_canonical_and_stays_noindex(self) -> None:
        # prod.datatalks.club serves the same corpus the apex still serves, so it
        # must consolidate to the apex and must not be indexable (spec 02).
        self.assertEqual(PRODUCTION.canonical_origin, "https://datatalks.club")
        self.assertNotEqual(PRODUCTION.canonical_origin, PRODUCTION.origin)
        self.assertTrue(PRODUCTION.robots_noindex)

    def test_production_pins_no_aws_generated_identifier(self) -> None:
        # These are generated at apply time; inventing them here would be a guess.
        self.assertEqual(dict(PRODUCTION.pinned_identifiers), {})


class DeploymentEnvironmentValidationTests(SimpleTestCase):
    def test_every_role_profile_accepts_its_own_targets_exact_values(self) -> None:
        for target in DEPLOYMENT_TARGETS.values():
            for profile in ROLE_PROFILES:
                with self.subTest(target=target.name, profile=profile):
                    validate_environment(profile, deployer_variables(target), target=target)

    def test_another_targets_values_are_rejected_for_every_role_profile(self) -> None:
        foreign = deployer_variables(SANDBOX)
        for profile in ROLE_PROFILES:
            with self.subTest(profile=profile), self.assertRaises(ReleaseContractError):
                validate_environment(profile, foreign, target=PRODUCTION)

    def test_a_wrong_physical_value_names_the_value_that_disagreed(self) -> None:
        values = deployer_variables(PRODUCTION)
        cases = (
            ("deployer", "AWS_REGION", "eu-west-2"),
            ("deployer", "WEB_DESIRED_COUNT", "1"),
            ("deployer", "WORKER_DESIRED_COUNT", "2"),
            ("deployer", "ASSIGN_PUBLIC_IP", "true"),
            ("deployer", "ENVIRONMENT_TAG", "sandbox"),
            ("deployer", "ECR_REPOSITORY_URI", "example.invalid/repository"),
            ("deployer", "ECS_CLUSTER_ARN", SANDBOX.ecs_cluster_arn),
            ("deployer", "DEPLOYER_ROLE_ARN", SANDBOX.deployer_role_arn),
            ("deployer", "TASK_ROLE_ARN", SANDBOX.task_role_arn),
            ("deployer", "WEB_SERVICE_NAME", SANDBOX.web_service_name),
            ("publisher", "PUBLISHER_ROLE_ARN", SANDBOX.publisher_role_arn),
            # Real ids, but the destroyed sandbox stack's.
            ("deployer", "ECS_SUBNET_IDS", SANDBOX.pinned_identifiers["ECS_SUBNET_IDS"]),
            (
                "deployer",
                "ECS_SECURITY_GROUP_IDS",
                SANDBOX.pinned_identifiers["ECS_SECURITY_GROUP_IDS"],
            ),
            ("deployer-probe", "KMS_KEY_ARN", SANDBOX.pinned_identifiers["KMS_KEY_ARN"]),
            ("deployer-probe", "HOSTED_ZONE_ID", SANDBOX.pinned_identifiers["HOSTED_ZONE_ID"]),
            (
                "deployer",
                "WEB_TARGET_GROUP_ARN",
                "arn:aws:elasticloadbalancing:eu-west-1:817685572750:"
                "targetgroup/website-sandbox-web/0123456789abcdef",
            ),
        )
        for profile, name, invalid in cases:
            with self.subTest(profile=profile, name=name):
                with self.assertRaises(ReleaseContractError) as raised:
                    validate_environment(profile, {**values, name: invalid}, target=PRODUCTION)
                self.assertIn(name, str(raised.exception))

    def test_a_missing_value_fails_closed_and_names_it(self) -> None:
        values = deployer_variables(PRODUCTION)
        for name in ("DEPLOYER_ROLE_ARN", "ECS_SUBNET_IDS", "WEB_TARGET_GROUP_ARN"):
            incomplete = {key: value for key, value in values.items() if key != name}
            with self.subTest(name=name):
                with self.assertRaises(ReleaseContractError) as raised:
                    validate_environment("deployer", incomplete, target=PRODUCTION)
                self.assertIn(name, str(raised.exception))
                self.assertIn("missing", str(raised.exception))

    def test_an_unknown_role_profile_fails_closed(self) -> None:
        with self.assertRaises(ReleaseContractError):
            validate_environment("root", deployer_variables(PRODUCTION), target=PRODUCTION)

    def test_container_names_compare_independently_of_json_formatting(self) -> None:
        values = deployer_variables(PRODUCTION)
        reordered = json.dumps(
            {"web": "web", "worker": "worker", "migration": "migration"}, indent=2
        )
        validate_environment(
            "deployer", {**values, "CONTAINER_NAMES": reordered}, target=PRODUCTION
        )
        with self.assertRaises(ReleaseContractError):
            validate_environment(
                "deployer",
                {**values, "CONTAINER_NAMES": '{"web":"web","worker":"worker"}'},
                target=PRODUCTION,
            )

    def test_the_default_selection_is_what_the_workflow_validates_against(self) -> None:
        validate_environment("deployer", deployer_variables(SELECTED_TARGET))


class SuppliedIdentifierTests(SimpleTestCase):
    def test_every_pinned_identifier_also_satisfies_its_own_shape(self) -> None:
        for target in DEPLOYMENT_TARGETS.values():
            for name, value in target.pinned_identifiers.items():
                with self.subTest(target=target.name, name=name):
                    self.assertIsNone(target.identifier_disagreement(name, value))

    def test_supplied_shapes_are_scoped_to_their_own_account_and_region(self) -> None:
        for identifier in SUPPLIED_IDENTIFIERS:
            with self.subTest(name=identifier.name):
                self.assertTrue(identifier.source)
                pattern = identifier.pattern(PRODUCTION).pattern
                if "arn:aws" in pattern:
                    self.assertIn(PRODUCTION.aws_account_id, pattern)
                    self.assertIn(re.escape(PRODUCTION.aws_region), pattern)
                self.assertNotIn(SANDBOX.aws_account_id, pattern)

    def test_a_malformed_supplied_identifier_is_rejected(self) -> None:
        cases = {
            "HOSTED_ZONE_ID": "z05963572wvwfhdqzh5ne",
            "KMS_KEY_ARN": "arn:aws:kms:eu-west-1:387546586013:key/not-a-uuid",
            "ECS_SUBNET_IDS": '["subnet-0123456789abcdef0","vpc-0123456789abcdef0"]',
            "ECS_SECURITY_GROUP_IDS": '["subnet-0123456789abcdef0"]',
            "WEB_TARGET_GROUP_ARN": (
                "arn:aws:elasticloadbalancing:eu-west-1:387546586013:"
                "targetgroup/other/0123456789abcdef"
            ),
        }
        for name, value in cases.items():
            with self.subTest(name=name):
                self.assertIsNotNone(PRODUCTION.identifier_disagreement(name, value))

    def test_an_unknown_supplied_identifier_name_fails_closed(self) -> None:
        with self.assertRaises(DeploymentTargetError):
            PRODUCTION.identifier_disagreement("DATABASE_PASSWORD", "anything")

    def test_a_target_cannot_pin_a_value_that_is_not_a_supplied_identifier(self) -> None:
        with self.assertRaises(DeploymentTargetError):
            DeploymentTarget(
                name="invalid",
                aws_account_id="000000000000",
                aws_region="eu-west-1",
                hostname="example.invalid",
                resource_namespace="example",
                settings_module="website.settings.production",
                dtc_environment="production",
                canonical_origin="https://example.invalid",
                robots_noindex=True,
                github_environment="example",
                terraform_root="main/example",
                terraform_state_bucket="example",
                task_cpu_architecture=PRODUCTION.task_cpu_architecture,
                assign_public_ip=False,
                web_desired_count=1,
                worker_desired_count=1,
                resource_project_tag="website",
                resource_environment_tag="example",
                pinned_identifiers={"DJANGO_SECRET_KEY": "nope"},
            )


class ReleaseRecordTests(SimpleTestCase):
    @staticmethod
    def record(target: DeploymentTarget) -> dict[str, object]:
        return {
            "source_sha": "a" * 40,
            "image_digest": "sha256:" + "a" * 64,
            "web_task_definition_arn": (
                target.task_definition_arn_prefix(target.web_task_family) + "7"
            ),
            "worker_task_definition_arn": (
                target.task_definition_arn_prefix(target.worker_task_family) + "8"
            ),
            "migration_task_definition_arn": (
                target.task_definition_arn_prefix(target.migration_task_family) + "9"
            ),
            "web_desired_count": target.web_desired_count,
            "worker_desired_count": target.worker_desired_count,
            "rollback_eligible": True,
        }

    def test_each_target_accepts_only_its_own_record(self) -> None:
        for target in DEPLOYMENT_TARGETS.values():
            with self.subTest(target=target.name):
                validate_release_record(self.record(target), target=target)
        with self.assertRaises(ReleaseContractError):
            validate_release_record(self.record(SANDBOX), target=PRODUCTION)

    def test_production_requires_its_reviewed_desired_counts(self) -> None:
        payload = self.record(PRODUCTION)
        self.assertEqual(payload["web_desired_count"], 2)
        for name, value in (("web_desired_count", 1), ("worker_desired_count", 2)):
            with self.subTest(name=name), self.assertRaises(ReleaseContractError):
                validate_release_record({**payload, name: value}, target=PRODUCTION)

    def test_release_record_rejects_a_foreign_task_family(self) -> None:
        foreign = self.record(PRODUCTION)
        foreign["web_task_definition_arn"] = (
            "arn:aws:ecs:eu-west-1:387546586013:task-definition/foreign-web:7"
        )
        with self.assertRaises(ReleaseContractError):
            validate_release_record(foreign, target=PRODUCTION)

    def test_a_task_family_outside_the_target_fails_closed(self) -> None:
        with self.assertRaises(ReleaseContractError):
            PRODUCTION.task_definition_arn_prefix(SANDBOX.web_task_family)


class DeploymentTargetCoherenceTests(SimpleTestCase):
    def test_container_names_match_the_terraform_module_contract(self) -> None:
        self.assertEqual(
            json.loads(CONTAINER_NAMES),
            {"web": "web", "worker": "worker", "migration": "migration"},
        )

    def test_a_development_settings_target_names_a_reviewed_development_host(self) -> None:
        for target in DEPLOYMENT_TARGETS.values():
            if target.settings_module == "website.settings.development":
                with self.subTest(target=target.name):
                    self.assertIn(target.hostname, PERMITTED_DEVELOPMENT_HOSTNAMES)

    def test_no_two_targets_share_a_physical_namespace(self) -> None:
        namespaces = [
            (target.aws_account_id, target.resource_namespace)
            for target in DEPLOYMENT_TARGETS.values()
        ]
        self.assertEqual(len(namespaces), len(set(namespaces)))
