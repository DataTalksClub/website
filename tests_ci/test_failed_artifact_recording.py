from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ci.verification import build_plan, dump_json
from tests_ci.helpers import repository_with_change, selection_for

ROOT = Path(__file__).resolve().parents[1]


def _plan_and_record_paths(tmp_path: Path, *, result: str, machine_output: Path):
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
        now=datetime(2026, 8, 19, 12, tzinfo=UTC),
    )
    root = tmp_path / "evidence"
    root.mkdir(parents=True, exist_ok=True)
    environment = root / "quality-environment.json"
    dump_json(plan["components"]["quality"]["environment"], environment)
    plan_path = root / "verification-plan.json"
    dump_json(plan, plan_path)
    evidence = root / "quality-evidence.json"
    arguments = [
        "record",
        "--plan",
        str(plan_path),
        "--component",
        "quality",
        "--result",
        result,
        "--command",
        plan["components"]["quality"]["command"],
        "--output",
        str(evidence),
        "--artifact-root",
        str(root),
        "--machine-output",
        str(machine_output),
        "--execution-environment",
        str(environment),
        "--origin-kind",
        "local",
        "--issue",
        "202",
        "--producer-role",
        "engineer",
        "--worktree",
        "issue-202-engineer",
    ]
    return plan, root, evidence, arguments


def _run_record(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ci.verification", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("shape", ["missing", "directory"])
def test_failed_component_records_envelope_and_aggregate_when_output_is_irregular(
    tmp_path: Path, shape: str
) -> None:
    machine_output = tmp_path / "evidence" / "quality-output.log"
    if shape == "directory":
        machine_output.mkdir(parents=True)

    plan, root, evidence, arguments = _plan_and_record_paths(
        tmp_path, result="failure", machine_output=machine_output
    )
    recorded = _run_record(arguments)

    assert recorded.returncode == 0, recorded.stderr
    envelope = json.loads(evidence.read_text(encoding="utf-8"))
    assert envelope["result"] == "failure"
    assert envelope["output"]["artifact"]["path"] == "quality-artifact-collection.json"

    marker = json.loads((root / "quality-artifact-collection.json").read_text(encoding="utf-8"))
    assert marker["artifact_collection"]["status"] == "partial"
    assert marker["artifact_collection"]["errors"] == [
        {"path": "quality-output.log", "reason": "artifact must be a regular file"}
    ]

    result_payload = json.loads((root / "quality-result.json").read_text(encoding="utf-8"))
    assert result_payload["artifact_collection"]["marker"] == ("quality-artifact-collection.json")
    report_path = root / "verification-report.json"
    reported = subprocess.run(
        [
            sys.executable,
            "-m",
            "ci.verification",
            "report",
            "--plan",
            str(root / "verification-plan.json"),
            "--result-directory",
            str(root),
            "--phase",
            "engineer",
            "--output",
            str(report_path),
            "--no-fail-exit",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert reported.returncode == 0, reported.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    quality = next(entry for entry in report["buckets"]["rerun"] if entry["component"] == "quality")
    assert report["verdict"] == "failure"
    assert quality["result"] == "failure"
    assert report["head"] == plan["head"]


@pytest.mark.parametrize("shape", ["missing", "directory"])
def test_success_component_recording_remains_strict_for_irregular_output(
    tmp_path: Path, shape: str
) -> None:
    machine_output = tmp_path / "evidence" / "quality-output.log"
    if shape == "directory":
        machine_output.mkdir(parents=True)

    _plan, _root, evidence, arguments = _plan_and_record_paths(
        tmp_path, result="success", machine_output=machine_output
    )
    recorded = _run_record(arguments)

    assert recorded.returncode != 0
    assert "artifact must be a regular file" in recorded.stderr
    assert not evidence.exists()
