from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import venv
from collections.abc import Sequence
from contextlib import redirect_stderr
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

from django.test import SimpleTestCase

from core.tests import test_gate_b_evidence as evidence_fixtures
from deploy import gate_b_assembler as assembler
from deploy import gate_b_evidence as evidence
from deploy import gate_b_operator as operator

ROOT = Path(__file__).resolve().parents[2]
CAPTURE_STARTED = datetime.now(UTC).replace(microsecond=0)
CAPTURE_ID = f"{CAPTURE_STARTED:%Y%m%dT%H%M%SZ}-012345abcdef"
REDACTION_CANARIES = (
    "ASIAZZZZZZZZZZZZZZZZ",
    "secret-key-redaction-canary",
    "session-token-redaction-canary",
    "Bearer bearer-redaction-canary",
    "provider-message-redaction-canary",
    "secret-value-redaction-canary",
    "x-origin-header-redaction-canary",
    "state-content-redaction-canary",
    "Authorization: authorization-redaction-canary",
)


def load_contract() -> dict[str, Any]:
    return assembler.load_execution_contract()


def load_seed() -> dict[str, Any]:
    return assembler.load_seed()


def load_manifest() -> dict[str, Any]:
    return assembler.load_manifest()


def credentials(expiration: datetime | None = None) -> operator.FrozenCredentials:
    return operator.FrozenCredentials(
        access_key_id=REDACTION_CANARIES[0],
        secret_access_key=REDACTION_CANARIES[1],
        session_token=REDACTION_CANARIES[2],
        expiration=expiration or datetime.now(UTC) + timedelta(minutes=15),
    )


def accepted_provider_responses(
    seed: dict[str, Any], manifest: dict[str, Any], bindings: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    policy = evidence_fixtures.policy_document(manifest)["payload"]
    resource = evidence_fixtures.resource_document(manifest, bindings)["payload"]
    result: dict[str, dict[str, Any]] = {
        "sts-caller": {
            "Account": "817685572750",
            "Arn": (
                "arn:aws:sts::817685572750:assumed-role/phone-aws-sandbox-role/"
                "phone-sandbox-01234567"
            ),
            "UserId": "AROA34YO3VSHI2OCVBKTW:phone-sandbox-01234567",
        },
        "kms-rotation": {"KeyRotationEnabled": policy["kms"]["rotation_enabled"]},
        "kms-policy": {"Policy": policy["kms"]["key_policy"]},
        "kms-grants": {"Grants": policy["kms"]["grant_inventory"]},
        "s3-bucket": {},
        "s3-location": {"LocationConstraint": None},
        "s3-ownership": {
            "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}
        },
        "s3-encryption": {
            "ServerSideEncryptionConfiguration": {
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
            }
        },
        "s3-versioning": {"Status": "Enabled"},
        "s3-public-access": {
            "PublicAccessBlockConfiguration": resource["s3"]["public_access_block"]
        },
        "s3-state-object": {
            "AcceptRanges": "bytes",
            "LastModified": "2026-08-07T00:00:00Z",
            "ContentLength": 1,
            "ETag": f'"{REDACTION_CANARIES[7]}"',
            "VersionId": "fixture-version",
            "ServerSideEncryption": "AES256",
        },
        "ecr-repository": {
            "repositories": [
                {
                    "repositoryArn": resource["ecr"]["arn"],
                    "registryId": resource["ecr"]["registry_id"],
                    "repositoryName": resource["ecr"]["name"],
                    "repositoryUri": seed["github_repository_variables"][
                        "SANDBOX_ECR_REPOSITORY_URI"
                    ],
                    "createdAt": "2026-08-07T00:00:00Z",
                    "imageTagMutability": resource["ecr"]["image_tag_mutability"],
                    "imageScanningConfiguration": {"scanOnPush": resource["ecr"]["scan_on_push"]},
                    "encryptionConfiguration": {
                        "encryptionType": "KMS",
                        "kmsKey": resource["ecr"]["kms_key_arn"],
                    },
                }
            ],
            "nextToken": None,
        },
        "ecr-images": {
            "ImageCount": 0,
            "ImageDetailsType": "array",
            "NextToken": None,
        },
        "cloudfront-distribution": {
            "Id": resource["cloudfront"]["distribution_id"],
            "ARN": resource["cloudfront"]["arn"],
            "Status": resource["cloudfront"]["status"],
            "DomainName": resource["cloudfront"]["domain_name"],
            "Enabled": resource["cloudfront"]["enabled"],
            "Aliases": {
                "Quantity": len(resource["cloudfront"]["aliases"]),
                "Items": resource["cloudfront"]["aliases"],
            },
        },
        "target-group": {
            "TargetGroups": [
                {
                    "TargetGroupArn": resource["runtime"]["target_group"]["arn"],
                    "TargetGroupName": resource["runtime"]["target_group"]["name"],
                    "Protocol": resource["runtime"]["target_group"]["protocol"],
                    "Port": resource["runtime"]["target_group"]["port"],
                    "VpcId": resource["runtime"]["target_group"]["vpc_id"],
                    "TargetType": resource["runtime"]["target_group"]["target_type"],
                    "HealthCheckPath": resource["runtime"]["target_group"]["health_check_path"],
                }
            ],
            "NextMarker": None,
        },
        "target-health": {
            "TargetCount": resource["runtime"]["target_count"],
            "TargetHealthDescriptionsType": "array",
        },
        "ecs-cluster": {
            "clusters": [
                {
                    "clusterArn": resource["runtime"]["ecs_cluster"]["arn"],
                    "clusterName": resource["runtime"]["ecs_cluster"]["name"],
                    "status": resource["runtime"]["ecs_cluster"]["status"],
                    "registeredContainerInstancesCount": resource["runtime"]["ecs_cluster"][
                        "registered_container_instances"
                    ],
                    "runningTasksCount": resource["runtime"]["ecs_cluster"]["running_tasks"],
                    "pendingTasksCount": resource["runtime"]["ecs_cluster"]["pending_tasks"],
                    "activeServicesCount": resource["runtime"]["ecs_cluster"]["active_services"],
                }
            ],
            "failures": [],
        },
        "ecs-services": {
            "services": [
                {
                    "serviceArn": service["arn"],
                    "serviceName": service["name"],
                    "status": service["status"],
                    "desiredCount": service["desired"],
                    "runningCount": service["running"],
                    "pendingCount": service["pending"],
                    "taskDefinition": service["task_definition"],
                }
                for service in resource["runtime"]["ecs_services"].values()
            ],
            "failures": [],
        },
        "rds-database": {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": resource["runtime"]["database"]["identifier"],
                    "DBInstanceArn": resource["runtime"]["database"]["arn"],
                    "DBInstanceStatus": resource["runtime"]["database"]["status"],
                    "StorageEncrypted": resource["runtime"]["database"]["encrypted"],
                    "KmsKeyId": resource["runtime"]["database"]["kms_key_arn"],
                    "PubliclyAccessible": resource["runtime"]["database"]["publicly_accessible"],
                }
            ],
            "Marker": None,
        },
        "github-sandbox-branch-policy": {
            "total_count": 1,
            "branch_policies": [{"id": 1, "node_id": "fixture", "name": "main", "type": "branch"}],
        },
    }
    for role_class, role in policy["roles"].items():
        result[f"iam-{role_class}-role"] = {
            "Role": {
                "RoleName": role["name"],
                "Path": role["path"],
                "Arn": role["arn"],
                "MaxSessionDuration": role["max_session_duration"],
                "AssumeRolePolicyDocument": role["trust_policy"],
            }
        }
        result[f"iam-{role_class}-inline-list"] = {
            "PolicyNames": [role["name"]],
            "IsTruncated": False,
        }
        result[f"iam-{role_class}-attached-list"] = {
            "AttachedPolicies": [],
            "IsTruncated": False,
        }
        result[f"iam-{role_class}-inline"] = {
            "RoleName": role["name"],
            "PolicyName": role["name"],
            "PolicyDocument": role["inline_policies"][role["name"]],
        }
    key_metadata = {
        "AWSAccountId": "817685572750",
        "KeyId": policy["kms"]["key_id"],
        "Arn": policy["kms"]["arn"],
        "CreationDate": "2026-08-07T00:00:00Z",
        "Enabled": True,
        "Description": "fixture",
        "KeyUsage": "ENCRYPT_DECRYPT",
        "KeyState": "Enabled",
        "Origin": "AWS_KMS",
        "KeyManager": "CUSTOMER",
        "CustomerMasterKeySpec": "SYMMETRIC_DEFAULT",
        "KeySpec": "SYMMETRIC_DEFAULT",
        "EncryptionAlgorithms": ["SYMMETRIC_DEFAULT"],
        "MultiRegion": False,
    }
    result["kms-key"] = {"KeyMetadata": key_metadata}
    result["kms-alias"] = {"KeyMetadata": copy.deepcopy(key_metadata)}
    for state in ("running", "pending", "stopped"):
        result[f"ecs-{state}-tasks"] = {
            "TaskCount": resource["runtime"][f"{state}_tasks"],
            "TaskArnsType": "array",
            "NextToken": None,
        }
    for name, definition in resource["runtime"]["task_definitions"].items():
        result[f"ecs-{name}-task-definition"] = {
            "taskDefinition": {
                "taskDefinitionArn": definition["arn"],
                "family": definition["family"],
                "revision": definition["revision"],
                "status": definition["status"],
                "taskRoleArn": definition["task_role_arn"],
                "executionRoleArn": definition["execution_role_arn"],
            }
        }
    for key, secret in resource["secrets"].items():
        result[f"secret-{key}-metadata"] = {
            "ARN": secret["arn"],
            "Name": secret["name"],
            "Description": secret["description"],
            "KmsKeyId": secret["kms_key_id"],
            "RotationEnabled": secret["rotation_enabled"],
            "OwningService": secret["owning_service"],
            "PrimaryRegion": secret["primary_region"],
            "DeletedDate": secret["deleted_date"],
            "VersionIdsToStages": secret["version_ids_to_stages"],
        }
        result[f"secret-{key}-policy"] = {
            **secret["resource_policy_response"],
            "ResourcePolicyPresent": False,
        }
    route_ids = (
        "route53-certificate-origin",
        "route53-certificate-web",
        "route53-origin-a",
        "route53-origin-aaaa",
        "route53-web-a",
        "route53-web-aaaa",
    )
    for command_id, record in zip(route_ids, seed["normalized_dns_full_records"], strict=True):
        result[command_id] = {"ResourceRecordSets": [copy.deepcopy(record)]}
    for name, value in seed["github_repository_variables"].items():
        result[f"github-repository-{name}"] = {"name": name, "value": value}
    for name, value in seed["github_environment_variables"].items():
        result[f"github-environment-{name}"] = {"name": name, "value": value}
    simulator = evidence_fixtures.simulator_document(manifest, bindings)["payload"]["results"]
    for row in simulator:
        result[f"simulator-{row['row_id']}"] = {
            "EvaluationResults": row["EvaluationResults"],
            "IsTruncated": False,
        }
    return result


