from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from typing import Any
from unittest.mock import patch

from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from django.test import SimpleTestCase

from deploy.contracts import ReleaseContractError
from deploy.oidc_claim_probe import prove_wrong_claim_denied
from deploy.oidc_probe import (
    ECR_DELETE_DENIAL_CODES,
    KMS_DENIAL_CODES,
    KMS_KEY_ARN,
    LIVE_CALL_ALLOWLIST,
    RETAINED_DENIAL_ACTIONS,
    ROUTE53_DENIAL_CODES,
    ROUTE53_HOSTED_ZONE_ID,
    S3_DENIAL_CODES,
    SANDBOX_ACCOUNT_ID,
    SANDBOX_REGION,
    SANDBOX_REPOSITORY,
    STATE_BUCKET,
    STATE_BUCKET_OWNER,
    STATE_KEY,
    OidcProbe,
    ProbeConfig,
    build_parser,
    main,
)

ACCOUNT_ID = "817685572750"
REGION = "eu-west-1"
REPOSITORY = "website-sandbox"
CLUSTER = f"arn:aws:ecs:{REGION}:{ACCOUNT_ID}:cluster/website-sandbox"
TARGET_GROUP = (
    f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT_ID}:"
    "targetgroup/website-sandbox-web/0123456789abcdef"
)
FAMILIES = tuple(f"website-sandbox-{name}" for name in ("web", "worker", "migration"))
DUPLICATE_HOSTED_ZONE_ID = "Z04966063K9K29ZGHVRYN"


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
        self.error_overrides: dict[str, str | Exception] = {}
        self.response_overrides: dict[str, Any] = {}

    def __getattr__(self, method: str):  # type: ignore[no-untyped-def]
        def call(**kwargs):  # type: ignore[no-untyped-def]
            self.calls.append((method, kwargs))
            if method in self.error_overrides:
                error = self.error_overrides[method]
                if isinstance(error, str):
                    raise ClientError(
                        {"Error": {"Code": error, "Message": "redacted"}},
                        method,
                    )
                raise error
            if method in self.response_overrides:
                return self.response_overrides[method]
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
        for service in {service for service, _method in LIVE_CALL_ALLOWLIST[role]}
    }


