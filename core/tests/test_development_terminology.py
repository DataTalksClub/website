from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

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
from scripts.check_development_terminology import FORMER_NAME, check

ROOT = Path(__file__).resolve().parents[2]


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
        self.assertEqual(ECR_REPOSITORY_NAME, "website-" + FORMER_NAME)
        self.assertEqual(RESOURCE_ENVIRONMENT_TAG, FORMER_NAME)

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


class DevelopmentTerminologyInventoryTests(SimpleTestCase):
    def test_repository_inventory_is_fully_classified(self) -> None:
        self.assertEqual(
            check(
                ROOT,
                Path("_docs/compatibility/development-terminology-allowlist.json"),
            ),
            [],
        )

    def test_unclassified_mixed_case_source_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "current.py"
            source.write_text(f'ENVIRONMENT = "{FORMER_NAME.title()}"\n')
            policy_path = root / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "self_reason": "policy data is excluded",
                        "legacy_paths": [],
                        "whole_files": [],
                        "rules": [],
                    }
                )
            )
            with patch(
                "scripts.check_development_terminology._tracked_paths",
                return_value=(source, policy_path),
            ):
                errors = check(root, Path("policy.json"))
        self.assertTrue(any("unclassified legacy term" in error for error in errors))

    def test_whole_file_allowance_is_hash_and_count_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "evidence.txt"
            source.write_text(FORMER_NAME + "\n")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            policy_path = root / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "self_reason": "policy data is excluded",
                        "legacy_paths": [],
                        "whole_files": [
                            {
                                "path": "evidence.txt",
                                "sha256": digest,
                                "class": "frozen_historical",
                                "reason": "fixture",
                                "follow_up": "none; immutable",
                                "expected_count": 1,
                            }
                        ],
                        "rules": [],
                    }
                )
            )
            with patch(
                "scripts.check_development_terminology._tracked_paths",
                return_value=(source, policy_path),
            ):
                self.assertEqual(check(root, Path("policy.json")), [])
                source.write_text(FORMER_NAME + " changed\n")
                errors = check(root, Path("policy.json"))
        self.assertTrue(any("digest differs" in error for error in errors))

    def test_path_rule_allowance_is_count_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "capture.md"
            source.write_text(f"{FORMER_NAME}\n{FORMER_NAME}\n")
            policy_path = root / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "self_reason": "policy data is excluded",
                        "legacy_paths": [],
                        "whole_files": [],
                        "rules": [
                            {
                                "path": "capture.md",
                                "pattern": FORMER_NAME,
                                "class": "captured_compatibility",
                                "reason": "fixture",
                                "follow_up": "#94",
                                "expected_count": 2,
                            }
                        ],
                    }
                )
            )
            with patch(
                "scripts.check_development_terminology._tracked_paths",
                return_value=(source, policy_path),
            ):
                self.assertEqual(check(root, Path("policy.json")), [])
                source.write_text(f"{FORMER_NAME}\n")
                errors = check(root, Path("policy.json"))
        self.assertTrue(any("rule count differs" in error for error in errors))

    def test_stale_path_rule_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "capture.md"
            source.write_text("development\n")
            policy_path = root / "policy.json"
            policy_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "self_reason": "policy data is excluded",
                        "legacy_paths": [],
                        "whole_files": [],
                        "rules": [
                            {
                                "path": "capture.md",
                                "pattern": FORMER_NAME,
                                "class": "captured_compatibility",
                                "reason": "fixture",
                                "follow_up": "#94",
                                "expected_count": 1,
                            }
                        ],
                    }
                )
            )
            with patch(
                "scripts.check_development_terminology._tracked_paths",
                return_value=(source, policy_path),
            ):
                errors = check(root, Path("policy.json"))
        self.assertTrue(any("stale rule allowance" in error for error in errors))

    def test_legacy_link_notices_are_small_and_non_executable(self) -> None:
        for relative in (
            "_docs/runbooks/" + FORMER_NAME + "-release.md",
            "_docs/specs/08-aws-" + FORMER_NAME + "-terraform.md",
        ):
            text = (ROOT / relative).read_text()
            self.assertLess(len(text.encode()), 600)
            self.assertNotIn("```", text)
            self.assertIn("development", text.casefold())
            self.assertIn("#94", text)
