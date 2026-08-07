from __future__ import annotations

import ast
import copy
import hashlib
import io
import json
import stat
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import quote

from django.test import SimpleTestCase

from deploy import gate_b_evidence as evidence

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "deploy/gate_b_manifest.json"
CAPTURE_ID = "20260807T220000Z-012345abcdef"
WEBSITE_SHA = "07186fc9bf9cf353fa12b74e97018d7f951d0fe6"
INFRA_SHA = "95d93f7e07ded19e482a0c6d6471fbd93fb608d8"
KMS_ARN = "arn:aws:kms:eu-west-1:817685572750:key/b9181223-d870-4bae-92d2-fc28b7813887"


def load_manifest() -> dict[str, Any]:
    return evidence.parse_json(MANIFEST_PATH.read_text())


def envelope(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "capture_id": CAPTURE_ID,
        "website_sha": WEBSITE_SHA,
        "infra_sha": INFRA_SHA,
        "kind": kind,
        "payload": payload,
        "payload_sha256": hashlib.sha256(evidence.canonical_json_bytes(payload)).hexdigest(),
    }


def refresh_envelope(document: dict[str, Any]) -> dict[str, Any]:
    document["payload_sha256"] = hashlib.sha256(
        evidence.canonical_json_bytes(document["payload"])
    ).hexdigest()
    return document


def binding_document(manifest: dict[str, Any]) -> dict[str, Any]:
    domain = "d111111abcdef8.cloudfront.net"
    repository_variables = {
        name: "synthetic" for name in manifest["static"]["repository_variable_names"]
    }
    repository_variables.update(
        {
            "SANDBOX_AUTO_DEPLOY": "false",
            "SANDBOX_AWS_REGION": "eu-west-1",
            "SANDBOX_ECR_REPOSITORY_NAME": "website-sandbox",
            "SANDBOX_ECR_REPOSITORY_URI": (
                "817685572750.dkr.ecr.eu-west-1.amazonaws.com/website-sandbox"
            ),
            "SANDBOX_KMS_KEY_ARN": KMS_ARN,
            "SANDBOX_PUBLISHER_ROLE_ARN": (
                "arn:aws:iam::817685572750:role/website-sandbox-github-publisher"
            ),
            "SANDBOX_ROUTE53_HOSTED_ZONE_ID": "Z05963572WVWFHDQZH5NE",
        }
    )
    target_group_arn = (
        "arn:aws:elasticloadbalancing:eu-west-1:817685572750:"
        "targetgroup/website-sandbox-web/0123456789abcdef"
    )
    subnet_ids = ["subnet-0123456789abcdef0", "subnet-0fedcba9876543210"]
    security_group_ids = ["sg-0123456789abcdef0"]
    environment_variables = {
        "SANDBOX_DEPLOYER_ROLE_ARN": (
            "arn:aws:iam::817685572750:role/website-sandbox-github-deployer"
        ),
        "SANDBOX_ECS_ASSIGN_PUBLIC_IP": "true",
        "SANDBOX_ECS_CLUSTER_ARN": ("arn:aws:ecs:eu-west-1:817685572750:cluster/website-sandbox"),
        "SANDBOX_ECS_CONTAINER_NAMES": ('{"migration":"migration","web":"web","worker":"worker"}'),
        "SANDBOX_ECS_EXECUTION_ROLE_ARN": (
            "arn:aws:iam::817685572750:role/website-sandbox-task-execution"
        ),
        "SANDBOX_ECS_MIGRATION_TASK_FAMILY": "website-sandbox-migration",
        "SANDBOX_ECS_SECURITY_GROUP_IDS": json.dumps(security_group_ids, separators=(",", ":")),
        "SANDBOX_ECS_SUBNET_IDS": json.dumps(subnet_ids, separators=(",", ":")),
        "SANDBOX_ECS_TASK_ROLE_ARN": (
            "arn:aws:iam::817685572750:role/website-sandbox-task-application"
        ),
        "SANDBOX_ECS_WEB_SERVICE_NAME": "website-sandbox-web",
        "SANDBOX_ECS_WEB_TASK_FAMILY": "website-sandbox-web",
        "SANDBOX_ECS_WORKER_SERVICE_NAME": "website-sandbox-worker",
        "SANDBOX_ECS_WORKER_TASK_FAMILY": "website-sandbox-worker",
        "SANDBOX_RESOURCE_ENVIRONMENT_TAG": "sandbox",
        "SANDBOX_RESOURCE_PROJECT_TAG": "website",
        "SANDBOX_WEB_RELEASE_DESIRED_COUNT": "1",
        "SANDBOX_WEB_TARGET_GROUP_ARN": target_group_arn,
        "SANDBOX_WORKER_RELEASE_DESIRED_COUNT": "1",
    }
    return envelope(
        "bindings",
        {
            "cloudfront": {
                "distribution_id": "E1234567890ABC",
                "distribution_arn": (
                    "arn:aws:cloudfront::817685572750:distribution/E1234567890ABC"
                ),
                "domain_name": domain,
            },
            "target_group": {
                "arn": target_group_arn,
                "suffix": "0123456789abcdef",
            },
            "task_definitions": {
                "web": ("arn:aws:ecs:eu-west-1:817685572750:task-definition/website-sandbox-web:1"),
                "worker": (
                    "arn:aws:ecs:eu-west-1:817685572750:task-definition/website-sandbox-worker:1"
                ),
                "migration": (
                    "arn:aws:ecs:eu-west-1:817685572750:task-definition/website-sandbox-migration:1"
                ),
            },
            "secret_arns": {
                key: (f"arn:aws:secretsmanager:eu-west-1:817685572750:secret:{name}-a1B2c3")
                for key, name in manifest["static"]["secret_names"].items()
            },
            "network": {
                "alb_dns_name": "website-sandbox-alb.eu-west-1.elb.amazonaws.com",
                "alb_zone_id": "Z2IFOLAFXWLO4F",
                "security_group_ids": security_group_ids,
                "subnet_ids": subnet_ids,
                "vpc_id": "vpc-0123456789abcdef0",
            },
            "dns_records": [
                {"name": "web.dtcdev.click.", "type": "A", "value": f"{domain}."},
                {"name": "web.dtcdev.click.", "type": "AAAA", "value": f"{domain}."},
                {
                    "name": "origin.web.dtcdev.click.",
                    "type": "A",
                    "value": "website-sandbox-alb.eu-west-1.elb.amazonaws.com.",
                },
                {
                    "name": "origin.web.dtcdev.click.",
                    "type": "AAAA",
                    "value": "website-sandbox-alb.eu-west-1.elb.amazonaws.com.",
                },
                {
                    "name": "_cert-one.web.dtcdev.click.",
                    "type": "CNAME",
                    "value": "_validation-one.acm-validations.aws.",
                },
                {
                    "name": "_cert-two.origin.web.dtcdev.click.",
                    "type": "CNAME",
                    "value": "_validation-two.acm-validations.aws.",
                },
            ],
            "operator_identity": {
                "account_id": "817685572750",
                "arn": "arn:aws:iam::817685572750:user/gate-b-operator",
                "user_id": "AIDAEXAMPLEGATEB",
            },
            "github_repository_variables": repository_variables,
            "github_environment_variables": environment_variables,
        },
    )


