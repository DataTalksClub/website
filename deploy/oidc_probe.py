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

DENIAL_CODES = {"403", "AccessDenied", "AccessDeniedException", "UnauthorizedOperation"}
STATE_KEY = "sandbox/website/terraform.tfstate"
DATABASE_IDENTIFIER = "website-sandbox"
ROUTE53_HOSTED_ZONE_ID = "Z05963572WVWFHDQZH5NE"
ROLE_NAMES = {
    "publisher": "website-sandbox-github-publisher",
    "deployer": "website-sandbox-github-deployer",
}
WEB_TARGET_GROUP_ARN_PATTERN = re.compile(
    r"^arn:aws:elasticloadbalancing:eu-west-1:817685572750:"
    r"targetgroup/website-sandbox-web/[0-9a-f]{16}$"
)


@dataclass(frozen=True)
class ProbeConfig:
    role: str
    account_id: str
    region: str
    repository_name: str
    probe_id: str
    hosted_zone_id: str
    cluster_arn: str | None = None
    web_target_group_arn: str | None = None
    web_service_name: str | None = None
    worker_service_name: str | None = None
    task_families: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.role not in ROLE_NAMES:
            raise ReleaseContractError("OIDC probe role must be publisher or deployer")
        if not re.fullmatch(r"[0-9]{12}", self.account_id):
            raise ReleaseContractError("OIDC probe account ID must contain 12 digits")
        if not re.fullmatch(r"[a-z]{2}-[a-z]+-[0-9]", self.region):
            raise ReleaseContractError("OIDC probe region is invalid")
        if not self.repository_name or not re.fullmatch(r"[0-9]{1,12}", self.probe_id):
            raise ReleaseContractError("OIDC probe identifiers are invalid")
        if self.hosted_zone_id != ROUTE53_HOSTED_ZONE_ID:
            raise ReleaseContractError("OIDC probe hosted-zone ID is not the exact sandbox zone")
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
        return f"datamailer-sandbox-{self.account_id}-us-east-1-tfstate"


