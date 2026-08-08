from __future__ import annotations

import argparse
import json
from pathlib import Path

from deploy.aws_gateway import (
    MAX_STAGE_TIMEOUT_SECONDS,
    MAX_WEB_STABILIZATION_TIMEOUT_SECONDS,
    MAX_WORKER_STABILIZATION_TIMEOUT_SECONDS,
    WEB_STABILIZATION_TIMEOUT_SECONDS,
    WORKER_STABILIZATION_TIMEOUT_SECONDS,
    AwsReleaseConfig,
    AwsReleaseGateway,
)
from deploy.contracts import ActiveServicePair, ReleaseContractError, ReleaseIdentity, ReleaseRecord
from deploy.release import (
    FAILURE_INJECTIONS,
    PromotionConfig,
    RecoveryContext,
    capture_current_service_pair,
    capture_recovery_context,
    promote,
    restore_after_finalization_failure,
    rollback,
)
from deploy.task_definitions import TaskDefinitionConfig


def _boolean(value: str) -> bool:
    normalized = value.lower()
    if normalized not in {"true", "false"}:
        raise argparse.ArgumentTypeError("must be true or false")
    return normalized == "true"


def _add_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--region", required=True)
    parser.add_argument("--cluster-arn", required=True)
    parser.add_argument("--web-target-group-arn", required=True)
    parser.add_argument("--web-service-name", required=True)
    parser.add_argument("--worker-service-name", required=True)
    parser.add_argument("--web-family", required=True)
    parser.add_argument("--worker-family", required=True)
    parser.add_argument("--migration-family", required=True)
    parser.add_argument("--web-container-name", required=True)
    parser.add_argument("--worker-container-name", required=True)
    parser.add_argument("--migration-container-name", required=True)
    parser.add_argument("--task-role-arn", required=True)
    parser.add_argument("--execution-role-arn", required=True)
    parser.add_argument("--subnet-id", action="append", required=True)
    parser.add_argument("--security-group-id", action="append", required=True)
    parser.add_argument("--assign-public-ip", type=_boolean, required=True)
    parser.add_argument("--base-url", default="https://web.dtcdev.click")
    parser.add_argument("--screenshot-directory", type=Path, default=Path(".tmp/deployed-smoke"))
    parser.add_argument("--timeout-seconds", type=int, default=MAX_STAGE_TIMEOUT_SECONDS)
    parser.add_argument(
        "--web-stabilization-timeout-seconds",
        type=int,
        default=WEB_STABILIZATION_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--worker-stabilization-timeout-seconds",
        type=int,
        default=WORKER_STABILIZATION_TIMEOUT_SECONDS,
    )
    parser.add_argument("--poll-seconds", type=int, default=10)


def _gateway(arguments: argparse.Namespace) -> AwsReleaseGateway:
    if (
        type(arguments.timeout_seconds) is not int
        or type(arguments.web_stabilization_timeout_seconds) is not int
        or type(arguments.worker_stabilization_timeout_seconds) is not int
        or type(arguments.poll_seconds) is not int
        or arguments.timeout_seconds < 1
        or arguments.web_stabilization_timeout_seconds < 1
        or arguments.worker_stabilization_timeout_seconds < 1
        or arguments.poll_seconds < 1
    ):
        raise ReleaseContractError("timeouts must be positive integers")
    if arguments.timeout_seconds > MAX_STAGE_TIMEOUT_SECONDS:
        raise ReleaseContractError("development stage timeout exceeds the recovery-safe maximum")
    if arguments.web_stabilization_timeout_seconds > MAX_WEB_STABILIZATION_TIMEOUT_SECONDS:
        raise ReleaseContractError("web stabilization timeout exceeds the recovery-safe maximum")
    if arguments.worker_stabilization_timeout_seconds > MAX_WORKER_STABILIZATION_TIMEOUT_SECONDS:
        raise ReleaseContractError("worker stabilization timeout exceeds the recovery-safe maximum")
    if arguments.poll_seconds > arguments.timeout_seconds:
        raise ReleaseContractError("poll interval must not exceed the stage timeout")
    if arguments.poll_seconds > arguments.web_stabilization_timeout_seconds:
        raise ReleaseContractError("poll interval must not exceed the web stabilization timeout")
    if arguments.poll_seconds > arguments.worker_stabilization_timeout_seconds:
        raise ReleaseContractError("poll interval must not exceed the worker stabilization timeout")
    return AwsReleaseGateway(
        AwsReleaseConfig(
            region=arguments.region,
            cluster_arn=arguments.cluster_arn,
            web_target_group_arn=arguments.web_target_group_arn,
            service_names={
                "web": arguments.web_service_name,
                "worker": arguments.worker_service_name,
            },
            task_families={
                "web": arguments.web_family,
                "worker": arguments.worker_family,
                "migration": arguments.migration_family,
            },
            container_names={
                "web": arguments.web_container_name,
                "worker": arguments.worker_container_name,
                "migration": arguments.migration_container_name,
            },
            task_role_arn=arguments.task_role_arn,
            execution_role_arn=arguments.execution_role_arn,
            subnet_ids=arguments.subnet_id,
            security_group_ids=arguments.security_group_id,
            assign_public_ip=arguments.assign_public_ip,
            base_url=arguments.base_url,
            screenshot_directory=arguments.screenshot_directory,
            timeout_seconds=arguments.timeout_seconds,
            web_stabilization_timeout_seconds=arguments.web_stabilization_timeout_seconds,
            worker_stabilization_timeout_seconds=(arguments.worker_stabilization_timeout_seconds),
            poll_seconds=arguments.poll_seconds,
        )
    )