class GateBContractTests(SimpleTestCase):
    def setUp(self) -> None:
        self.seed = load_seed()
        self.contract = load_contract()
        self.manifest = load_manifest()

    def test_seed_and_execution_contract_are_code_pinned_and_valid(self) -> None:
        seed_result = assembler.validate_seed(self.seed, self.manifest)
        contract_result = assembler.validate_execution_contract(
            self.contract, self.seed, self.manifest
        )

        self.assertEqual(seed_result["status"], "PASS")
        self.assertEqual(contract_result["status"], "PASS")
        self.assertEqual(
            self.seed["dns_records_sha256"],
            hashlib.sha256(evidence.canonical_json_bytes(self.seed["dns_records"])).hexdigest(),
        )
        self.assertNotEqual(
            self.seed["dns_records_sha256"],
            self.seed["source_dns_full_records_sha256"],
        )
        self.assertEqual(
            self.seed["normalized_dns_full_records_sha256"],
            hashlib.sha256(
                evidence.canonical_json_bytes(self.seed["normalized_dns_full_records"])
            ).hexdigest(),
        )
        self.assertEqual(self.seed["source_dns_full_records_bytes"], 1242)
        self.assertNotEqual(
            self.seed["normalized_dns_full_records_sha256"],
            self.seed["source_dns_full_records_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(assembler.SEED_PATH.read_bytes()).hexdigest(),
            assembler.EXPECTED_SEED_FILE_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(assembler.CONTRACT_PATH.read_bytes()).hexdigest(),
            assembler.EXPECTED_CONTRACT_FILE_SHA256,
        )

    def test_contract_freezes_review_binding_and_complete_graph(self) -> None:
        accepted = self.contract["accepted_execution_binding"]
        self.assertEqual(
            accepted["website"],
            {
                "sha": "eded5f05336ceaaa35ff7c62397a2785e74b4e62",
                "tree": "40887d94f6149d1a2841483f9ada5a8bda5843a1",
                "green_run_id": 31227813522,
            },
        )
        self.assertEqual(accepted["infrastructure"]["green_run_id"], 31221824132)
        graph = self.contract["graph"]
        self.assertEqual(len(graph["operations"]), 84)
        self.assertEqual(graph["provider_operation_count"], 174)
        self.assertEqual(graph["aws_readback_operation_count"], 58)
        self.assertEqual(graph["github_operation_count"], 26)
        self.assertEqual(graph["simulator_operation_count"], 90)
        self.assertEqual(graph["operations"][0]["id"], "sts-caller")

    def test_absence_contract_and_sensitive_projections_are_exact(self) -> None:
        operations = {item["id"]: item for item in self.contract["graph"]["operations"]}
        errors = {
            item["id"]: item["expected"]["error_code"]
            for item in operations.values()
            if item["expected"]["exit_code"] == "nonzero"
        }
        self.assertEqual(
            errors,
            {
                "s3-policy": "NoSuchBucketPolicy",
                "s3-lock-object": "404",
                "ecr-zero-digest": "ImageNotFoundException",
                "ecr-repository-policy": "RepositoryPolicyNotFoundException",
                "ecr-registry-policy": "RegistryPolicyNotFoundException",
            },
        )
        secret_queries = [
            item for item in operations.values() if item["mapper"] == "secret-policy-v1"
        ]
        self.assertEqual(len(secret_queries), 6)
        for item in secret_queries:
            self.assertIn("--query", item["argv"])
            query = item["argv"][item["argv"].index("--query") + 1]
            self.assertEqual(
                query,
                "{ARN:ARN,Name:Name,ResourcePolicyPresent:ResourcePolicy!=`null`}",
            )
        self.assertNotIn("--query", operations["sts-caller"]["argv"])
        state_argv = operations["s3-state-object"]["argv"]
        state_query = state_argv[state_argv.index("--query") + 1]
        self.assertEqual(
            state_query,
            "{AcceptRanges:AcceptRanges,LastModified:LastModified,ContentLength:ContentLength,"
            "ETag:ETag,VersionId:VersionId,ServerSideEncryption:ServerSideEncryption}",
        )
        for name in ("web", "worker", "migration"):
            argv = operations[f"ecs-{name}-task-definition"]["argv"]
            query = argv[argv.index("--query") + 1]
            self.assertNotIn("containerDefinitions", query)
            self.assertNotIn("environment", query.lower())

        expected_count_queries = {
            "ecr-images": (
                "{ImageCount:length(imageDetails),ImageDetailsType:type(imageDetails),"
                "NextToken:NextToken}"
            ),
            "target-health": (
                "{TargetCount:length(TargetHealthDescriptions),"
                "TargetHealthDescriptionsType:type(TargetHealthDescriptions)}"
            ),
            "ecs-running-tasks": (
                "{TaskCount:length(taskArns),TaskArnsType:type(taskArns),NextToken:NextToken}"
            ),
            "ecs-pending-tasks": (
                "{TaskCount:length(taskArns),TaskArnsType:type(taskArns),NextToken:NextToken}"
            ),
            "ecs-stopped-tasks": (
                "{TaskCount:length(taskArns),TaskArnsType:type(taskArns),NextToken:NextToken}"
            ),
        }
        for command_id, expected_query in expected_count_queries.items():
            argv = operations[command_id]["argv"]
            self.assertEqual(argv[argv.index("--query") + 1], expected_query)
            if command_id != "target-health":
                self.assertEqual(argv[argv.index("--max-items") + 1], "1")
        repository_argv = operations["ecr-repository"]["argv"]
        repository_query = repository_argv[repository_argv.index("--query") + 1]
        self.assertNotIn("imageTagMutabilityExclusionFilters", repository_query)

    def test_projected_counts_reject_unknown_shapes_types_counts_and_tokens(self) -> None:
        accepted = {
            "ImageCount": 0,
            "ImageDetailsType": "array",
            "NextToken": None,
        }
        self.assertEqual(
            assembler._projected_count(
                accepted,
                count_field="ImageCount",
                type_field="ImageDetailsType",
                token_field="NextToken",
                code="invalid-ecr-response",
                truncated_code="truncated-ecr-response",
            ),
            0,
        )
        mutations: list[dict[str, Any]] = [
            {**accepted, "unknown": []},
            {**accepted, "ImageDetailsType": "object"},
            {**accepted, "ImageCount": True},
            {**accepted, "ImageCount": -1},
            {**accepted, "ImageCount": 0.5},
            {**accepted, "ImageCount": "0"},
            {**accepted, "ImageCount": None},
            {**accepted, "NextToken": "opaque"},
            {"imageDetails": [], "nextToken": None},
        ]
        for changed in mutations:
            with self.subTest(changed=changed), self.assertRaises(assembler.AssemblyError):
                assembler._projected_count(
                    changed,
                    count_field="ImageCount",
                    type_field="ImageDetailsType",
                    token_field="NextToken",
                    code="invalid-ecr-response",
                    truncated_code="truncated-ecr-response",
                )

        count_cases: list[tuple[dict[str, Any], dict[str, Any]]] = [
            (
                {"TaskCount": 0, "TaskArnsType": "array", "NextToken": None},
                {
                    "count_field": "TaskCount",
                    "type_field": "TaskArnsType",
                    "token_field": "NextToken",
                    "code": "invalid-ecs-task-list",
                    "truncated_code": "truncated-ecs-task-list",
                },
            ),
            (
                {
                    "TargetCount": 0,
                    "TargetHealthDescriptionsType": "array",
                },
                {
                    "count_field": "TargetCount",
                    "type_field": "TargetHealthDescriptionsType",
                    "code": "invalid-target-health-response",
                },
            ),
        ]
        for document, arguments in count_cases:
            self.assertEqual(assembler._projected_count(document, **arguments), 0)
            missing = dict(document)
            missing.pop(next(iter(document)))
            with self.assertRaises(assembler.AssemblyError):
                assembler._projected_count(missing, **arguments)
            wrong_type = dict(document)
            wrong_type[arguments["type_field"]] = "object"
            with self.assertRaises(assembler.AssemblyError):
                assembler._projected_count(wrong_type, **arguments)

    def test_explicit_single_resource_pagination_fields_are_required_and_null(self) -> None:
        sts = {
            "Account": "817685572750",
            "Arn": (
                "arn:aws:sts::817685572750:assumed-role/phone-aws-sandbox-role/"
                "phone-sandbox-01234567"
            ),
            "UserId": "AROA34YO3VSHI2OCVBKTW:phone-sandbox-01234567",
        }
        bindings = assembler.build_bindings_envelope(self.seed, self.manifest, CAPTURE_ID, sts)
        responses = accepted_provider_responses(self.seed, self.manifest, bindings)
        pagination_cases = (
            ("ecr-repository", "nextToken", "invalid-ecr-response", "truncated-ecr-response"),
            (
                "target-group",
                "NextMarker",
                "invalid-target-group-response",
                "truncated-target-group-response",
            ),
            ("rds-database", "Marker", "invalid-rds-response", "truncated-rds-response"),
        )
        for command_id, token_field, missing_code, truncated_code in pagination_cases:
            for mutation, expected_code in (("missing", missing_code), ("opaque", truncated_code)):
                changed = copy.deepcopy(responses)
                if mutation == "missing":
                    changed[command_id].pop(token_field)
                else:
                    changed[command_id][token_field] = "opaque"
                raw = {name: {"response": value} for name, value in changed.items()}
                with self.subTest(command_id=command_id, mutation=mutation):
                    with self.assertRaises(assembler.AssemblyError) as caught:
                        assembler.build_resource_envelope(raw, self.seed, self.manifest, bindings)
                    self.assertEqual(str(caught.exception), expected_code)

    def test_discarded_provider_cardinalities_and_alias_items_are_strict(self) -> None:
        sts = {
            "Account": "817685572750",
            "Arn": (
                "arn:aws:sts::817685572750:assumed-role/phone-aws-sandbox-role/"
                "phone-sandbox-01234567"
            ),
            "UserId": "AROA34YO3VSHI2OCVBKTW:phone-sandbox-01234567",
        }
        bindings = assembler.build_bindings_envelope(self.seed, self.manifest, CAPTURE_ID, sts)
        responses = accepted_provider_responses(self.seed, self.manifest, bindings)
        for command_id, field_path in (
            ("cloudfront-distribution", ("Aliases", "Quantity")),
            ("github-sandbox-branch-policy", ("total_count",)),
            ("github-sandbox-branch-policy", ("branch_policies", 0, "id")),
        ):
            for invalid in (True, -1, 0.5, "1", None):
                changed = copy.deepcopy(responses)
                target: Any = changed[command_id]
                for component in field_path[:-1]:
                    target = target[component]
                target[field_path[-1]] = invalid
                raw = {name: {"response": value} for name, value in changed.items()}
                with (
                    self.subTest(command_id=command_id, field_path=field_path, invalid=invalid),
                    self.assertRaises(assembler.AssemblyError),
                ):
                    assembler.build_resource_envelope(raw, self.seed, self.manifest, bindings)

        invalid_alias_items: tuple[Any, ...] = ("alias", None, 1, {})
        for invalid_items in invalid_alias_items:
            changed = copy.deepcopy(responses)
            changed["cloudfront-distribution"]["Aliases"]["Items"] = invalid_items
            raw = {name: {"response": value} for name, value in changed.items()}
            with (
                self.subTest(invalid_items=invalid_items),
                self.assertRaises(assembler.AssemblyError),
            ):
                assembler.build_resource_envelope(raw, self.seed, self.manifest, bindings)
        empty_node_id = copy.deepcopy(responses)
        empty_node_id["github-sandbox-branch-policy"]["branch_policies"][0]["node_id"] = ""
        raw = {name: {"response": value} for name, value in empty_node_id.items()}
        with self.assertRaisesRegex(assembler.AssemblyError, "invalid-github-branch-policy"):
            assembler.build_resource_envelope(raw, self.seed, self.manifest, bindings)

    def test_consumed_provider_booleans_and_numbers_are_strict(self) -> None:
        sts = {
            "Account": "817685572750",
            "Arn": (
                "arn:aws:sts::817685572750:assumed-role/phone-aws-sandbox-role/"
                "phone-sandbox-01234567"
            ),
            "UserId": "AROA34YO3VSHI2OCVBKTW:phone-sandbox-01234567",
        }
        bindings = assembler.build_bindings_envelope(self.seed, self.manifest, CAPTURE_ID, sts)
        responses = accepted_provider_responses(self.seed, self.manifest, bindings)
        invalid_locations: tuple[Any, ...] = (False, 0, "", [], {})
        mutations: list[tuple[str, tuple[str | int, ...], Any]] = [
            ("s3-location", ("LocationConstraint",), invalid) for invalid in invalid_locations
        ]
        mutations.extend(
            (
                "s3-public-access",
                ("PublicAccessBlockConfiguration", field),
                1,
            )
            for field in (
                "BlockPublicAcls",
                "IgnorePublicAcls",
                "BlockPublicPolicy",
                "RestrictPublicBuckets",
            )
        )
        mutations.extend(
            [
                (
                    "ecr-repository",
                    ("repositories", 0, "imageScanningConfiguration", "scanOnPush"),
                    1,
                ),
                ("ecr-repository", ("repositories", 0, "repositoryUri"), "wrong"),
                ("ecr-repository", ("repositories", 0, "createdAt"), False),
                ("ecr-repository", ("repositories", 0, "createdAt"), ""),
                (
                    "ecr-repository",
                    ("repositories", 0, "createdAt"),
                    "not-a-timestamp",
                ),
                (
                    "ecr-repository",
                    ("repositories", 0, "encryptionConfiguration", "encryptionType"),
                    "AES256",
                ),
                ("cloudfront-distribution", ("Enabled",), 1),
                ("rds-database", ("DBInstances", 0, "StorageEncrypted"), 1),
                ("rds-database", ("DBInstances", 0, "PubliclyAccessible"), 0),
                ("target-group", ("TargetGroups", 0, "Port"), True),
            ]
        )
        mutations.extend(
            ("s3-state-object", (field,), invalid)
            for field, invalid in (
                ("AcceptRanges", 1),
                ("AcceptRanges", "other"),
                ("LastModified", 1),
                ("LastModified", ""),
                ("LastModified", "not-a-timestamp"),
                ("ContentLength", True),
                ("ContentLength", 0),
                ("ETag", 1),
                ("ETag", ""),
                ("ETag", "unquoted"),
                ("VersionId", None),
                ("VersionId", ""),
                ("ServerSideEncryption", 1),
                ("ServerSideEncryption", "aws:kms"),
            )
        )
        mutations.extend(
            ("ecs-cluster", ("clusters", 0, field), False)
            for field in (
                "registeredContainerInstancesCount",
                "runningTasksCount",
                "pendingTasksCount",
                "activeServicesCount",
            )
        )
        mutations.extend(
            ("ecs-services", ("services", service_index, field), False)
            for service_index in range(2)
            for field in ("desiredCount", "runningCount", "pendingCount")
        )
        mutations.extend(
            (f"ecs-{name}-task-definition", ("taskDefinition", "revision"), True)
            for name in ("web", "worker", "migration")
        )
        mutations.extend(
            (f"secret-{key}-metadata", ("RotationEnabled",), 0)
            for key in self.manifest["static"]["secret_names"]
        )
        for command_id, field_path, invalid in mutations:
            changed = copy.deepcopy(responses)
            target: Any = changed[command_id]
            for component in field_path[:-1]:
                target = target[component]
            target[field_path[-1]] = invalid
            raw = {name: {"response": value} for name, value in changed.items()}
            with (
                self.subTest(command_id=command_id, field_path=field_path, invalid=invalid),
                self.assertRaises(assembler.AssemblyError),
            ):
                assembler.build_resource_envelope(raw, self.seed, self.manifest, bindings)
        for command_id, response_field, invalid in (
            ("s3-encryption", "BucketKeyEnabled", 1),
            ("s3-encryption", "BucketKeyEnabled", True),
            ("s3-versioning", "MFADelete", 1),
        ):
            changed = copy.deepcopy(responses)
            if command_id == "s3-encryption":
                changed[command_id]["ServerSideEncryptionConfiguration"]["Rules"][0][
                    response_field
                ] = invalid
            else:
                changed[command_id][response_field] = invalid
            raw = {name: {"response": value} for name, value in changed.items()}
            with (
                self.subTest(command_id=command_id, response_field=response_field),
                self.assertRaises(assembler.AssemblyError),
            ):
                assembler.build_resource_envelope(raw, self.seed, self.manifest, bindings)
        invalid_kms_master_key_ids: tuple[Any, ...] = ({}, "wrong")
        for invalid in invalid_kms_master_key_ids:
            changed = copy.deepcopy(responses)
            changed["s3-encryption"]["ServerSideEncryptionConfiguration"]["Rules"][0][
                "ApplyServerSideEncryptionByDefault"
            ]["KMSMasterKeyID"] = invalid
            raw = {name: {"response": value} for name, value in changed.items()}
            with (
                self.subTest(kms_master_key_id=invalid),
                self.assertRaises(assembler.AssemblyError),
            ):
                assembler.build_resource_envelope(raw, self.seed, self.manifest, bindings)

    def test_route53_realistic_paginator_token_and_exact_record_shape_pass(self) -> None:
        response = {
            "ResourceRecordSets": [
                {
                    "Name": "web.dtcdev.click.",
                    "Type": "A",
                    "AliasTarget": {
                        "HostedZoneId": "Z2FDTNDATAQYW2",
                        "DNSName": "d3gb5jeh0h8cok.cloudfront.net.",
                        "EvaluateTargetHealth": False,
                    },
                }
            ],
            "NextToken": "private-paginator-token",
        }
        self.assertEqual(
            assembler._route53_record(response),
            {
                "name": "web.dtcdev.click.",
                "type": "A",
                "value": "d3gb5jeh0h8cok.cloudfront.net.",
            },
        )
        changed: dict[str, Any] = copy.deepcopy(response)
        changed["ResourceRecordSets"][0]["Unexpected"] = "not discarded"
        with self.assertRaisesRegex(assembler.AssemblyError, "invalid-route53-response"):
            assembler._route53_record(changed)

        for field, invalid in (
            ("IsTruncated", 0),
            ("MaxItems", "1"),
            ("NextRecordName", "ignored.example."),
            ("NextRecordType", "A"),
            ("NextRecordIdentifier", "ignored"),
            ("NextToken", None),
            ("NextToken", False),
            ("NextToken", 0),
            ("NextToken", ""),
        ):
            changed = copy.deepcopy(response)
            changed[field] = invalid
            with (
                self.subTest(field=field, invalid=invalid),
                self.assertRaisesRegex(assembler.AssemblyError, "invalid-route53-response"),
            ):
                assembler._route53_record(changed)

        changed = copy.deepcopy(response)
        changed["ResourceRecordSets"][0] = {
            "Name": "certificate.web.dtcdev.click.",
            "Type": "CNAME",
            "TTL": 300.0,
            "ResourceRecords": [{"Value": "target.example."}],
        }
        with self.assertRaisesRegex(assembler.AssemblyError, "invalid-route53-response"):
            assembler._route53_record(changed)

        responses: list[dict[str, Any]] = [
            {"ResourceRecordSets": [copy.deepcopy(record)], "NextToken": "private-token"}
            for record in self.seed["normalized_dns_full_records"]
        ]
        self.assertEqual(
            assembler._validated_route53_records(responses, self.seed),
            self.seed["dns_records"],
        )
        responses[4]["ResourceRecordSets"][0]["AliasTarget"]["HostedZoneId"] = "ZWRONG"
        with self.assertRaisesRegex(
            assembler.AssemblyError, "route53-full-record-binding-mismatch"
        ):
            assembler._validated_route53_records(responses, self.seed)

    def test_resolved_simulator_plan_is_exact_and_atomic(self) -> None:
        bindings = operator._provisional_bindings(self.seed, self.manifest, CAPTURE_ID)
        plan = assembler.resolve_simulator_plan(self.contract, self.manifest, bindings)

        self.assertEqual(len(plan), 90)
        self.assertEqual([item["sequence"] for item in plan], list(range(85, 175)))
        self.assertEqual(len({item["id"] for item in plan}), 90)
        for item in plan:
            argv = item["argv"]
            self.assertEqual(argv.count("--action-names"), 1)
            self.assertEqual(argv.count("--resource-arns"), 1)
            self.assertNotIn("--policy-input-list", argv)
            self.assertNotIn("--resource-policy", argv)
            self.assertNotIn("--caller-arn", argv)
            self.assertEqual(len(item["request"]["action_names"]), 1)
            self.assertEqual(len(item["request"]["resource_arns"]), 1)
        self.assertEqual(
            len(assembler.complete_operation_specs(self.contract, self.manifest, bindings)),
            174,
        )
        self.assertEqual(
            assembler.execution_graph_sha256(
                assembler.complete_operation_specs(self.contract, self.manifest, bindings)
            ),
            assembler.EXPECTED_RESOLVED_GRAPH_SHA256,
        )

    def test_phone_role_identity_is_exact_and_application_roles_are_rejected(self) -> None:
        valid = {
            "account_id": "817685572750",
            "arn": (
                "arn:aws:sts::817685572750:assumed-role/phone-aws-sandbox-role/"
                "phone-sandbox-01234567"
            ),
            "user_id": "AROA34YO3VSHI2OCVBKTW:phone-sandbox-01234567",
        }
        assembler.validate_operator_identity(valid, self.seed)
        rejected = [
            {
                "account_id": "817685572750",
                "arn": f"arn:aws:sts::817685572750:assumed-role/{role}/phone-sandbox-01234567",
                "user_id": "AROA34YO3VSHI2OCVBKTW:phone-sandbox-01234567",
            }
            for role in (
                "website-sandbox-github-publisher",
                "website-sandbox-github-deployer",
                "website-sandbox-task-application",
                "website-sandbox-task-execution",
            )
        ]
        rejected.extend(
            [
                {**valid, "account_id": "000000000000"},
                {
                    **valid,
                    "arn": "arn:aws:iam::817685572750:role/phone-aws-sandbox-role",
                },
                {**valid, "arn": "malformed"},
                {
                    **valid,
                    "arn": ("arn:aws:sts::817685572750:assumed-role/phone-aws-sandbox-role/wrong"),
                    "user_id": "AROA34YO3VSHI2OCVBKTW:wrong",
                },
                {**valid, "user_id": "AROAOTHER:phone-sandbox-01234567"},
                {"account_id": "", "arn": "", "user_id": ""},
            ]
        )
        for changed in rejected:
            with self.subTest(identity=changed), self.assertRaises(assembler.AssemblyError):
                assembler.validate_operator_identity(changed, self.seed)

    def test_contract_mutations_fail_closed(self) -> None:
        mutations: list[dict[str, Any]] = []
        for mutate in ("binding", "identity", "error", "config", "retry"):
            changed = copy.deepcopy(self.contract)
            if mutate == "binding":
                changed["accepted_execution_binding"]["website"]["green_run_id"] += 1
            elif mutate == "identity":
                changed["graph"]["operations"][0]["id"] = "not-sts"
            elif mutate == "error":
                changed["graph"]["operations"][22]["expected"]["error_code"] = "403"
            elif mutate == "config":
                changed["child_environments"]["aws_fixed_values"]["AWS_CONFIG_FILE"] = (
                    "/home/alexey/.aws/config"
                )
            else:
                changed["limits"]["retry_count"] = 1
            mutations.append(changed)
        names = ("binding", "identity", "error", "config", "retry")
        for name, changed in zip(names, mutations, strict=True):
            with self.subTest(mutation=name), self.assertRaises(assembler.AssemblyError):
                assembler.validate_execution_contract(changed, self.seed, self.manifest)

    def test_graph_mutations_templates_executables_and_simulator_axes_fail_closed(self) -> None:
        graph_mutations: list[tuple[str, dict[str, Any]]] = []
        for name in ("missing", "extra", "duplicate", "reordered", "executable", "shell"):
            changed = copy.deepcopy(self.contract)
            operations = changed["graph"]["operations"]
            if name == "missing":
                operations.pop()
            elif name == "extra":
                extra = copy.deepcopy(operations[-1])
                extra["id"] = "extra-command"
                extra["sequence"] = 85
                operations.append(extra)
            elif name == "duplicate":
                operations[1]["id"] = operations[0]["id"]
            elif name == "reordered":
                operations[1], operations[2] = operations[2], operations[1]
            elif name == "executable":
                operations[1]["argv"][0] = "/bin/sh"
            else:
                operations[1]["argv"].append("$(touch /tmp/gate-b-shell-injection)")
            graph_mutations.append((name, changed))
        unresolved = copy.deepcopy(self.contract)
        unresolved["graph"]["operations"][1]["argv"].append("${unresolved}")
        graph_mutations.append(("unresolved", unresolved))
        for name, changed in graph_mutations:
            with self.subTest(mutation=name), self.assertRaises(assembler.AssemblyError):
                assembler.validate_execution_contract(changed, self.seed, self.manifest)

        bindings = operator._provisional_bindings(self.seed, self.manifest, CAPTURE_ID)
        for axis in ("action", "resource", "context"):
            changed_manifest = copy.deepcopy(self.manifest)
            row = changed_manifest["simulator_rows"][0]
            if axis == "context":
                row[axis] = {"aws:RequestedRegion": "us-east-1"}
            else:
                row[axis] = f"changed-{axis}"
            with self.subTest(axis=axis), self.assertRaises(assembler.AssemblyError):
                assembler.complete_operation_specs(self.contract, changed_manifest, bindings)

    def test_modules_have_no_sdk_or_unbounded_command_surface(self) -> None:
        assembler_source = (ROOT / "deploy/gate_b_assembler.py").read_text()
        operator_source = (ROOT / "deploy/gate_b_operator.py").read_text()
        self.assertNotIn("boto3", assembler_source + operator_source)
        self.assertNotIn("botocore", assembler_source + operator_source)
        self.assertNotIn("shell=True", operator_source)
        self.assertNotIn("os.environ.copy", operator_source)
        self.assertNotIn("requests", assembler_source)
        self.assertNotIn("urllib", assembler_source)

    def test_policy_mapper_builds_bundle_accepted_by_unchanged_validator(self) -> None:
        bindings = operator._provisional_bindings(self.seed, self.manifest, CAPTURE_ID)
        expected = evidence_fixtures.policy_document(self.manifest)["payload"]
        raw: dict[str, dict[str, Any]] = {}

        def item(response: dict[str, Any]) -> dict[str, Any]:
            return {"response": response, "error": {}, "status": {}}

        for role_class, role in expected["roles"].items():
            raw[f"iam-{role_class}-role"] = item(
                {
                    "Role": {
                        "RoleName": role["name"],
                        "Path": role["path"],
                        "Arn": role["arn"],
                        "MaxSessionDuration": role["max_session_duration"],
                        "AssumeRolePolicyDocument": role["trust_policy"],
                    }
                }
            )
            raw[f"iam-{role_class}-inline-list"] = item(
                {"PolicyNames": [role["name"]], "IsTruncated": False}
            )
            raw[f"iam-{role_class}-attached-list"] = item(
                {"AttachedPolicies": [], "IsTruncated": False}
            )
            raw[f"iam-{role_class}-inline"] = item(
                {
                    "RoleName": role["name"],
                    "PolicyName": role["name"],
                    "PolicyDocument": role["inline_policies"][role["name"]],
                }
            )
        kms = expected["kms"]
        metadata = {
            "AWSAccountId": "817685572750",
            "KeyId": kms["key_id"],
            "Arn": kms["arn"],
            "CreationDate": "2026-08-07T00:00:00Z",
            "Enabled": kms["enabled"],
            "Description": "fixture",
            "KeyUsage": kms["key_usage"],
            "KeyState": kms["key_state"],
            "Origin": kms["origin"],
            "KeyManager": kms["key_manager"],
            "CustomerMasterKeySpec": kms["spec"],
            "KeySpec": kms["spec"],
            "EncryptionAlgorithms": ["SYMMETRIC_DEFAULT"],
            "MultiRegion": kms["multi_region"],
        }
        raw["kms-key"] = item({"KeyMetadata": metadata})
        raw["kms-alias"] = item({"KeyMetadata": copy.deepcopy(metadata)})
        raw["kms-rotation"] = item({"KeyRotationEnabled": True})
        raw["kms-policy"] = item({"Policy": kms["key_policy"]})
        raw["kms-grants"] = item({"Grants": kms["grant_inventory"]})

        document = assembler.build_policy_envelope(raw, self.manifest, bindings)
        binding_result = evidence.validate_bindings(bindings, self.manifest)
        result = evidence.validate_policy_bundle(document, self.manifest, binding_result)
        self.assertEqual(result["status"], "PASS")
        changed = copy.deepcopy(raw)
        changed["iam-publisher-inline"]["response"]["RoleName"] = "wrong"
        with self.assertRaisesRegex(assembler.AssemblyError, "iam-policy-inventory-mismatch"):
            assembler.build_policy_envelope(changed, self.manifest, bindings)
        truncated = copy.deepcopy(raw)
        truncated["kms-grants"]["response"]["Truncated"] = True
        with self.assertRaisesRegex(assembler.AssemblyError, "truncated-kms-grants"):
            assembler.build_policy_envelope(truncated, self.manifest, bindings)
        for command_id, field in (
            ("iam-publisher-inline-list", "IsTruncated"),
            ("iam-publisher-attached-list", "IsTruncated"),
            ("kms-grants", "Truncated"),
        ):
            for invalid in (0, "false", None):
                changed = copy.deepcopy(raw)
                changed[command_id]["response"][field] = invalid
                with (
                    self.subTest(command_id=command_id, field=field, invalid=invalid),
                    self.assertRaises(assembler.AssemblyError),
                ):
                    assembler.build_policy_envelope(changed, self.manifest, bindings)
        for command_id, field_path, invalid in (
            ("kms-key", ("KeyMetadata", "Enabled"), 1),
            ("kms-key", ("KeyMetadata", "MultiRegion"), 0),
            ("kms-rotation", ("KeyRotationEnabled",), 1),
            ("iam-publisher-role", ("Role", "MaxSessionDuration"), True),
        ):
            changed = copy.deepcopy(raw)
            target: Any = changed[command_id]["response"]
            for component in field_path[:-1]:
                target = target[component]
            target[field_path[-1]] = invalid
            if command_id == "kms-key":
                changed["kms-alias"] = copy.deepcopy(changed["kms-key"])
            with (
                self.subTest(command_id=command_id, field_path=field_path, invalid=invalid),
                self.assertRaises(assembler.AssemblyError),
            ):
                assembler.build_policy_envelope(changed, self.manifest, bindings)
        for field, invalid in (("Enabled", 1), ("MultiRegion", 0)):
            changed = copy.deepcopy(raw)
            changed["kms-alias"]["response"]["KeyMetadata"][field] = invalid
            with (
                self.subTest(alias_field=field, invalid=invalid),
                self.assertRaisesRegex(assembler.AssemblyError, "kms-alias-target-mismatch"),
            ):
                assembler.build_policy_envelope(changed, self.manifest, bindings)
        synchronized_kms_mutations: tuple[tuple[str, Any], ...] = (
            ("EncryptionAlgorithms", {}),
            ("AWSAccountId", False),
            ("CustomerMasterKeySpec", "RSA_2048"),
            ("Description", {}),
            ("CreationDate", False),
            ("SigningAlgorithms", []),
        )
        for field, invalid in synchronized_kms_mutations:
            changed = copy.deepcopy(raw)
            changed["kms-key"]["response"]["KeyMetadata"][field] = invalid
            changed["kms-alias"] = copy.deepcopy(changed["kms-key"])
            with (
                self.subTest(kms_field=field, invalid=invalid),
                self.assertRaises(assembler.AssemblyError),
            ):
                assembler.build_policy_envelope(changed, self.manifest, bindings)
        optional_provider_mutations: tuple[tuple[str, str, Any], ...] = (
            ("kms-rotation", "RotationPeriodInDays", True),
            ("kms-rotation", "NextRotationDate", {}),
            ("kms-policy", "PolicyName", 1),
            ("iam-publisher-role", "RoleId", {}),
            ("iam-publisher-role", "CreateDate", False),
            ("iam-publisher-role", "RoleLastUsed", []),
            ("iam-publisher-role", "Tags", {}),
            ("iam-publisher-role", "Description", {}),
        )
        for command_id, field, invalid in optional_provider_mutations:
            changed = copy.deepcopy(raw)
            if command_id == "iam-publisher-role":
                changed[command_id]["response"]["Role"][field] = invalid
            else:
                changed[command_id]["response"][field] = invalid
            with (
                self.subTest(command_id=command_id, optional_field=field, invalid=invalid),
                self.assertRaises(assembler.AssemblyError),
            ):
                assembler.build_policy_envelope(changed, self.manifest, bindings)
        explicit_null_boundary = copy.deepcopy(raw)
        explicit_null_boundary["iam-publisher-role"]["response"]["Role"]["PermissionsBoundary"] = (
            None
        )
        with self.assertRaisesRegex(assembler.AssemblyError, "iam-permissions-boundary-mismatch"):
            assembler.build_policy_envelope(explicit_null_boundary, self.manifest, bindings)

    def test_simulator_mapper_handles_all_rows_and_rejects_truncation(self) -> None:
        bindings = operator._provisional_bindings(self.seed, self.manifest, CAPTURE_ID)
        expected = evidence_fixtures.simulator_document(self.manifest, bindings)["payload"][
            "results"
        ]
        raw = {
            f"simulator-{result['row_id']}": {
                "response": {
                    "EvaluationResults": result["EvaluationResults"],
                    "IsTruncated": False,
                },
                "error": {},
                "status": {},
            }
            for result in expected
        }
        document = assembler.build_simulator_envelope(raw, self.contract, self.manifest, bindings)
        binding_result = evidence.validate_bindings(bindings, self.manifest)
        result = evidence.validate_simulator_bundle(
            document, self.manifest, bindings, binding_result
        )
        self.assertEqual(result["row_count"], 90)
        first_id = next(iter(raw))
        for name in (
            "zero-results",
            "multiple-results",
            "wrong-action",
            "wrong-resource",
            "wrong-decision",
            "missing-context",
        ):
            changed = copy.deepcopy(raw)
            evaluations = changed[first_id]["response"]["EvaluationResults"]
            if name == "zero-results":
                evaluations.clear()
            elif name == "multiple-results":
                evaluations.append(copy.deepcopy(evaluations[0]))
            elif name == "wrong-action":
                evaluations[0]["EvalActionName"] = "changed:Action"
            elif name == "wrong-resource":
                evaluations[0]["EvalResourceName"] = "arn:aws:s3:::changed"
            elif name == "wrong-decision":
                evaluations[0]["EvalDecision"] = (
                    "allowed" if evaluations[0]["EvalDecision"] != "allowed" else "implicitDeny"
                )
            else:
                evaluations[0]["MissingContextValues"] = ["aws:RequestedRegion"]
            with self.subTest(name=name), self.assertRaises(assembler.AssemblyError):
                assembler.build_simulator_envelope(changed, self.contract, self.manifest, bindings)
        for invalid in (True, 0, "false", None):
            changed = copy.deepcopy(raw)
            first = next(iter(changed.values()))
            first["response"]["IsTruncated"] = invalid
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesRegex(
                    assembler.AssemblyError, "truncated-simulator-provider-response"
                ),
            ):
                assembler.build_simulator_envelope(changed, self.contract, self.manifest, bindings)


