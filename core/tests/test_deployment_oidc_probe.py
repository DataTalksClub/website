from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from dataclasses import replace
from typing import Any

from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from django.test import SimpleTestCase

from deploy.contracts import ReleaseContractError
from deploy.oidc_claim_probe import prove_wrong_claim_denied
from deploy.oidc_probe import OidcProbe, ProbeConfig

ACCOUNT_ID = "817685572750"
REGION = "eu-west-1"
REPOSITORY = "website-sandbox"
CLUSTER = f"arn:aws:ecs:{REGION}:{ACCOUNT_ID}:cluster/website-sandbox"
TARGET_GROUP = (
    f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT_ID}:"
    "targetgroup/website-sandbox-web/0123456789abcdef"
)
FAMILIES = tuple(f"website-sandbox-{name}" for name in ("web", "worker", "migration"))


def access_denied(operation: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "redacted"}},
        operation,
    )


def fake_token(audience: str) -> str:
    return f"header.{audience * 10}.signature"


class FakeAwsClient:
    def __init__(self, service: str, role: str) -> None:
        self.service = service
        self.role = role
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error_overrides: dict[str, str] = {}

    def __getattr__(self, method: str):  # type: ignore[no-untyped-def]
        def call(**kwargs):  # type: ignore[no-untyped-def]
            self.calls.append((method, kwargs))
            if method in self.error_overrides:
                raise ClientError(
                    {"Error": {"Code": self.error_overrides[method], "Message": "redacted"}},
                    method,
                )
            if self.service == "sts" and method == "get_caller_identity":
                return {
                    "Account": ACCOUNT_ID,
                    "Arn": (
                        f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/"
                        f"website-sandbox-github-{self.role}/probe"
                    ),
                }
            if self.service == "ecr" and method == "describe_images":
                if kwargs["repositoryName"] != REPOSITORY:
                    raise access_denied(method)
                return {"imageDetails": []}
            if self.service == "ecs" and method == "describe_clusters":
                return {"clusters": [{"clusterArn": CLUSTER}], "failures": []}
            if self.service == "ecs" and method == "describe_services":
                if kwargs["cluster"] != CLUSTER:
                    raise access_denied(method)
                return {"services": [{"serviceName": "web"}, {"serviceName": "worker"}]}
            if self.service == "ecs" and method == "describe_task_definition":
                return {"taskDefinition": {"family": kwargs["taskDefinition"]}}
            if self.service == "ecs" and method == "list_tasks":
                return {"taskArns": []}
            if self.service == "elbv2" and method == "describe_target_health":
                return {"TargetHealthDescriptions": []}
            raise access_denied(method)

        return call


