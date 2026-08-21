"""A hung component command must never prevent the aggregate report.

These regression tests cover the bounded component execution added for #199:
every component runs under an explicit wall-clock timeout, expiry terminates
and then kills the component's whole process group while retaining its partial
output, the result envelope records an explicit ``timed_out`` failure, the
remaining components still execute, and the aggregate report is still emitted
with a ``failure`` verdict instead of wedging the run forever.
"""

from __future__ import annotations

import json
import os
import shlex
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ci.runner import (
    DEFAULT_COMPONENT_TIMEOUT_SECONDS,
    TIMEOUT_EXIT_CODE,
    ComponentExecution,
    RunnerError,
    execute_component,
    run_plan,
)
from ci.verification import build_plan, create_report
from tests_ci.helpers import repository_with_change, selection_for

# The documented default in `_docs/ci/change-selective-ci.md` is one hour per
# component; pinning it here keeps the code and the document from drifting
# apart silently.
assert DEFAULT_COMPONENT_TIMEOUT_SECONDS == 3600.0


def plan_for(tmp_path: Path, changed: dict[str, str]):
    repository, base, head = repository_with_change(tmp_path, changed)
    selection, records = selection_for(tuple(changed), base=base, head=head)
    return repository, build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=base,
        head=head,
        selection=selection,
        records=records,
        now=datetime(2026, 8, 19, 12, tzinfo=UTC),
    )


def green_component_commands(repository: Path, output: Path, *, hang_evidence_validation: bool):
    """Allowlisted execution vectors that finish instantly, plus one optional hang."""

    def command_for(plan, component):
        if component == "evidence_validation" and hang_evidence_validation:
            return ("sh", "-c", "echo before-hang; sleep 30")
        if component == "container":
            payload = json.dumps(
                {
                    "assertions": ["container_contract"],
                    "revision": plan["head"],
                    "schema_version": 1,
                    "status": "pass",
                }
            )
            destination = shlex.quote(str(output / "container-check.json"))
            return ("sh", "-c", f"echo {shlex.quote(payload)} > {destination}")
        log_output = "2 passed in 0.01s\n"
        if component == "playwright":
            log_output += (
                "DTC_FLAKE_POLICY_V1 attempted=2 passed=2 failed=0 skipped=0 "
                "rerun=0 quarantined=0 complete=1\n"
            )
        return ("printf", log_output)

    return command_for


