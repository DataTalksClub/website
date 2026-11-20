"""The Coolify API token guard fails loudly on rejection and near expiry."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import yaml

from deploy.coolify_token import RUNBOOK, check, decide_auth, decide_expiry

ROOT = Path(__file__).resolve().parents[2]
TODAY = dt.date(2026, 11, 21)


def workflow(name: str) -> dict[str, Any]:
    return yaml.load(
        (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def test_rejected_token_names_the_cause_and_runbook() -> None:
    for status in (401, 403):
        decision = decide_auth(status)
        assert not decision.ok
        assert "expired" in decision.message
        assert RUNBOOK in decision.message


def test_accepted_token_and_other_failures() -> None:
    assert decide_auth(200).ok
    assert not decide_auth(500).ok
    assert not decide_auth(None, "timed out").ok


def test_expiry_threshold() -> None:
    assert decide_expiry("2027-11-20", today=TODAY, warn_days=30).ok
    assert not decide_expiry("2026-12-15", today=TODAY, warn_days=30).ok
    assert not decide_expiry("2026-11-20", today=TODAY, warn_days=30).ok
    assert not decide_expiry("", today=TODAY, warn_days=30).ok
    assert not decide_expiry("next year", today=TODAY, warn_days=30).ok


def test_check_skips_expiry_unless_requested_and_rejects_empty_inputs() -> None:
    def ok_prober(url: str, token: str) -> tuple[int, str]:
        return 200, ""

    decisions = check(
        url="https://c", token="t", expires_at=None, warn_days=30, today=TODAY, prober=ok_prober
    )
    assert [d.ok for d in decisions] == [True]
    decisions = check(
        url="https://c",
        token="t",
        expires_at="2026-12-01",
        warn_days=30,
        today=TODAY,
        prober=ok_prober,
    )
    assert [d.ok for d in decisions] == [True, False]
    assert not check(
        url="", token="t", expires_at=None, warn_days=30, today=TODAY, prober=ok_prober
    )[0].ok


def test_deploy_checks_the_token_before_the_first_coolify_call() -> None:
    steps = workflow("deploy-coolify.yml")["jobs"]["deploy"]["steps"]
    names = [step.get("name", "") for step in steps]
    guard = names.index("Check the Coolify API token")
    assert guard < names.index("Point staging at the pushed release")
    assert "deploy.coolify_token check" in steps[guard]["run"]
    assert steps[guard]["env"]["COOLIFY_TOKEN"] == "${{ secrets.COOLIFY_TOKEN }}"


def test_scheduled_check_watches_the_recorded_expiry() -> None:
    wf = workflow("coolify-token-check.yml")
    assert "schedule" in wf["on"]
    job = wf["jobs"]["check"]
    assert job["environment"] == "coolify-staging"
    step = job["steps"][-1]
    assert step["env"]["COOLIFY_TOKEN_EXPIRES_AT"] == "${{ vars.COOLIFY_TOKEN_EXPIRES_AT }}"
    assert "--expires-at" in step["run"]
