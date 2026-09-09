"""The deploy workflows depend on the real CI verdict, fail closed (REL-01).

Two layers are pinned here.  The parsed-workflow tests assert that automatic
delivery (``deploy-dev.yml``) and manual production promotion
(``deploy-prod.yml``) both route through ``deploy.ci_verdict require`` for an
exact source SHA, with the dependencies, permissions, ordering, and release
binding that make the gate load-bearing.  The behavior tests drive the
code-owned decision logic in ``deploy/ci_verdict.py`` with synthetic GitHub API
payloads: a failed, canceled, pending, or missing verdict never authorizes a
release, a green verdict for a different SHA authorizes nothing, and only a
completed run whose ``ci-gate`` job actually succeeded counts as verification.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from deploy.ci_verdict import (
    GATE_JOB_NAME,
    decide,
    green_requires_gate_job,
    require,
)

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "DataTalksClub/website"
SHA = "a" * 40
WORKFLOW_PATH = ".github/workflows/ci.yml"


def workflow(name: str) -> dict[str, Any]:
    return yaml.load(
        (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def runs(job: dict[str, Any]) -> str:
    return "\n".join(
        step["run"] for step in job["steps"] if isinstance(step, dict) and "run" in step
    )


def run_step(job: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        step for step in job["steps"] if isinstance(step, dict) and step.get("name") == name
    )


# ---------------------------------------------------------------------------
# Parsed workflow dependencies
# ---------------------------------------------------------------------------


def test_dev_publish_depends_on_the_ci_verdict_gate() -> None:
    dev = workflow("deploy-dev.yml")
    jobs = dev["jobs"]

    assert "verify-ci" in jobs
    assert set(jobs["publish"]["needs"]) == {"test", "verify-ci"}

    verify = jobs["verify-ci"]
    # The gate is load-bearing only where delivery happens; elsewhere it would
    # hold the serialized queue for nothing.
    assert verify["if"] == "github.ref == 'refs/heads/main'"
    assert int(verify["timeout-minutes"]) <= 120
    assert dev["permissions"].get("actions") == "read"

    script = runs(verify)
    # REL-19: the gate runs through the locked environment, not the runner's
    # unpinned python3.
    assert "uv run --frozen python -m deploy.ci_verdict require" in script
    assert "--workflow ci.yml" in script
    assert '--repository "$GITHUB_REPOSITORY"' in script
    # The authorized source is this run's checkout SHA -- the same value the
    # image labels and the dev release record carry.
    assert '--sha "$GITHUB_SHA"' in script
    assert "--timeout-seconds" in script


def test_prod_revalidates_the_verdict_for_the_selected_dev_source() -> None:
    prod = workflow("deploy-prod.yml")
    deploy = prod["jobs"]["deploy"]

    gate_step = next(
        step
        for step in deploy["steps"]
        if isinstance(step, dict) and "deploy.ci_verdict require" in step.get("run", "")
    )
    assert gate_step["env"]["EXPECTED_SOURCE_SHA"] == ("${{ steps.dev-run.outputs.source_sha }}")
    assert '--sha "$EXPECTED_SOURCE_SHA"' in gate_step["run"]
    assert "--workflow ci.yml" in gate_step["run"]
    assert deploy["permissions"].get("actions") == "read"

    # The verdict is revalidated before production assumes any AWS role.
    step_names = [
        step.get("name") or step.get("uses", "")
        for step in deploy["steps"]
        if isinstance(step, dict)
    ]
    assert step_names.index(gate_step["name"]) < next(
        index
        for index, name in enumerate(step_names)
        if name.startswith("aws-actions/configure-aws-credentials")
    )


def test_the_dev_release_record_binds_the_verified_source() -> None:
    dev = workflow("deploy-dev.yml")
    publish = dev["jobs"]["publish"]
    deploy = dev["jobs"]["deploy"]

    # publish stamps the exact checkout SHA into the release identity, and the
    # deploy job propagates that same value into the deployed release and the
    # dev-release record the production gate validates.
    assert publish["outputs"]["source_sha"] == "${{ steps.identity.outputs.source_sha }}"
    deploy_script = runs(deploy)
    assert '"${{ needs.publish.outputs.source_sha }}"' in deploy_script
    upload = next(
        step
        for step in deploy["steps"]
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("actions/upload-artifact")
    )
    assert "${{ needs.publish.outputs.source_sha }}" in upload["with"]["name"]

    prod = workflow("deploy-prod.yml")
    record_step = run_step(
        prod["jobs"]["deploy"],
        "Validate the dev release record before assuming AWS",
    )
    assert record_step["env"]["EXPECTED_SOURCE_SHA"] == ("${{ steps.dev-run.outputs.source_sha }}")
    assert ".source_sha == $source_sha" in record_step["run"]


# ---------------------------------------------------------------------------
# Verdict decision behavior
# ---------------------------------------------------------------------------


def make_run(**overrides: Any) -> dict[str, Any]:
    run: dict[str, Any] = {
        "id": 101,
        "path": WORKFLOW_PATH,
        "head_sha": SHA,
        "head_branch": "main",
        "status": "completed",
        "conclusion": "success",
        "html_url": f"https://github.example/{REPOSITORY}/actions/runs/101",
        "head_repository": {"full_name": REPOSITORY},
    }
    run.update(overrides)
    return run


def decide_single(run: dict[str, Any] | None) -> str:
    runs = [run] if run is not None else []
    return decide(
        runs,
        workflow_path=WORKFLOW_PATH,
        expected_sha=SHA,
        repository=REPOSITORY,
    ).state


def test_an_exact_sha_success_is_green() -> None:
    decision = decide_single(make_run())

    assert decision == "green"


def test_failed_canceled_and_timed_out_verdicts_are_not_green() -> None:
    for conclusion in ("failure", "cancelled", "timed_out", "startup_failure"):
        decision = decide_single(make_run(conclusion=conclusion))

        assert decision == "blocked"


def test_pending_and_missing_verdicts_are_not_green() -> None:
    assert decide_single(make_run(status="in_progress", conclusion=None)) == "pending"
    assert decide_single(make_run(status="queued", conclusion=None)) == "pending"
    assert decide_single(None) == "missing"


def test_a_green_verdict_for_a_different_sha_authorizes_nothing() -> None:
    runs = [
        make_run(id=202, head_sha="b" * 40, html_url="https://github.example/other"),
    ]

    decision = decide(
        runs,
        workflow_path=WORKFLOW_PATH,
        expected_sha=SHA,
        repository=REPOSITORY,
    )

    assert decision.state == "missing"


def test_the_decision_trusts_only_fields_it_rechecked() -> None:
    # A run record whose workflow path or head repository does not match this
    # repository is not evidence for this SHA, even when the API query
    # returned it.
    assert decide_single(make_run(path=".github/workflows/other.yml")) == "missing"
    assert decide_single(make_run(head_repository={"full_name": "Somewhere/else"})) == "missing"


def test_the_newest_completed_run_is_authoritative() -> None:
    runs = [
        make_run(id=303, conclusion="failure", html_url="https://github.example/newer"),
        make_run(id=101, conclusion="success"),
    ]

    decision = decide(
        runs,
        workflow_path=WORKFLOW_PATH,
        expected_sha=SHA,
        repository=REPOSITORY,
    )

    assert decision.state == "blocked"
    assert decision.run_id == 303


def test_an_older_success_under_an_active_rerun_reads_as_green() -> None:
    # A rerun keeps its run id and flips the record back to in_progress, so a
    # completed success plus an active record means the last *concluded*
    # verification for this SHA passed.
    runs = [
        make_run(id=101, conclusion="success"),
        make_run(id=101, status="in_progress", conclusion=None),
    ]

    decision = decide(
        runs,
        workflow_path=WORKFLOW_PATH,
        expected_sha=SHA,
        repository=REPOSITORY,
    )

    assert decision.state == "green"


def test_a_green_conclusion_requires_the_aggregate_gate_job() -> None:
    assert (
        green_requires_gate_job([{"name": GATE_JOB_NAME, "conclusion": "success"}], run_id=101)
        is None
    )

    # A probe-mode run skips every verification job yet can conclude success;
    # its ci-gate job reads as skipped, which is not green.
    for gate_job in (
        {"name": GATE_JOB_NAME, "conclusion": "skipped"},
        None,
    ):
        jobs = [gate_job] if gate_job is not None else []
        decision = green_requires_gate_job(jobs, run_id=101)

        assert decision is not None
        assert decision.state == "blocked"


# ---------------------------------------------------------------------------
# CLI poll behavior (injected API/clock, no network, no real sleeps)
# ---------------------------------------------------------------------------


class FakeApi:
    def __init__(self, responses: list[str], *, repeating: bool = False):
        self.responses = responses
        self.repeating = repeating
        self.calls: list[str] = []

    def __call__(self, arguments: list[str]) -> str:
        self.calls.append(arguments[0])
        if not self.responses:
            if self.repeating:
                raise AssertionError("repeating fake needs an initial response")
            raise AssertionError("unexpected extra API call")
        return self.responses.pop(0) if not self.repeating else self.responses[0]

    def runs_calls(self) -> list[str]:
        return [call for call in self.calls if "/runs?head_sha=" in call]

    def jobs_calls(self) -> list[str]:
        return [call for call in self.calls if "/jobs?per_page=" in call]


def runs_payload(*run: dict[str, Any]) -> str:
    return json.dumps({"workflow_runs": list(run)})


def jobs_payload(*job: dict[str, Any]) -> str:
    return json.dumps({"jobs": list(job)})


def drive_require(
    api: FakeApi,
    *,
    timeout: int,
    interval: int,
) -> tuple[int, list[float]]:
    clock = [0.0]
    sleeps: list[float] = []

    def monotonic() -> float:
        return clock[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    exit_code = require(
        [
            "require",
            "--workflow",
            "ci.yml",
            "--repository",
            REPOSITORY,
            "--sha",
            SHA,
            "--timeout-seconds",
            str(timeout),
            "--interval-seconds",
            str(interval),
        ],
        api=api,
        monotonic=monotonic,
        sleep=sleep,
    )
    return exit_code, sleeps


def test_pending_verification_polls_until_green() -> None:
    api = FakeApi(
        [
            runs_payload(make_run(id=101, status="queued", conclusion=None)),
            runs_payload(make_run(id=101, conclusion="success")),
            jobs_payload({"name": GATE_JOB_NAME, "conclusion": "success"}),
        ]
    )

    exit_code, sleeps = drive_require(api, timeout=600, interval=60)

    assert exit_code == 0
    assert sleeps == [60.0]
    assert len(api.runs_calls()) == 2
    # The exact SHA travels in the query, so a green run for another SHA can
    # never be mistaken for this one.
    assert f"head_sha={SHA}" in api.runs_calls()[0]
    assert api.jobs_calls() == [f"repos/{REPOSITORY}/actions/runs/101/jobs?per_page=100"]


def test_a_failed_verdict_refuses_immediately() -> None:
    api = FakeApi([runs_payload(make_run(conclusion="failure"))])

    exit_code, sleeps = drive_require(api, timeout=600, interval=60)

    assert exit_code == 1
    assert sleeps == []
    assert len(api.calls) == 1


def test_pending_verification_times_out_fail_closed() -> None:
    api = FakeApi(
        [runs_payload(make_run(status="in_progress", conclusion=None))],
        repeating=True,
    )

    exit_code, sleeps = drive_require(api, timeout=120, interval=60)

    assert exit_code == 1
    assert sum(sleeps) <= 120
    assert len(api.runs_calls()) >= 2


def test_a_never_created_verdict_is_not_green() -> None:
    api = FakeApi([runs_payload()], repeating=True)

    exit_code, _sleeps = drive_require(api, timeout=90, interval=30)

    assert exit_code == 1


def test_a_green_conclusion_without_the_gate_job_is_refused() -> None:
    # Probe-mode shape: run success, aggregate gate skipped.
    api = FakeApi(
        [
            runs_payload(make_run(id=101, conclusion="success")),
            jobs_payload({"name": GATE_JOB_NAME, "conclusion": "skipped"}),
        ]
    )

    exit_code, _sleeps = drive_require(api, timeout=600, interval=60)

    assert exit_code == 1


def test_a_malformed_sha_is_refused_before_any_api_call() -> None:
    api = FakeApi([])

    exit_code = require(
        [
            "require",
            "--workflow",
            "ci.yml",
            "--repository",
            REPOSITORY,
            "--sha",
            "a" * 39,
        ],
        api=api,
        monotonic=lambda: 0.0,
        sleep=lambda _seconds: None,
    )

    assert exit_code == 1
    assert api.calls == []