def _promote(arguments: argparse.Namespace) -> ReleaseRecord:
    identity = ReleaseIdentity(
        source_sha=arguments.source_sha,
        image_digest=arguments.image_digest,
        repository_uri=arguments.repository_uri,
    )
    task_config = TaskDefinitionConfig(
        families={
            "web": arguments.web_family,
            "worker": arguments.worker_family,
            "migration": arguments.migration_family,
        },
        container_names={
            "web": arguments.web_container_name,
            "worker": arguments.worker_container_name,
            "migration": arguments.migration_container_name,
        },
        task_role_arn=arguments.task_role_arn,
        execution_role_arn=arguments.execution_role_arn,
    )
    if arguments.prior_release_record and arguments.active_service_pair:
        raise ReleaseContractError(
            "promotion accepts either a manual prior release or an active service pair"
        )
    expected: ReleaseRecord | ActiveServicePair | None
    if arguments.active_service_pair:
        expected = ActiveServicePair.read(arguments.active_service_pair)
    elif arguments.prior_release_record:
        expected = ReleaseRecord.read(arguments.prior_release_record)
    else:
        expected = None
    return promote(
        _gateway(arguments),
        PromotionConfig(
            identity=identity,
            task_definitions=task_config,
            web_desired_count=arguments.web_desired_count,
            worker_desired_count=arguments.worker_desired_count,
            project_tag=arguments.project_tag,
            environment_tag=arguments.environment_tag,
            release_record_path=arguments.release_record_path,
            expected_prior_release=expected,
            failure_injection=arguments.failure_injection,
            evidence_path=arguments.evidence_path,
            recovery_context_path=arguments.recovery_context_path,
        ),
    )


def _rollback(arguments: argparse.Namespace) -> ReleaseRecord:
    return rollback(
        _gateway(arguments),
        ReleaseRecord.read(arguments.target_release_record),
        ReleaseRecord.read(arguments.current_release_record),
        arguments.repository_uri,
        arguments.release_record_path,
        arguments.evidence_path,
        arguments.recovery_context_path,
    )


def _restore_finalization(arguments: argparse.Namespace) -> RecoveryContext:
    failed_release = ReleaseRecord.read(arguments.failed_release_record)
    context = RecoveryContext.read(arguments.recovery_context)
    restore_after_finalization_failure(
        _gateway(arguments),
        context,
        failed_release,
    )
    return context


def _capture_current(arguments: argparse.Namespace) -> ActiveServicePair:
    return capture_current_service_pair(
        _gateway(arguments),
        arguments.repository_uri,
        arguments.release_record_path,
        expected_web_count=arguments.expected_web_count,
        expected_worker_count=arguments.expected_worker_count,
    )