def policy_document(manifest: dict[str, Any]) -> dict[str, Any]:
    roles = manifest["static"]["roles"]
    fixtures = manifest["policy_fixtures"]
    return envelope(
        "policies",
        {
            "roles": {
                role_class: {
                    "name": role["name"],
                    "arn": role["arn"],
                    "path": role["path"],
                    "max_session_duration": role["max_session_duration"],
                    "trust_policy": copy.deepcopy(fixtures[f"{role_class}-trust"]),
                    "inline_policies": {
                        role["name"]: copy.deepcopy(fixtures[f"{role_class}-inline"])
                    },
                    "attached_policies": [],
                    "permissions_boundary": None,
                }
                for role_class, role in roles.items()
            },
            "kms": {
                "arn": KMS_ARN,
                "key_id": "b9181223-d870-4bae-92d2-fc28b7813887",
                "alias_name": "alias/website-sandbox-runtime",
                "alias_target_key_id": "b9181223-d870-4bae-92d2-fc28b7813887",
                "enabled": True,
                "key_state": "Enabled",
                "key_manager": "CUSTOMER",
                "origin": "AWS_KMS",
                "key_usage": "ENCRYPT_DECRYPT",
                "spec": "SYMMETRIC_DEFAULT",
                "multi_region": False,
                "rotation_enabled": True,
                "policy_name": "default",
                "key_policy": copy.deepcopy(fixtures["kms-key-policy"]),
                "grant_inventory": [
                    {
                        "GrantId": "service-grant-1",
                        "GranteePrincipal": (
                            "arn:aws:iam::817685572750:role/aws-service-role/"
                            "logs.amazonaws.com/AWSServiceRoleForLogs"
                        ),
                        "Operations": ["Decrypt", "Encrypt"],
                    },
                    {
                        "GrantId": "service-grant-2",
                        "GranteePrincipal": (
                            "arn:aws:iam::817685572750:role/aws-service-role/"
                            "rds.amazonaws.com/AWSServiceRoleForRDS"
                        ),
                        "Operations": ["Decrypt"],
                    },
                ],
                "grant_inventory_truncated": False,
            },
        },
    )


