from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from ci.evidence import (
    EvidenceError,
    artifact_records,
    build_envelope,
    machine_output_claim,
)
from ci.flake_policy import (
    FLAKE_POLICY_COMMAND,
    FlakePolicyError,
    build_report,
    parse_policy_output,
    validate_report,
)
from ci.playwright_flake_policy import _quarantine_marker
from ci.verification import build_plan, create_report, dump_json, report_summary
from tests_ci.helpers import component_output, repository_with_change, selection_for


def policy_log(
    *,
    attempted: int,
    passed: int,
    failed: int,
    skipped: int,
    rerun: int,
    quarantined: int,
    complete: int = 1,
) -> str:
    return (
        f"{passed} passed, {failed} failed, {skipped} skipped in 0.01s\n"
        "DTC_FLAKE_POLICY_V1 "
        f"attempted={attempted} passed={passed} failed={failed} skipped={skipped} "
        f"rerun={rerun} quarantined={quarantined} complete={complete}\n"
    )


def test_policy_parser_exposes_attempts_and_quarantine_without_inventing_retries() -> None:
    parsed = parse_policy_output(
        policy_log(
            attempted=3,
            passed=2,
            failed=0,
            skipped=1,
            rerun=0,
            quarantined=1,
        ).encode()
    )

    assert parsed == {
        "attempted": 3,
        "passed": 2,
        "failed": 0,
        "skipped": 1,
        "rerun": 0,
        "quarantined": 1,
        "complete": True,
    }


def test_quarantine_marker_rejects_positional_arguments() -> None:
    marker = SimpleNamespace(args=(208,), kwargs={"issue": 208})

    class Item:
        nodeid = "playwright_tests/test_flaky.py::test_flaky"

        def iter_markers(self, name: str):
            assert name == "quarantine"
            return iter((marker,))

    with pytest.raises(pytest.UsageError, match="exactly"):
        _quarantine_marker(Item())


def test_partial_passing_subset_is_not_a_complete_policy_summary() -> None:
    with pytest.raises(FlakePolicyError, match="outcomes"):
        parse_policy_output(
            policy_log(
                attempted=3,
                passed=2,
                failed=0,
                skipped=0,
                rerun=0,
                quarantined=1,
                complete=0,
            ).encode()
        )


def test_incomplete_successful_blocking_output_cannot_become_evidence(tmp_path: Path) -> None:
    repository, base, head = repository_with_change(
        tmp_path,
        {"api/service.py": "changed\n"},
        initial={"api/service.py": "initial\n", "Dockerfile": "FROM scratch\n"},
    )
    selection, records = selection_for(("api/service.py",), base=base, head=head)
    plan = build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=base,
        head=head,
        selection=selection,
        records=records,
        now=datetime(2026, 8, 9, 12, tzinfo=UTC),
    )
    root = tmp_path / "evidence"
    root.mkdir()
    output = root / "playwright-output.log"
    output.write_text(
        policy_log(
            attempted=1,
            passed=1,
            failed=0,
            skipped=0,
            rerun=0,
            quarantined=1,
            complete=0,
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvidenceError, match="partial"):
        machine_output_claim(
            output,
            root=root,
            component="playwright",
            plan=plan,
            result="success",
        )


def test_failed_quarantined_case_keeps_the_aggregate_report_red(tmp_path: Path) -> None:
    repository, base, head = repository_with_change(
        tmp_path,
        {"api/service.py": "changed\n"},
        initial={"api/service.py": "initial\n", "Dockerfile": "FROM scratch\n"},
    )
    selection, records = selection_for(("api/service.py",), base=base, head=head)
    plan = build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=base,
        head=head,
        selection=selection,
        records=records,
        now=datetime(2026, 8, 9, 12, tzinfo=UTC),
    )
    root = tmp_path / "evidence"
    root.mkdir()
    origin = {
        "issue": 208,
        "kind": "local",
        "producer_role": "engineer",
        "worktree": "issue-208-engineer",
    }
    for component, component_plan in plan["components"].items():
        if component_plan["disposition"] != "rerun":
            continue
        if component == "playwright":
            output_path = root / "playwright-output.log"
            output_path.write_text(
                policy_log(
                    attempted=2,
                    passed=1,
                    failed=1,
                    skipped=0,
                    rerun=0,
                    quarantined=1,
                ),
                encoding="utf-8",
            )
            artifacts = artifact_records((output_path,), root=root)
            machine_output = machine_output_claim(
                output_path,
                root=root,
                component=component,
                plan=plan,
                result="failure",
            )
            result = "failure"
            exit_code = 1
        else:
            artifacts, machine_output = component_output(root, plan, component)
            result = "success"
            exit_code = 0
        envelope = build_envelope(
            plan=plan,
            component=component,
            result=result,
            origin=origin,
            command=component_plan["command"],
            execution_environment=component_plan["environment"],
            artifacts=artifacts,
            machine_output=machine_output,
            exit_code=exit_code,
            completed_at=datetime(2026, 8, 9, 12, tzinfo=UTC),
        )
        dump_json(envelope, root / f"{component}-evidence.json")

    report = create_report(plan=plan, result_directory=root, phase="engineer")
    assert report["verdict"] == "failure"
    playwright = next(
        entry for entry in report["buckets"]["rerun"] if entry["component"] == "playwright"
    )
    assert playwright["result"] == "failure"
    assert {
        key: playwright["evidence"]["counts"][key]
        for key in ("attempted", "failed", "quarantined", "rerun")
    } == {
        "attempted": 2,
        "failed": 1,
        "quarantined": 1,
        "rerun": 0,
    }
    summary = report_summary(report)
    assert "Flake-policy counts: attempted=2, passed=1, failed=1, rerun=0, quarantined=1" in summary


def test_scheduled_quarantine_failure_is_reported_without_becoming_a_pass(tmp_path: Path) -> None:
    output = tmp_path / "playwright-quarantine-output.log"
    output.write_text(
        policy_log(
            attempted=2,
            passed=1,
            failed=1,
            skipped=0,
            rerun=0,
            quarantined=2,
        ),
        encoding="utf-8",
    )

    report = build_report(
        output_path=output,
        command=FLAKE_POLICY_COMMAND,
        exit_code=1,
    )

    assert report["verdict"] == "failure"
    assert report["reason"] == "quarantined_test_failed"
    assert report["counts"] == {
        "attempted": 2,
        "passed": 1,
        "failed": 1,
        "skipped": 0,
        "rerun": 0,
        "quarantined": 2,
    }
    assert validate_report(report) == report


def test_unexpected_rerun_cannot_make_a_successful_monitor_pass(tmp_path: Path) -> None:
    output = tmp_path / "playwright-quarantine-output.log"
    output.write_text(
        policy_log(
            attempted=1,
            passed=1,
            failed=0,
            skipped=0,
            rerun=1,
            quarantined=1,
        ),
        encoding="utf-8",
    )

    report = build_report(
        output_path=output,
        command=FLAKE_POLICY_COMMAND,
        exit_code=0,
    )

    assert report["verdict"] == "failure"
    assert report["reason"] == "rerun_not_allowed"


def test_malformed_or_partial_monitor_output_fails_closed(tmp_path: Path) -> None:
    output = tmp_path / "playwright-quarantine-output.log"
    output.write_text("1 passed in 0.01s\n", encoding="utf-8")

    report = build_report(
        output_path=output,
        command=FLAKE_POLICY_COMMAND,
        exit_code=0,
    )

    assert report["verdict"] == "failure"
    assert report["reason"] == "invalid_output"
    assert report["complete"] is False