def config(role: str) -> ProbeConfig:
    common: dict[str, Any] = {
        "role": role,
        "account_id": ACCOUNT_ID,
        "region": REGION,
        "repository_name": REPOSITORY,
        "probe_id": "12345",
        "hosted_zone_id": ROUTE53_HOSTED_ZONE_ID,
        "kms_key_arn": KMS_KEY_ARN,
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
        denied = [record["action"] for record in records if record["result"] == "denied"]
        self.assertEqual(denied, list(RETAINED_DENIAL_ACTIONS))
        self.assertEqual(
            {
                service: [method for method, _arguments in client.calls]
                for service, client in fake_clients.items()
            },
            {
                "ecr": ["describe_images", "batch_delete_image"],
                "kms": ["create_grant"],
                "route53": ["change_resource_record_sets"],
                "s3": ["head_object"],
                "sts": ["get_caller_identity"],
            },
        )
        expected_fields = {"action", "caller_arn", "resource", "result", "role", "timestamp"}
        self.assertTrue(all(set(record) == expected_fields for record in records))
        role_marker = "assumed-role/website-sandbox-github-publisher/"
        self.assertTrue(all(role_marker in record["caller_arn"] for record in records))

        ecr_delete = fake_clients["ecr"].calls[-1]
        self.assertEqual(ecr_delete[0], "batch_delete_image")
        self.assertEqual(ecr_delete[1]["imageIds"], [{"imageDigest": f"sha256:{'0' * 64}"}])

    def test_s3_head_request_is_exact_and_uses_the_state_region(self) -> None:
        self.assertEqual(SANDBOX_ACCOUNT_ID, ACCOUNT_ID)
        self.assertEqual(SANDBOX_REGION, REGION)
        self.assertEqual(SANDBOX_REPOSITORY, REPOSITORY)
        self.assertEqual(STATE_BUCKET, "datamailer-sandbox-817685572750-us-east-1-tfstate")
        self.assertEqual(STATE_BUCKET_OWNER, ACCOUNT_ID)
        self.assertEqual(STATE_KEY, "sandbox/website/terraform.tfstate")
        created_clients: dict[str, FakeAwsClient] = {}

        def client_factory(service: str, *, region_name: str) -> FakeAwsClient:
            created_clients[service] = FakeAwsClient(service, "publisher")
            return created_clients[service]

        with (
            patch("deploy.oidc_probe.boto3.client", side_effect=client_factory) as factory,
            redirect_stdout(io.StringIO()),
        ):
            OidcProbe(config("publisher")).run()

        factory.assert_any_call("s3", region_name="us-east-1")
        self.assertEqual(
            created_clients["s3"].calls,
            [
                (
                    "head_object",
                    {
                        "Bucket": STATE_BUCKET,
                        "Key": STATE_KEY,
                        "ExpectedBucketOwner": STATE_BUCKET_OWNER,
                    },
                )
            ],
        )

    def test_s3_head_accepts_only_exact_access_denial_codes(self) -> None:
        self.assertEqual(S3_DENIAL_CODES, {"403", "AccessDenied", "AccessDeniedException"})
        for code in sorted(S3_DENIAL_CODES):
            with self.subTest(code=code):
                fake_clients = clients("publisher")
                fake_clients["s3"].error_overrides["head_object"] = code

                with redirect_stdout(io.StringIO()):
                    OidcProbe(config("publisher"), fake_clients).run()

                self.assertEqual(
                    fake_clients["s3"].calls.count(
                        (
                            "head_object",
                            {
                                "Bucket": STATE_BUCKET,
                                "Key": STATE_KEY,
                                "ExpectedBucketOwner": STATE_BUCKET_OWNER,
                            },
                        )
                    ),
                    1,
                )

    def test_s3_head_notfound_validation_and_other_results_fail_closed(self) -> None:
        for code in (
            "400",
            "401",
            "404",
            "NoSuchBucket",
            "NoSuchKey",
            "InvalidRequest",
            "ValidationException",
            "InternalError",
            "UnauthorizedOperation",
        ):
            with self.subTest(code=code):
                fake_clients = clients("publisher")
                fake_clients["s3"].error_overrides["head_object"] = code
                output = io.StringIO()

                with (
                    self.assertRaisesMessage(
                        ReleaseContractError,
                        f"permission denial was not proven for s3:GetObject ({code})",
                    ),
                    redirect_stdout(output),
                ):
                    OidcProbe(config("publisher"), fake_clients).run()

                self.assertEqual(len(fake_clients["s3"].calls), 1)
                self.assertEqual(fake_clients["route53"].calls, [])
                self.assertNotIn("redacted", output.getvalue())

    def test_s3_head_transport_failure_and_success_fail_closed_without_leakage(self) -> None:
        network_clients = clients("publisher")
        network_clients["s3"].error_overrides["head_object"] = TimeoutError(
            "sensitive-provider-detail"
        )
        network_output = io.StringIO()
        with (
            self.assertRaisesMessage(
                ReleaseContractError,
                "permission denial probe failed for s3:GetObject (TimeoutError)",
            ),
            redirect_stdout(network_output),
        ):
            OidcProbe(config("publisher"), network_clients).run()
        self.assertEqual(len(network_clients["s3"].calls), 1)
        self.assertEqual(network_clients["route53"].calls, [])
        self.assertNotIn("sensitive-provider-detail", network_output.getvalue())

        success_clients = clients("publisher")
        success_clients["s3"].response_overrides["head_object"] = {
            "ETag": "must-not-log-state-etag"
        }
        success_output = io.StringIO()
        with (
            self.assertRaisesMessage(
                ReleaseContractError,
                "unsafe permission unexpectedly allowed: s3:GetObject",
            ),
            redirect_stdout(success_output),
        ):
            OidcProbe(config("publisher"), success_clients).run()
        self.assertEqual(len(success_clients["s3"].calls), 1)
        self.assertEqual(success_clients["route53"].calls, [])
        self.assertNotIn("must-not-log-state-etag", success_output.getvalue())

    def test_kms_request_is_exact_nonmutating_and_once_only_for_each_role(self) -> None:
        for role in ("publisher", "deployer"):
            with self.subTest(role=role):
                records, fake_clients = self.run_probe(role)

                self.assertEqual(len(fake_clients["kms"].calls), 1)
                method, arguments = fake_clients["kms"].calls[0]
                self.assertEqual(method, "create_grant")
                self.assertEqual(
                    arguments,
                    {
                        "KeyId": KMS_KEY_ARN,
                        "GranteePrincipal": (
                            f"arn:aws:iam::{ACCOUNT_ID}:role/website-sandbox-github-{role}"
                        ),
                        "Operations": ["Decrypt"],
                        "Name": f"oidc-denial-probe-{role}-12345",
                        "DryRun": True,
                    },
                )
                kms_record = next(
                    record for record in records if record["action"] == "kms:CreateGrant"
                )
                self.assertEqual(kms_record["resource"], KMS_KEY_ARN)
                self.assertEqual(kms_record["result"], "denied")
                self.assertNotIn("GrantId", json.dumps(records))
                self.assertNotIn("GrantToken", json.dumps(records))

    def test_kms_accepts_only_exact_access_denial_codes(self) -> None:
        self.assertEqual(KMS_DENIAL_CODES, {"AccessDenied", "AccessDeniedException"})
        for code in sorted(KMS_DENIAL_CODES):
            with self.subTest(code=code):
                fake_clients = clients("publisher")
                fake_clients["kms"].error_overrides["create_grant"] = code

                with redirect_stdout(io.StringIO()):
                    OidcProbe(config("publisher"), fake_clients).run()

                self.assertEqual(len(fake_clients["kms"].calls), 1)

    def test_kms_non_authorization_service_results_fail_closed(self) -> None:
        for code in (
            "403",
            "UnauthorizedOperation",
            "DryRunOperationException",
            "NotFoundException",
            "ValidationException",
            "InvalidArnException",
            "KMSInvalidStateException",
            "DisabledException",
            "KMSInternalException",
        ):
            with self.subTest(code=code):
                fake_clients = clients("publisher")
                fake_clients["kms"].error_overrides["create_grant"] = code
                output = io.StringIO()

                with (
                    self.assertRaisesMessage(
                        ReleaseContractError,
                        f"permission denial was not proven for kms:CreateGrant ({code})",
                    ),
                    redirect_stdout(output),
                ):
                    OidcProbe(config("publisher"), fake_clients).run()

                self.assertEqual(len(fake_clients["kms"].calls), 1)
                self.assertNotIn("redacted", output.getvalue())

    def test_kms_transport_failure_and_success_fail_closed_without_response_leakage(self) -> None:
        network_clients = clients("publisher")
        network_clients["kms"].error_overrides["create_grant"] = TimeoutError(
            "sensitive-provider-detail"
        )
        network_output = io.StringIO()
        with (
            self.assertRaisesMessage(
                ReleaseContractError,
                "permission denial probe failed for kms:CreateGrant (TimeoutError)",
            ),
            redirect_stdout(network_output),
        ):
            OidcProbe(config("publisher"), network_clients).run()
        self.assertEqual(len(network_clients["kms"].calls), 1)
        self.assertNotIn("sensitive-provider-detail", network_output.getvalue())

        success_clients = clients("publisher")
        success_clients["kms"].response_overrides["create_grant"] = {
            "GrantId": "must-not-log-grant-id",
            "GrantToken": "must-not-log-grant-token",
        }
        success_output = io.StringIO()
        with (
            self.assertRaisesMessage(
                ReleaseContractError,
                "unsafe permission unexpectedly allowed: kms:CreateGrant",
            ),
            redirect_stdout(success_output),
        ):
            OidcProbe(config("publisher"), success_clients).run()
        self.assertEqual(len(success_clients["kms"].calls), 1)
        self.assertNotIn("must-not-log", success_output.getvalue())

    def test_retained_ecr_delete_is_exact_nonmutating_and_once_only(self) -> None:
        records, fake_clients = self.run_probe("publisher")

        delete_calls = [
            arguments
            for method, arguments in fake_clients["ecr"].calls
            if method == "batch_delete_image"
        ]
        self.assertEqual(
            delete_calls,
            [
                {
                    "repositoryName": REPOSITORY,
                    "imageIds": [{"imageDigest": f"sha256:{'0' * 64}"}],
                }
            ],
        )
        record = next(record for record in records if record["action"] == "ecr:BatchDeleteImage")
        self.assertEqual(record["resource"], REPOSITORY)
        self.assertNotIn(f"sha256:{'0' * 64}", json.dumps(records))

    def test_retained_ecr_delete_access_denials_pass_independently(self) -> None:
        self.assertEqual(ECR_DELETE_DENIAL_CODES, {"AccessDenied", "AccessDeniedException"})
        for code in sorted(ECR_DELETE_DENIAL_CODES):
            with self.subTest(code=code):
                fake_clients = clients("publisher")
                fake_clients["ecr"].error_overrides["batch_delete_image"] = code

                with redirect_stdout(io.StringIO()):
                    OidcProbe(config("publisher"), fake_clients).run()

                self.assertEqual(
                    [call[0] for call in fake_clients["ecr"].calls].count("batch_delete_image"),
                    1,
                )

    def test_retained_ecr_delete_notfound_validation_and_responses_fail_closed(self) -> None:
        for code in (
            "403",
            "UnauthorizedOperation",
            "RepositoryNotFoundException",
            "ImageNotFoundException",
            "ValidationException",
        ):
            with self.subTest(code=code):
                fake_clients = clients("publisher")
                fake_clients["ecr"].error_overrides["batch_delete_image"] = code
                with (
                    self.assertRaisesMessage(
                        ReleaseContractError,
                        f"permission denial was not proven for ecr:BatchDeleteImage ({code})",
                    ),
                    redirect_stdout(io.StringIO()),
                ):
                    OidcProbe(config("publisher"), fake_clients).run()

        for response in (
            {
                "failures": [
                    {
                        "failureCode": "ImageNotFound",
                        "failureReason": "must-not-log-provider-body",
                    }
                ],
                "imageIds": [],
            },
            {"failures": [], "imageIds": [{"imageDigest": f"sha256:{'0' * 64}"}]},
        ):
            with self.subTest(response=response):
                fake_clients = clients("publisher")
                fake_clients["ecr"].response_overrides["batch_delete_image"] = response
                output = io.StringIO()
                with (
                    self.assertRaisesMessage(
                        ReleaseContractError,
                        "unsafe permission unexpectedly allowed: ecr:BatchDeleteImage",
                    ),
                    redirect_stdout(output),
                ):
                    OidcProbe(config("publisher"), fake_clients).run()
                self.assertNotIn("must-not-log-provider-body", output.getvalue())

    def test_retained_ecr_delete_transport_failure_fails_without_detail_leakage(self) -> None:
        fake_clients = clients("publisher")
        fake_clients["ecr"].error_overrides["batch_delete_image"] = TimeoutError(
            "sensitive-provider-detail"
        )
        output = io.StringIO()
        with (
            self.assertRaisesMessage(
                ReleaseContractError,
                "permission denial probe failed for ecr:BatchDeleteImage (TimeoutError)",
            ),
            redirect_stdout(output),
        ):
            OidcProbe(config("publisher"), fake_clients).run()

        self.assertEqual(
            [call[0] for call in fake_clients["ecr"].calls].count("batch_delete_image"),
            1,
        )
        self.assertNotIn("sensitive-provider-detail", output.getvalue())

    def test_removed_missing_resource_sentinels_are_never_called_for_either_role(self) -> None:
        removed_methods = {
            "create_invalidation",
            "deregister_task_definition",
            "get_secret_value",
            "modify_db_instance",
            "modify_target_group_attributes",
            "run_task",
            "update_role_description",
            "update_service",
        }
        removed_actions = {
            "cloudfront:CreateInvalidation",
            "ecs:DeregisterTaskDefinition",
            "ecs:RunTask",
            "ecs:UpdateService",
            "elasticloadbalancing:ModifyTargetGroupAttributes",
            "iam:UpdateRoleDescription",
            "rds:ModifyDBInstance",
            "secretsmanager:GetSecretValue",
        }
        removed_clients = {"cloudfront", "iam", "rds", "secretsmanager"}
        for role in ("publisher", "deployer"):
            with self.subTest(role=role):
                records, fake_clients = self.run_probe(role)
                methods = {
                    method
                    for client in fake_clients.values()
                    for method, _arguments in client.calls
                }
                actions = {record["action"] for record in records}
                serialized_calls = json.dumps(
                    {service: client.calls for service, client in fake_clients.items()},
                    sort_keys=True,
                )
                described_repositories = [
                    arguments["repositoryName"]
                    for method, arguments in fake_clients["ecr"].calls
                    if method == "describe_images"
                ]

                self.assertTrue(removed_methods.isdisjoint(methods))
                self.assertTrue(removed_actions.isdisjoint(actions))
                self.assertTrue(removed_clients.isdisjoint(fake_clients))
                self.assertEqual(described_repositories, [REPOSITORY])
                for removed_marker in (
                    "999999999",
                    "E0000000000000",
                    "foreign-probe",
                    "probe-denied",
                    "production-probe",
                ):
                    self.assertNotIn(removed_marker, serialized_calls)

                if role == "publisher":
                    self.assertNotIn("elbv2", fake_clients)
                else:
                    self.assertEqual(
                        fake_clients["elbv2"].calls,
                        [("describe_target_health", {"TargetGroupArn": TARGET_GROUP})],
                    )

    def test_runtime_allowlist_rejects_every_extra_client_and_action(self) -> None:
        for role in ("publisher", "deployer"):
            with self.subTest(role=role):
                fake_clients = clients(role)
                probe = OidcProbe(config(role), fake_clients)
                for service in ("cloudfront", "iam", "rds", "secretsmanager"):
                    with self.assertRaisesMessage(
                        ReleaseContractError,
                        f"AWS client is outside the OIDC probe allowlist: {service}",
                    ):
                        probe._client(service)
                for service, method in (
                    ("ecr", "delete_repository"),
                    ("kms", "schedule_key_deletion"),
                    ("route53", "create_hosted_zone"),
                    ("s3", "delete_object"),
                    ("elbv2", "modify_target_group_attributes"),
                ):
                    with self.assertRaisesMessage(
                        ReleaseContractError,
                        f"AWS operation is outside the OIDC probe allowlist: {service}.{method}",
                    ):
                        probe._call(service, method)

                self.assertTrue(all(not client.calls for client in fake_clients.values()))

    def test_denial_sequence_allowlist_rejects_reordered_and_extra_actions(self) -> None:
        probe = OidcProbe(config("publisher"), clients("publisher"))
        with self.assertRaisesMessage(
            ReleaseContractError,
            "OIDC denial action is out of order: expected s3:GetObject, got kms:CreateGrant",
        ):
            probe._deny(
                "kms:CreateGrant",
                KMS_KEY_ARN,
                lambda: None,
                denial_codes=KMS_DENIAL_CODES,
            )

        exhausted_probe = OidcProbe(config("publisher"), clients("publisher"))
        exhausted_probe._denial_index = len(RETAINED_DENIAL_ACTIONS)
        with self.assertRaisesMessage(
            ReleaseContractError,
            "unexpected extra OIDC denial action: s3:GetObject",
        ):
            exhausted_probe._deny(
                "s3:GetObject",
                f"s3://{STATE_BUCKET}/{STATE_KEY}",
                lambda: None,
                denial_codes=S3_DENIAL_CODES,
            )

    def test_route53_request_is_one_exact_duplicate_delete_batch_without_managed_records(
        self,
    ) -> None:
        records, fake_clients = self.run_probe("publisher")

        self.assertEqual(len(fake_clients["route53"].calls), 1)
        method, arguments = fake_clients["route53"].calls[0]
        self.assertEqual(method, "change_resource_record_sets")
        self.assertEqual(arguments["HostedZoneId"], ROUTE53_HOSTED_ZONE_ID)
        self.assertEqual(arguments["ChangeBatch"]["Comment"], "OIDC denial probe")
        changes = arguments["ChangeBatch"]["Changes"]
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[0], changes[1])
        self.assertEqual(
            json.dumps(changes[0], sort_keys=True).encode(),
            json.dumps(changes[1], sort_keys=True).encode(),
        )
        self.assertEqual(
            changes[0],
            {
                "Action": "DELETE",
                "ResourceRecordSet": {
                    "Name": "oidc-denial-probe-12345.dtcdev.click.",
                    "Type": "TXT",
                    "TTL": 60,
                    "ResourceRecords": [{"Value": '"oidc-denial-probe-12345"'}],
                },
            },
        )
        serialized_request = json.dumps(arguments, sort_keys=True)
        for managed_dns_identifier in (
            "web.dtcdev.click",
            "origin.web.dtcdev.click",
            "acm-validations.aws",
        ):
            self.assertNotIn(managed_dns_identifier, serialized_request)
        route_record = next(
            record for record in records if record["action"] == "route53:ChangeResourceRecordSets"
        )
        self.assertEqual(route_record["resource"], ROUTE53_HOSTED_ZONE_ID)
        self.assertNotIn("oidc-denial-probe-12345", json.dumps(records))

    def test_route53_access_denial_codes_pass(self) -> None:
        self.assertEqual(
            ROUTE53_DENIAL_CODES,
            {"AccessDenied", "AccessDeniedException"},
        )
        for code in sorted(ROUTE53_DENIAL_CODES):
            with self.subTest(code=code):
                fake_clients = clients("publisher")
                fake_clients["route53"].error_overrides["change_resource_record_sets"] = code
                output = io.StringIO()

                with redirect_stdout(output):
                    OidcProbe(config("publisher"), fake_clients).run()

                route_records = [
                    json.loads(line)
                    for line in output.getvalue().splitlines()
                    if '"action": "route53:ChangeResourceRecordSets"' in line
                ]
                self.assertEqual(len(route_records), 1)
                self.assertEqual(route_records[0]["result"], "denied")
                self.assertEqual(len(fake_clients["route53"].calls), 1)

    def test_route53_non_authorization_results_fail_closed(self) -> None:
        for code in (
            "403",
            "UnauthorizedOperation",
            "InvalidChangeBatch",
            "NoSuchHostedZone",
            "InternalError",
        ):
            with self.subTest(code=code):
                fake_clients = clients("publisher")
                fake_clients["route53"].error_overrides["change_resource_record_sets"] = code
                output = io.StringIO()
                with (
                    self.assertRaisesMessage(
                        ReleaseContractError,
                        "permission denial was not proven for "
                        f"route53:ChangeResourceRecordSets ({code})",
                    ),
                    redirect_stdout(output),
                ):
                    OidcProbe(config("publisher"), fake_clients).run()
                self.assertEqual(len(fake_clients["route53"].calls), 1)
                self.assertNotIn("redacted", output.getvalue())

    def test_route53_network_failure_and_success_fail_closed(self) -> None:
        network_failure_clients = clients("publisher")
        network_failure_clients["route53"].error_overrides["change_resource_record_sets"] = (
            TimeoutError("must not be logged")
        )
        network_output = io.StringIO()
        with (
            self.assertRaisesMessage(
                ReleaseContractError,
                "permission denial probe failed for "
                "route53:ChangeResourceRecordSets (TimeoutError)",
            ),
            redirect_stdout(network_output),
        ):
            OidcProbe(config("publisher"), network_failure_clients).run()
        self.assertEqual(len(network_failure_clients["route53"].calls), 1)
        self.assertNotIn("must not be logged", network_output.getvalue())

        success_clients = clients("publisher")
        success_clients["route53"].response_overrides["change_resource_record_sets"] = {
            "ChangeInfo": {"Id": "must-not-be-accepted"}
        }
        with (
            self.assertRaisesMessage(
                ReleaseContractError,
                "unsafe permission unexpectedly allowed: route53:ChangeResourceRecordSets",
            ),
            redirect_stdout(io.StringIO()),
        ):
            OidcProbe(config("publisher"), success_clients).run()
        self.assertEqual(len(success_clients["route53"].calls), 1)

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
        self.assertEqual(foreign_repositories, [])
        denied_actions = [record["action"] for record in records if record["result"] == "denied"]
        self.assertEqual(denied_actions, list(RETAINED_DENIAL_ACTIONS))
        self.assertNotIn("ecs:DescribeServices", denied_actions)
        self.assertNotIn("ecs:UpdateService", denied_actions)
        self.assertNotIn("ecs:RunTask", denied_actions)

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
                hosted_zone_id=ROUTE53_HOSTED_ZONE_ID,
                kms_key_arn=KMS_KEY_ARN,
            )

    def test_probe_requires_the_exact_delegated_hosted_zone(self) -> None:
        invalid_zone_ids = (
            "",
            "Z00000000000000000000",
            DUPLICATE_HOSTED_ZONE_ID,
            ROUTE53_HOSTED_ZONE_ID.lower(),
            f"{ROUTE53_HOSTED_ZONE_ID} ",
        )
        for hosted_zone_id in invalid_zone_ids:
            with (
                self.subTest(hosted_zone_id=hosted_zone_id),
                self.assertRaisesMessage(
                    ReleaseContractError,
                    "OIDC probe hosted-zone ID is not the exact sandbox zone",
                ),
            ):
                replace(config("publisher"), hosted_zone_id=hosted_zone_id)

    def test_cli_requires_once_only_hosted_zone_and_kms_values(self) -> None:
        common_arguments = [
            "publisher",
            "--account-id",
            ACCOUNT_ID,
            "--region",
            REGION,
            "--repository-name",
            REPOSITORY,
            "--probe-id",
            "12345",
        ]
        valid_resource_arguments = [
            "--hosted-zone-id",
            ROUTE53_HOSTED_ZONE_ID,
            "--kms-key-arn",
            KMS_KEY_ARN,
        ]
        for resource_arguments in (
            ["--kms-key-arn", KMS_KEY_ARN],
            ["--hosted-zone-id", ROUTE53_HOSTED_ZONE_ID],
            valid_resource_arguments + ["--hosted-zone-id", ROUTE53_HOSTED_ZONE_ID],
            valid_resource_arguments + ["--kms-key-arn", KMS_KEY_ARN],
        ):
            with (
                self.subTest(resource_arguments=resource_arguments),
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                build_parser().parse_args(common_arguments + resource_arguments)
            self.assertEqual(raised.exception.code, 2)

    def test_invalid_cli_zone_fails_before_any_aws_client_is_created(self) -> None:
        arguments = [
            "oidc_probe.py",
            "publisher",
            "--account-id",
            ACCOUNT_ID,
            "--region",
            REGION,
            "--repository-name",
            REPOSITORY,
            "--probe-id",
            "12345",
            "--hosted-zone-id",
            DUPLICATE_HOSTED_ZONE_ID,
            "--kms-key-arn",
            KMS_KEY_ARN,
        ]
        with (
            patch("sys.argv", arguments),
            patch("deploy.oidc_probe.boto3.client") as client_factory,
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            main()

        self.assertEqual(raised.exception.code, 1)
        client_factory.assert_not_called()

    def test_nonexact_sandbox_scope_fails_before_any_aws_client_is_created(self) -> None:
        exact_arguments = [
            "oidc_probe.py",
            "publisher",
            "--account-id",
            ACCOUNT_ID,
            "--region",
            REGION,
            "--repository-name",
            REPOSITORY,
            "--probe-id",
            "12345",
            "--hosted-zone-id",
            ROUTE53_HOSTED_ZONE_ID,
            "--kms-key-arn",
            KMS_KEY_ARN,
        ]
        for argument_index, nonexact_value, expected_message in (
            (3, "000000000000", "OIDC probe account ID is not the exact sandbox account"),
            (5, "eu-west-2", "OIDC probe region is not the exact sandbox region"),
            (
                7,
                "website-production",
                "OIDC probe repository is not the exact sandbox repository",
            ),
        ):
            arguments = exact_arguments.copy()
            arguments[argument_index] = nonexact_value
            error_output = io.StringIO()
            with (
                self.subTest(argument_index=argument_index, nonexact_value=nonexact_value),
                patch("sys.argv", arguments),
                patch("deploy.oidc_probe.boto3.client") as client_factory,
                redirect_stderr(error_output),
                self.assertRaises(SystemExit) as raised,
            ):
                main()

            self.assertEqual(raised.exception.code, 1)
            self.assertIn(expected_message, error_output.getvalue())
            client_factory.assert_not_called()

    def test_s3_client_rejects_every_region_except_us_east_1_before_creation(self) -> None:
        probe = OidcProbe(config("publisher"))
        for region in (None, REGION, "us-west-2"):
            with (
                self.subTest(region=region),
                patch("deploy.oidc_probe.boto3.client") as client_factory,
                self.assertRaisesMessage(
                    ReleaseContractError,
                    "AWS client region is outside the OIDC probe contract: s3",
                ),
            ):
                probe._client("s3", region=region)

            client_factory.assert_not_called()

    def test_probe_rejects_every_nonexact_kms_arn(self) -> None:
        invalid_key_arns = (
            "",
            "b9181223-d870-4bae-92d2-fc28b7813887",
            KMS_KEY_ARN.replace("eu-west-1", "eu-west-2"),
            KMS_KEY_ARN.replace(ACCOUNT_ID, "000000000000"),
            KMS_KEY_ARN.replace("b9181223", "a0000000"),
            KMS_KEY_ARN.upper(),
            f"{KMS_KEY_ARN} ",
        )
        for kms_key_arn in invalid_key_arns:
            with (
                self.subTest(kms_key_arn=kms_key_arn),
                self.assertRaisesMessage(
                    ReleaseContractError,
                    "OIDC probe KMS key ARN is not the exact sandbox key",
                ),
            ):
                replace(config("publisher"), kms_key_arn=kms_key_arn)

    def test_invalid_cli_kms_arn_fails_before_any_aws_client_is_created(self) -> None:
        common_arguments = [
            "oidc_probe.py",
            "publisher",
            "--account-id",
            ACCOUNT_ID,
            "--region",
            REGION,
            "--repository-name",
            REPOSITORY,
            "--probe-id",
            "12345",
            "--hosted-zone-id",
            ROUTE53_HOSTED_ZONE_ID,
        ]
        invalid_key_arns = (
            "",
            "b9181223-d870-4bae-92d2-fc28b7813887",
            KMS_KEY_ARN.replace("eu-west-1", "eu-west-2"),
            KMS_KEY_ARN.replace(ACCOUNT_ID, "000000000000"),
            KMS_KEY_ARN.replace("b9181223", "a0000000"),
            KMS_KEY_ARN.upper(),
            f"{KMS_KEY_ARN} ",
        )
        for kms_key_arn in invalid_key_arns:
            with (
                self.subTest(kms_key_arn=kms_key_arn),
                patch("sys.argv", common_arguments + ["--kms-key-arn", kms_key_arn]),
                patch("deploy.oidc_probe.boto3.client") as client_factory,
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                main()

            self.assertEqual(raised.exception.code, 1)
            client_factory.assert_not_called()

    def test_missing_or_repeated_cli_kms_value_fails_before_aws_client_creation(self) -> None:
        common_arguments = [
            "oidc_probe.py",
            "publisher",
            "--account-id",
            ACCOUNT_ID,
            "--region",
            REGION,
            "--repository-name",
            REPOSITORY,
            "--probe-id",
            "12345",
            "--hosted-zone-id",
            ROUTE53_HOSTED_ZONE_ID,
        ]
        for kms_arguments in (
            [],
            ["--kms-key-arn", KMS_KEY_ARN, "--kms-key-arn", KMS_KEY_ARN],
        ):
            with (
                self.subTest(kms_arguments=kms_arguments),
                patch("sys.argv", common_arguments + kms_arguments),
                patch("deploy.oidc_probe.boto3.client") as client_factory,
                redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as raised,
            ):
                main()

            self.assertEqual(raised.exception.code, 2)
            client_factory.assert_not_called()

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