def resource_document(manifest: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any]:
    binding_payload = bindings["payload"]
    secret_arns = binding_payload["secret_arns"]
    secret_names = manifest["static"]["secret_names"]
    task_definitions = binding_payload["task_definitions"]
    return envelope(
        "resources",
        {
            "s3": {
                "bucket": "datamailer-sandbox-817685572750-us-east-1-tfstate",
                "bucket_location": "us-east-1",
                "key": "sandbox/website/terraform.tfstate",
                "expected_bucket_owner": "817685572750",
                "object_exists": True,
                "ownership": "BucketOwnerEnforced",
                "encryption_algorithm": "AES256",
                "versioning": "Enabled",
                "public_access_block": {
                    "BlockPublicAcls": True,
                    "BlockPublicPolicy": True,
                    "IgnorePublicAcls": True,
                    "RestrictPublicBuckets": True,
                },
                "bucket_policy_error": "NoSuchBucketPolicy",
            },
            "ecr": {
                "name": "website-sandbox",
                "arn": "arn:aws:ecr:eu-west-1:817685572750:repository/website-sandbox",
                "registry_id": "817685572750",
                "image_tag_mutability": "IMMUTABLE",
                "scan_on_push": True,
                "kms_key_arn": KMS_ARN,
                "zero_digest_present": False,
                "zero_digest_error": "ImageNotFoundException",
                "repository_policy_error": "RepositoryPolicyNotFoundException",
                "registry_policy_error": "RegistryPolicyNotFoundException",
            },
            "secrets": {
                key: {
                    "arn": secret_arns[key],
                    "name": secret_names[key],
                    "kms_key_id": KMS_ARN,
                    "description": (
                        "Out-of-band value container for website-sandbox "
                        f"{secret_names[key].removeprefix('website-sandbox/').replace('-', ' ')}"
                    ),
                    "deleted_date": None,
                    "owning_service": None,
                    "primary_region": None,
                    "rotation_enabled": False,
                    "version_ids_to_stages": {},
                    "resource_policy_response": {
                        "ARN": secret_arns[key],
                        "Name": secret_names[key],
                    },
                }
                for key in secret_names
            },
            "cloudfront": {
                "distribution_id": binding_payload["cloudfront"]["distribution_id"],
                "arn": binding_payload["cloudfront"]["distribution_arn"],
                "domain_name": binding_payload["cloudfront"]["domain_name"],
                "enabled": True,
                "status": "Deployed",
                "aliases": ["web.dtcdev.click"],
                "route53_web_targets": {
                    "A": binding_payload["cloudfront"]["domain_name"],
                    "AAAA": binding_payload["cloudfront"]["domain_name"],
                },
            },
            "runtime": {
                "ecs_cluster": {
                    "active_services": 2,
                    "arn": ("arn:aws:ecs:eu-west-1:817685572750:cluster/website-sandbox"),
                    "name": "website-sandbox",
                    "pending_tasks": 0,
                    "registered_container_instances": 0,
                    "running_tasks": 0,
                    "status": "ACTIVE",
                },
                "ecs_services": {
                    role: {
                        "arn": (
                            "arn:aws:ecs:eu-west-1:817685572750:service/"
                            f"website-sandbox/website-sandbox-{role}"
                        ),
                        "desired": 0,
                        "name": f"website-sandbox-{role}",
                        "pending": 0,
                        "running": 0,
                        "status": "ACTIVE",
                        "task_definition": task_definitions[role],
                    }
                    for role in ("web", "worker")
                },
                "task_definitions": {
                    role: {
                        "arn": task_definitions[role],
                        "execution_role_arn": (
                            "arn:aws:iam::817685572750:role/website-sandbox-task-execution"
                        ),
                        "family": f"website-sandbox-{role}",
                        "revision": 1,
                        "status": "ACTIVE",
                        "task_role_arn": (
                            "arn:aws:iam::817685572750:role/website-sandbox-task-application"
                        ),
                    }
                    for role in ("web", "worker", "migration")
                },
                "running_tasks": 0,
                "pending_tasks": 0,
                "stopped_tasks": 0,
                "ecr_image_count": 0,
                "target_count": 0,
                "target_group": {
                    "arn": binding_payload["target_group"]["arn"],
                    "health_check_path": "/health/ready",
                    "name": "website-sandbox-web",
                    "port": 8000,
                    "protocol": "HTTP",
                    "target_type": "ip",
                    "vpc_id": binding_payload["network"]["vpc_id"],
                },
                "database": {
                    "identifier": "website-sandbox",
                    "arn": "arn:aws:rds:eu-west-1:817685572750:db:website-sandbox",
                    "status": "available",
                    "encrypted": True,
                    "kms_key_arn": KMS_ARN,
                    "publicly_accessible": False,
                },
            },
            "caller_identity": binding_payload["operator_identity"],
            "dns_records": binding_payload["dns_records"],
            "route53": {
                "record_count": 6,
                "records_sha256": hashlib.sha256(
                    evidence.canonical_json_bytes(binding_payload["dns_records"])
                ).hexdigest(),
                "zone_id": "Z05963572WVWFHDQZH5NE",
            },
            "github": {
                "repository_variables": binding_payload["github_repository_variables"],
                "environment_variables": binding_payload["github_environment_variables"],
                "branch_policy": ["main"],
            },
            "terraform": {
                "address_count": 98,
                "locked": False,
                "state_metadata_sha256": "0" * 64,
            },
        },
    )


def simulator_document(manifest: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any]:
    rows = evidence._resolved_simulator_rows(manifest, bindings["payload"])
    return envelope(
        "simulator",
        {
            "results": [
                {
                    "row_id": row["id"],
                    "request": {
                        "policy_source_arn": manifest["static"]["roles"][row["principal"]]["arn"],
                        "action_names": [row["action"]],
                        "resource_arns": [row["resource"]],
                        "context_entries": evidence._simulator_context_entries(row["context"]),
                    },
                    "is_truncated": False,
                    "EvaluationResults": [
                        {
                            "EvalActionName": row["action"],
                            "EvalResourceName": row["resource"],
                            "EvalDecision": row["expected"],
                            "MissingContextValues": [],
                        }
                    ],
                }
                for row in rows
            ]
        },
    )