class GateBOperatorTests(SimpleTestCase):
    def setUp(self) -> None:
        self.contract = load_contract()

    def test_child_environments_are_exact_and_separate(self) -> None:
        aws = operator.build_aws_child_env(self.contract, credentials())
        github = operator.build_github_child_env(self.contract)

        self.assertEqual(aws["AWS_CONFIG_FILE"], "/dev/null")
        self.assertEqual(aws["AWS_SHARED_CREDENTIALS_FILE"], "/dev/null")
        self.assertEqual(aws["AWS_EC2_METADATA_DISABLED"], "true")
        self.assertEqual(aws["AWS_MAX_ATTEMPTS"], "1")
        self.assertEqual(aws["AWS_RETRY_MODE"], "standard")
        self.assertEqual(
            set(aws),
            set(self.contract["child_environments"]["base_values"])
            | set(self.contract["child_environments"]["aws_fixed_values"])
            | set(self.contract["child_environments"]["aws_added_names"]),
        )
        self.assertFalse(any(name.startswith("AWS_") for name in github))
        self.assertNotIn("GH_TOKEN", github)
        self.assertNotIn("GITHUB_TOKEN", github)

    def test_credential_process_runs_once_and_never_persists_credentials(self) -> None:
        (ROOT / ".tmp").mkdir(mode=0o700, exist_ok=True)
        (ROOT / ".tmp").chmod(0o700)
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temporary:
            directory = Path(temporary)
            script = directory / "gate.py"
            env_file = directory / "env"
            config_file = directory / "config"
            aws_tool = directory / "aws"
            github_tool = directory / "gh"
            script.write_text("# accepted fixture\n")
            env_file.write_text("fixture\n")
            aws_tool.write_text("fixture aws\n")
            github_tool.write_text("fixture gh\n")
            script.chmod(0o775)
            env_file.chmod(0o600)
            aws_tool.chmod(0o775)
            github_tool.chmod(0o755)
            changed = copy.deepcopy(self.contract)
            credential = changed["credential_process"]
            credential["script_path"] = str(script)
            credential["script_file_sha256"] = hashlib.sha256(script.read_bytes()).hexdigest()
            credential["script_mode"] = "0775"
            credential["env_path"] = str(env_file)
            credential["config_path"] = str(config_file)
            credential["config_section"] = "profile phone"
            credential["configured_argv"] = [
                "python3",
                str(script),
                "credential-process",
                "--env",
                str(env_file),
            ]
            credential["execution_argv"] = [
                "/usr/bin/python3",
                str(script),
                "credential-process",
                "--env",
                str(env_file),
            ]
            changed["tools"] = {
                "aws": {
                    "resolved_path": str(aws_tool),
                    "mode": "0775",
                    "owner_uid": os.geteuid(),
                    "file_sha256": hashlib.sha256(aws_tool.read_bytes()).hexdigest(),
                },
                "github": {
                    "resolved_path": str(github_tool),
                    "mode": "0755",
                    "owner_uid": os.geteuid(),
                    "file_sha256": hashlib.sha256(github_tool.read_bytes()).hexdigest(),
                },
            }
            config_file.write_text(
                "[profile phone]\ncredential_process = "
                + " ".join(credential["configured_argv"])
                + "\n"
            )
            config_file.chmod(0o600)
            credential["config_file_sha256"] = hashlib.sha256(config_file.read_bytes()).hexdigest()
            now = datetime(2026, 8, 8, 12, tzinfo=UTC)
            output = {
                "Version": 1,
                "AccessKeyId": "ASIAAAAAAAAAAAAAAAAA",
                "SecretAccessKey": "secret-access-key-canary",
                "SessionToken": "session-token-canary",
                "Expiration": (now + timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
            }
            calls: list[tuple[Sequence[str], dict[str, str]]] = []

            def runner(
                argv: Sequence[str],
                *,
                cwd: Path,
                env: dict[str, str],
                timeout: int,
            ) -> subprocess.CompletedProcess[bytes]:
                del cwd, timeout
                calls.append((argv, env))
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout=evidence.canonical_json_bytes(output),
                    stderr=b"",
                )

            result = operator.load_frozen_credentials(changed, runner=runner, now=now)

            self.assertEqual(len(calls), 1)
            self.assertEqual(result.access_key_id, output["AccessKeyId"])
            self.assertEqual(
                set(calls[0][1]),
                {"HOME", "LANG", "LC_ALL", "TZ", "NO_COLOR", "PATH"},
            )
            for path in directory.iterdir():
                self.assertNotIn(output["AccessKeyId"], path.read_text())
                self.assertNotIn(output["SessionToken"], path.read_text())

            output["Expiration"] = (now + timedelta(seconds=901)).isoformat().replace("+00:00", "Z")
            with self.assertRaisesRegex(
                operator.OperatorError, "credential-lifetime-out-of-contract"
            ):
                operator.load_frozen_credentials(changed, runner=runner, now=now)
            output["Expiration"] = (now + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
            output["Version"] = True
            with self.assertRaisesRegex(operator.OperatorError, "invalid-credential-response"):
                operator.load_frozen_credentials(changed, runner=runner, now=now)

    def test_ttl_reserve_and_exact_provider_result_rules(self) -> None:
        spec = copy.deepcopy(self.contract["graph"]["operations"][0])
        now = datetime(2026, 8, 8, 12, tzinfo=UTC)
        expiring = credentials(now + timedelta(seconds=119))
        runner = mock.Mock()
        with self.assertRaisesRegex(operator.OperatorError, "credential-reserve-crossed"):
            operator.run_exact_argv(spec, self.contract, expiring, runner=runner, now=now)
        runner.assert_not_called()

        runner.return_value = subprocess.CompletedProcess(
            spec["argv"], 0, stdout=b"{}", stderr=b"warning\n"
        )
        with self.assertRaisesRegex(operator.OperatorError, "unexpected-provider-result"):
            operator.run_exact_argv(spec, self.contract, credentials(), runner=runner)

        absence = next(
            item for item in self.contract["graph"]["operations"] if item["id"] == "s3-lock-object"
        )
        good_error = b"An error occurred (404) when calling the HeadObject operation: missing\n"
        runner.return_value = subprocess.CompletedProcess(
            absence["argv"], 255, stdout=b"", stderr=good_error
        )
        response, error, code, _started, _finished, _stdout, _stderr = operator.run_exact_argv(
            absence, self.contract, credentials(), runner=runner
        )
        self.assertEqual(response, {})
        self.assertEqual(error["code"], "404")
        self.assertEqual(code, 255)

    def test_capture_start_and_offline_status_time_boundaries_fail_closed(self) -> None:
        now = datetime(2026, 8, 8, 12, tzinfo=UTC)

        def capture_id(offset: int) -> str:
            value = now + timedelta(seconds=offset)
            return f"{value:%Y%m%dT%H%M%SZ}-012345abcdef"

        operator._validate_capture_start(capture_id(-5), now)
        operator._validate_capture_start(capture_id(5), now)
        for offset in (-6, 6):
            with self.assertRaisesRegex(operator.OperatorError, "stale-capture-id"):
                operator._validate_capture_start(capture_id(offset), now)

        seed = load_seed()
        manifest = load_manifest()
        runner = mock.Mock()
        with (
            mock.patch.object(operator, "load_frozen_credentials") as vending,
            self.assertRaisesRegex(operator.OperatorError, "stale-capture-id"),
        ):
            operator.run_gate_b(
                capture_id(-6),
                seed,
                self.contract,
                manifest,
                runner=runner,
                now=now,
            )
        vending.assert_not_called()
        runner.assert_not_called()

        second_runner = mock.Mock()
        with (
            mock.patch.object(
                operator,
                "_validate_capture_start",
                side_effect=[None, operator.OperatorError("stale-capture-id")],
            ) as clock_check,
            mock.patch.object(operator, "_prepare_capture", return_value=ROOT / ".tmp"),
            mock.patch.object(
                operator, "load_frozen_credentials", return_value=credentials()
            ) as vending,
            self.assertRaisesRegex(operator.OperatorError, "stale-capture-id"),
        ):
            operator.run_gate_b(
                capture_id(0),
                seed,
                self.contract,
                manifest,
                runner=second_runner,
                now=now,
            )
        self.assertEqual(clock_check.call_count, 2)
        vending.assert_called_once()
        second_runner.assert_not_called()

        base_id = capture_id(0)

        def timestamp(offset: int) -> str:
            return (now + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")

        assembler._validated_status_times(base_id, timestamp(-5), timestamp(26))
        assembler._validated_status_times(base_id, timestamp(900), timestamp(900))
        assembler._validated_status_times(base_id, timestamp(5), timestamp(5), identity=True)
        with self.assertRaisesRegex(assembler.AssemblyError, "invalid-raw-status-time"):
            assembler._validated_status_times(base_id, timestamp(6), timestamp(6), identity=True)
        for started, finished in (
            (timestamp(-6), timestamp(-6)),
            (timestamp(901), timestamp(901)),
            (timestamp(899), timestamp(901)),
            (timestamp(0), timestamp(32)),
        ):
            with self.assertRaisesRegex(assembler.AssemblyError, "invalid-raw-status-time"):
                assembler._validated_status_times(base_id, started, finished)

        phases: dict[str, datetime] = {}
        assembler._record_phase_finished("identity", now, now + timedelta(seconds=2), phases)
        with self.assertRaisesRegex(assembler.AssemblyError, "invalid-raw-phase-order"):
            assembler._record_phase_finished(
                "readback", now + timedelta(seconds=1), now + timedelta(seconds=3), phases
            )
        assembler._record_phase_finished(
            "readback", now + timedelta(seconds=2), now + timedelta(seconds=4), phases
        )
        with self.assertRaisesRegex(assembler.AssemblyError, "invalid-raw-phase-order"):
            assembler._record_phase_finished(
                "simulator", now + timedelta(seconds=3), now + timedelta(seconds=5), phases
            )

    def test_default_runner_reaps_children_on_interrupt_and_closed_pipe_timeout(
        self,
    ) -> None:
        real_popen = subprocess.Popen
        children: list[subprocess.Popen[bytes]] = []

        def spawn(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child

        with (
            mock.patch.object(operator.subprocess, "Popen", side_effect=spawn),
            mock.patch.object(
                operator.selectors.DefaultSelector,
                "select",
                side_effect=KeyboardInterrupt,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            operator._default_runner(
                ["/usr/bin/python3", "-c", "import time; time.sleep(30)"],
                cwd=ROOT,
                env={"PATH": "/usr/bin:/bin"},
                timeout=1,
            )
        self.assertIsNotNone(children[-1].poll())

        with (
            mock.patch.object(operator.subprocess, "Popen", side_effect=spawn),
            mock.patch.object(
                operator.selectors.DefaultSelector,
                "register",
                side_effect=RuntimeError("selector registration failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "selector registration failed"),
        ):
            operator._default_runner(
                ["/usr/bin/python3", "-c", "import time; time.sleep(30)"],
                cwd=ROOT,
                env={"PATH": "/usr/bin:/bin"},
                timeout=1,
            )
        self.assertIsNotNone(children[-1].poll())

        with (
            mock.patch.object(operator.subprocess, "Popen", side_effect=spawn),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            operator._default_runner(
                [
                    "/usr/bin/python3",
                    "-c",
                    "import os,time; os.close(1); os.close(2); time.sleep(30)",
                ],
                cwd=ROOT,
                env={"PATH": "/usr/bin:/bin"},
                timeout=1,
            )
        self.assertIsNotNone(children[-1].poll())

    def test_phase_interrupt_cancels_active_and_late_registered_children(self) -> None:
        contract = copy.deepcopy(self.contract)
        contract["limits"]["max_concurrency"] = 2
        specs = contract["graph"]["operations"][:2]
        started = threading.Event()
        registry = operator._ProcessRegistry()
        real_popen = subprocess.Popen
        children: list[subprocess.Popen[bytes]] = []

        class SleepingRunner:
            def __call__(
                self,
                argv: Sequence[str],
                *,
                cwd: Path,
                env: dict[str, str],
                timeout: int,
            ) -> subprocess.CompletedProcess[bytes]:
                del argv, env, timeout
                started.set()
                return operator._run_bounded_process(
                    ["/usr/bin/python3", "-c", "import time; time.sleep(30)"],
                    cwd=cwd,
                    env={"PATH": "/usr/bin:/bin"},
                    timeout=30,
                    process_registry=registry,
                )

            def cancel_all(self) -> None:
                registry.kill_all()

        def spawn(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child

        def interrupt_wait(*args: Any, **kwargs: Any) -> None:
            del args, kwargs
            self.assertTrue(started.wait(timeout=2))
            raise KeyboardInterrupt

        began = time.monotonic()
        with (
            mock.patch.object(operator.subprocess, "Popen", side_effect=spawn),
            mock.patch.object(operator, "wait", side_effect=interrupt_wait),
            self.assertRaises(KeyboardInterrupt),
        ):
            operator._capture_bounded_phase(
                specs,
                contract,
                credentials(),
                CAPTURE_ID,
                ROOT / ".tmp",
                "0" * 64,
                runner=SleepingRunner(),
            )
        self.assertLess(time.monotonic() - began, 5)
        self.assertTrue(children)
        self.assertTrue(all(child.poll() is not None for child in children))

        late_registry = operator._ProcessRegistry()
        late_registry.kill_all()
        late_process = mock.Mock()
        late_process.pid = 12345
        late_process.poll.return_value = None
        with mock.patch.object(operator.os, "killpg") as killpg:
            late_registry.add(late_process)
        killpg.assert_called_once_with(12345, operator.signal.SIGKILL)
        late_process.wait.assert_called_once()

    def test_bound_runner_holds_reviewed_inodes_and_preserves_aws_venv_argv0(self) -> None:
        (ROOT / ".tmp").mkdir(mode=0o700, exist_ok=True)
        (ROOT / ".tmp").chmod(0o700)
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temporary:
            directory = Path(temporary)
            python = directory / "python"
            credential_script = directory / "credential.py"
            credential_env = directory / "env"
            aws_cli = directory / "aws"
            github_cli = directory / "gh"
            shutil.copyfile("/usr/bin/python3.12", python)
            credential_script.write_bytes(b"credential-original")
            credential_env.write_bytes(b"private-env")
            aws_cli.write_bytes(b"aws-original")
            github_cli.write_bytes(b"github-original")
            for path, mode in (
                (python, 0o755),
                (credential_script, 0o775),
                (credential_env, 0o600),
                (aws_cli, 0o775),
                (github_cli, 0o755),
            ):
                path.chmod(mode)

            changed = copy.deepcopy(self.contract)
            credential = changed["credential_process"]
            credential.update(
                {
                    "execution_argv": [
                        "/usr/bin/python3",
                        str(credential_script),
                        "credential-process",
                        "--env",
                        str(credential_env),
                    ],
                    "interpreter_resolved_path": str(python),
                    "interpreter_file_sha256": hashlib.sha256(python.read_bytes()).hexdigest(),
                    "interpreter_mode": "0755",
                    "interpreter_owner_uid": os.geteuid(),
                    "script_path": str(credential_script),
                    "script_file_sha256": hashlib.sha256(
                        credential_script.read_bytes()
                    ).hexdigest(),
                    "script_mode": "0775",
                    "env_path": str(credential_env),
                    "env_mode": "0600",
                }
            )
            changed["tools"] = {
                "aws": {
                    "resolved_path": str(aws_cli),
                    "file_sha256": hashlib.sha256(aws_cli.read_bytes()).hexdigest(),
                    "mode": "0775",
                    "owner_uid": os.geteuid(),
                    "interpreter_invocation_path": str(directory / "venv/bin/python"),
                    "interpreter_resolved_path": str(python),
                    "interpreter_file_sha256": hashlib.sha256(python.read_bytes()).hexdigest(),
                    "interpreter_mode": "0755",
                    "interpreter_owner_uid": os.geteuid(),
                },
                "github": {
                    "resolved_path": str(github_cli),
                    "file_sha256": hashlib.sha256(github_cli.read_bytes()).hexdigest(),
                    "mode": "0755",
                    "owner_uid": os.geteuid(),
                },
            }
            identity = [str(aws_cli), "identity"]
            logical = [str(aws_cli), "safe-readback"]
            changed["graph"]["operations"][0]["argv"] = identity
            held_descriptors: set[int] = set()

            def bounded(
                argv: Sequence[str],
                *,
                cwd: Path,
                env: dict[str, str],
                timeout: int,
                pass_fds: Sequence[int],
                executable: str | None,
                process_registry: operator._ProcessRegistry,
            ) -> subprocess.CompletedProcess[bytes]:
                del cwd, env, timeout
                self.assertIsNotNone(process_registry)
                held_descriptors.update(pass_fds)
                if len(pass_fds) == 3:
                    self.assertEqual(argv[0], credential["execution_argv"][0])
                    self.assertEqual(executable, f"/proc/self/fd/{pass_fds[0]}")
                else:
                    self.assertEqual(
                        argv[0], changed["tools"]["aws"]["interpreter_invocation_path"]
                    )
                    self.assertEqual(executable, f"/proc/self/fd/{pass_fds[0]}")
                    os.lseek(pass_fds[1], 0, os.SEEK_SET)
                    self.assertEqual(os.read(pass_fds[1], 64), b"aws-original")
                return subprocess.CompletedProcess(argv, 0, stdout=b"{}", stderr=b"")

            with (
                mock.patch.object(operator, "_run_bounded_process", side_effect=bounded),
                mock.patch.object(
                    assembler,
                    "execution_graph_sha256",
                    return_value=assembler.EXPECTED_RESOLVED_GRAPH_SHA256,
                ),
                operator._BoundRunner(changed) as bound,
            ):
                bound.authorize([{"argv": identity}, *[{"argv": logical}] * 173])
                aws_env = operator.build_aws_child_env(changed, credentials())
                with self.assertRaisesRegex(operator.OperatorError, "unbound-provider-operation"):
                    bound(identity, cwd=ROOT, env=aws_env, timeout=30)
                bound(
                    credential["execution_argv"],
                    cwd=ROOT,
                    env=operator._credential_child_env(),
                    timeout=30,
                )
                with self.assertRaisesRegex(
                    operator.OperatorError, "credential-resolution-repeated"
                ):
                    bound(
                        credential["execution_argv"],
                        cwd=ROOT,
                        env=operator._credential_child_env(),
                        timeout=30,
                    )
                with self.assertRaisesRegex(operator.OperatorError, "unbound-provider-operation"):
                    bound(logical, cwd=ROOT, env=aws_env, timeout=30)
                with self.assertRaisesRegex(
                    operator.OperatorError, "unsafe-bound-execution-context"
                ):
                    bound(
                        identity,
                        cwd=ROOT,
                        env={**aws_env, "HTTPS_PROXY": "http://unsafe.invalid"},
                        timeout=30,
                    )
                aws_cli.unlink()
                aws_cli.write_bytes(b"replacement")
                aws_cli.chmod(0o775)
                bound(identity, cwd=ROOT, env=aws_env, timeout=30)
                with self.assertRaisesRegex(operator.OperatorError, "unbound-provider-operation"):
                    bound(identity, cwd=ROOT, env=aws_env, timeout=30)
                bound.seal_identity()
                completed = bound(logical, cwd=ROOT, env=aws_env, timeout=30)
                self.assertEqual(completed.returncode, 0)

            for descriptor in held_descriptors:
                with self.assertRaises(OSError):
                    os.fstat(descriptor)

    def test_held_interpreter_execution_preserves_virtual_environment_context(self) -> None:
        (ROOT / ".tmp").mkdir(mode=0o700, exist_ok=True)
        (ROOT / ".tmp").chmod(0o700)
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temporary:
            directory = Path(temporary)
            environment = directory / "venv"
            venv.EnvBuilder(with_pip=False).create(environment)
            interpreter_path = environment / "bin/python"
            resolved_interpreter = interpreter_path.resolve(strict=True)
            version = f"python{sys.version_info.major}.{sys.version_info.minor}"
            site_packages = environment / "lib" / version / "site-packages"
            marker = site_packages / "gate_b_marker.py"
            marker.write_text("VALUE = 'venv-module-loaded'\n")
            script = directory / "entrypoint.py"
            script.write_text(
                "import json,sys\n"
                "import gate_b_marker\n"
                "print(json.dumps({'prefix':sys.prefix,'value':gate_b_marker.VALUE}))\n"
            )
            script.chmod(0o600)
            interpreter_fd = os.open(resolved_interpreter, os.O_RDONLY)
            script_fd = os.open(script, os.O_RDONLY)
            try:
                completed = operator._run_bounded_process(
                    [str(interpreter_path), f"/proc/self/fd/{script_fd}"],
                    cwd=ROOT,
                    env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                    timeout=5,
                    pass_fds=(interpreter_fd, script_fd),
                    executable=f"/proc/self/fd/{interpreter_fd}",
                )
            finally:
                os.close(script_fd)
                os.close(interpreter_fd)
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stderr, b"")
            self.assertEqual(
                json.loads(completed.stdout),
                {"prefix": str(environment), "value": "venv-module-loaded"},
            )

    def test_main_stops_cleanly_on_keyboard_interrupt(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch.object(operator, "run_gate_b", side_effect=KeyboardInterrupt),
            redirect_stderr(stderr),
        ):
            code = operator.main(["capture", "--capture-id", CAPTURE_ID])
        self.assertEqual(code, 1)
        self.assertEqual(stderr.getvalue(), "gate-b-operator-stop\n")

        stderr = io.StringIO()
        with (
            mock.patch.object(
                assembler, "validate_execution_contract", side_effect=KeyboardInterrupt
            ),
            redirect_stderr(stderr),
        ):
            code = assembler.main(["validate-contract"])
        self.assertEqual(code, 1)
        self.assertEqual(stderr.getvalue(), "gate-b-assembly-stop\n")

    def test_plan_cli_is_offline_and_reports_exact_counts(self) -> None:
        stdout = mock.Mock()
        stdout.buffer = io.BytesIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(operator, "load_frozen_credentials") as vending,
            mock.patch.object(operator.sys, "stdout", stdout),
            redirect_stderr(stderr),
        ):
            code = operator.main(["plan"])
        self.assertEqual(code, 0)
        vending.assert_not_called()
        result = json.loads(stdout.buffer.getvalue())
        self.assertEqual(result["provider_operation_count"], 174)
        self.assertEqual(result["aws_operation_count"], 148)
        self.assertEqual(result["github_operation_count"], 26)
        self.assertEqual(
            result["execution_graph_sha256"],
            assembler.EXPECTED_RESOLVED_GRAPH_SHA256,
        )
        self.assertEqual(result["seed_file_sha256"], assembler.EXPECTED_SEED_FILE_SHA256)
        self.assertEqual(
            result["execution_contract_canonical_sha256"],
            assembler.EXPECTED_CONTRACT_CANONICAL_SHA256,
        )
        self.assertEqual(
            result["manifest_sha256"],
            self.contract["evidence"]["manifest_canonical_sha256"],
        )
        encoded_ceiling = (self.contract["limits"]["max_stdout_bytes"] + 2) // 3 * 4 + 4096
        self.assertLess(encoded_ceiling, assembler.MAX_INPUT_BYTES)
        self.assertEqual(stderr.getvalue(), "")


class GateBRawCaptureTests(SimpleTestCase):
    def setUp(self) -> None:
        (ROOT / ".tmp").mkdir(mode=0o700, exist_ok=True)
        (ROOT / ".tmp").chmod(0o700)
        self.temporary = Path(tempfile.mkdtemp(dir=ROOT / ".tmp"))
        self.temporary.chmod(0o700)
        self.tmp_root = self.temporary / "private-root"
        self.tmp_root.mkdir(mode=0o700)
        self.capture_dir = self.tmp_root / f"gate-b-{CAPTURE_ID}"
        self.capture_dir.mkdir(mode=0o700)
        self.raw_dir = self.capture_dir / "raw"
        self.raw_dir.mkdir(mode=0o700)

    def tearDown(self) -> None:
        shutil.rmtree(self.temporary)

    def test_raw_triplet_is_private_complete_and_graph_bound(self) -> None:
        contract = load_contract()
        spec = contract["graph"]["operations"][0]
        graph_hash = "a" * 64
        response = {
            "Account": "817685572750",
            "Arn": (
                "arn:aws:sts::817685572750:assumed-role/phone-aws-sandbox-role/"
                "phone-sandbox-01234567"
            ),
            "UserId": "AROA34YO3VSHI2OCVBKTW:phone-sandbox-01234567",
        }
        status_doc = {
            "schema_version": 1,
            "capture_id": CAPTURE_ID,
            "command_id": spec["id"],
            "sequence": spec["sequence"],
            "phase": spec["phase"],
            "provider": spec["provider"],
            "argv_sha256": assembler._canonical_sha256(spec["argv"]),
            "graph_sha256": graph_hash,
            "started_at": CAPTURE_STARTED.isoformat().replace("+00:00", "Z"),
            "finished_at": (CAPTURE_STARTED + timedelta(seconds=1))
            .isoformat()
            .replace("+00:00", "Z"),
            "exit_code": 0,
            "response_sha256": hashlib.sha256(evidence.canonical_json_bytes(response)).hexdigest(),
            "error_sha256": hashlib.sha256(b"").hexdigest(),
        }
        for suffix, value in (
            (
                "response",
                {
                    "stdout_base64": base64.b64encode(
                        evidence.canonical_json_bytes(response)
                    ).decode("ascii")
                },
            ),
            ("error", {"stderr_base64": ""}),
            ("status", status_doc),
        ):
            operator._write_private_json(self.raw_dir / f"{spec['id']}.{suffix}.json", value)
        with mock.patch.object(assembler, "TMP_ROOT", self.tmp_root):
            loaded = assembler.load_raw_capture_set(
                self.capture_dir, [spec], expected_graph_sha256=graph_hash
            )
        self.assertEqual(loaded["sts-caller"]["response"], response)
        for path in self.raw_dir.iterdir():
            info = path.stat()
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
            self.assertEqual(info.st_uid, os.geteuid())
            self.assertEqual(info.st_nlink, 1)

        status_path = self.raw_dir / f"{spec['id']}.status.json"
        invalid_version = copy.deepcopy(status_doc)
        invalid_version["schema_version"] = True
        status_path.write_bytes(evidence.canonical_json_bytes(invalid_version) + b"\n")
        with (
            mock.patch.object(assembler, "TMP_ROOT", self.tmp_root),
            self.assertRaisesRegex(assembler.AssemblyError, "raw-status-mismatch"),
        ):
            assembler.load_raw_capture_set(
                self.capture_dir, [spec], expected_graph_sha256=graph_hash
            )

        stale = copy.deepcopy(status_doc)
        stale["started_at"] = "2025-08-08T12:00:00Z"
        stale["finished_at"] = "2025-08-08T12:00:01Z"
        status_path.write_bytes(evidence.canonical_json_bytes(stale) + b"\n")
        with (
            mock.patch.object(assembler, "TMP_ROOT", self.tmp_root),
            self.assertRaisesRegex(assembler.AssemblyError, "invalid-raw-status-time"),
        ):
            assembler.load_raw_capture_set(
                self.capture_dir, [spec], expected_graph_sha256=graph_hash
            )

    def test_extra_file_wrong_mode_and_hardlink_stop(self) -> None:
        extra = self.raw_dir / "extra"
        extra.write_text("{}")
        extra.chmod(0o600)
        with (
            mock.patch.object(assembler, "TMP_ROOT", self.tmp_root),
            self.assertRaises(assembler.AssemblyError),
        ):
            assembler.load_raw_capture_set(self.capture_dir, [])

        extra.unlink()
        source = self.raw_dir / "source.response.json"
        source.write_text("{}")
        source.chmod(0o644)
        with self.assertRaisesRegex(assembler.AssemblyError, "unsafe-raw-capture"):
            assembler._read_private_json(source)
        source.chmod(0o600)
        link = self.raw_dir / "linked.response.json"
        os.link(source, link)
        with self.assertRaisesRegex(assembler.AssemblyError, "unsafe-raw-capture"):
            assembler._read_private_json(source)

        lexical_alias = self.capture_dir / ".." / self.capture_dir.name
        with (
            mock.patch.object(assembler, "TMP_ROOT", self.tmp_root),
            self.assertRaisesRegex(assembler.AssemblyError, "capture-path-outside-tmp"),
        ):
            assembler._safe_capture_directory(lexical_alias)

        outside = self.temporary / "outside"
        outside.mkdir(mode=0o700)
        moved_raw = self.capture_dir / "raw-original"
        self.raw_dir.rename(moved_raw)
        self.raw_dir.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(operator.OperatorError, "unsafe-private-directory"):
            operator._write_private_json(self.raw_dir / "redirected.json", {})
        self.assertFalse((outside / "redirected.json").exists())

    def test_intermediate_capture_symlink_cannot_redirect_private_write(self) -> None:
        outside = self.temporary / "outside"
        outside.mkdir(mode=0o700)
        escaped_capture = outside / self.capture_dir.name
        escaped_capture.mkdir(mode=0o700)
        escaped_raw = escaped_capture / "raw"
        escaped_raw.mkdir(mode=0o700)
        original_capture = self.tmp_root / "original-capture"
        self.capture_dir.rename(original_capture)
        self.capture_dir.symlink_to(escaped_capture, target_is_directory=True)

        with self.assertRaisesRegex(operator.OperatorError, "unsafe-private-directory"):
            operator._write_private_json(self.capture_dir / "raw" / "redirected.json", {})
        self.assertFalse((escaped_raw / "redirected.json").exists())

    def test_stdout_stderr_and_private_json_size_boundaries_are_exact(self) -> None:
        self.assertEqual(
            operator._parse_success_stdout(b" " * assembler.MAX_STDOUT_BYTES, 2 * 1024 * 1024),
            {},
        )
        with self.assertRaisesRegex(operator.OperatorError, "provider-output-too-large"):
            operator._parse_success_stdout(b" " * (assembler.MAX_STDOUT_BYTES + 1), 2 * 1024 * 1024)

        contract = load_contract()
        spec = next(
            item for item in contract["graph"]["operations"] if item["id"] == "s3-lock-object"
        )
        prefix = b"An error occurred (404) when calling the HeadObject operation: "
        exact_error = prefix + b"x" * (assembler.MAX_STDERR_BYTES - len(prefix))
        self.assertEqual(
            operator._parse_expected_aws_error(exact_error, spec, assembler.MAX_STDERR_BYTES),
            "404",
        )
        private_error = prefix + " ".join(REDACTION_CANARIES).encode()
        safe_code = operator._parse_expected_aws_error(
            private_error, spec, assembler.MAX_STDERR_BYTES
        )
        self.assertEqual(safe_code, "404")
        self.assertTrue(all(canary not in safe_code for canary in REDACTION_CANARIES))
        with self.assertRaises(operator.OperatorError) as failure:
            operator._parse_success_stdout(
                ("not-json " + " ".join(REDACTION_CANARIES)).encode(),
                assembler.MAX_STDOUT_BYTES,
            )
        self.assertTrue(all(canary not in str(failure.exception) for canary in REDACTION_CANARIES))
        with self.assertRaisesRegex(operator.OperatorError, "provider-error-too-large"):
            operator._parse_expected_aws_error(exact_error + b"x", spec, assembler.MAX_STDERR_BYTES)

        boundary = self.raw_dir / "boundary.json"
        prefix_json = b'{"value":1}'
        boundary.write_bytes(prefix_json + b" " * (assembler.MAX_INPUT_BYTES - len(prefix_json)))
        boundary.chmod(0o600)
        self.assertEqual(assembler._read_private_json(boundary), {"value": 1})
        boundary.write_bytes(
            prefix_json + b" " * (assembler.MAX_INPUT_BYTES + 1 - len(prefix_json))
        )
        with self.assertRaisesRegex(assembler.AssemblyError, "unsafe-raw-capture"):
            assembler._read_private_json(boundary)

    def test_all_174_commands_use_one_frozen_session_and_offline_inventory(self) -> None:
        contract = load_contract()
        seed = load_seed()
        manifest = load_manifest()
        bindings = operator._provisional_bindings(seed, manifest, CAPTURE_ID)
        specs = assembler.complete_operation_specs(contract, manifest, bindings)
        graph_hash = assembler.execution_graph_sha256(specs)
        by_argv = {tuple(item["argv"]): item for item in specs}
        calls: list[tuple[int, str]] = []
        lock = threading.Lock()

        def runner(
            argv: Sequence[str],
            *,
            cwd: Path,
            env: dict[str, str],
            timeout: int,
        ) -> subprocess.CompletedProcess[bytes]:
            del cwd, timeout
            spec = by_argv[tuple(argv)]
            with lock:
                calls.append(
                    (
                        spec["sequence"],
                        env["AWS_SESSION_TOKEN"] if spec["provider"] == "aws" else "github",
                    )
                )
            if spec["expected"]["exit_code"] == 0:
                return subprocess.CompletedProcess(argv, 0, stdout=b"{}", stderr=b"")
            message = (
                f"An error occurred ({spec['expected']['error_code']}) when calling the "
                f"{spec['operation']} operation: accepted absence\n"
            ).encode()
            return subprocess.CompletedProcess(argv, 255, stdout=b"", stderr=message)

        frozen = credentials()
        operator.capture_one(
            specs[0],
            contract,
            frozen,
            CAPTURE_ID,
            self.raw_dir,
            graph_hash,
            runner=runner,
        )
        operator._capture_bounded_phase(
            specs[1:84],
            contract,
            frozen,
            CAPTURE_ID,
            self.raw_dir,
            graph_hash,
            runner=runner,
        )
        operator._capture_bounded_phase(
            specs[84:],
            contract,
            frozen,
            CAPTURE_ID,
            self.raw_dir,
            graph_hash,
            runner=runner,
        )
        with mock.patch.object(assembler, "TMP_ROOT", self.tmp_root):
            loaded = assembler.load_raw_capture_set(self.capture_dir, specs)

        self.assertEqual(calls[0][0], 1)
        self.assertEqual(len(calls), 174)
        self.assertEqual({token for _, token in calls if token != "github"}, {frozen.session_token})
        self.assertEqual(len(loaded), 174)
        self.assertEqual(len(list(self.raw_dir.iterdir())), 522)

    def test_full_synthetic_capture_and_standalone_assembly_produce_exact_outputs(self) -> None:
        contract = load_contract()
        seed = load_seed()
        manifest = load_manifest()
        private_root = self.temporary / "operator-root"
        private_root.mkdir(mode=0o700)
        capture_started = datetime.now(UTC).replace(microsecond=0)
        capture_id = f"{capture_started:%Y%m%dT%H%M%SZ}-abcdefabcdef"
        sts = {
            "Account": "817685572750",
            "Arn": (
                "arn:aws:sts::817685572750:assumed-role/phone-aws-sandbox-role/"
                "phone-sandbox-01234567"
            ),
            "UserId": "AROA34YO3VSHI2OCVBKTW:phone-sandbox-01234567",
        }
        bindings = assembler.build_bindings_envelope(seed, manifest, capture_id, sts)
        responses = accepted_provider_responses(seed, manifest, bindings)
        specs = assembler.complete_operation_specs(contract, manifest, bindings)
        by_argv = {tuple(spec["argv"]): spec for spec in specs}
        calls: list[str] = []

        def runner(
            argv: Sequence[str],
            *,
            cwd: Path,
            env: dict[str, str],
            timeout: int,
        ) -> subprocess.CompletedProcess[bytes]:
            del cwd, env, timeout
            spec = by_argv[tuple(argv)]
            calls.append(spec["id"])
            if spec["expected"]["exit_code"] == 0:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    stdout=evidence.canonical_json_bytes(responses[spec["id"]]),
                    stderr=b"",
                )
            stderr = (
                f"An error occurred ({spec['expected']['error_code']}) when calling the "
                f"{spec['operation']} operation: {REDACTION_CANARIES[4]}\n"
            ).encode()
            return subprocess.CompletedProcess(argv, 255, stdout=b"", stderr=stderr)

        with (
            mock.patch.object(operator, "TMP_ROOT", private_root),
            mock.patch.object(assembler, "TMP_ROOT", private_root),
            mock.patch.object(
                operator, "load_frozen_credentials", return_value=credentials()
            ) as vending,
        ):
            outcome = operator.run_gate_b(
                capture_id,
                seed,
                contract,
                manifest,
                runner=runner,
                now=capture_started,
            )
            capture_dir = private_root / f"gate-b-{capture_id}"
            raw = assembler.load_raw_capture_set(capture_dir, specs)
            documents = assembler.validate_complete_chain(seed, contract, manifest, raw, bindings)
            assembler.validate_sealed_binding_outputs(capture_dir, documents)

            self.assertEqual(outcome["status"], "PASS")
            self.assertEqual(len(calls), 174)
            self.assertEqual(calls[0], "sts-caller")
            self.assertEqual(len(set(calls)), 174)
            vending.assert_called_once()
            self.assertEqual(
                outcome["attestation_sha256"],
                assembler._canonical_sha256(documents["execution-attestation"]),
            )
            attestation = documents["execution-attestation"]
            self.assertEqual(
                attestation["accepted_execution_binding"],
                contract["accepted_execution_binding"],
            )
            self.assertEqual(attestation["source_binding"], contract["source_binding"])
            self.assertEqual(
                attestation["operator_parent_role_arn"],
                seed["operator_parent"]["role_arn"],
            )
            self.assertEqual(
                attestation["operator_identity"], bindings["payload"]["operator_identity"]
            )
            self.assertEqual(attestation["provider_operation_count"], 174)
            self.assertEqual(attestation["aws_operation_count"], 148)
            self.assertEqual(attestation["github_operation_count"], 26)
            self.assertEqual(
                attestation["execution_graph_sha256"],
                assembler.EXPECTED_RESOLVED_GRAPH_SHA256,
            )
            self.assertEqual(
                attestation["raw_capture_sha256"],
                assembler.raw_capture_sha256(raw, specs),
            )
            self.assertEqual(
                attestation["sts_triplet_sha256"],
                {
                    "stdout": raw["sts-caller"]["stdout_sha256"],
                    "stderr": raw["sts-caller"]["stderr_sha256"],
                    "status": assembler._canonical_sha256(raw["sts-caller"]["status"]),
                },
            )
            for filename in contract["outputs"]:
                key = filename.removesuffix(".json")
                self.assertEqual(
                    evidence.parse_json((capture_dir / filename).read_text()),
                    documents[key],
                )
                self.assertEqual(stat.S_IMODE((capture_dir / filename).stat().st_mode), 0o600)
            public_outputs = b"".join(
                (capture_dir / filename).read_bytes() for filename in contract["outputs"]
            ).decode("utf-8")
            self.assertTrue(all(canary not in public_outputs for canary in REDACTION_CANARIES))

            for filename in contract["outputs"]:
                if filename not in {"bindings.json", "bindings.result.json"}:
                    (capture_dir / filename).unlink()
            stdout = mock.Mock()
            stdout.buffer = io.BytesIO()
            with mock.patch.object(assembler.sys, "stdout", stdout):
                code = assembler.main(["assemble", "--capture-dir", str(capture_dir)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.buffer.getvalue())["status"], "PASS")
            self.assertEqual(
                {path.name for path in capture_dir.iterdir() if path.is_file()},
                set(contract["outputs"]),
            )

            binding_result_path = capture_dir / "bindings.result.json"
            changed_result = copy.deepcopy(documents["bindings.result"])
            changed_result["status"] = "STOP"
            binding_result_path.write_bytes(evidence.canonical_json_bytes(changed_result) + b"\n")
            with self.assertRaisesRegex(assembler.AssemblyError, "sealed-binding-output-mismatch"):
                assembler.validate_sealed_binding_outputs(capture_dir, documents)

    def test_failed_completed_batch_does_not_submit_more_commands(self) -> None:
        contract = copy.deepcopy(load_contract())
        contract["limits"]["max_concurrency"] = 2
        specs = contract["graph"]["operations"][:3]
        started: list[str] = []

        def capture(*args: Any, **kwargs: Any) -> mock.Mock:
            del kwargs
            spec = args[0]
            started.append(spec["id"])
            if spec["id"] == specs[1]["id"]:
                raise operator.OperatorError("expected-test-stop")
            return mock.Mock()

        def complete_batch(
            futures: Sequence[Any], *, return_when: str
        ) -> tuple[set[Any], set[Any]]:
            del return_when
            for future in futures:
                try:
                    future.result()
                except operator.OperatorError:
                    pass
            return set(futures), set()

        with (
            mock.patch.object(operator, "capture_one", side_effect=capture),
            mock.patch.object(operator, "wait", side_effect=complete_batch),
            self.assertRaisesRegex(operator.OperatorError, "expected-test-stop"),
        ):
            operator._capture_bounded_phase(
                specs,
                contract,
                credentials(),
                CAPTURE_ID,
                self.raw_dir,
                "0" * 64,
                runner=mock.Mock(),
            )

        self.assertCountEqual(started, [specs[0]["id"], specs[1]["id"]])

    def test_offline_error_parser_rejects_near_misses(self) -> None:
        contract = load_contract()
        spec = next(
            item for item in contract["graph"]["operations"] if item["id"] == "s3-lock-object"
        )
        for message in (
            b"An error occurred (403) when calling the HeadObject operation: denied\n",
            b"An error occurred (NoSuchKey) when calling the HeadObject operation: missing\n",
            b"prefix An error occurred (404) when calling the HeadObject operation: missing\n",
            b"An error occurred (404) when calling the HeadObject operation: missing\nextra\n",
        ):
            with self.subTest(message=message), self.assertRaises(assembler.AssemblyError):
                assembler._parse_anchored_error(message, spec, assembler.MAX_STDERR_BYTES)
