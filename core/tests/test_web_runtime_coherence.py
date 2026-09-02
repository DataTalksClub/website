from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from core.runtime_identity import read_runtime_identity
from deploy.aws_gateway import AwsReleaseConfig, AwsReleaseGateway
from deploy.contracts import (
    ReleaseContractError,
    ReleaseIdentity,
    ServicePredecessor,
    ServiceTarget,
    ServiceUpdateReceipt,
    WebRuntimeBinding,
)
from deploy.deployment_targets import SELECTED_TARGET
from test_support.safety import authorize_from_environment

SOURCE_SHA = "a" * 40
IMAGE_DIGEST = f"sha256:{'b' * 64}"
VERSION = f"20260809-143205-{SOURCE_SHA[:7]}"
NAMESPACE = SELECTED_TARGET.resource_namespace
WEB_DEFINITION = SELECTED_TARGET.task_definition_arn_prefix(SELECTED_TARGET.web_task_family) + "14"
OLD_WEB_DEFINITION = (
    SELECTED_TARGET.task_definition_arn_prefix(SELECTED_TARGET.web_task_family) + "13"
)
WORKER_DEFINITION = (
    SELECTED_TARGET.task_definition_arn_prefix(SELECTED_TARGET.worker_task_family) + "14"
)
OLD_WORKER_DEFINITION = (
    SELECTED_TARGET.task_definition_arn_prefix(SELECTED_TARGET.worker_task_family) + "13"
)
TASK_PREFIX = (
    f"arn:aws:ecs:{SELECTED_TARGET.aws_region}:{SELECTED_TARGET.aws_account_id}:task/{NAMESPACE}/"
)
TASK_ARN = TASK_PREFIX + "1" * 32
STALE_TASK_ARN = TASK_PREFIX + "2" * 32
REPLACEMENT_TASK_ARN = TASK_PREFIX + "3" * 32
PRIVATE_ADDRESS = "10.0.1.17"
TARGET_PORT = 8000


class FakeClock:
    def __init__(self) -> None:
        self.current = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += seconds


def service_document() -> dict[str, Any]:
    return {
        "serviceName": SELECTED_TARGET.web_service_name,
        "taskDefinition": WEB_DEFINITION,
        "desiredCount": 1,
        "runningCount": 1,
        "pendingCount": 0,
        "deployments": [
            {
                "id": "ecs-svc/web-14",
                "status": "PRIMARY",
                "taskDefinition": WEB_DEFINITION,
                "desiredCount": 1,
                "runningCount": 1,
                "pendingCount": 0,
                "failedTasks": 0,
                "rolloutState": "COMPLETED",
            },
            {
                "id": "ecs-svc/web-13",
                "status": "DRAINING",
                "taskDefinition": OLD_WEB_DEFINITION,
                "desiredCount": 0,
                "runningCount": 0,
                "pendingCount": 0,
                "failedTasks": 0,
                "rolloutState": "COMPLETED",
            },
        ],
    }


def receipt() -> ServiceUpdateReceipt:
    return ServiceUpdateReceipt(
        workload="web",
        configured_service_identity=SELECTED_TARGET.web_service_name,
        target=ServiceTarget(WEB_DEFINITION, 1),
        primary_deployment_id="ecs-svc/web-14",
        predecessors=(
            ServicePredecessor(
                target=ServiceTarget(OLD_WEB_DEFINITION, 1),
                primary_deployment_id="ecs-svc/web-13",
                role="terminal",
            ),
        ),
    )


def identity() -> ReleaseIdentity:
    return ReleaseIdentity(SOURCE_SHA, IMAGE_DIGEST, SELECTED_TARGET.ecr_repository_uri, VERSION)


def task_definition() -> dict[str, Any]:
    return {
        "taskDefinitionArn": WEB_DEFINITION,
        "status": "ACTIVE",
        "networkMode": "awsvpc",
        "containerDefinitions": [
            {
                "name": "web",
                "image": f"{SELECTED_TARGET.ecr_repository_uri}@{IMAGE_DIGEST}",
                "environment": [
                    {"name": "IMAGE_DIGEST", "value": IMAGE_DIGEST},
                    {"name": "SOURCE_SHA", "value": SOURCE_SHA},
                    {"name": "VERSION", "value": VERSION},
                ],
                "portMappings": [
                    {
                        "containerPort": TARGET_PORT,
                        "hostPort": TARGET_PORT,
                        "protocol": "tcp",
                    }
                ],
            }
        ],
    }


def running_task(task_arn: str = TASK_ARN) -> dict[str, Any]:
    return {
        "taskArn": task_arn,
        "taskDefinitionArn": WEB_DEFINITION,
        "desiredStatus": "RUNNING",
        "lastStatus": "RUNNING",
        "healthStatus": "HEALTHY",
        "overrides": {"containerOverrides": [{"name": "web"}]},
        "attachments": [
            {
                "id": "attachment-web",
                "type": "ElasticNetworkInterface",
                "status": "ATTACHED",
                "details": [
                    {"name": "networkInterfaceId", "value": "eni-0123456789abcdef0"},
                    {"name": "privateIPv4Address", "value": PRIVATE_ADDRESS},
                ],
            }
        ],
        "containers": [
            {
                "name": "web",
                "healthStatus": "HEALTHY",
                "image": f"{SELECTED_TARGET.ecr_repository_uri}@{IMAGE_DIGEST}",
                "imageDigest": IMAGE_DIGEST,
                "networkInterfaces": [
                    {
                        "attachmentId": "attachment-web",
                        "privateIpv4Address": PRIVATE_ADDRESS,
                    }
                ],
                "networkBindings": [{"containerPort": TARGET_PORT, "hostPort": TARGET_PORT}],
            }
        ],
    }