class FakeClaimSts:
    def __init__(self, error_code: str | None = "AccessDenied") -> None:
        self.error_code = error_code
        self.calls: list[dict[str, Any]] = []
        self.response: dict[str, Any] = {"Credentials": {"SecretAccessKey": "must-not-log"}}

    def assume_role_with_web_identity(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        if self.error_code:
            raise ClientError(
                {"Error": {"Code": self.error_code, "Message": "redacted"}},
                "AssumeRoleWithWebIdentity",
            )
        return self.response


def clients(role: str) -> dict[str, FakeAwsClient]:
    return {
        service: FakeAwsClient(service, role)
        for service in (
            "cloudfront",
            "ecr",
            "ecs",
            "elbv2",
            "iam",
            "kms",
            "rds",
            "route53",
            "s3",
            "secretsmanager",
            "sts",
        )
    }


def config(role: str) -> ProbeConfig:
    common: dict[str, Any] = {
        "role": role,
        "account_id": ACCOUNT_ID,
        "region": REGION,
        "repository_name": REPOSITORY,
        "probe_id": "12345",
    }
    if role == "deployer":
        common |= {
            "cluster_arn": CLUSTER,
            "web_target_group_arn": TARGET_GROUP,
            "web_service_name": "website-sandbox-web",
            "worker_service_name": "website-sandbox-worker",
            "task_families": FAMILIES,
        }
    return ProbeConfig(**common)


class OidcProbeTests(SimpleTestCase):
    def run_probe(self, role: str) -> tuple[list[dict[str, str]], dict[str, FakeAwsClient]]:
        fake_clients = clients(role)
        output = io.StringIO()
        with redirect_stdout(output):
            OidcProbe(config(role), fake_clients).run()
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        return records, fake_clients

    def test_publisher_probe_uses_only_metadata_reads_and_safe_denial_requests(self) -> None:
        records, fake_clients = self.run_probe("publisher")

        allowed = [record["action"] for record in records if record["result"] == "allowed"]
        self.assertEqual(allowed, ["sts:GetCallerIdentity", "ecr:DescribeImages"])
        denied = {record["action"] for record in records if record["result"] == "denied"}
        self.assertEqual(
            denied,
            {
                "cloudfront:CreateInvalidation",
                "ecr:BatchDeleteImage",
                "ecr:DescribeImages",
                "ecs:DeregisterTaskDefinition",
                "elasticloadbalancing:ModifyTargetGroupAttributes",
                "iam:UpdateRoleDescription",
                "kms:CreateGrant",
                "rds:ModifyDBInstance",
                "route53:ChangeResourceRecordSets",
                "s3:GetObject",
                "secretsmanager:GetSecretValue",
            },
        )
        expected_fields = {"action", "caller_arn", "resource", "result", "role", "timestamp"}
        self.assertTrue(all(set(record) == expected_fields for record in records))
        role_marker = "assumed-role/website-sandbox-github-publisher/"
        self.assertTrue(all(role_marker in record["caller_arn"] for record in records))

        ecr_delete = fake_clients["ecr"].calls[-1]
        self.assertEqual(ecr_delete[0], "batch_delete_image")
        self.assertEqual(ecr_delete[1]["imageIds"], [{"imageDigest": f"sha256:{'0' * 64}"}])
        secret_probe = fake_clients["secretsmanager"].calls[0]
        self.assertEqual(secret_probe[1]["SecretId"], "website-sandbox/probe-denied-12345")
        self.assertNotIn("VersionStage", secret_probe[1])
        route_probe = fake_clients["route53"].calls[0]
        self.assertEqual(route_probe[1]["HostedZoneId"], "Z00000000000000000000")
        self.assertEqual(route_probe[1]["ChangeBatch"]["Changes"][0]["Action"], "DELETE")
        cloudfront_probe = fake_clients["cloudfront"].calls[0]
        self.assertEqual(cloudfront_probe[1]["DistributionId"], "E0000000000000")
        self.assertEqual(
            cloudfront_probe[1]["InvalidationBatch"]["Paths"],
            {"Quantity": 1, "Items": ["/*"]},
        )
        alb_probe = fake_clients["elbv2"].calls[0]
        self.assertIn("targetgroup/probe-denied-12345/", alb_probe[1]["TargetGroupArn"])
        self.assertEqual(
            alb_probe[1]["Attributes"],
            [{"Key": "deregistration_delay.timeout_seconds", "Value": "300"}],
        )
        rds_probe = fake_clients["rds"].calls[0]
        self.assertEqual(rds_probe[1]["DBInstanceIdentifier"], "website-sandbox-probe-denied-12345")
        self.assertEqual(rds_probe[1]["BackupRetentionPeriod"], 7)
        kms_probe = fake_clients["kms"].calls[0]
        self.assertEqual(kms_probe[1]["KeyId"], "00000000-0000-0000-0000-000000000000")
        self.assertEqual(
            kms_probe[1]["GranteePrincipal"],
            "arn:aws:iam::817685572750:role/website-sandbox-probe-denied-12345",
        )
        ecs_probe = fake_clients["ecs"].calls[0]
        self.assertEqual(ecs_probe[1]["taskDefinition"], "website-sandbox-web:999999999")

    def test_deployer_probe_reads_only_exact_runtime_metadata(self) -> None:
        records, fake_clients = self.run_probe("deployer")

        allowed = [record["action"] for record in records if record["result"] == "allowed"]
        self.assertEqual(
            allowed,
            [
                "sts:GetCallerIdentity",
                "ecr:DescribeImages",
                "ecs:DescribeClusters",
                "ecs:DescribeServices",
                "ecs:DescribeTaskDefinition",
                "ecs:DescribeTaskDefinition",
                "ecs:DescribeTaskDefinition",
                "ecs:ListTasks",
                "elasticloadbalancing:DescribeTargetHealth",
            ],
        )
        service_call = next(
            call for call in fake_clients["ecs"].calls if call[0] == "describe_services"
        )
        self.assertEqual(service_call[1]["cluster"], CLUSTER)
        self.assertEqual(
            service_call[1]["services"],
            ["website-sandbox-web", "website-sandbox-worker"],
        )
        target_call = fake_clients["elbv2"].calls[0]
        self.assertEqual(target_call, ("describe_target_health", {"TargetGroupArn": TARGET_GROUP}))
        foreign_repositories = [
            arguments["repositoryName"]
            for method, arguments in fake_clients["ecr"].calls
            if method == "describe_images" and arguments["repositoryName"] != REPOSITORY
        ]
        self.assertEqual(
            foreign_repositories,
            ["website-sandbox-foreign-probe-12345", "website-production-probe-12345"],
        )
        denied_actions = [record["action"] for record in records if record["result"] == "denied"]
        self.assertEqual(denied_actions.count("ecs:DescribeServices"), 2)
        self.assertEqual(denied_actions.count("ecs:UpdateService"), 2)
        self.assertEqual(denied_actions.count("ecs:RunTask"), 2)

    def test_probe_fails_closed_when_denial_is_not_an_access_denial(self) -> None:
        fake_clients = clients("publisher")
        fake_clients["s3"].error_overrides["head_object"] = "NoSuchKey"

        with self.assertRaisesMessage(
            ReleaseContractError,
            "permission denial was not proven for s3:GetObject (NoSuchKey)",
        ):
            OidcProbe(config("publisher"), fake_clients).run()

    def test_deployer_requires_all_exact_runtime_identifiers(self) -> None:
        with self.assertRaisesMessage(
            ReleaseContractError, "deployer OIDC probe requires exact runtime identifiers"
        ):
            ProbeConfig(
                role="deployer",
                account_id=ACCOUNT_ID,
                region=REGION,
                repository_name=REPOSITORY,
                probe_id="12345",
            )

    def test_deployer_rejects_stale_or_unrelated_target_group_arns(self) -> None:
        invalid_arns = (
            TARGET_GROUP.replace("eu-west-1", "eu-west-2"),
            TARGET_GROUP.replace(ACCOUNT_ID, "000000000000"),
            TARGET_GROUP.replace("website-sandbox-web", "other-web"),
            TARGET_GROUP[:-1],
            TARGET_GROUP[:-1] + "G",
        )
        for target_group_arn in invalid_arns:
            with (
                self.subTest(target_group_arn=target_group_arn),
                self.assertRaisesMessage(
                    ReleaseContractError,
                    "deployer target-group ARN is not the exact website target",
                ),
            ):
                replace(config("deployer"), web_target_group_arn=target_group_arn)


class OidcWrongClaimProbeTests(SimpleTestCase):
    def test_access_denial_is_required_and_token_is_never_logged(self) -> None:
        sts = FakeClaimSts()
        fetched: list[str] = []

        def fetcher(audience: str) -> str:
            fetched.append(audience)
            return f"header.{'sensitive-token-body' * 10}.signature"

        output = io.StringIO()
        with redirect_stdout(output):
            prove_wrong_claim_denied(
                role_arn=("arn:aws:iam::817685572750:role/website-sandbox-github-deployer"),
                audience="sts.amazonaws.com",
                claim_label="main-subject-to-environment-role",
                probe_id="12345",
                token_fetcher=fetcher,
                sts=sts,
            )

        self.assertEqual(fetched, ["sts.amazonaws.com"])
        self.assertEqual(sts.calls[0]["WebIdentityToken"].count("."), 2)
        self.assertNotIn("sensitive-token-body", output.getvalue())
        record = json.loads(output.getvalue())
        self.assertEqual(record["result"], "denied")
        self.assertEqual(record["claim"], "main-subject-to-environment-role")

    def test_wrong_audience_identity_rejection_is_an_expected_denial(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            prove_wrong_claim_denied(
                role_arn=("arn:aws:iam::817685572750:role/website-sandbox-github-publisher"),
                audience="dtc.invalid.example",
                claim_label="wrong-audience-to-main-role",
                probe_id="12345",
                token_fetcher=fake_token,
                sts=FakeClaimSts("InvalidIdentityToken"),
            )
        self.assertEqual(json.loads(output.getvalue())["result"], "denied")

    def test_other_error_and_unexpected_assumption_fail_closed(self) -> None:
        with self.assertRaisesMessage(
            ReleaseContractError, "wrong-claim denial was not proven (IDPCommunicationError)"
        ):
            prove_wrong_claim_denied(
                role_arn=("arn:aws:iam::817685572750:role/website-sandbox-github-publisher"),
                audience="dtc.invalid.example",
                claim_label="wrong-audience-to-main-role",
                probe_id="12345",
                token_fetcher=fake_token,
                sts=FakeClaimSts("IDPCommunicationError"),
            )

        unexpectedly_allowed = FakeClaimSts(None)
        with self.assertRaisesMessage(
            ReleaseContractError, "wrong OIDC claim unexpectedly assumed the application role"
        ):
            prove_wrong_claim_denied(
                role_arn=("arn:aws:iam::817685572750:role/website-sandbox-github-publisher"),
                audience="sts.amazonaws.com",
                claim_label="environment-subject-to-main-role",
                probe_id="12345",
                token_fetcher=fake_token,
                sts=unexpectedly_allowed,
            )
        self.assertEqual(unexpectedly_allowed.response, {})
