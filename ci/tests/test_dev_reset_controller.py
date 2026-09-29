"""Exercise reset failure stages through the supported controller with fake AWS."""

from __future__ import annotations

import json
from pathlib import Path

from ci.tests.test_deploy_release_verification import SOURCE_SHA, VerificationHarness


def _harness(tmp_path: Path) -> VerificationHarness:
    harness = VerificationHarness(tmp_path)
    for name in ("gh", "git"):
        script = harness.bin / name
        script.write_text(f"#!/bin/sh\nprintf '%s\\n' '{SOURCE_SHA}'\n", encoding="utf-8")
        script.chmod(0o755)
    return harness


def _run(harness: VerificationHarness, **overrides: str):
    return harness.deploy(
        DTC_DEV_SCHEMA_RESET_CONFIRMATION="RESET dtc_website_dev.public",
        GITHUB_EVENT_NAME="workflow_dispatch",
        GITHUB_REF="refs/heads/main",
        GITHUB_RUN_ATTEMPT="1",
        GITHUB_SHA=SOURCE_SHA,
        GITHUB_REPOSITORY="DataTalksClub/website",
        **overrides,
    )


def _receipt(harness: VerificationHarness) -> dict:
    files = list(harness.receipts.glob("dev-*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


def _counts(harness: VerificationHarness) -> dict[str, int]:
    path = harness.state / "updates.json"
    if not path.exists():
        return {}
    updates = json.loads(path.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for name, update in updates.items():
        counts[name] = update["desired"]
    return counts


def test_drain_failure_restores_prior_state_without_schema_task(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    result = _run(harness, FAKE_FAIL_AT="update-website-dev-web")
    assert result.returncode != 0
    assert not any("run-task" in call for call in harness.calls())
    assert _counts(harness) == {"website-dev-web": 1, "website-dev-worker": 0}, result.stderr
    assert _receipt(harness)["reset"]["mutation_may_have_begun"] is False


def test_migration_failure_keeps_both_services_stopped(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    result = _run(harness, FAKE_MIGRATION_EXIT="21")
    assert result.returncode != 0
    assert _counts(harness) == {"website-dev-web": 0, "website-dev-worker": 0}
    receipt = _receipt(harness)
    assert receipt["outcome"] == "in_progress"
    assert receipt["reset"]["mutation_may_have_begun"] is True
    assert receipt["reset"]["stage"] == "schema_reset_or_migration"
    assert receipt["reset"]["observed_services"]["website-dev-web"]["desiredCount"] == 0


def test_post_promotion_failure_never_restores_old_images(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    result = _run(harness, FAKE_FAIL_AT="selfcheck")
    assert result.returncode != 0
    assert _counts(harness) == {"website-dev-web": 0, "website-dev-worker": 0}
    receipt = _receipt(harness)
    assert receipt["outcome"] == "in_progress"
    assert receipt["reset"]["stage"] == "worker_selfcheck"
    assert receipt["reset"]["observed_services"]["website-dev-worker"]["desiredCount"] == 0
    assert "recovery" not in receipt


def test_failed_post_reset_stop_is_reported_as_unresolved(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    result = _run(harness, FAKE_FAIL_AT="selfcheck", FAKE_FAIL_STOP="1")
    assert result.returncode != 0
    assert "STOP NOT VERIFIED" in result.stderr
    assert _counts(harness)["website-dev-web"] == 1
    receipt = _receipt(harness)
    assert receipt["reset"]["service_action_succeeded"] is False
    assert receipt["reset"]["observed_services"]["website-dev-web"]["desiredCount"] == 1