def stopped_task() -> dict[str, Any]:
    task = running_task(STALE_TASK_ARN)
    task["desiredStatus"] = "STOPPED"
    task["lastStatus"] = "STOPPED"
    task["healthStatus"] = "UNKNOWN"
    return task


def set_runtime_address(task: dict[str, Any], address: object) -> None:
    task["attachments"][0]["details"][1]["value"] = address
    task["containers"][0]["networkInterfaces"][0]["privateIpv4Address"] = address


def target_health(
    state: str = "healthy",
    *,
    address: str = PRIVATE_ADDRESS,
    port: int = TARGET_PORT,
    reason: str | None = None,
) -> dict[str, Any]:
    health: dict[str, Any] = {"State": state}
    if reason is not None:
        health["Reason"] = reason
        health["Description"] = reason
    return {
        "TargetHealthDescriptions": [
            {"Target": {"Id": address, "Port": port}, "TargetHealth": health}
        ]
    }


def config(*, poll_seconds: int = 10, timeout_seconds: int = 180) -> AwsReleaseConfig:
    return AwsReleaseConfig(
        region="eu-west-1",
        cluster_arn=SELECTED_TARGET.ecs_cluster_arn,
        web_target_group_arn=(
            f"arn:aws:elasticloadbalancing:{SELECTED_TARGET.aws_region}:"
            f"{SELECTED_TARGET.aws_account_id}:targetgroup/{NAMESPACE}-web/0123456789abcdef"
        ),
        service_names={
            "web": SELECTED_TARGET.web_service_name,
            "worker": SELECTED_TARGET.worker_service_name,
        },
        task_families={
            "web": SELECTED_TARGET.web_task_family,
            "worker": SELECTED_TARGET.worker_task_family,
            "migration": SELECTED_TARGET.migration_task_family,
        },
        container_names={"web": "web", "worker": "worker", "migration": "migration"},
        task_role_arn=SELECTED_TARGET.task_role_arn,
        execution_role_arn=SELECTED_TARGET.execution_role_arn,
        subnet_ids=["subnet-0123456789abcdef0"],
        security_group_ids=["sg-0123456789abcdef0"],
        assign_public_ip=True,
        base_url=SELECTED_TARGET.origin,
        screenshot_directory=Path(".tmp/deployed-smoke"),
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )


def gateway_with_sequences(
    task_arn_pages: list[list[str]],
    *,
    task_documents: dict[str, dict[str, Any]] | None = None,
    task_definition_document: dict[str, Any] | None = None,
    target_health_documents: list[dict[str, Any]] | None = None,
    poll_seconds: int = 10,
    timeout_seconds: int = 180,
) -> AwsReleaseGateway:
    gateway = AwsReleaseGateway.__new__(AwsReleaseGateway)
    gateway.config = config(poll_seconds=poll_seconds, timeout_seconds=timeout_seconds)
    gateway.ecs = Mock()
    gateway.ecr = Mock()
    gateway.elbv2 = Mock()
    pages = [list(page) for page in task_arn_pages]

    def list_tasks(**kwargs: Any) -> dict[str, Any]:
        if not pages:
            raise AssertionError("unexpected ListTasks read")
        if kwargs.get("desiredStatus") != "RUNNING" or "nextToken" in kwargs:
            raise AssertionError("ListTasks request differs")
        return {"taskArns": pages.pop(0)}

    documents = task_documents or {
        TASK_ARN: running_task(),
        STALE_TASK_ARN: stopped_task(),
        REPLACEMENT_TASK_ARN: running_task(REPLACEMENT_TASK_ARN),
    }

    def describe_tasks(**kwargs: Any) -> dict[str, Any]:
        requested = kwargs["tasks"]
        return {
            "failures": [],
            "tasks": [copy.deepcopy(documents[task_arn]) for task_arn in requested],
        }

    gateway.ecs.list_tasks.side_effect = list_tasks
    gateway.ecs.describe_tasks.side_effect = describe_tasks
    gateway.ecs.describe_services.side_effect = lambda **_kwargs: {
        "failures": [],
        "services": [service_document()],
    }
    gateway.ecs.describe_task_definition.side_effect = lambda **_kwargs: {
        "taskDefinition": copy.deepcopy(task_definition_document or task_definition())
    }
    health_documents = list(target_health_documents or [target_health()] * 20)
    gateway.elbv2.describe_target_health.side_effect = lambda **_kwargs: copy.deepcopy(
        health_documents.pop(0)
    )
    return gateway


def observed_binding() -> WebRuntimeBinding:
    current_receipt = receipt()
    return WebRuntimeBinding(
        configured_service_identity=current_receipt.configured_service_identity,
        primary_deployment_id=current_receipt.primary_deployment_id,
        task_definition_arn=current_receipt.target.task_definition_arn,
        predecessors=current_receipt.predecessors,
        source_sha=SOURCE_SHA,
        image_digest=IMAGE_DIGEST,
        task_arn=TASK_ARN,
        network_attachment_id="attachment-web",
        network_interface_id="eni-0123456789abcdef0",
        private_ipv4_address=PRIVATE_ADDRESS,
        container_port=TARGET_PORT,
        target_port=TARGET_PORT,
        version=VERSION,
    )


