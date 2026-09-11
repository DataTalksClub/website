"""The deploy orchestrator captures a redacted receipt and recovers (REL-02).

Two layers are pinned here.  The unit tests drive ``deploy/recovery_receipt.py``
directly with an injected command runner: the receipt holds only allowlisted
identifiers and counts, a missing service is recorded rather than invented, and
``recover`` restores the exact prior task-definition ARNs and desired counts.
The integration tests run ``deploy/deploy_website.sh`` end to end against a
fake ``aws``/``curl`` on ``PATH``, failing each stage after the web mutation:
every failure attempts bounded recovery of both workloads from the receipt, the
script still exits nonzero, a migration failure before the first mutation
mutates nothing, and a successful rollout records ``promoted`` with the
intended counts.  A runner killed mid-deployment leaves the receipt usable.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from ci.tests.test_deploy_release_verification import (
    IMAGE,
    SOURCE_SHA,
    VERSION,
    WEB_ARN_NEW,
    WORKER_ARN_NEW,
    VerificationHarness,
)
from ci.tests.test_deploy_release_verification import (
    WEB_OLD as WEB_ARN,
)
from ci.tests.test_deploy_release_verification import (
    WORKER_OLD as WORKER_ARN,
)
from deploy.recovery_receipt import (
    OUTCOME_IN_PROGRESS,
    OUTCOME_PROMOTED,
    build_receipt,
    capture_command,
    mark_promoted,
    recover_command,
)

ROOT = Path(__file__).resolve().parents[2]
REGION = "eu-west-1"
CLUSTER = "website-production"
WEB = "website-dev-web"
WORKER = "website-dev-worker"

#: Keys that must never appear anywhere in a receipt.  The receipt is built
#: from an allowlist, so this is a structural tripwire, not a filter.
FORBIDDEN_KEYS = frozenset(
    {
        "environment",
        "environmentVariables",
        "secrets",
        "value",
        "valueFrom",
        "credentials",
        "password",
        "networkConfiguration",
        "containerDefinitions",
    }
)


def assert_redacted(payload: Any) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key not in FORBIDDEN_KEYS, key
            assert_redacted(value)
    elif isinstance(payload, list):
        for item in payload:
            assert_redacted(item)


# ---------------------------------------------------------------------------
# Unit tests: the receipt module
# ---------------------------------------------------------------------------


def services_document() -> dict[str, Any]:
    return {
        "failures": [],
        "services": [
            {
                "serviceName": WEB,
                "taskDefinition": WEB_ARN,
                "desiredCount": 1,
                "deployments": [{"id": "ecs-svc/111"}, {"id": "ecs-svc/110"}],
            },
            {
                "serviceName": WORKER,
                "taskDefinition": WORKER_ARN,
                "desiredCount": 0,
                "deployments": [{"id": "ecs-svc/222"}],
            },
        ],
    }


def test_capture_records_the_allowlisted_prior_state(tmp_path: Path) -> None:
    source = tmp_path / "services.json"
    source.write_text(json.dumps(services_document()))
    receipt_path = tmp_path / "receipts" / "dev.json"

    exit_code = capture_command(
        [
            "--target",
            "dev",
            "--cluster",
            CLUSTER,
            "--region",
            REGION,
            "--version",
            VERSION,
            "--source-sha",
            SOURCE_SHA,
            "--image",
            IMAGE,
            "--service",
            WEB,
            "--service",
            WORKER,
            "--services-json",
            str(source),
            "--output",
            str(receipt_path),
        ]
    )

    assert exit_code == 0
    receipt = json.loads(receipt_path.read_text())
    assert receipt["schema"] == 2
    assert receipt["outcome"] == "in_progress"
    assert receipt["first_mutation"] == "update-service"
    # A local orchestrator run supplies no provenance bindings; the production
    # workflow always does (REL-07).
    assert receipt["controller_sha"] == ""
    assert receipt["dev_run_id"] == ""
    assert receipt["services"][WEB] == {
        "exists": True,
        "task_definition_arn": WEB_ARN,
        "desired_count": 1,
        "deployment_ids": ["ecs-svc/111", "ecs-svc/110"],
    }
    assert receipt["services"][WORKER]["desired_count"] == 0
    assert_redacted(receipt)


def test_capture_records_the_promotion_provenance(tmp_path: Path) -> None:
    source = tmp_path / "services.json"
    source.write_text(json.dumps(services_document()))
    receipt_path = tmp_path / "receipt.json"

    capture_command(
        [
            "--target",
            "production",
            "--cluster",
            CLUSTER,
            "--region",
            REGION,
            "--version",
            VERSION,
            "--source-sha",
            SOURCE_SHA,
            "--image",
            IMAGE,
            "--controller-sha",
            "c" * 40,
            "--dev-run-id",
            "1234567890",
            "--service",
            WEB,
            "--service",
            WORKER,
            "--services-json",
            str(source),
            "--output",
            str(receipt_path),
        ]
    )

    receipt = json.loads(receipt_path.read_text())
    assert receipt["controller_sha"] == "c" * 40
    assert receipt["dev_run_id"] == "1234567890"
    assert receipt["target"] == "production"
    assert_redacted(receipt)


def test_capture_marks_a_missing_service_as_absent(tmp_path: Path) -> None:
    source = tmp_path / "services.json"
    document = services_document()
    document["services"] = document["services"][:1]
    document["failures"] = [{"reason": "MISSING", "arn": WORKER}]
    source.write_text(json.dumps(document))
    receipt_path = tmp_path / "receipt.json"

    capture_command(
        [
            "--target",
            "dev",
            "--cluster",
            CLUSTER,
            "--region",
            REGION,
            "--version",
            VERSION,
            "--source-sha",
            SOURCE_SHA,
            "--image",
            IMAGE,
            "--service",
            WEB,
            "--service",
            WORKER,
            "--services-json",
            str(source),
            "--output",
            str(receipt_path),
        ]
    )

    receipt = json.loads(receipt_path.read_text())
    assert receipt["services"][WEB]["exists"] is True
    assert receipt["services"][WORKER] == {"exists": False}


class RecordingRunner:
    def __init__(self, returncodes: dict[str, int] | None = None):
        self.returncodes = returncodes or {}
        self.calls: list[tuple[list[str], float | None]] = []

    def __call__(self, args: list[str], timeout: float | None = None):
        self.calls.append((args, timeout))
        command = " ".join(args[:3])
        return subprocess.CompletedProcess(args, self.returncodes.get(command, 0))


def write_receipt(tmp_path: Path) -> Path:
    receipt_path = tmp_path / "receipt.json"
    receipt = build_receipt(
        target="dev",
        cluster=CLUSTER,
        region=REGION,
        version=VERSION,
        source_sha=SOURCE_SHA,
        image=IMAGE,
        service_names=[WEB, WORKER],
        services_document=services_document(),
    )
    receipt_path.write_text(json.dumps(receipt))
    return receipt_path


def test_recover_restores_the_exact_prior_targets(tmp_path: Path) -> None:
    receipt_path = write_receipt(tmp_path)
    runner = RecordingRunner()

    exit_code = recover_command(
        ["--receipt", str(receipt_path), "--region", REGION],
        runner=runner,
    )

    assert exit_code == 0
    update_args = [args for args, _timeout in runner.calls if "update-service" in args]
    assert update_args == [
        [
            "aws",
            "ecs",
            "update-service",
            "--region",
            REGION,
            "--cluster",
            CLUSTER,
            "--service",
            WEB,
            "--task-definition",
            WEB_ARN,
            "--desired-count",
            "1",
        ],
        [
            "aws",
            "ecs",
            "update-service",
            "--region",
            REGION,
            "--cluster",
            CLUSTER,
            "--service",
            WORKER,
            "--task-definition",
            WORKER_ARN,
            "--desired-count",
            "0",
        ],
    ]
    wait_args = next(args for args, _timeout in runner.calls if "wait" in args)
    assert wait_args[-2:] == [WEB, WORKER]
    wait_timeout = next(timeout for args, timeout in runner.calls if "wait" in args)
    assert wait_timeout is not None and wait_timeout > 0
    receipt = json.loads(receipt_path.read_text())
    assert receipt["recovery"]["stabilized"] is True
    assert receipt["recovery"]["restored"] == [WEB, WORKER]
    assert receipt["recovery"]["failed"] == []


def test_recover_reports_restoration_failure_and_stays_failed(tmp_path: Path) -> None:
    receipt_path = write_receipt(tmp_path)
    runner = RecordingRunner(returncodes={"aws ecs update-service": 5})

    exit_code = recover_command(
        ["--receipt", str(receipt_path), "--region", REGION],
        runner=runner,
    )

    assert exit_code == 1
    receipt = json.loads(receipt_path.read_text())
    assert [entry["service"] for entry in receipt["recovery"]["failed"]] == [WEB, WORKER]
    assert receipt["recovery"]["stabilized"] is False


def test_mark_promoted_flips_the_outcome(tmp_path: Path) -> None:
    receipt_path = write_receipt(tmp_path)

    mark_promoted(receipt_path)

    receipt = json.loads(receipt_path.read_text())
    assert receipt["outcome"] == OUTCOME_PROMOTED
    assert receipt["promoted_at"]
    assert "promoted" not in receipt


def test_mark_promoted_records_the_observed_pair(tmp_path: Path) -> None:
    receipt_path = write_receipt(tmp_path)
    observed = {"web": WEB_ARN, "worker": WORKER_ARN}

    mark_promoted(receipt_path, observed)

    receipt = json.loads(receipt_path.read_text())
    assert receipt["outcome"] == OUTCOME_PROMOTED
    assert receipt["promoted"] == observed


def test_mark_promoted_refuses_a_non_arn_observed_pair(tmp_path: Path) -> None:
    receipt_path = write_receipt(tmp_path)

    with pytest.raises(ValueError, match="not an ECS ARN"):
        mark_promoted(receipt_path, {"web": "website-production-web:42"})

    # The failed promotion left the receipt untouched: no success record.
    receipt = json.loads(receipt_path.read_text())
    assert receipt["outcome"] == OUTCOME_IN_PROGRESS


def test_both_workflows_upload_the_receipts_with_always() -> None:
    for name in ("deploy-dev.yml", "deploy-prod.yml"):
        workflow = yaml.load(
            (ROOT / ".github" / "workflows" / name).read_text(),
            Loader=yaml.BaseLoader,
        )
        steps = workflow["jobs"]["deploy"]["steps"]
        upload = next(
            step
            for step in steps
            if isinstance(step, dict)
            and str(step.get("uses", "")).startswith("actions/upload-artifact")
            and "recovery-receipts" in step.get("with", {}).get("name", "")
        )
        assert upload["if"] == "always()"
        assert upload["with"]["path"] == ".tmp/deploy-receipts/"
        assert upload["with"]["if-no-files-found"] == "ignore"


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Integration: deploy_website.sh against the shared stateful fake aws/curl
# (the fake and harness live with the REL-06 verification suite, which drives
# the same orchestrator and must see identical behavior).
# ---------------------------------------------------------------------------


def test_worker_failure_restores_both_services_and_stays_failed(
    tmp_path: Path,
) -> None:
    harness = VerificationHarness(tmp_path)

    completed = harness.deploy(FAKE_FAIL_AT="update-website-dev-worker")

    assert completed.returncode != 0
    # Promote calls (web succeeds, worker fails) then the two recovery
    # restores back to the exact prior targets.
    updates = harness.update_service_calls()
    assert updates[0] == (WEB, WEB_ARN_NEW, "1")
    assert updates[1] == (WORKER, WORKER_ARN_NEW, "0")
    assert updates[-2] == (WEB, WEB_ARN, "1")
    assert updates[-1] == (WORKER, WORKER_ARN, "0")
    receipt = harness.receipt()
    assert receipt["services"][WEB]["task_definition_arn"] == WEB_ARN
    assert receipt["services"][WORKER]["desired_count"] == 0
    assert receipt["recovery"]["stabilized"] is True
    assert receipt["recovery"]["restored"] == [WEB, WORKER]
    assert receipt["outcome"] == "in_progress"
    assert_redacted(receipt)


def test_failed_recovery_is_reported_and_retained(tmp_path: Path) -> None:
    harness = VerificationHarness(tmp_path)

    completed = harness.deploy(
        FAKE_FAIL_AT="update-website-dev-worker",
        FAKE_FAIL_EVERY_TIME="1",
    )

    assert completed.returncode != 0
    assert "AUTOMATIC RECOVERY FAILED" in completed.stderr
    updates = harness.update_service_calls()
    # The worker restore was attempted at the exact prior target and refused;
    # the failure and the receipt survive for a human.
    assert updates[-2] == (WEB, WEB_ARN, "1")
    assert updates[-1] == (WORKER, WORKER_ARN, "0")
    receipt = harness.receipt()
    assert [entry["service"] for entry in receipt["recovery"]["failed"]] == [WORKER]
    assert receipt["recovery"]["stabilized"] is False


def test_health_failure_triggers_recovery(tmp_path: Path) -> None:
    harness = VerificationHarness(tmp_path)

    completed = harness.deploy(FAKE_FAIL_AT="health")

    assert completed.returncode != 0
    updates = harness.update_service_calls()
    assert updates[-2] == (WEB, WEB_ARN, "1")
    assert updates[-1] == (WORKER, WORKER_ARN, "0")
    assert harness.receipt()["recovery"]["stabilized"] is True


def test_stabilization_timeout_triggers_recovery(tmp_path: Path) -> None:
    harness = VerificationHarness(tmp_path)

    completed = harness.deploy(FAKE_FAIL_AT="wait-stable")

    assert completed.returncode != 0
    updates = harness.update_service_calls()
    assert updates[-2] == (WEB, WEB_ARN, "1")
    assert updates[-1] == (WORKER, WORKER_ARN, "0")
    assert harness.receipt()["recovery"]["stabilized"] is True


def test_migration_failure_mutates_no_services(tmp_path: Path) -> None:
    harness = VerificationHarness(tmp_path)

    completed = harness.deploy(FAKE_MIGRATION_EXIT="1")

    assert completed.returncode != 0
    assert harness.update_service_calls() == []
    # The receipt exists as evidence, but no recovery was attempted: nothing
    # was mutated.
    receipt = harness.receipt()
    assert "recovery" not in receipt
    assert_redacted(receipt)


def test_successful_rollout_verifies_and_marks_promoted(tmp_path: Path) -> None:
    harness = VerificationHarness(tmp_path)

    completed = harness.deploy()

    assert completed.returncode == 0, completed.stderr
    assert harness.update_service_calls() == [
        (WEB, WEB_ARN_NEW, "1"),
        (WORKER, WORKER_ARN_NEW, "0"),
    ]
    receipt = harness.receipt()
    assert receipt["outcome"] == "promoted"
    assert "recovery" not in receipt


def test_runner_interruption_leaves_a_usable_receipt(tmp_path: Path) -> None:
    harness = VerificationHarness(tmp_path)

    process = subprocess.Popen(  # noqa: S603
        [
            "bash",
            str(ROOT / "deploy" / "deploy_website.sh"),
            "dev",
            IMAGE,
            VERSION,
            SOURCE_SHA,
        ],
        cwd=harness.root,
        env=harness.env(FAKE_SLEEP_AT=f"update-{WORKER}"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + 30
    worker_seen = False
    while time.monotonic() < deadline:
        calls = harness.update_service_calls()
        if any(service == WORKER for service, _arn, _count in calls):
            worker_seen = True
            break
        if process.poll() is not None:
            break
        time.sleep(0.05)
    assert worker_seen, "the fake worker update never started"

    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    process.wait(timeout=30)

    # SIGKILL skips the bash EXIT trap: no recovery ran, but the pre-mutation
    # receipt survived and names the exact restore targets.
    receipt = harness.receipt()
    assert "recovery" not in receipt
    assert receipt["services"][WEB]["task_definition_arn"] == WEB_ARN
    assert receipt["services"][WORKER]["task_definition_arn"] == WORKER_ARN
    assert_redacted(receipt)