class OidcProbe:
    def __init__(self, config: ProbeConfig, clients: dict[str, Any] | None = None) -> None:
        self.config = config
        self._clients = clients or {}
        self._caller_arn: str | None = None

    def _client(self, service: str, *, region: str | None = None) -> Any:
        if service not in self._clients:
            self._clients[service] = boto3.client(service, region_name=region or self.config.region)
        return self._clients[service]

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

    def _deny(self, action: str, resource: str, operation: Callable[[], Any]) -> None:
        try:
            operation()
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code not in DENIAL_CODES:
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
            response = self._client("sts").get_caller_identity()
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
        ecr = self._client("ecr")
        self._allow(
            "ecr:DescribeImages",
            self.config.repository_name,
            lambda: ecr.describe_images(repositoryName=self.config.repository_name),
        )
        if self.config.role != "deployer":
            return

        ecs = self._client("ecs")
        cluster_response = self._allow(
            "ecs:DescribeClusters",
            self.config.cluster,
            lambda: ecs.describe_clusters(clusters=[self.config.cluster]),
        )
        if cluster_response.get("failures") or len(cluster_response.get("clusters", [])) != 1:
            raise ReleaseContractError("exact ECS cluster metadata was not returned")
        service_names = [self.config.web_service_name, self.config.worker_service_name]
        service_response = self._allow(
            "ecs:DescribeServices",
            ",".join(str(name) for name in service_names),
            lambda: ecs.describe_services(
                cluster=self.config.cluster,
                services=service_names,
            ),
        )
        if service_response.get("failures") or len(service_response.get("services", [])) != 2:
            raise ReleaseContractError("exact ECS service metadata was not returned")
        for family in self.config.task_families:

            def describe_task_definition(selected_family: str = family) -> Any:
                return ecs.describe_task_definition(taskDefinition=selected_family)

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
            lambda: ecs.list_tasks(cluster=self.config.cluster, desiredStatus="RUNNING"),
        )
        self._allow(
            "elasticloadbalancing:DescribeTargetHealth",
            str(self.config.web_target_group_arn),
            lambda: self._client("elbv2").describe_target_health(
                TargetGroupArn=self.config.web_target_group_arn
            ),
        )

    def _denied_boundaries(self) -> None:
        foreign_repository = f"website-sandbox-foreign-probe-{self.config.probe_id}"
        production_repository = f"website-production-probe-{self.config.probe_id}"
        for repository in (foreign_repository, production_repository):

            def describe_denied_repository(selected_repository: str = repository) -> Any:
                return self._client("ecr").describe_images(repositoryName=selected_repository)

            self._deny(
                "ecr:DescribeImages",
                repository,
                describe_denied_repository,
            )
        self._deny(
            "s3:GetObject",
            f"s3://{self.config.state_bucket}/{STATE_KEY}",
            lambda: self._client("s3", region="us-east-1").head_object(
                Bucket=self.config.state_bucket,
                Key=STATE_KEY,
            ),
        )
        self._deny(
            "iam:UpdateRoleDescription",
            f"website-sandbox-probe-denied-{self.config.probe_id}",
            lambda: self._client("iam").update_role_description(
                RoleName=f"website-sandbox-probe-denied-{self.config.probe_id}",
                Description="OIDC denial probe",
            ),
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
            lambda: self._client("route53").change_resource_record_sets(
                HostedZoneId=self.config.hosted_zone_id,
                ChangeBatch={
                    "Comment": "OIDC denial probe",
                    "Changes": [duplicate_delete, duplicate_delete],
                },
            ),
        )

        distribution_id = "E0000000000000"
        self._deny(
            "cloudfront:CreateInvalidation",
            distribution_id,
            lambda: self._client("cloudfront", region="us-east-1").create_invalidation(
                DistributionId=distribution_id,
                InvalidationBatch={
                    "Paths": {"Quantity": 1, "Items": ["/*"]},
                    "CallerReference": f"denied-{self.config.probe_id}",
                },
            ),
        )

        target_group = (
            "arn:aws:elasticloadbalancing:"
            f"{self.config.region}:{self.config.account_id}:"
            f"targetgroup/probe-denied-{self.config.probe_id}/0000000000000000"
        )
        self._deny(
            "elasticloadbalancing:ModifyTargetGroupAttributes",
            target_group,
            lambda: self._client("elbv2").modify_target_group_attributes(
                TargetGroupArn=target_group,
                Attributes=[{"Key": "deregistration_delay.timeout_seconds", "Value": "300"}],
            ),
        )
        self._deny(
            "rds:ModifyDBInstance",
            f"{DATABASE_IDENTIFIER}-probe-denied-{self.config.probe_id}",
            lambda: self._client("rds").modify_db_instance(
                DBInstanceIdentifier=f"{DATABASE_IDENTIFIER}-probe-denied-{self.config.probe_id}",
                BackupRetentionPeriod=7,
                ApplyImmediately=False,
            ),
        )
        self._deny(
            "kms:CreateGrant",
            "00000000-0000-0000-0000-000000000000",
            lambda: self._client("kms").create_grant(
                KeyId="00000000-0000-0000-0000-000000000000",
                GranteePrincipal=(
                    f"arn:aws:iam::{self.config.account_id}:"
                    f"role/website-sandbox-probe-denied-{self.config.probe_id}"
                ),
                Operations=["Decrypt"],
                Name=f"probe-denied-{self.config.probe_id}",
            ),
        )
        missing_secret_name = f"website-sandbox/probe-denied-{self.config.probe_id}"
        self._deny(
            "secretsmanager:GetSecretValue",
            missing_secret_name,
            lambda: self._client("secretsmanager").get_secret_value(
                SecretId=missing_secret_name,
            ),
        )

        task_family = (
            self.config.task_families[0] if self.config.task_families else "website-sandbox-web"
        )
        self._deny(
            "ecs:DeregisterTaskDefinition",
            f"{task_family}:999999999",
            lambda: self._client("ecs").deregister_task_definition(
                taskDefinition=f"{task_family}:999999999"
            ),
        )
        self._deny(
            "ecr:BatchDeleteImage",
            self.config.repository_name,
            lambda: self._client("ecr").batch_delete_image(
                repositoryName=self.config.repository_name,
                imageIds=[{"imageDigest": f"sha256:{'0' * 64}"}],
            ),
        )
        if self.config.role == "deployer":
            foreign_cluster = (
                f"arn:aws:ecs:{self.config.region}:{self.config.account_id}:"
                f"cluster/website-sandbox-foreign-probe-{self.config.probe_id}"
            )
            foreign_service = f"website-sandbox-foreign-probe-{self.config.probe_id}"
            production_cluster = (
                f"arn:aws:ecs:{self.config.region}:{self.config.account_id}:"
                f"cluster/website-production-probe-{self.config.probe_id}"
            )
            production_service = f"website-production-probe-{self.config.probe_id}"
            ecs = self._client("ecs")
            for cluster, service in (
                (foreign_cluster, foreign_service),
                (production_cluster, production_service),
            ):

                def describe_denied_service(
                    selected_cluster: str = cluster,
                    selected_service: str = service,
                ) -> Any:
                    return ecs.describe_services(
                        cluster=selected_cluster,
                        services=[selected_service],
                    )

                def update_denied_service(
                    selected_cluster: str = cluster,
                    selected_service: str = service,
                ) -> Any:
                    return ecs.update_service(
                        cluster=selected_cluster,
                        service=selected_service,
                        desiredCount=0,
                    )

                self._deny(
                    "ecs:DescribeServices",
                    f"{cluster}/{service}",
                    describe_denied_service,
                )
                self._deny(
                    "ecs:UpdateService",
                    f"{cluster}/{service}",
                    update_denied_service,
                )

            foreign_family = f"website-sandbox-foreign-probe-{self.config.probe_id}:999999999"
            production_family = f"website-production-probe-{self.config.probe_id}:999999999"
            for family in (foreign_family, production_family):

                def run_denied_task(selected_family: str = family) -> Any:
                    return ecs.run_task(
                        cluster=self.config.cluster,
                        taskDefinition=selected_family,
                        count=1,
                        launchType="FARGATE",
                        networkConfiguration={
                            "awsvpcConfiguration": {
                                "subnets": ["subnet-00000000000000000"],
                                "securityGroups": ["sg-00000000000000000"],
                                "assignPublicIp": "DISABLED",
                            }
                        },
                    )

                self._deny(
                    "ecs:RunTask",
                    family,
                    run_denied_task,
                )

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