def test_execute_component_times_out_and_retains_partial_output(tmp_path: Path) -> None:
    output = tmp_path / "partial-output.log"

    execution = execute_component(
        ("sh", "-c", "echo partial-output; sleep 30"),
        repository=tmp_path,
        environment={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        output_path=output,
        timeout_seconds=1.0,
        grace_seconds=0.5,
    )

    assert execution.timed_out is True
    assert execution.result == "timed_out"
    assert execution.exit_code == TIMEOUT_EXIT_CODE
    assert output.read_text(encoding="utf-8") == "partial-output\n"


def test_execute_component_kills_a_group_that_ignores_termination(tmp_path: Path) -> None:
    output = tmp_path / "stuck-output.log"
    started = time.monotonic()

    execution = execute_component(
        ("sh", "-c", "echo stuck-output; trap '' TERM INT; while true; do sleep 1; done"),
        repository=tmp_path,
        environment={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        output_path=output,
        timeout_seconds=1.0,
        grace_seconds=0.5,
    )

    assert time.monotonic() - started < 20
    assert execution.timed_out is True
    assert execution.returncode < 0  # reaped only after the escalating kill
    assert execution.exit_code == TIMEOUT_EXIT_CODE
    assert output.read_text(encoding="utf-8") == "stuck-output\n"


def test_execute_component_drains_stragglers_before_returning(tmp_path: Path) -> None:
    # A grandchild that survives SIGTERM past its parent must finish writing
    # before the runner freezes the output artifact: the direct child
    # (`exec sleep`) dies immediately, while the trapped grandchild emits its
    # line afterwards.  Without draining the group, evidence digests would be
    # taken while the log can still grow (the quality component hit exactly
    # this when its inner `make` printed "Error 143" after its driver died).
    straggler = "trap '' TERM; sleep 0.8; echo late-write"
    output = tmp_path / "late-write.log"

    execution = execute_component(
        ("sh", "-c", f"sh -c {shlex.quote(straggler)} & exec sleep 30"),
        repository=tmp_path,
        environment={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        output_path=output,
        timeout_seconds=0.5,
        grace_seconds=1.0,
    )

    assert execution.timed_out is True
    assert output.read_text(encoding="utf-8") == "late-write\n"


def test_component_execution_maps_plain_exit_statuses(tmp_path: Path) -> None:
    success = ComponentExecution(("true",), tmp_path / "a.log", 0, False)
    assert (success.result, success.exit_code) == ("success", 0)
    failure = ComponentExecution(("false",), tmp_path / "b.log", 3, False)
    assert (failure.result, failure.exit_code) == ("failure", 3)
    killed = ComponentExecution(("hang",), tmp_path / "c.log", -9, True)
    assert (killed.result, killed.exit_code) == ("timed_out", TIMEOUT_EXIT_CODE)


def test_run_plan_rejects_a_non_positive_component_timeout(tmp_path: Path) -> None:
    repository, plan = plan_for(tmp_path, {"api/service.py": "changed\n"})
    with pytest.raises(RunnerError, match="positive number of seconds"):
        run_plan(
            plan=plan,
            repository=repository,
            output_directory=tmp_path / "evidence",
            issue=199,
            worktree="issue-199-runner-component-timeout",
            producer_role="engineer",
            component_timeout_seconds=0,
        )


def test_run_plan_rejects_a_non_finite_component_timeout(tmp_path: Path) -> None:
    repository, plan = plan_for(tmp_path, {"api/service.py": "changed\n"})
    with pytest.raises(RunnerError, match="positive number of seconds"):
        run_plan(
            plan=plan,
            repository=repository,
            output_directory=tmp_path / "evidence",
            issue=199,
            worktree="issue-199-runner-component-timeout",
            producer_role="engineer",
            component_timeout_seconds=float("nan"),
        )


def test_run_plan_bounds_a_hanging_component_and_still_emits_the_report(
    monkeypatch, tmp_path: Path
) -> None:
    repository, plan = plan_for(tmp_path, {"api/service.py": "changed\n"})
    output = tmp_path / "evidence"
    monkeypatch.setattr(
        "ci.runner.command_for",
        green_component_commands(repository, output, hang_evidence_validation=True),
    )
    started = time.monotonic()

    exit_code = run_plan(
        plan=plan,
        repository=repository,
        output_directory=output,
        issue=199,
        worktree="issue-199-runner-component-timeout",
        producer_role="engineer",
        component_timeout_seconds=2.0,
    )

    assert exit_code == 1
    assert time.monotonic() - started < 30  # the 30s hang never ran to completion
    result = json.loads((output / "evidence_validation-result.json").read_text(encoding="utf-8"))
    assert result["result"] == "timed_out"
    assert result["timed_out"] is True
    assert result["exit_code"] == TIMEOUT_EXIT_CODE
    assert result["command"] == "sh -c 'echo before-hang; sleep 30'"
    assert (output / "evidence_validation-output.log").read_text(encoding="utf-8") == (
        "before-hang\n"
    )
    envelope = json.loads(
        (output / "evidence_validation-evidence.json").read_text(encoding="utf-8")
    )
    assert envelope["result"] == "timed_out"
    assert envelope["exit_code"] == TIMEOUT_EXIT_CODE
    assert envelope["command"] == plan["components"]["evidence_validation"]["command"]
    assert envelope["counts"]["tests"] == 0

    # The remaining components still executed after the timed-out one.
    executed = {
        path.name.removesuffix("-result.json"): json.loads(path.read_text(encoding="utf-8"))
        for path in output.glob("*-result.json")
    }
    assert set(executed) == {
        "container",
        "django",
        "evidence_validation",
        "playwright",
        "quality",
        "selector",
    }
    for component, item in executed.items():
        expected = "timed_out" if component == "evidence_validation" else "success"
        assert item["result"] == expected
        assert item["timed_out"] is (component == "evidence_validation")

    report = create_report(plan=plan, result_directory=output, phase="engineer")
    assert report["verdict"] == "failure"
    rerun = {entry["component"]: entry for entry in report["buckets"]["rerun"]}
    assert rerun["evidence_validation"]["result"] == "timed_out"
    assert report["buckets"]["skipped"] == []


def test_run_plan_green_run_is_unaffected_by_the_timeout_plumbing(
    monkeypatch, tmp_path: Path
) -> None:
    repository, plan = plan_for(tmp_path, {"api/service.py": "changed\n"})
    output = tmp_path / "evidence"
    monkeypatch.setattr(
        "ci.runner.command_for",
        green_component_commands(repository, output, hang_evidence_validation=False),
    )

    assert (
        run_plan(
            plan=plan,
            repository=repository,
            output_directory=output,
            issue=199,
            worktree="issue-199-runner-component-timeout",
            producer_role="engineer",
        )
        == 0
    )

    for path in output.glob("*-result.json"):
        result = json.loads(path.read_text(encoding="utf-8"))
        assert result["result"] == "success"
        assert result["timed_out"] is False
        assert result["exit_code"] == 0
    report = create_report(plan=plan, result_directory=output, phase="engineer")
    assert report["verdict"] == "success"
