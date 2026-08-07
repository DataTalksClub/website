from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from deploy.contracts import ReleaseContractError

SANDBOX_ACCOUNT_ID = "817685572750"
SANDBOX_REGION = "eu-west-1"
SANDBOX_REPOSITORY = "website-sandbox"
STATE_BUCKET = "datamailer-sandbox-817685572750-us-east-1-tfstate"
STATE_BUCKET_OWNER = SANDBOX_ACCOUNT_ID
STATE_KEY = "sandbox/website/terraform.tfstate"
S3_DENIAL_CODES = frozenset({"403", "AccessDenied", "AccessDeniedException"})
ROUTE53_DENIAL_CODES = frozenset({"AccessDenied", "AccessDeniedException"})
KMS_DENIAL_CODES = frozenset({"AccessDenied", "AccessDeniedException"})
ECR_DELETE_DENIAL_CODES = frozenset({"AccessDenied", "AccessDeniedException"})
ROUTE53_HOSTED_ZONE_ID = "Z05963572WVWFHDQZH5NE"
KMS_KEY_ARN = "arn:aws:kms:eu-west-1:817685572750:key/b9181223-d870-4bae-92d2-fc28b7813887"
ROLE_NAMES = {
    "publisher": "website-sandbox-github-publisher",
    "deployer": "website-sandbox-github-deployer",
}
WEB_TARGET_GROUP_ARN_PATTERN = re.compile(
    r"^arn:aws:elasticloadbalancing:eu-west-1:817685572750:"
    r"targetgroup/website-sandbox-web/[0-9a-f]{16}$"
)
PUBLISHER_LIVE_CALLS = frozenset(
    {
        ("ecr", "batch_delete_image"),
        ("ecr", "describe_images"),
        ("kms", "create_grant"),
        ("route53", "change_resource_record_sets"),
        ("s3", "head_object"),
        ("sts", "get_caller_identity"),
    }
)
LIVE_CALL_ALLOWLIST = {
    "publisher": PUBLISHER_LIVE_CALLS,
    "deployer": PUBLISHER_LIVE_CALLS
    | {
        ("ecs", "describe_clusters"),
        ("ecs", "describe_services"),
        ("ecs", "describe_task_definition"),
        ("ecs", "list_tasks"),
        ("elbv2", "describe_target_health"),
    },
}
RETAINED_DENIAL_ACTIONS = (
    "s3:GetObject",
    "route53:ChangeResourceRecordSets",
    "kms:CreateGrant",
    "ecr:BatchDeleteImage",
)


@dataclass(frozen=True)
class ProbeConfig:
    role: str
    account_id: str
    region: str
    repository_name: str
    probe_id: str
    hosted_zone_id: str
    kms_key_arn: str
    cluster_arn: str | None = None
    web_target_group_arn: str | None = None
    web_service_name: str | None = None
    worker_service_name: str | None = None
    task_families: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.role not in ROLE_NAMES:
            raise ReleaseContractError("OIDC probe role must be publisher or deployer")
        if self.account_id != SANDBOX_ACCOUNT_ID:
            raise ReleaseContractError("OIDC probe account ID is not the exact sandbox account")
        if self.region != SANDBOX_REGION:
            raise ReleaseContractError("OIDC probe region is not the exact sandbox region")
        if self.repository_name != SANDBOX_REPOSITORY:
            raise ReleaseContractError("OIDC probe repository is not the exact sandbox repository")
        if not re.fullmatch(r"[0-9]{1,12}", self.probe_id):
            raise ReleaseContractError("OIDC probe ID is invalid")
        if self.hosted_zone_id != ROUTE53_HOSTED_ZONE_ID:
            raise ReleaseContractError("OIDC probe hosted-zone ID is not the exact sandbox zone")
        if self.kms_key_arn != KMS_KEY_ARN:
            raise ReleaseContractError("OIDC probe KMS key ARN is not the exact sandbox key")
        if self.role == "deployer" and (
            not self.cluster_arn
            or not self.web_target_group_arn
            or not self.web_service_name
            or not self.worker_service_name
            or len(self.task_families) != 3
        ):
            raise ReleaseContractError("deployer OIDC probe requires exact runtime identifiers")
        if self.role == "deployer" and not WEB_TARGET_GROUP_ARN_PATTERN.fullmatch(
            str(self.web_target_group_arn)
        ):
            raise ReleaseContractError("deployer target-group ARN is not the exact website target")

    @property
    def cluster(self) -> str:
        return self.cluster_arn or (
            f"arn:aws:ecs:{self.region}:{self.account_id}:cluster/website-sandbox"
        )

    @property
    def web_service(self) -> str:
        return self.web_service_name or "website-sandbox-web"

    @property
    def state_bucket(self) -> str:
        return STATE_BUCKET


