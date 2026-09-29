"""Fail-closed control-plane checks for the one-shot website dev reset."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

from deploy import dev_reset_ecs

ROOT = Path(__file__).resolve().parents[2]
TARGET = dev_reset_ecs.TARGET


def _service(name: str, family: str, desired: int) -> dict:
    return {
        "serviceName": name,
        "serviceArn": (
            f"arn:aws:ecs:{TARGET.aws_region}:{TARGET.aws_account_id}:service/"
            f"{TARGET.ecs_cluster_name}/{name}"
        ),
        "taskDefinition": TARGET.task_definition_arn_prefix(family) + "1",
        "desiredCount": desired,
        "runningCount": desired,
        "pendingCount": 0,
    }


class FakeAws:
    def __init__(
        self,
        account: str = "387546586013",
        *,
        writer: bool = False,
        writer_listing: str = "RUNNING",
        writer_status: str = "RUNNING",
        secret: str = "",
    ):
        self.account = account
        self.writer = writer
        self.writer_listing = writer_listing
        self.writer_status = writer_status
        self.secret = secret or (
            "arn:aws:secretsmanager:eu-west-1:387546586013:secret:website-dev/database-url-abcdef"
        )
        self.services = {
            TARGET.web_service_name: _service(TARGET.web_service_name, TARGET.web_task_family, 1),
            TARGET.worker_service_name: _service(
                TARGET.worker_service_name, TARGET.worker_task_family, 0
            ),
        }
        self.calls: list[tuple[str, ...]] = []

    def _identity(self, action: str) -> dict:
        if action == "get-caller-identity":
            return {
                "Account": self.account,
                "Arn": f"arn:aws:sts::{self.account}:assumed-role/website-dev-github-deployer/test",
            }
        if action == "describe-clusters":
            arn = (
                f"arn:aws:ecs:{TARGET.aws_region}:{self.account}:cluster/{TARGET.ecs_cluster_name}"
            )
            return {
                "failures": [],
                "clusters": [{"clusterArn": arn}],
            }
        raise AssertionError(action)

    def _task_definition(self) -> dict:
        return {
            "taskDefinition": {
                "family": TARGET.migration_task_family,
                "containerDefinitions": [
                    {
                        "name": "migration",
                        "secrets": [{"name": "DATABASE_URL", "valueFrom": self.secret}],
                    }
                ],
            }
        }

    def _describe(self, action: str) -> dict:
        if action == "describe-services":
            return {"failures": [], "services": list(self.services.values())}
        if action == "describe-task-definition":
            return self._task_definition()
        if action == "describe-tasks":
            return {
                "failures": [],
                "tasks": [
                    {
                        "taskArn": "arn:writer",
                        "lastStatus": self.writer_status,
                        "taskDefinitionArn": TARGET.task_definition_arn_prefix(
                            TARGET.migration_task_family
                        )
                        + "1",
                    }
                ],
            }
        raise AssertionError(action)

    def __call__(self, *arguments: str) -> dict:
        self.calls.append(arguments)
        action = arguments[1]
        if action in {"get-caller-identity", "describe-clusters"}:
            return self._identity(action)
        if action in {
            "describe-services",
            "describe-task-definition",
            "describe-tasks",
        }:
            return self._describe(action)
        if action == "list-tasks":
            desired = arguments[arguments.index("--desired-status") + 1]
            if self.writer and desired == self.writer_listing:
                return {"taskArns": ["arn:writer"]}
            return {"taskArns": []}
        if action == "update-service":
            service = self.services[arguments[arguments.index("--service") + 1]]
            service["desiredCount"] = 0
            service["runningCount"] = 0
            return {}
        if action == "wait":
            return {}
        raise AssertionError(arguments)


def test_preflight_denies_wrong_account_before_service_mutation(monkeypatch) -> None:
    fake = FakeAws(account="000000000000")
    monkeypatch.setattr(dev_reset_ecs, "_aws", fake)
    monkeypatch.setenv("DTC_DEPLOYMENT_TARGET", TARGET.name)
    monkeypatch.setenv("AWS_REGION", TARGET.aws_region)
    with pytest.raises(dev_reset_ecs.ResetRefused, match="account"):
        dev_reset_ecs.preflight()
    assert not any(call[1] == "update-service" for call in fake.calls)


def test_preflight_denies_foreign_database_secret(monkeypatch) -> None:
    secret = "arn:aws:secretsmanager:eu-west-1:387546586013:secret:relay/database-url-abcdef"
    fake = FakeAws(secret=secret)
    monkeypatch.setattr(dev_reset_ecs, "_aws", fake)
    monkeypatch.setenv("DTC_DEPLOYMENT_TARGET", TARGET.name)
    monkeypatch.setenv("AWS_REGION", TARGET.aws_region)
    with pytest.raises(dev_reset_ecs.ResetRefused, match="database secret"):
        dev_reset_ecs.preflight()
    assert not any(call[1] == "update-service" for call in fake.calls)


def test_drain_rejects_a_remaining_website_writer(monkeypatch) -> None:
    fake = FakeAws(writer=True)
    monkeypatch.setattr(dev_reset_ecs, "_aws", fake)
    with pytest.raises(dev_reset_ecs.ResetRefused, match="writer"):
        dev_reset_ecs.drain()
    assert all(service["desiredCount"] == 0 for service in fake.services.values())
    updates = 0
    for call in fake.calls:
        if call[1] == "update-service":
            updates += 1
    assert updates == 2


@pytest.mark.parametrize(
    ("desired", "actual"),
    [("RUNNING", "PENDING"), ("STOPPED", "RUNNING")],
)
def test_quiescence_checks_actual_status_during_ecs_transitions(
    monkeypatch,
    desired: str,
    actual: str,
) -> None:
    fake = FakeAws(writer=True, writer_listing=desired, writer_status=actual)
    for service in fake.services.values():
        service["desiredCount"] = 0
        service["runningCount"] = 0
    monkeypatch.setattr(dev_reset_ecs, "_aws", fake)
    with pytest.raises(dev_reset_ecs.ResetRefused, match="writer"):
        dev_reset_ecs.assert_quiescent()


def test_stop_attempts_both_services_after_one_update_failure(monkeypatch) -> None:
    fake = FakeAws()

    def failing_aws(*arguments: str) -> dict:
        if arguments[1] == "update-service" and TARGET.web_service_name in arguments:
            raise dev_reset_ecs.ResetRefused("synthetic failure")
        return fake(*arguments)

    monkeypatch.setattr(dev_reset_ecs, "_aws", failing_aws)
    with pytest.raises(dev_reset_ecs.ResetRefused, match="could not be stopped"):
        dev_reset_ecs.stop()
    assert fake.services[TARGET.worker_service_name]["desiredCount"] == 0


def test_production_controller_refuses_schema_reset() -> None:
    helper = (ROOT / "deploy/dev_reset_controller.sh").read_text(encoding="utf-8")
    assert '"$TARGET" != dev' in helper
    assert 'source "$REPO_ROOT/deploy/dev_reset_controller.sh"' in (
        ROOT / "deploy/deploy_website.sh"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("confirmation", "event", "ref", "attempt", "main", "allowed"),
    [
        ("", "push", "refs/heads/main", "1", "a" * 40, True),
        (
            "RESET dtc_website_dev.public",
            "workflow_dispatch",
            "refs/heads/main",
            "1",
            "a" * 40,
            True,
        ),
        ("wrong", "workflow_dispatch", "refs/heads/main", "1", "a" * 40, False),
        ("RESET dtc_website_dev.public", "push", "refs/heads/main", "1", "a" * 40, False),
        (
            "RESET dtc_website_dev.public",
            "workflow_dispatch",
            "refs/heads/other",
            "1",
            "a" * 40,
            False,
        ),
        (
            "RESET dtc_website_dev.public",
            "workflow_dispatch",
            "refs/heads/main",
            "2",
            "a" * 40,
            False,
        ),
        (
            "RESET dtc_website_dev.public",
            "workflow_dispatch",
            "refs/heads/main",
            "1",
            "b" * 40,
            False,
        ),
    ],
)
def test_dispatch_guard_refuses_invalid_reset_before_aws(
    confirmation: str,
    event: str,
    ref: str,
    attempt: str,
    main: str,
    allowed: bool,
) -> None:
    workflow = yaml.load(
        (ROOT / ".github/workflows/deploy-dev.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    script = workflow["jobs"]["test"]["steps"][1]["run"]
    (ROOT / ".tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as temporary:
        directory = Path(temporary)
        guard = directory / "guard.sh"
        guard.write_text(script, encoding="utf-8")
        fake = directory / "gh"
        fake.write_text('#!/bin/sh\nprintf "%s\\n" "$FAKE_MAIN_SHA"\n', encoding="utf-8")
        fake.chmod(0o755)
        environment = _guard_environment(directory, confirmation, event, ref, attempt, main)
        result = subprocess.run(
            ["bash", str(guard)], env=environment, check=False, capture_output=True
        )
    assert (result.returncode == 0) is allowed


def _guard_environment(
    directory: Path,
    confirmation: str,
    event: str,
    ref: str,
    attempt: str,
    main: str,
) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "PATH": f"{directory}:{environment['PATH']}",
            "RESET_CONFIRMATION": confirmation,
            "GITHUB_EVENT_NAME": event,
            "GITHUB_REF": ref,
            "GITHUB_RUN_ATTEMPT": attempt,
            "GITHUB_SHA": "a" * 40,
            "GITHUB_REPOSITORY": "DataTalksClub/website",
            "FAKE_MAIN_SHA": main,
        }
    )
    return environment