def _capture_recovery(arguments: argparse.Namespace) -> RecoveryContext:
    if arguments.prior_release_record and arguments.active_service_pair:
        raise ReleaseContractError("recovery capture accepts only one expected prior")
    expected: ReleaseRecord | ActiveServicePair | None = None
    if arguments.prior_release_record:
        expected = ReleaseRecord.read(arguments.prior_release_record)
    elif arguments.active_service_pair:
        expected = ActiveServicePair.read(arguments.active_service_pair)
    return capture_recovery_context(
        _gateway(arguments),
        arguments.repository_uri,
        arguments.recovery_context_path,
        expected,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Promote or roll back an immutable ECS release")
    subparsers = parser.add_subparsers(dest="command", required=True)

    promote_parser = subparsers.add_parser("promote")
    _add_runtime_arguments(promote_parser)
    promote_parser.add_argument("--source-sha", required=True)
    promote_parser.add_argument("--image-digest", required=True)
    promote_parser.add_argument("--repository-uri", required=True)
    promote_parser.add_argument("--web-desired-count", type=int, required=True)
    promote_parser.add_argument("--worker-desired-count", type=int, required=True)
    promote_parser.add_argument("--project-tag", required=True)
    promote_parser.add_argument("--environment-tag", required=True)
    promote_parser.add_argument("--prior-release-record", type=Path)
    promote_parser.add_argument("--active-service-pair", type=Path)
    promote_parser.add_argument(
        "--failure-injection",
        choices=sorted(FAILURE_INJECTIONS),
        default="none",
    )
    promote_parser.add_argument("--release-record-path", type=Path, required=True)
    promote_parser.add_argument("--evidence-path", type=Path)
    promote_parser.add_argument("--recovery-context-path", type=Path)
    promote_parser.set_defaults(handler=_promote)

    rollback_parser = subparsers.add_parser("rollback")
    _add_runtime_arguments(rollback_parser)
    rollback_parser.add_argument("--repository-uri", required=True)
    rollback_parser.add_argument("--target-release-record", type=Path, required=True)
    rollback_parser.add_argument("--current-release-record", type=Path, required=True)
    rollback_parser.add_argument("--release-record-path", type=Path, required=True)
    rollback_parser.add_argument("--evidence-path", type=Path)
    rollback_parser.add_argument("--recovery-context-path", type=Path)
    rollback_parser.set_defaults(handler=_rollback)

    restore_parser = subparsers.add_parser("restore-finalization")
    _add_runtime_arguments(restore_parser)
    restore_parser.add_argument("--recovery-context", type=Path, required=True)
    restore_parser.add_argument("--failed-release-record", type=Path, required=True)
    restore_parser.set_defaults(handler=_restore_finalization)

    capture_parser = subparsers.add_parser("capture-current")
    _add_runtime_arguments(capture_parser)
    capture_parser.add_argument("--repository-uri", required=True)
    capture_parser.add_argument("--expected-web-count", type=int, required=True)
    capture_parser.add_argument("--expected-worker-count", type=int, required=True)
    capture_parser.add_argument("--release-record-path", type=Path, required=True)
    capture_parser.set_defaults(handler=_capture_current)

    recovery_capture_parser = subparsers.add_parser("capture-recovery")
    _add_runtime_arguments(recovery_capture_parser)
    recovery_capture_parser.add_argument("--repository-uri", required=True)
    recovery_capture_parser.add_argument("--prior-release-record", type=Path)
    recovery_capture_parser.add_argument("--active-service-pair", type=Path)
    recovery_capture_parser.add_argument("--recovery-context-path", type=Path, required=True)
    recovery_capture_parser.set_defaults(handler=_capture_recovery)
    return parser


def main() -> None:
    parser = build_parser()
    arguments = parser.parse_args()
    try:
        record = arguments.handler(arguments)
    except ReleaseContractError as error:
        parser.exit(1, f"release failed safely: {error}\n")
    except Exception as error:
        parser.exit(1, f"release failed safely: AWS operation failed ({type(error).__name__})\n")
    if arguments.command == "restore-finalization":
        print(
            json.dumps(
                {
                    "status": "restored_prior",
                    "release": record.source_sha or "bootstrap-disabled",
                    "image_digest": record.image_digest,
                },
                sort_keys=True,
            )
        )
    elif arguments.command == "capture-recovery":
        print(json.dumps({"status": "recovery_checkpoint_captured"}, sort_keys=True))
    else:
        print(json.dumps({"status": "successful", "release": record.source_sha}, sort_keys=True))


if __name__ == "__main__":
    main()