class GateBEvidenceManifestTests(SimpleTestCase):
    def setUp(self) -> None:
        self.manifest = load_manifest()

    def test_manifest_is_self_validating_and_source_bound(self) -> None:
        result = evidence.validate_manifest(self.manifest)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["source_binding"]["website_sha"], WEBSITE_SHA)
        self.assertEqual(result["source_binding"]["infra_sha"], INFRA_SHA)
        self.assertEqual(len(self.manifest["static"]["secret_names"]), 6)
        self.assertEqual(
            self.manifest["static"]["kms"]["alias_name"],
            "alias/website-sandbox-runtime",
        )

        changed = copy.deepcopy(self.manifest)
        changed["static"]["roles"]["publisher"]["arn"] = "arn:aws:iam::000000000000:role/admin"
        with self.assertRaisesRegex(evidence.EvidenceError, "manifest-digest-mismatch"):
            evidence.validate_manifest(changed)

    def test_manifest_freezes_complete_ecr_and_pass_role_controls(self) -> None:
        rows = self.manifest["simulator_rows"]
        publisher_ecr_allowed = {
            row["action"]
            for row in rows
            if row["principal"] == "publisher"
            and row["expected"] == "allowed"
            and row["action"].startswith("ecr:")
        }
        self.assertEqual(
            publisher_ecr_allowed,
            {
                "ecr:BatchCheckLayerAvailability",
                "ecr:BatchGetImage",
                "ecr:CompleteLayerUpload",
                "ecr:DescribeImages",
                "ecr:GetAuthorizationToken",
                "ecr:InitiateLayerUpload",
                "ecr:PutImage",
                "ecr:UploadLayerPart",
            },
        )
        deployer_ecr_allowed = {
            row["action"]
            for row in rows
            if row["principal"] == "deployer"
            and row["expected"] == "allowed"
            and row["action"].startswith("ecr:")
        }
        self.assertEqual(deployer_ecr_allowed, {"ecr:BatchGetImage", "ecr:DescribeImages"})
        pass_role_resources = {
            row["resource"]
            for row in rows
            if row["principal"] == "deployer"
            and row["action"] == "iam:PassRole"
            and row["expected"] == "allowed"
        }
        self.assertEqual(
            pass_role_resources,
            {
                self.manifest["static"]["ecs"]["task_role_arn"],
                self.manifest["static"]["ecs"]["execution_role_arn"],
            },
        )
        self.assertEqual(len(rows), 90)
        row_ids = {row["id"] for row in rows}
        for parent, axes in {
            "deployer-update-web": ("resource", "cluster", "family"),
            "deployer-update-worker": ("resource", "cluster", "family"),
            "deployer-run-migration": ("resource", "cluster"),
            "deployer-pass-task-role": ("resource", "context"),
            "deployer-pass-execution-role": ("resource", "context"),
        }.items():
            for axis in axes:
                for shape in ("foreign", "production"):
                    self.assertIn(f"{parent}-{axis}-{shape}", row_ids)
        for action_fragment in (
            "batch-check",
            "batch-get",
            "complete-upload",
            "describe",
            "initiate-upload",
            "put-image",
            "upload-part",
        ):
            self.assertIn(f"publisher-ecr-{action_fragment}-foreign", row_ids)
            self.assertIn(f"publisher-ecr-{action_fragment}-production", row_ids)
        for service in ("web", "worker"):
            self.assertIn(f"deployer-describe-{service}-foreign", row_ids)
            self.assertIn(f"deployer-describe-{service}-production", row_ids)

        for mutation in ("missing", "extra"):
            changed = copy.deepcopy(self.manifest)
            if mutation == "missing":
                changed["simulator_rows"].pop()
            else:
                duplicate = copy.deepcopy(changed["simulator_rows"][-1])
                duplicate["id"] = "unexpected-extra-row"
                changed["simulator_rows"].append(duplicate)
            with self.subTest(mutation=mutation), self.assertRaises(evidence.EvidenceError):
                evidence.validate_manifest(changed)

    def test_strict_json_rejects_duplicates_nonfinite_and_bom(self) -> None:
        for text, error in (
            ('{"a":1,"a":2}', "duplicate-json-key"),
            ('{"a":NaN}', "non-finite-json"),
            ('\ufeff{"a":1}', "json-bom"),
        ):
            with self.subTest(text=text), self.assertRaisesRegex(evidence.EvidenceError, error):
                evidence.parse_json(text)

    def test_policy_canonicalization_sorts_only_policy_sets(self) -> None:
        expected: dict[str, Any] = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "B",
                    "Effect": "Allow",
                    "Action": ["x:B", "x:A"],
                    "Resource": ["b", "a"],
                    "Principal": {"AWS": ["b", "a"]},
                    "Condition": {"StringEquals": {"x:key": ["b", "a"]}},
                    "OrderedMetadata": ["first", "second"],
                },
                {"Sid": "A", "Effect": "Deny", "Action": "x:C", "Resource": "*"},
            ],
        }
        reordered = copy.deepcopy(expected)
        reordered["Statement"].reverse()
        reordered["Statement"][1]["Action"].reverse()
        reordered["Statement"][1]["Resource"].reverse()
        reordered["Statement"][1]["Principal"]["AWS"].reverse()
        reordered["Statement"][1]["Condition"]["StringEquals"]["x:key"].reverse()

        self.assertEqual(
            evidence.canonicalize_policy(expected), evidence.canonicalize_policy(reordered)
        )
        reordered["Statement"][1]["OrderedMetadata"].reverse()
        self.assertNotEqual(
            evidence.canonicalize_policy(expected), evidence.canonicalize_policy(reordered)
        )

    def test_policy_canonicalization_rejects_invalid_sids(self) -> None:
        fixture = self.manifest["policy_fixtures"]["publisher-trust"]
        for sid in (None, "", 1):
            changed = copy.deepcopy(fixture)
            if sid is None:
                del changed["Statement"][0]["Sid"]
            else:
                changed["Statement"][0]["Sid"] = sid
            with self.subTest(sid=sid), self.assertRaises(evidence.EvidenceError):
                evidence.canonicalize_policy(changed)
        duplicate = copy.deepcopy(fixture)
        duplicate["Statement"].append(copy.deepcopy(duplicate["Statement"][0]))
        with self.assertRaisesRegex(evidence.EvidenceError, "duplicate-statement-sid"):
            evidence.canonicalize_policy(duplicate)

    def test_policy_scalar_and_singleton_list_are_not_equivalent(self) -> None:
        fixture = self.manifest["policy_fixtures"]["publisher-trust"]
        changed = copy.deepcopy(fixture)
        changed["Statement"][0]["Action"] = [changed["Statement"][0]["Action"]]

        self.assertNotEqual(
            evidence.canonicalize_policy(fixture), evidence.canonicalize_policy(changed)
        )

    def test_rfc3986_and_declared_nested_policy_strings(self) -> None:
        fixture = self.manifest["policy_fixtures"]["publisher-trust"]
        raw = json.dumps(fixture, separators=(",", ":"))
        encoded = quote(raw, safe="")
        nested = json.dumps(raw)

        self.assertEqual(evidence.policy_sha256(fixture), evidence.policy_sha256(encoded))
        self.assertEqual(evidence.policy_sha256(fixture), evidence.policy_sha256(nested))
        for invalid in ("%ZZ", "%FF", json.dumps(json.dumps(raw))):
            with self.subTest(invalid=invalid), self.assertRaises(evidence.EvidenceError):
                evidence.canonicalize_policy(invalid)

    def test_policy_comparator_rejects_every_structural_change(self) -> None:
        fixture = self.manifest["policy_fixtures"]["publisher-inline"]
        mutations = []
        for field in ("Action", "Resource", "Condition"):
            changed = copy.deepcopy(fixture)
            changed["Statement"][0][field] = "changed"
            mutations.append(changed)
        missing = copy.deepcopy(fixture)
        missing["Statement"].pop()
        mutations.append(missing)
        extra = copy.deepcopy(fixture)
        extra["Statement"].append(
            {"Sid": "Unexpected", "Effect": "Allow", "Action": "*", "Resource": "*"}
        )
        mutations.append(extra)

        for changed in mutations:
            with (
                self.subTest(changed=changed),
                self.assertRaisesRegex(evidence.EvidenceError, "policy-mismatch"),
            ):
                evidence.compare_policy("publisher-inline", fixture, changed)