class OidcProbe:
    def __init__(self, config: ProbeConfig, clients: dict[str, Any] | None = None) -> None:
        self.config = config
        self._clients = clients or {}
        self._caller_arn: str | None = None
        self._denial_index = 0

    def _client(self, service: str, *, region: str | None = None) -> Any:
        allowed_services = {
            allowed_service for allowed_service, _ in LIVE_CALL_ALLOWLIST[self.config.role]
        }
        if service not in allowed_services:
            raise ReleaseContractError(f"AWS client is outside the OIDC probe allowlist: {service}")
        requested_region = region or self.config.region
        expected_region = "us-east-1" if service == "s3" else self.config.region
        if requested_region != expected_region:
            raise ReleaseContractError(
                f"AWS client region is outside the OIDC probe contract: {service}"
            )
        if service not in self._clients:
            self._clients[service] = boto3.client(service, region_name=requested_region)
        return self._clients[service]

    def _call(
        self,
        service: str,
        method: str,
        *,
        region: str | None = None,
        **arguments: Any,
    ) -> Any:
        if (service, method) not in LIVE_CALL_ALLOWLIST[self.config.role]:
            raise ReleaseContractError(
                f"AWS operation is outside the OIDC probe allowlist: {service}.{method}"
            )
        operation = getattr(self._client(service, region=region), method)
        return operation(**arguments)

    def _record(self, action: str, resource: str, result: str) -> None:
        print(
            json.dumps(
                {
                    "action": action,
                    "caller_arn": self._caller_arn,
                    "resource": resource,
                    "result": result,
                    "role": self.config.role,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                sort_keys=True,
            )
        )

    def _allow(self, action: str, resource: str, operation: Callable[[], Any]) -> Any:
        try:
            response = operation()
        except Exception as error:
            raise ReleaseContractError(
                f"permitted metadata probe failed for {action} ({type(error).__name__})"
            ) from error
        self._record(action, resource, "allowed")
        return response

    def _deny(
        self,
        action: str,
        resource: str,
        operation: Callable[[], Any],
        *,
        denial_codes: frozenset[str],
    ) -> None:
        if self._denial_index >= len(RETAINED_DENIAL_ACTIONS):
            raise ReleaseContractError(f"unexpected extra OIDC denial action: {action}")
        expected_action = RETAINED_DENIAL_ACTIONS[self._denial_index]
        if action != expected_action:
            raise ReleaseContractError(
                f"OIDC denial action is out of order: expected {expected_action}, got {action}"
            )
        self._denial_index += 1
        try:
            operation()
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code not in denial_codes:
                raise ReleaseContractError(
                    f"permission denial was not proven for {action} ({code or 'unknown'})"
                ) from error
            self._record(action, resource, "denied")
            return
        except Exception as error:
            raise ReleaseContractError(
                f"permission denial probe failed for {action} ({type(error).__name__})"
            ) from error
        raise ReleaseContractError(f"unsafe permission unexpectedly allowed: {action}")

    def _identity(self) -> None:
        try:
            response = self._call("sts", "get_caller_identity")
        except Exception as error:
            error_name = type(error).__name__
            raise ReleaseContractError(
                f"permitted metadata probe failed for sts:GetCallerIdentity ({error_name})"
            ) from error
        account = response.get("Account")
        arn = response.get("Arn")
        role_marker = f":assumed-role/{ROLE_NAMES[self.config.role]}/"
        if account != self.config.account_id or not isinstance(arn, str) or role_marker not in arn:
            raise ReleaseContractError("OIDC probe assumed an unexpected caller identity")
        self._caller_arn = arn
        self._record("sts:GetCallerIdentity", arn, "allowed")

    def _allowed_metadata(self) -> None:
        self._allow(
            "ecr:DescribeImages",
            self.config.repository_name,
            lambda: self._call(
                "ecr",
                "describe_images",
                repositoryName=self.config.repository_name,
            ),
        )
        if self.config.role != "deployer":
            return

        cluster_response = self._allow(
            "ecs:DescribeClusters",
            self.config.cluster,
            lambda: self._call(
                "ecs",
                "describe_clusters",
                clusters=[self.config.cluster],
            ),
        )
        if cluster_response.get("failures") or len(cluster_response.get("clusters", [])) != 1:
            raise ReleaseContractError("exact ECS cluster metadata was not returned")
        service_names = [self.config.web_service_name, self.config.worker_service_name]
        service_response = self._allow(
            "ecs:DescribeServices",
            ",".join(str(name) for name in service_names),
            lambda: self._call(
                "ecs",
                "describe_services",
                cluster=self.config.cluster,
                services=service_names,
            ),
        )
        if service_response.get("failures") or len(service_response.get("services", [])) != 2:
            raise ReleaseContractError("exact ECS service metadata was not returned")
        for family in self.config.task_families:

            def describe_task_definition(selected_family: str = family) -> Any:
                return self._call(
                    "ecs",
                    "describe_task_definition",
                    taskDefinition=selected_family,
                )

            response = self._allow(
                "ecs:DescribeTaskDefinition",
                family,
                describe_task_definition,
            )
            if not response.get("taskDefinition"):
                raise ReleaseContractError("exact ECS task-definition metadata was not returned")
        self._allow(
            "ecs:ListTasks",
            self.config.cluster,
            lambda: self._call(
                "ecs",
                "list_tasks",
                cluster=self.config.cluster,
                desiredStatus="RUNNING",
            ),
        )
        self._allow(
            "elasticloadbalancing:DescribeTargetHealth",
            str(self.config.web_target_group_arn),
            lambda: self._call(
                "elbv2",
                "describe_target_health",
                TargetGroupArn=self.config.web_target_group_arn,
            ),
        )

    def _denied_boundaries(self) -> None:
        self._deny(
            "s3:GetObject",
            f"s3://{STATE_BUCKET}/{STATE_KEY}",
            lambda: self._call(
                "s3",
                "head_object",
                region="us-east-1",
                Bucket=STATE_BUCKET,
                Key=STATE_KEY,
                ExpectedBucketOwner=STATE_BUCKET_OWNER,
            ),
            denial_codes=S3_DENIAL_CODES,
        )
        synthetic_dns_name = f"oidc-denial-probe-{self.config.probe_id}.dtcdev.click."
        synthetic_dns_value = f'"oidc-denial-probe-{self.config.probe_id}"'
        duplicate_delete = {
            "Action": "DELETE",
            "ResourceRecordSet": {
                "Name": synthetic_dns_name,
                "Type": "TXT",
                "TTL": 60,
                "ResourceRecords": [{"Value": synthetic_dns_value}],
            },
        }
        self._deny(
            "route53:ChangeResourceRecordSets",
            self.config.hosted_zone_id,
            lambda: self._call(
                "route53",
                "change_resource_record_sets",
                HostedZoneId=self.config.hosted_zone_id,
                ChangeBatch={
                    "Comment": "OIDC denial probe",
                    "Changes": [duplicate_delete, duplicate_delete],
                },
            ),
            denial_codes=ROUTE53_DENIAL_CODES,
        )
        self._deny(
            "kms:CreateGrant",
            self.config.kms_key_arn,
            lambda: self._call(
                "kms",
                "create_grant",
                KeyId=self.config.kms_key_arn,
                GranteePrincipal=(
                    f"arn:aws:iam::{self.config.account_id}:role/{ROLE_NAMES[self.config.role]}"
                ),
                Operations=["Decrypt"],
                Name=f"oidc-denial-probe-{self.config.role}-{self.config.probe_id}",
                DryRun=True,
            ),
            denial_codes=KMS_DENIAL_CODES,
        )
        self._deny(
            "ecr:BatchDeleteImage",
            self.config.repository_name,
            lambda: self._call(
                "ecr",
                "batch_delete_image",
                repositoryName=self.config.repository_name,
                imageIds=[{"imageDigest": f"sha256:{'0' * 64}"}],
            ),
            denial_codes=ECR_DELETE_DENIAL_CODES,
        )
        if self._denial_index != len(RETAINED_DENIAL_ACTIONS):
            raise ReleaseContractError("OIDC denial sequence is incomplete")

    def run(self) -> None:
        self._identity()
        self._allowed_metadata()
        self._denied_boundaries()


class _StoreOnceAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self.dest, None) is not None:
            parser.error(f"{option_string} may be specified only once")
        setattr(namespace, self.dest, values)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the non-mutating sandbox OIDC probe")
    parser.add_argument("role", choices=sorted(ROLE_NAMES))
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--repository-name", required=True)
    parser.add_argument("--probe-id", required=True)
    parser.add_argument("--hosted-zone-id", required=True, action=_StoreOnceAction)
    parser.add_argument("--kms-key-arn", required=True, action=_StoreOnceAction)
    parser.add_argument("--cluster-arn")
    parser.add_argument("--web-target-group-arn")
    parser.add_argument("--web-service-name")
    parser.add_argument("--worker-service-name")
    parser.add_argument("--task-family", action="append", default=[])
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    try:
        config = ProbeConfig(
            role=arguments.role,
            account_id=arguments.account_id,
            region=arguments.region,
            repository_name=arguments.repository_name,
            probe_id=arguments.probe_id,
            hosted_zone_id=arguments.hosted_zone_id,
            kms_key_arn=arguments.kms_key_arn,
            cluster_arn=arguments.cluster_arn,
            web_target_group_arn=arguments.web_target_group_arn,
            web_service_name=arguments.web_service_name,
            worker_service_name=arguments.worker_service_name,
            task_families=tuple(arguments.task_family),
        )
        OidcProbe(config).run()
    except ReleaseContractError as error:
        print(f"OIDC probe failed safely: {error}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