class WebRuntimeCoherenceTests(SimpleTestCase):
    def test_deployed_browser_smoke_receives_complete_runtime_identity(self) -> None:
        gateway = AwsReleaseGateway.__new__(AwsReleaseGateway)
        gateway.config = config()
        observed: dict[str, Any] = {}

        def collect_subprocess(
            command: list[str],
            *,
            check: bool,
            env: dict[str, str],
            timeout: int,
        ) -> None:
            observed.update(
                command=command,
                check=check,
                env=env,
                timeout=timeout,
                runtime_identity=read_runtime_identity(env),
            )

        workflow_environment = {
            "VERSION": VERSION,
            "IMAGE_DIGEST": IMAGE_DIGEST,
            "RELEASE_SHA": SOURCE_SHA,
            "DTC_TEST_SAFETY_COMMAND": "remote_readonly",
            "DTC_TEST_TARGET_CLASS": "isolated_development",
            "DTC_TEST_REMOTE_NAMESPACE": "deploy-12345678-1",
        }
        with (
            patch.dict("os.environ", workflow_environment, clear=True),
            patch("deploy.aws_gateway.run_http_smoke"),
            patch("deploy.aws_gateway.subprocess.run", side_effect=collect_subprocess),
        ):
            gateway.run_deployed_smoke(identity())

        runtime_identity = observed["runtime_identity"]
        self.assertEqual(runtime_identity.version, VERSION)
        self.assertEqual(runtime_identity.source_sha, SOURCE_SHA)
        self.assertEqual(runtime_identity.image_digest, IMAGE_DIGEST)
        environment = cast(dict[str, str], observed["env"])
        self.assertEqual(environment["VERSION"], VERSION)
        self.assertEqual(environment["SOURCE_SHA"], SOURCE_SHA)
        self.assertEqual(environment["IMAGE_DIGEST"], IMAGE_DIGEST)
        self.assertEqual(environment["DTC_EXPECTED_VERSION"], VERSION)
        self.assertEqual(environment["DTC_EXPECTED_SOURCE_SHA"], SOURCE_SHA)
        self.assertEqual(environment["DTC_EXPECTED_IMAGE_DIGEST"], IMAGE_DIGEST)
        self.assertEqual(environment["DTC_TEST_SAFETY_COMMAND"], "remote_readonly")
        self.assertEqual(environment["DTC_TEST_TARGET_CLASS"], "isolated_development")
        self.assertEqual(environment["DTC_TEST_REMOTE_NAMESPACE"], "deploy-12345678-1")
        self.assertEqual(environment["DTC_TEST_BASE_URL"], SELECTED_TARGET.origin)
        with patch.dict("os.environ", environment, clear=True):
            authorization = authorize_from_environment("remote_readonly")
        self.assertEqual(authorization.base_url, SELECTED_TARGET.origin)

    def test_eventual_visibility_freezes_two_samples_around_public_health(self) -> None:
        events: list[str] = []
        clock = FakeClock()
        gateway = gateway_with_sequences(
            [[], [STALE_TASK_ARN], [TASK_ARN], [TASK_ARN]],
        )
        original_list = gateway.ecs.list_tasks.side_effect

        def traced_list(**kwargs: Any) -> dict[str, Any]:
            events.append("task-observation")
            return original_list(**kwargs)

        gateway.ecs.list_tasks.side_effect = traced_list
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
            patch(
                "deploy.aws_gateway.verify_health",
                side_effect=lambda *_args: events.append("public"),
            ),
        ):
            binding = gateway.establish_web_runtime_binding(
                receipt(),
                identity(),
                deadline=180.0,
            )

        self.assertEqual(binding, observed_binding())
        self.assertEqual(events, ["task-observation"] * 3 + ["public", "task-observation"])
        self.assertEqual(clock.sleeps, [10, 10])
        self.assertEqual(
            gateway.ecs.list_tasks.call_args_list[-1].kwargs,
            {
                "cluster": gateway.config.cluster_arn,
                "serviceName": gateway.config.service_names["web"],
                "desiredStatus": "RUNNING",
            },
        )

    def test_inventory_is_fully_paginated_and_validates_membership(self) -> None:
        gateway = gateway_with_sequences([])
        calls: list[dict[str, Any]] = []

        def paginated(**kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            if kwargs.get("nextToken") is None:
                return {"taskArns": [], "nextToken": "page-2"}
            return {"taskArns": [TASK_ARN]}

        gateway.ecs.list_tasks.side_effect = paginated
        with patch("deploy.aws_gateway.time.monotonic", return_value=0.0):
            result = gateway._list_running_web_task_arns(deadline=180.0)
        self.assertEqual(result, [TASK_ARN])
        self.assertEqual(calls[1]["nextToken"], "page-2")

        malformed_responses = [
            {"taskArns": [], "nextToken": "loop"},
            {"taskArns": [], "nextToken": "loop"},
        ]
        gateway.ecs.list_tasks.side_effect = malformed_responses
        with (
            patch("deploy.aws_gateway.time.monotonic", return_value=0.0),
            self.assertRaisesMessage(ReleaseContractError, "pagination"),
        ):
            gateway._list_running_web_task_arns(deadline=180.0)

    def test_task_inventory_provider_and_membership_fail_closed_without_payload(self) -> None:
        canary = "provider-canary-task-arn-private-ip-secret"
        gateway = gateway_with_sequences([[TASK_ARN]])
        gateway.ecs.list_tasks.side_effect = RuntimeError(canary)
        with (
            patch("deploy.aws_gateway.time.monotonic", return_value=0.0),
            self.assertRaises(ReleaseContractError) as caught,
        ):
            gateway._list_running_web_task_arns(deadline=180.0)
        self.assertNotIn(canary, str(caught.exception))

        gateway = gateway_with_sequences([[TASK_ARN]])
        gateway.ecs.describe_tasks.return_value = {
            "failures": [{"arn": canary, "reason": canary}],
            "tasks": [],
        }
        gateway.ecs.describe_tasks.side_effect = None
        with (
            patch("deploy.aws_gateway.time.monotonic", return_value=0.0),
            self.assertRaisesMessage(ReleaseContractError, "descriptions failed"),
        ):
            gateway._describe_running_web_tasks([TASK_ARN], deadline=180.0)

    def test_task_and_definition_contradictions_fail_immediately(self) -> None:
        mutations: dict[str, Callable[[dict[str, Any], dict[str, Any]], None]] = {
            "wrong task definition": lambda task, _definition: task.__setitem__(
                "taskDefinitionArn", OLD_WEB_DEFINITION
            ),
            "pending task": lambda task, _definition: task.__setitem__("lastStatus", "PENDING"),
            "unhealthy task": lambda task, _definition: task.__setitem__(
                "healthStatus", "UNHEALTHY"
            ),
            "missing container": lambda task, _definition: task.__setitem__("containers", []),
            "duplicate container": lambda task, _definition: task["containers"].append(
                copy.deepcopy(task["containers"][0])
            ),
            "wrong runtime digest": lambda task, _definition: task["containers"][0].__setitem__(
                "imageDigest", f"sha256:{'c' * 64}"
            ),
            "missing runtime digest": lambda task, _definition: task["containers"][0].pop(
                "imageDigest"
            ),
            "runtime APP_VERSION override": lambda task, _definition: task["overrides"].update(
                {
                    "containerOverrides": [
                        {
                            "name": "web",
                            "environment": [{"name": "APP_VERSION", "value": "c" * 40}],
                        }
                    ]
                }
            ),
            "wrong APP_VERSION": lambda _task, definition: definition["containerDefinitions"][
                0
            ].__setitem__("environment", [{"name": "APP_VERSION", "value": "c" * 40}]),
            "missing APP_VERSION": lambda _task, definition: definition["containerDefinitions"][
                0
            ].__setitem__("environment", []),
            "duplicate APP_VERSION": lambda _task, definition: definition["containerDefinitions"][
                0
            ]["environment"].append({"name": "APP_VERSION", "value": SOURCE_SHA}),
            "missing definition container": lambda _task, definition: definition.__setitem__(
                "containerDefinitions", []
            ),
            "duplicate definition container": lambda _task, definition: definition[
                "containerDefinitions"
            ].append(copy.deepcopy(definition["containerDefinitions"][0])),
            "wrong definition ARN": lambda _task, definition: definition.__setitem__(
                "taskDefinitionArn", OLD_WEB_DEFINITION
            ),
            "wrong definition image": lambda _task, definition: definition["containerDefinitions"][
                0
            ].__setitem__("image", f"{SELECTED_TARGET.ecr_repository_uri}@sha256:{'c' * 64}"),
            "malformed overrides": lambda task, _definition: task.__setitem__(
                "overrides", {"containerOverrides": "not-a-list"}
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                task = running_task()
                definition = task_definition()
                mutate(task, definition)
                gateway = gateway_with_sequences(
                    [[TASK_ARN]],
                    task_documents={TASK_ARN: task},
                    task_definition_document=definition,
                )
                with (
                    patch("deploy.aws_gateway.time.monotonic", return_value=0.0),
                    self.assertRaises(ReleaseContractError),
                ):
                    gateway._observe_web_runtime_once(
                        receipt(),
                        identity(),
                        deadline=180.0,
                        frozen_binding=None,
                    )
                self.assertEqual(gateway.ecs.list_tasks.call_count, 1)

    def test_duplicate_active_task_cannot_be_ignored(self) -> None:
        second = running_task(REPLACEMENT_TASK_ARN)
        gateway = gateway_with_sequences(
            [[TASK_ARN, REPLACEMENT_TASK_ARN]],
            task_documents={TASK_ARN: running_task(), REPLACEMENT_TASK_ARN: second},
        )
        with (
            patch("deploy.aws_gateway.time.monotonic", return_value=0.0),
            self.assertRaisesMessage(ReleaseContractError, "active task count"),
        ):
            gateway._observe_web_runtime_once(
                receipt(), identity(), deadline=180.0, frozen_binding=None
            )

    def test_network_and_target_contradictions_fail_closed(self) -> None:
        task_mutations: dict[str, Callable[[dict[str, Any]], None]] = {
            "missing ENI": lambda task: task.__setitem__("attachments", []),
            "duplicate ENI": lambda task: task["attachments"].append(
                copy.deepcopy(task["attachments"][0])
            ),
            "detached ENI": lambda task: task["attachments"][0].__setitem__("status", "DETACHED"),
            "interface disagreement": lambda task: task["containers"][0]["networkInterfaces"][
                0
            ].__setitem__("privateIpv4Address", "10.0.1.99"),
            "duplicate runtime port": lambda task: task["containers"][0]["networkBindings"].append(
                {"containerPort": TARGET_PORT, "hostPort": TARGET_PORT}
            ),
            "wrong runtime host port": lambda task: task["containers"][0]["networkBindings"][
                0
            ].__setitem__("hostPort", 9000),
        }
        for name, mutate in task_mutations.items():
            with self.subTest(name=name):
                task = running_task()
                mutate(task)
                gateway = gateway_with_sequences([[TASK_ARN]], task_documents={TASK_ARN: task})
                with (
                    patch("deploy.aws_gateway.time.monotonic", return_value=0.0),
                    self.assertRaises(ReleaseContractError),
                ):
                    gateway._observe_web_runtime_once(
                        receipt(), identity(), deadline=180.0, frozen_binding=None
                    )

        missing_target = gateway_with_sequences(
            [[TASK_ARN]], target_health_documents=[{"TargetHealthDescriptions": []}]
        )
        with patch("deploy.aws_gateway.time.monotonic", return_value=0.0):
            self.assertIsNone(
                missing_target._observe_web_runtime_once(
                    receipt(), identity(), deadline=180.0, frozen_binding=None
                )
            )

        target_cases = {
            "duplicate tuple": {
                "TargetHealthDescriptions": target_health()["TargetHealthDescriptions"] * 2
            },
            "wrong healthy target": target_health(address="10.0.1.99"),
            "alien initial target": {
                "TargetHealthDescriptions": [
                    *target_health()["TargetHealthDescriptions"],
                    *target_health("initial", address="10.0.1.99")["TargetHealthDescriptions"],
                ]
            },
            "wrong port": target_health(port=9000),
        }
        for name, target_document in target_cases.items():
            with self.subTest(name=name):
                gateway = gateway_with_sequences(
                    [[TASK_ARN]], target_health_documents=[target_document]
                )
                with (
                    patch("deploy.aws_gateway.time.monotonic", return_value=0.0),
                    self.assertRaises(ReleaseContractError),
                ):
                    gateway._observe_web_runtime_once(
                        receipt(), identity(), deadline=180.0, frozen_binding=None
                    )

    def test_consistent_non_rfc1918_addresses_fail_immediately_and_redacted(self) -> None:
        invalid_addresses: dict[str, object] = {
            "below 10/8": "9.255.255.255",
            "above 10/8": "11.0.0.0",
            "CGNAT shared": "100.64.0.1",
            "loopback": "127.0.0.1",
            "below 172.16/12": "172.15.255.255",
            "above 172.16/12": "172.32.0.0",
            "link local": "169.254.1.1",
            "documentation TEST-NET-1": "192.0.2.1",
            "documentation TEST-NET-2": "198.51.100.1",
            "documentation TEST-NET-3": "203.0.113.5",
            "below 192.168/16": "192.167.255.255",
            "above 192.168/16": "192.169.0.0",
            "unspecified": "0.0.0.0",
            "multicast": "224.0.0.1",
            "reserved": "240.0.0.1",
            "limited broadcast": "255.255.255.255",
            "global": "8.8.8.8",
            "IPv6 unique local": "fd00::1",
            "malformed": "not-an-ip-address",
            "null": None,
            "integer": 167_772_161,
            "bytes": b"10.0.0.1",
        }
        for name, address in invalid_addresses.items():
            with self.subTest(name=name, address=address):
                task = running_task()
                set_runtime_address(task, address)
                target_document = target_health()
                target_document["TargetHealthDescriptions"][0]["Target"]["Id"] = address
                self.assertEqual(
                    task["attachments"][0]["details"][1]["value"],
                    task["containers"][0]["networkInterfaces"][0]["privateIpv4Address"],
                )
                self.assertEqual(
                    target_document["TargetHealthDescriptions"][0]["Target"]["Id"],
                    address,
                )
                gateway = gateway_with_sequences(
                    [[TASK_ARN]],
                    task_documents={TASK_ARN: task},
                    target_health_documents=[target_document],
                )
                expected_message = (
                    "web runtime private address is malformed"
                    if isinstance(address, str)
                    else "web runtime attached network interface is malformed"
                )
                with (
                    patch("deploy.aws_gateway.time.monotonic", return_value=0.0),
                    patch("deploy.aws_gateway.time.sleep") as sleep,
                    patch("deploy.aws_gateway.verify_health") as public_health,
                    self.assertRaisesMessage(
                        ReleaseContractError,
                        expected_message,
                    ) as caught,
                ):
                    gateway.establish_web_runtime_binding(
                        receipt(),
                        identity(),
                        deadline=180.0,
                    )
                self.assertNotIn(str(address), str(caught.exception))
                self.assertEqual(caught.exception.reason_code, "contract_contradiction")
                self.assertEqual(gateway.ecs.list_tasks.call_count, 1)
                self.assertEqual(gateway.ecs.describe_tasks.call_count, 1)
                gateway.elbv2.describe_target_health.assert_not_called()
                public_health.assert_not_called()
                sleep.assert_not_called()
                with self.assertRaisesMessage(
                    ReleaseContractError,
                    "web runtime private address is malformed",
                ):
                    replace(
                        observed_binding(),
                        private_ipv4_address=cast(str, address),
                    )

    def test_literal_rfc1918_boundaries_keep_stable_redacted_bindings(self) -> None:
        valid_boundaries = (
            "10.0.0.0",
            "10.255.255.255",
            "172.16.0.0",
            "172.31.255.255",
            "192.168.0.0",
            "192.168.255.255",
        )
        fingerprints: set[str] = set()
        for address in valid_boundaries:
            with self.subTest(address=address):
                task = running_task()
                set_runtime_address(task, address)
                gateway = gateway_with_sequences(
                    [[TASK_ARN]],
                    task_documents={TASK_ARN: task},
                    target_health_documents=[target_health(address=address)],
                )
                with patch("deploy.aws_gateway.time.monotonic", return_value=0.0):
                    binding = gateway._observe_web_runtime_once(
                        receipt(), identity(), deadline=180.0, frozen_binding=None
                    )
                assert binding is not None
                self.assertEqual(binding.private_ipv4_address, address)
                self.assertRegex(binding.fingerprint, r"^[0-9a-f]{64}$")
                self.assertEqual(binding.fingerprint, replace(binding).fingerprint)
                self.assertNotIn(
                    address,
                    json.dumps(
                        binding.safe_evidence(
                            observation_count=1,
                            deadline_budget_seconds=180,
                        ),
                        sort_keys=True,
                    ),
                )
                fingerprints.add(binding.fingerprint)
        self.assertEqual(len(fingerprints), len(valid_boundaries))

    def test_candidate_target_converges_before_binding_but_not_after_freeze(self) -> None:
        clock = FakeClock()
        gateway = gateway_with_sequences(
            [[TASK_ARN], [TASK_ARN], [TASK_ARN]],
            target_health_documents=[
                target_health("initial"),
                target_health("healthy"),
                target_health("healthy"),
            ],
        )
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
            patch("deploy.aws_gateway.verify_health"),
        ):
            gateway.establish_web_runtime_binding(receipt(), identity(), deadline=180.0)
        self.assertEqual(clock.sleeps, [10])

        gateway = gateway_with_sequences(
            [[TASK_ARN]], target_health_documents=[target_health("initial")]
        )
        with (
            patch("deploy.aws_gateway.time.monotonic", return_value=0.0),
            self.assertRaisesMessage(ReleaseContractError, "no longer healthy"),
        ):
            gateway.revalidate_web_runtime_binding(observed_binding(), deadline=420.0)

    def test_frozen_binding_rejects_task_eni_digest_and_target_replacement(self) -> None:
        changes: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}

        replacement_task = running_task(REPLACEMENT_TASK_ARN)
        changes["task ARN"] = (
            {REPLACEMENT_TASK_ARN: replacement_task},
            target_health(),
        )
        replacement_eni = running_task()
        replacement_eni["attachments"][0]["details"][0]["value"] = "eni-0fedcba9876543210"
        changes["ENI"] = ({TASK_ARN: replacement_eni}, target_health())
        replacement_digest = running_task()
        replacement_digest["containers"][0]["imageDigest"] = f"sha256:{'c' * 64}"
        changes["digest"] = ({TASK_ARN: replacement_digest}, target_health())
        changes["target"] = ({TASK_ARN: running_task()}, target_health(address="10.0.1.99"))

        for name, (documents, target_document) in changes.items():
            with self.subTest(name=name):
                task_arn = next(iter(documents))
                gateway = gateway_with_sequences(
                    [[task_arn]],
                    task_documents=documents,
                    target_health_documents=[target_document],
                )
                with (
                    patch("deploy.aws_gateway.time.monotonic", return_value=0.0),
                    self.assertRaises(ReleaseContractError),
                ):
                    gateway.revalidate_web_runtime_binding(observed_binding(), deadline=420.0)

    def test_frozen_missing_task_is_retryable_but_replacement_is_not(self) -> None:
        missing = gateway_with_sequences([[]])
        with patch("deploy.aws_gateway.time.monotonic", return_value=0.0):
            self.assertFalse(
                missing.revalidate_web_runtime_binding(observed_binding(), deadline=420.0)
            )

        replacement = gateway_with_sequences(
            [[REPLACEMENT_TASK_ARN]],
            task_documents={REPLACEMENT_TASK_ARN: running_task(REPLACEMENT_TASK_ARN)},
        )
        with (
            patch("deploy.aws_gateway.time.monotonic", return_value=0.0),
            self.assertRaisesMessage(ReleaseContractError, "replaced"),
        ):
            replacement.revalidate_web_runtime_binding(observed_binding(), deadline=420.0)

    def test_second_sample_can_arrive_at_exact_deadline_with_no_later_poll(self) -> None:
        clock = FakeClock()
        gateway = gateway_with_sequences(
            [[TASK_ARN], [], [], [TASK_ARN]],
            poll_seconds=10,
            timeout_seconds=20,
        )
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
            patch("deploy.aws_gateway.verify_health"),
        ):
            gateway.establish_web_runtime_binding(receipt(), identity(), deadline=20.0)
        self.assertEqual(clock.current, 20.0)
        self.assertEqual(clock.sleeps, [10, 10])
        self.assertEqual(gateway.ecs.list_tasks.call_count, 4)

        timeout_clock = FakeClock()
        timeout_gateway = gateway_with_sequences(
            [[TASK_ARN], [], [], []],
            poll_seconds=10,
            timeout_seconds=20,
        )
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=timeout_clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=timeout_clock.sleep),
            patch("deploy.aws_gateway.verify_health"),
            self.assertRaises(ReleaseContractError) as caught,
        ):
            timeout_gateway.establish_web_runtime_binding(receipt(), identity(), deadline=20.0)
        self.assertEqual(caught.exception.reason_code, "receipt_deadline_expired")
        self.assertEqual(timeout_gateway.ecs.list_tasks.call_count, 4)
        self.assertEqual(timeout_clock.sleeps, [10, 10])

    def test_safe_evidence_excludes_runtime_and_provider_canaries(self) -> None:
        binding = observed_binding()
        proof = binding.safe_evidence(observation_count=2, deadline_budget_seconds=180)
        text = json.dumps(proof, sort_keys=True)
        for forbidden in (
            TASK_ARN,
            "attachment-web",
            "eni-0123456789abcdef0",
            PRIVATE_ADDRESS,
            f"{PRIVATE_ADDRESS}:{TARGET_PORT}",
        ):
            self.assertNotIn(forbidden, text)
        self.assertRegex(proof["binding_fingerprint"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            set(proof),
            {
                "receipt_id",
                "expected_task_definition_arn",
                "expected_source_sha",
                "expected_image_digest",
                "expected_version",
                "identity_schema",
                "observation_count",
                "deadline_budget_seconds",
                "coherence_checks",
                "binding_fingerprint",
            },
        )

        provider_canary = "raw-provider-health-description-private-canary"
        gateway = gateway_with_sequences(
            [[TASK_ARN]],
            target_health_documents=[target_health(reason=provider_canary)],
        )
        with patch("deploy.aws_gateway.time.monotonic", return_value=0.0):
            observed = gateway._observe_web_runtime_once(
                receipt(), identity(), deadline=180.0, frozen_binding=None
            )
        assert observed is not None
        self.assertNotIn(
            provider_canary,
            json.dumps(
                observed.safe_evidence(
                    observation_count=1,
                    deadline_budget_seconds=180,
                )
            ),
        )

        override_canary = "raw-container-override-canary"
        task = running_task()
        task["overrides"]["containerOverrides"] = [
            {
                "name": "web",
                "environment": [{"name": "APP_VERSION", "value": override_canary}],
            }
        ]
        gateway = gateway_with_sequences([[TASK_ARN]], task_documents={TASK_ARN: task})
        with (
            patch("deploy.aws_gateway.time.monotonic", return_value=0.0),
            self.assertRaises(ReleaseContractError) as caught,
        ):
            gateway._observe_web_runtime_once(
                receipt(), identity(), deadline=180.0, frozen_binding=None
            )
        self.assertNotIn(override_canary, str(caught.exception))


class WorkerWebBindingGuardTests(SimpleTestCase):
    def test_worker_acknowledgement_and_terminal_wait_revalidate_binding(self) -> None:
        gateway = AwsReleaseGateway.__new__(AwsReleaseGateway)
        gateway.config = config()
        gateway.ecs = Mock()
        gateway.ecr = Mock()
        gateway.elbv2 = Mock()
        worker_receipt = ServiceUpdateReceipt(
            workload="worker",
            configured_service_identity=SELECTED_TARGET.worker_service_name,
            target=ServiceTarget(WORKER_DEFINITION, 1),
            primary_deployment_id="ecs-svc/worker-14",
            predecessors=(
                ServicePredecessor(
                    ServiceTarget(
                        OLD_WORKER_DEFINITION,
                        1,
                    ),
                    "ecs-svc/worker-13",
                    "terminal",
                ),
            ),
            binding_reason="partial_acknowledgement_reconciled",
            terminal_observed=True,
        )
        gateway.ecs.update_service.return_value = {
            "service": {
                "serviceName": SELECTED_TARGET.worker_service_name,
                "taskDefinition": WORKER_DEFINITION,
                "desiredCount": 1,
                "runningCount": 1,
                "pendingCount": 0,
                "deployments": [
                    {
                        "id": "ecs-svc/worker-14",
                        "status": "PRIMARY",
                        "taskDefinition": WORKER_DEFINITION,
                        "desiredCount": 1,
                        "runningCount": 1,
                        "pendingCount": 0,
                        "failedTasks": 0,
                        "rolloutState": "COMPLETED",
                    }
                ],
            }
        }
        gateway._receipt_from_acknowledgement = Mock(return_value=worker_receipt)  # type: ignore[method-assign]
        gateway.revalidate_web_runtime_binding = Mock(return_value=True)  # type: ignore[method-assign]
        with patch("deploy.aws_gateway.time.monotonic", return_value=0.0):
            result = gateway.update_service(
                "worker",
                worker_receipt.target,
                worker_receipt.predecessors,
                deadline=420.0,
                timeout_seconds=420,
                web_runtime_binding=observed_binding(),
            )
            gateway.wait_service_stable(
                result,
                worker_singleton=True,
                deadline=420.0,
                timeout_seconds=420,
                web_runtime_binding=observed_binding(),
            )
        self.assertEqual(gateway.revalidate_web_runtime_binding.call_count, 2)
        self.assertEqual(gateway.ecs.update_service.call_count, 1)

    def test_worker_poll_requires_final_web_proof_after_terminal_observation(self) -> None:
        gateway = AwsReleaseGateway.__new__(AwsReleaseGateway)
        gateway.config = config()
        worker_receipt = ServiceUpdateReceipt(
            workload="worker",
            configured_service_identity=SELECTED_TARGET.worker_service_name,
            target=ServiceTarget(WORKER_DEFINITION, 1),
            primary_deployment_id="ecs-svc/worker-14",
            predecessors=(
                ServicePredecessor(
                    ServiceTarget(
                        OLD_WORKER_DEFINITION,
                        1,
                    ),
                    "ecs-svc/worker-13",
                    "terminal",
                ),
            ),
        )
        events: list[str] = []

        def observe_worker(*_args: Any, **_kwargs: Any) -> bool:
            events.append("worker-terminal")
            return True

        def observe_web(*_args: Any, **_kwargs: Any) -> bool:
            events.append("web-final")
            return True

        gateway._observe_service_stable_once = Mock(  # type: ignore[method-assign]
            side_effect=observe_worker
        )
        gateway.revalidate_web_runtime_binding = Mock(  # type: ignore[method-assign]
            side_effect=observe_web
        )
        with patch("deploy.aws_gateway.time.monotonic", return_value=0.0):
            gateway.wait_service_stable(
                worker_receipt,
                worker_singleton=True,
                deadline=420.0,
                timeout_seconds=420,
                web_runtime_binding=observed_binding(),
            )
        self.assertEqual(events, ["worker-terminal", "web-final"])

    def test_every_partial_receipt_reconciliation_read_checks_the_binding(self) -> None:
        gateway = AwsReleaseGateway.__new__(AwsReleaseGateway)
        gateway.config = config()
        gateway.ecs = Mock()
        gateway.ecr = Mock()
        gateway.elbv2 = Mock()
        worker_receipt = self._worker_receipt()
        gateway.ecs.update_service.return_value = {"service": {}}
        gateway._service = Mock(side_effect=[{}, {}])  # type: ignore[method-assign]
        gateway._receipt_from_acknowledgement = Mock(  # type: ignore[method-assign]
            side_effect=[None, None, worker_receipt]
        )
        gateway.revalidate_web_runtime_binding = Mock(return_value=True)  # type: ignore[method-assign]
        clock = FakeClock()
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            result = gateway.update_service(
                "worker",
                worker_receipt.target,
                worker_receipt.predecessors,
                deadline=420.0,
                timeout_seconds=420,
                web_runtime_binding=observed_binding(),
            )
        self.assertEqual(result, worker_receipt)
        self.assertEqual(gateway.revalidate_web_runtime_binding.call_count, 3)
        self.assertEqual(gateway._service.call_count, 2)
        self.assertEqual(clock.sleeps, [10])

    def test_worker_poll_couples_retryable_web_staleness_to_existing_rounds(self) -> None:
        gateway = AwsReleaseGateway.__new__(AwsReleaseGateway)
        gateway.config = config(poll_seconds=10, timeout_seconds=20)
        worker_receipt = self._worker_receipt()
        gateway._observe_service_stable_once = Mock(  # type: ignore[method-assign]
            side_effect=[False, True]
        )
        gateway.revalidate_web_runtime_binding = Mock(  # type: ignore[method-assign]
            side_effect=[False, True]
        )
        clock = FakeClock()
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            gateway.wait_service_stable(
                worker_receipt,
                worker_singleton=True,
                deadline=20.0,
                timeout_seconds=20,
                web_runtime_binding=observed_binding(),
            )
        self.assertEqual(gateway._observe_service_stable_once.call_count, 2)
        self.assertEqual(gateway.revalidate_web_runtime_binding.call_count, 2)
        self.assertEqual(clock.sleeps, [10])

    def test_inclusive_worker_final_round_has_no_later_sleep_or_read(self) -> None:
        gateway = AwsReleaseGateway.__new__(AwsReleaseGateway)
        gateway.config = config(poll_seconds=10, timeout_seconds=20)
        worker_receipt = self._worker_receipt()
        gateway._observe_service_stable_once = Mock(  # type: ignore[method-assign]
            side_effect=[False, False, True]
        )
        gateway.revalidate_web_runtime_binding = Mock(return_value=True)  # type: ignore[method-assign]
        clock = FakeClock()
        with (
            patch("deploy.aws_gateway.time.monotonic", side_effect=clock.monotonic),
            patch("deploy.aws_gateway.time.sleep", side_effect=clock.sleep),
        ):
            gateway.wait_service_stable(
                worker_receipt,
                worker_singleton=True,
                deadline=20.0,
                timeout_seconds=20,
                web_runtime_binding=observed_binding(),
            )
        self.assertEqual(clock.current, 20.0)
        self.assertEqual(clock.sleeps, [10, 10])
        self.assertEqual(gateway._observe_service_stable_once.call_count, 3)
        self.assertEqual(gateway.revalidate_web_runtime_binding.call_count, 3)

    @staticmethod
    def _worker_receipt() -> ServiceUpdateReceipt:
        return ServiceUpdateReceipt(
            workload="worker",
            configured_service_identity=SELECTED_TARGET.worker_service_name,
            target=ServiceTarget(WORKER_DEFINITION, 1),
            primary_deployment_id="ecs-svc/worker-14",
            predecessors=(
                ServicePredecessor(
                    ServiceTarget(
                        OLD_WORKER_DEFINITION,
                        1,
                    ),
                    "ecs-svc/worker-13",
                    "terminal",
                ),
            ),
        )