class GateBEvidenceBundleTests(SimpleTestCase):
    def setUp(self) -> None:
        evidence.TMP_ROOT.mkdir(mode=0o700, exist_ok=True)
        evidence.TMP_ROOT.chmod(0o700)
        self.manifest = load_manifest()
        self.bindings = binding_document(self.manifest)
        self.binding_result = evidence.validate_bindings(self.bindings, self.manifest)

    def test_exact_bindings_pass_and_dynamic_mismatches_stop(self) -> None:
        self.assertEqual(self.binding_result["status"], "PASS")
        self.assertEqual(len(self.bindings["payload"]["secret_arns"]), 6)

        mutations = []
        cloudfront = copy.deepcopy(self.bindings)
        cloudfront["payload"]["cloudfront"]["distribution_arn"] += "-wrong"
        mutations.append(cloudfront)
        target = copy.deepcopy(self.bindings)
        target["payload"]["target_group"]["suffix"] = "ffffffffffffffff"
        mutations.append(target)
        task = copy.deepcopy(self.bindings)
        task["payload"]["task_definitions"]["worker"] = task["payload"]["task_definitions"]["web"]
        mutations.append(task)
        secret = copy.deepcopy(self.bindings)
        secret["payload"]["secret_arns"].pop("webhook")
        mutations.append(secret)
        auto = copy.deepcopy(self.bindings)
        auto["payload"]["github_repository_variables"]["SANDBOX_AUTO_DEPLOY"] = "true"
        mutations.append(auto)
        region = copy.deepcopy(self.bindings)
        region["payload"]["github_repository_variables"]["SANDBOX_AWS_REGION"] = "us-east-2"
        mutations.append(region)
        deployer = copy.deepcopy(self.bindings)
        deployer["payload"]["github_environment_variables"]["SANDBOX_DEPLOYER_ROLE_ARN"] = (
            "arn:aws:iam::817685572750:role/admin"
        )
        mutations.append(deployer)
        operator = copy.deepcopy(self.bindings)
        operator["payload"]["operator_identity"]["account_id"] = "000000000000"
        mutations.append(operator)
        for role_name in (
            "website-sandbox-github-publisher",
            "website-sandbox-github-deployer",
            "website-sandbox-task-application",
            "website-sandbox-task-execution",
        ):
            protected_session = copy.deepcopy(self.bindings)
            protected_session["payload"]["operator_identity"]["arn"] = (
                f"arn:aws:sts::817685572750:assumed-role/{role_name}/session"
            )
            mutations.append(protected_session)
        certificate = copy.deepcopy(self.bindings)
        certificate["payload"]["dns_records"][5]["name"] = "_cert-two.web.dtcdev.click."
        mutations.append(certificate)

        for changed in mutations:
            refresh_envelope(changed)
            with self.subTest(changed=changed), self.assertRaises(evidence.EvidenceError):
                evidence.validate_bindings(changed, self.manifest)

    def test_exact_policy_bundle_and_grant_order_are_stable(self) -> None:
        document = policy_document(self.manifest)
        first = evidence.validate_policy_bundle(document, self.manifest, self.binding_result)
        document["payload"]["kms"]["grant_inventory"].reverse()
        document["payload"]["kms"]["grant_inventory"][1]["Operations"].reverse()
        refresh_envelope(document)
        second = evidence.validate_policy_bundle(document, self.manifest, self.binding_result)

        self.assertEqual(first["status"], "PASS")
        self.assertEqual(first["grant_baseline_sha256"], second["grant_baseline_sha256"])
        self.assertEqual(
            set(first["policy_hashes"]),
            {
                "publisher-trust",
                "publisher-inline",
                "deployer-trust",
                "deployer-inline",
                "kms-key-policy",
            },
        )

    def test_application_roles_are_rejected_from_kms_policy_and_grants(self) -> None:
        role_arn = self.manifest["static"]["roles"]["publisher"]["arn"]
        grant = policy_document(self.manifest)
        grant["payload"]["kms"]["grant_inventory"][0]["RetiringPrincipal"] = role_arn
        refresh_envelope(grant)
        with self.assertRaisesRegex(evidence.EvidenceError, "application-role-kms-grant"):
            evidence.validate_policy_bundle(grant, self.manifest, self.binding_result)

        policy = policy_document(self.manifest)
        policy["payload"]["kms"]["key_policy"]["Statement"].append(
            {
                "Sid": "ApplicationRole",
                "Effect": "Allow",
                "Action": "kms:Decrypt",
                "Resource": "*",
                "Principal": {"AWS": role_arn},
            }
        )
        refresh_envelope(policy)
        with self.assertRaisesRegex(evidence.EvidenceError, "policy-mismatch"):
            evidence.validate_policy_bundle(policy, self.manifest, self.binding_result)

    def test_kms_grants_reject_malformed_principals_operations_and_truncation(self) -> None:
        mutations: list[dict[str, Any]] = []
        for field, value in (
            ("GranteePrincipal", ["not-a-string"]),
            ("GranteePrincipal", "123"),
            ("RetiringPrincipal", {"not": "a string"}),
            ("Operations", "Decrypt"),
            ("Operations", ["NotARealGrantOperation"]),
        ):
            document = policy_document(self.manifest)
            document["payload"]["kms"]["grant_inventory"][0][field] = value
            mutations.append(document)
        truncated = policy_document(self.manifest)
        truncated["payload"]["kms"]["grant_inventory_truncated"] = True
        mutations.append(truncated)

        for changed in mutations:
            refresh_envelope(changed)
            with self.subTest(changed=changed), self.assertRaises(evidence.EvidenceError):
                evidence.validate_policy_bundle(changed, self.manifest, self.binding_result)

    def test_role_policy_inventory_and_policy_bodies_fail_closed(self) -> None:
        for mutate in ("attached", "extra-inline", "changed-policy", "permission-boundary"):
            document = policy_document(self.manifest)
            role = document["payload"]["roles"]["deployer"]
            if mutate == "attached":
                role["attached_policies"] = ["arn:aws:iam::aws:policy/AdministratorAccess"]
            elif mutate == "extra-inline":
                role["inline_policies"]["extra"] = {"Version": "2012-10-17", "Statement": []}
            elif mutate == "changed-policy":
                role["inline_policies"][role["name"]]["Statement"][0]["Resource"] = "*"
            else:
                role["permissions_boundary"] = "unexpected"
            refresh_envelope(document)
            with self.subTest(mutate=mutate), self.assertRaises(evidence.EvidenceError):
                evidence.validate_policy_bundle(document, self.manifest, self.binding_result)

    def test_resource_bundle_passes_all_layers(self) -> None:
        result = evidence.validate_resource_bundle(
            resource_document(self.manifest, self.bindings),
            self.manifest,
            self.bindings,
            self.binding_result,
        )

        self.assertEqual(result["status"], "PASS")
        self.assertRegex(result["resource_sha256"], r"^[0-9a-f]{64}$")

    def test_each_resource_policy_layer_fails_closed(self) -> None:
        mutations: list[tuple[str, dict[str, Any]]] = []
        s3 = resource_document(self.manifest, self.bindings)
        s3["payload"]["s3"]["bucket_policy_error"] = "AccessDenied"
        mutations.append(("s3-policy", s3))
        owner = resource_document(self.manifest, self.bindings)
        owner["payload"]["s3"]["ownership"] = "ObjectWriter"
        mutations.append(("s3-ownership", owner))
        repo = resource_document(self.manifest, self.bindings)
        repo["payload"]["ecr"]["repository_policy_error"] = "AccessDenied"
        mutations.append(("ecr-repository", repo))
        registry = resource_document(self.manifest, self.bindings)
        registry["payload"]["ecr"]["registry_policy_error"] = "AccessDenied"
        mutations.append(("ecr-registry", registry))
        secret = resource_document(self.manifest, self.bindings)
        secret["payload"]["secrets"]["database_url"]["resource_policy_response"][
            "ResourcePolicy"
        ] = "{}"
        mutations.append(("secret-policy", secret))
        cloudfront = resource_document(self.manifest, self.bindings)
        cloudfront["payload"]["cloudfront"]["aliases"] = ["wrong.example"]
        mutations.append(("cloudfront", cloudfront))

        for name, changed in mutations:
            refresh_envelope(changed)
            with self.subTest(name=name), self.assertRaises(evidence.EvidenceError):
                evidence.validate_resource_bundle(
                    changed, self.manifest, self.bindings, self.binding_result
                )

    def test_every_resource_identity_group_fails_closed(self) -> None:
        mutations: list[tuple[str, dict[str, Any]]] = []
        paths = (
            ("caller", ("caller_identity", "arn"), "arn:aws:iam::817685572750:user/other"),
            ("s3-location", ("s3", "bucket_location"), "eu-west-1"),
            ("ecr-digest", ("ecr", "zero_digest_error"), "RepositoryNotFoundException"),
            ("secret-metadata", ("secrets", "database_url", "rotation_enabled"), True),
            ("cloudfront-status", ("cloudfront", "status"), "InProgress"),
            ("ecs-cluster", ("runtime", "ecs_cluster", "status"), "INACTIVE"),
            ("target-group", ("runtime", "target_group", "port"), 80),
            ("database", ("runtime", "database", "kms_key_arn"), "wrong"),
            ("route53", ("route53", "record_count"), 5),
        )
        for name, path, value in paths:
            document = resource_document(self.manifest, self.bindings)
            current: dict[str, Any] = document["payload"]
            for key in path[:-1]:
                current = current[key]
            current[path[-1]] = value
            refresh_envelope(document)
            mutations.append((name, document))

        for name, changed in mutations:
            with self.subTest(name=name), self.assertRaises(evidence.EvidenceError):
                evidence.validate_resource_bundle(
                    changed, self.manifest, self.bindings, self.binding_result
                )

    def test_secret_value_fields_are_rejected_without_echoing_canary(self) -> None:
        canary = "never-echo-this-canary"
        document = resource_document(self.manifest, self.bindings)
        document["payload"]["secrets"]["database_url"]["SecretString"] = canary
        refresh_envelope(document)

        with self.assertRaises(evidence.EvidenceError) as caught:
            evidence.validate_resource_bundle(
                document, self.manifest, self.bindings, self.binding_result
            )
        self.assertNotIn(canary, str(caught.exception))

    def test_simulator_requires_atomic_complete_exact_results(self) -> None:
        document = simulator_document(self.manifest, self.bindings)
        result = evidence.validate_simulator_bundle(
            document, self.manifest, self.bindings, self.binding_result
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["row_count"], len(self.manifest["simulator_rows"]))

        mutations = []
        missing = copy.deepcopy(document)
        missing["payload"]["results"].pop()
        mutations.append(missing)
        duplicate = copy.deepcopy(document)
        duplicate["payload"]["results"][0]["EvaluationResults"].append(
            copy.deepcopy(duplicate["payload"]["results"][0]["EvaluationResults"][0])
        )
        mutations.append(duplicate)
        context = copy.deepcopy(document)
        context["payload"]["results"][0]["EvaluationResults"][0]["MissingContextValues"] = [
            "ecs:cluster"
        ]
        mutations.append(context)
        decision = copy.deepcopy(document)
        decision["payload"]["results"][0]["EvaluationResults"][0]["EvalDecision"] = "implicitDeny"
        mutations.append(decision)
        principal = copy.deepcopy(document)
        principal["payload"]["results"][0]["request"]["policy_source_arn"] = (
            "arn:aws:iam::817685572750:role/admin"
        )
        mutations.append(principal)
        truncated = copy.deepcopy(document)
        truncated["payload"]["results"][0]["is_truncated"] = True
        mutations.append(truncated)

        for changed in mutations:
            refresh_envelope(changed)
            with self.subTest(changed=changed), self.assertRaises(evidence.EvidenceError):
                evidence.validate_simulator_bundle(
                    changed, self.manifest, self.bindings, self.binding_result
                )

    def test_one_axis_negatives_cannot_change_resource_and_context(self) -> None:
        changed = copy.deepcopy(self.manifest)
        row = next(
            row
            for row in changed["simulator_rows"]
            if row["id"] == "deployer-update-web-resource-foreign"
        )
        row["context"]["ecs:cluster"] = "arn:aws:ecs:eu-west-1:817685572750:cluster/website-foreign"

        with self.assertRaisesRegex(evidence.EvidenceError, "invalid-one-axis-row"):
            evidence.validate_one_axis_pairs(changed, self.bindings["payload"])

    def test_capture_and_source_mixing_stop(self) -> None:
        document = policy_document(self.manifest)
        document["capture_id"] = "20260807T220001Z-fedcba543210"
        with self.assertRaisesRegex(evidence.EvidenceError, "mixed-capture"):
            evidence.validate_policy_bundle(document, self.manifest, self.binding_result)

        document = policy_document(self.manifest)
        document["infra_sha"] = "f" * 40
        with self.assertRaisesRegex(evidence.EvidenceError, "source-binding-mismatch"):
            evidence.validate_policy_bundle(document, self.manifest, self.binding_result)

    def test_final_summary_contains_only_allowlisted_metadata(self) -> None:
        policies = evidence.validate_policy_bundle(
            policy_document(self.manifest), self.manifest, self.binding_result
        )
        resources = evidence.validate_resource_bundle(
            resource_document(self.manifest, self.bindings),
            self.manifest,
            self.bindings,
            self.binding_result,
        )
        simulator = evidence.validate_simulator_bundle(
            simulator_document(self.manifest, self.bindings),
            self.manifest,
            self.bindings,
            self.binding_result,
        )
        summary = evidence.build_final_summary(
            self.manifest, [self.binding_result, policies, resources, simulator]
        )

        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(
            set(summary),
            {
                "capture_id",
                "caller_identity",
                "contract_id",
                "grant_baseline_sha256",
                "input_sha256",
                "manifest_sha256",
                "schema_version",
                "source_binding",
                "status",
            },
        )
        serialized = json.dumps(summary).lower()
        self.assertEqual(summary["manifest_sha256"], evidence.EXPECTED_MANIFEST_SHA256)
        for forbidden in ("policydocument", "secretstring", "credential", "authorization"):
            self.assertNotIn(forbidden, serialized)

        forged = copy.deepcopy(policies)
        forged["grant_baseline_sha256"] = "not-a-hash"
        with self.assertRaisesRegex(evidence.EvidenceError, "invalid-summary-input"):
            evidence.build_final_summary(
                self.manifest, [self.binding_result, forged, resources, simulator]
            )

    def test_safe_summary_path_and_modes(self) -> None:
        with tempfile.TemporaryDirectory(dir=evidence.TMP_ROOT) as directory:
            root = Path(directory)
            root.chmod(0o700)
            output = root / "summary.json"
            evidence.write_safe_summary(
                str(output), {"status": "PASS", "sha256": hashlib.sha256(b"ok").hexdigest()}
            )
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with self.assertRaisesRegex(evidence.EvidenceError, "unsafe-output-write"):
                evidence.write_safe_summary(str(output), {"status": "PASS"})
            with self.assertRaisesRegex(evidence.EvidenceError, "path-outside-tmp"):
                evidence.write_safe_summary(str(ROOT / "unsafe.json"), {"status": "PASS"})
            with self.assertRaisesRegex(evidence.EvidenceError, "path-outside-tmp"):
                evidence._validate_tmp_parent(root / ".." / ".." / ".ssh" / "evidence.json")

            input_path = root / "input.json"
            input_path.write_text("{}")
            input_path.chmod(0o400)
            with self.assertRaisesRegex(evidence.EvidenceError, "unsafe-input-mode"):
                evidence._read_tmp_json(str(input_path))
            input_path.chmod(0o600)
            symlink = root / "symlink.json"
            symlink.symlink_to(input_path)
            with self.assertRaises(evidence.EvidenceError):
                evidence._read_tmp_json(str(symlink))

    def test_cli_successfully_validates_the_complete_offline_chain(self) -> None:
        documents = {
            "bindings": self.bindings,
            "policies": policy_document(self.manifest),
            "resources": resource_document(self.manifest, self.bindings),
            "simulator": simulator_document(self.manifest, self.bindings),
        }
        with tempfile.TemporaryDirectory(dir=evidence.TMP_ROOT) as directory:
            root = Path(directory)
            root.chmod(0o700)
            paths: dict[str, Path] = {}
            for kind, document in documents.items():
                path = root / f"{kind}.json"
                path.write_bytes(evidence.canonical_json_bytes(document) + b"\n")
                path.chmod(0o600)
                paths[kind] = path
            with redirect_stderr(io.StringIO()), mock.patch.object(evidence, "_print_summary"):
                self.assertEqual(
                    evidence.main(
                        [
                            "bindings",
                            "--input",
                            str(paths["bindings"]),
                            "--output",
                            str(root / "bindings.result.json"),
                        ]
                    ),
                    0,
                )
                for kind in ("policies", "resources", "simulator"):
                    self.assertEqual(
                        evidence.main(
                            [
                                kind,
                                "--bindings",
                                str(paths["bindings"]),
                                "--input",
                                str(paths[kind]),
                                "--output",
                                str(root / f"{kind}.result.json"),
                            ]
                        ),
                        0,
                    )
                summary_args = ["summary"]
                for kind in ("bindings", "policies", "resources", "simulator"):
                    summary_args.extend([f"--{kind}", str(root / f"{kind}.result.json")])
                summary_args.extend(["--output", str(root / "summary.json")])
                self.assertEqual(evidence.main(summary_args), 0)
            for path in root.glob("*.result.json"):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((root / "summary.json").stat().st_mode), 0o600)

    def test_module_has_no_acquisition_capability(self) -> None:
        source = (ROOT / "deploy/gate_b_evidence.py").read_text()
        tree = ast.parse(source)
        imports = {
            alias.name.split(".", maxsplit=1)[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }

        self.assertTrue(
            imports.isdisjoint({"boto3", "botocore", "requests", "socket", "subprocess", "urllib3"})
        )
        self.assertNotIn("urllib.request", source)

    def test_cli_failures_are_redacted_and_nonzero(self) -> None:
        canary = "never-echo-this-canary"
        with (
            mock.patch.object(evidence, "_load_manifest_file", return_value=self.manifest),
            mock.patch.object(evidence, "_read_tmp_json", return_value={"SecretString": canary}),
            mock.patch.object(evidence, "_print_summary") as printer,
            redirect_stderr(io.StringIO()),
        ):
            result = evidence.main(
                ["bindings", "--input", ".tmp/input.json", "--output", ".tmp/output.json"]
            )

        self.assertEqual(result, 2)
        printed = json.dumps(printer.call_args.args[0])
        self.assertNotIn(canary, printed)
        self.assertEqual(printer.call_args.args[0]["status"], "STOP")

        with mock.patch.object(evidence, "_print_summary") as printer:
            result = evidence.main(
                [
                    "bindings",
                    "--input",
                    ".tmp/first.json",
                    "--input",
                    f".tmp/{canary}.json",
                    "--output",
                    ".tmp/output.json",
                ]
            )
        self.assertEqual(result, 2)
        self.assertEqual(printer.call_args.args[0]["error"], "repeated-path-option")
        self.assertNotIn(canary, json.dumps(printer.call_args.args[0]))

        with (
            mock.patch.object(evidence, "_load_manifest_file", side_effect=RuntimeError(canary)),
            mock.patch.object(evidence, "_print_summary") as printer,
        ):
            result = evidence.main(["manifest"])
        self.assertEqual(result, 2)
        self.assertEqual(printer.call_args.args[0]["error"], "internal-validation-error")
        self.assertNotIn(canary, json.dumps(printer.call_args.args[0]))
