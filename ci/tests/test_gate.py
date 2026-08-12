from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ci.evidence import build_envelope
from ci.gate import (
    NORMAL_REQUIRED_JOBS,
    SCHEDULE_COMPONENTS,
    marker_gate,
    normal_gate,
    scheduled_gate,
)
from ci.provenance import (
    EvidenceResolution,
    build_provenance,
    dump_provenance,
    dump_resolution,
    selection_digest,
)
from ci.schedule import dump_schedule_decision, unavailable_decision
from ci.selection import ChangeRecord, classify_records, dump_selection
from ci.verification import build_plan, create_report, dump_json
from tests_ci.helpers import component_output, repository_with_change, selection_for

BASE = "1" * 40
HEAD = "2" * 40


def selection_file(tmp_path: Path) -> tuple[Path, Path]:
    selection = classify_records(
        (ChangeRecord("M", ("api/a.py",)),), event="push", base=BASE, head=HEAD
    )
    path = tmp_path / "selection.json"
    dump_selection(selection, path)
    provenance = build_provenance(
        selection,
        run_id="7",
        created_attempt=1,
        controller_sha="3" * 40,
        release_sha=HEAD,
        source_after_sha=HEAD,
        source_before_sha=BASE,
    )
    provenance_path = tmp_path / "provenance.json"
    dump_provenance(provenance, provenance_path)
    resolution_path = tmp_path / "resolution"
    dump_resolution(
        EvidenceResolution(
            selection=selection,
            provenance=provenance,
            mode="current_attempt",
            resolved_attempt=1,
        ),
        resolution_path,
    )
    return path, resolution_path / "ci-selection-resolution.json"


def decision_file(tmp_path: Path, *, skip: bool) -> Path:
    if skip:
        decision = {
            "coverage_anchor_run_id": 7,
            "coverage_anchor_sha": HEAD,
            "coverage_anchor_state_sha256": None,
            "current_sha": HEAD,
            "current_state_sha256": None,
            "decision": "skip",
            "event": "schedule",
            "history_depth_inspected": 1,
            "previous_run_conclusion": "success",
            "previous_run_id": 7,
            "reason": "already_successfully_covered",
            "schema_version": 1,
        }
    else:
        decision = unavailable_decision(HEAD)
    path = tmp_path / "decision.json"
    dump_schedule_decision(decision, path)
    return path


def test_normal_gate_requires_every_job_and_valid_selection(tmp_path: Path) -> None:
    outcomes = {name: "success" for name in NORMAL_REQUIRED_JOBS}
    selection, evidence = selection_file(tmp_path)
    payload = normal_gate(
        selection,
        outcomes,
        evidence_path=evidence,
        expected_event="push",
        expected_source_after_sha=HEAD,
        expected_source_before_sha=BASE,
        expected_selection_sha256=selection_digest(selection.read_bytes()),
    )
    assert payload["verdict"] == "failure"
    assert payload["verification_status"] == "invalid"
    assert payload["selection_evidence"]["source_after_sha"] == HEAD
    assert payload["selection_evidence"]["source_before_sha"] == BASE
    digest_mismatch = normal_gate(
        selection,
        outcomes,
        evidence_path=evidence,
        expected_selection_sha256="0" * 64,
    )
    assert digest_mismatch["verdict"] == "failure"
    assert digest_mismatch["selection_rejection_reason"] == "classifier_digest_mismatch"

    for name in NORMAL_REQUIRED_JOBS:
        failed = {**outcomes, name: "skipped"}
        assert normal_gate(selection, failed, evidence_path=evidence)["verdict"] == "failure"
    assert (
        normal_gate(tmp_path / "missing.json", outcomes, evidence_path=evidence)["verdict"]
        == "failure"
    )
    assert normal_gate(selection, outcomes)["selection_rejection_reason"] == "evidence_missing"


def test_normal_gate_requires_exact_ci_report_and_artifact_bound_evidence(
    tmp_path: Path,
) -> None:
    repository, base, head = repository_with_change(tmp_path, {"README.md": "changed\n"})
    selection, records = selection_for(("README.md",), base=base, head=head)
    plan = build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=base,
        head=head,
        selection=selection,
        records=records,
        now=datetime(2026, 8, 9, 12, tzinfo=UTC),
    )
    selection_path = tmp_path / "selection.json"
    plan_path = tmp_path / "plan.json"
    report_path = tmp_path / "report.json"
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    dump_selection(selection, selection_path)
    provenance = build_provenance(
        selection,
        run_id="7",
        created_attempt=1,
        controller_sha="3" * 40,
        release_sha=head,
        source_after_sha=head,
        source_before_sha=base,
    )
    provenance_path = evidence / "provenance.json"
    dump_provenance(provenance, provenance_path)
    resolution_path = evidence / "resolution"
    dump_resolution(
        EvidenceResolution(
            selection=selection,
            provenance=provenance,
            mode="current_attempt",
            resolved_attempt=1,
        ),
        resolution_path,
    )
    dump_json(plan, plan_path)
    origins = {
        "selector": ("classification", "ci-selection-7-attempt-1"),
        "evidence_validation": (
            "quality",
            "verification-component-quality-7-attempt-1",
        ),
    }
    for component, (job_id, artifact_id) in origins.items():
        artifact = evidence / f"{component}-result.json"
        artifact.write_text('{"status":"success"}\n', encoding="utf-8")
        records, output = component_output(evidence, plan, component, path=artifact)
        envelope = build_envelope(
            plan=plan,
            component=component,
            result="success",
            command=plan["components"][component]["command"],
            execution_environment=plan["components"][component]["environment"],
            origin={
                "artifact_id": artifact_id,
                "job_id": job_id,
                "kind": "github_actions",
                "ref": "refs/heads/main",
                "repository": "DataTalksClub/website",
                "run_attempt": 1,
                "run_id": 7,
                "workflow": ".github/workflows/ci.yml",
            },
            artifacts=records,
            machine_output=output,
            completed_at=datetime(2026, 8, 9, 12, tzinfo=UTC),
        )
        dump_json(envelope, evidence / f"{component}-evidence.json")
    report = create_report(plan=plan, result_directory=evidence, phase="ci")
    dump_json(report, report_path)
    outcomes = {name: "success" for name in NORMAL_REQUIRED_JOBS}
    result = normal_gate(
        selection_path,
        outcomes,
        evidence_path=resolution_path / "ci-selection-resolution.json",
        expected_event="push",
        expected_source_after_sha=head,
        expected_source_before_sha=base,
        verification_plan_path=plan_path,
        verification_report_path=report_path,
        verification_evidence_directory=evidence,
    )
    assert result["verdict"] == "success"

    unknown_id_report = deepcopy(report)
    selector_entry = next(
        entry for entry in unknown_id_report["buckets"]["rerun"] if entry["component"] == "selector"
    )
    selector_entry["evidence"]["evidence_id"] = "0" * 64
    selector_entry["evidence"]["envelope_sha256"] = "0" * 64
    dump_json(unknown_id_report, report_path)
    result = normal_gate(
        selection_path,
        outcomes,
        evidence_path=resolution_path / "ci-selection-resolution.json",
        verification_plan_path=plan_path,
        verification_report_path=report_path,
        verification_evidence_directory=evidence,
    )
    assert result["verdict"] == "failure"

    dump_json(report, report_path)
    (evidence / "selector-result.json").unlink()
    result = normal_gate(
        selection_path,
        outcomes,
        evidence_path=resolution_path / "ci-selection-resolution.json",
        verification_plan_path=plan_path,
        verification_report_path=report_path,
        verification_evidence_directory=evidence,
    )
    assert result["verdict"] == "failure"


def test_normal_gate_fails_closed_for_missing_or_malformed_verification_report(
    tmp_path: Path,
) -> None:
    outcomes = {name: "success" for name in NORMAL_REQUIRED_JOBS}
    result = normal_gate(
        selection_file(tmp_path)[0],
        outcomes,
        verification_plan_path=tmp_path / "missing-plan.json",
        verification_report_path=tmp_path / "missing-report.json",
    )
    assert result["verdict"] == "failure"
    assert result["verification_status"] == "invalid"

    result = normal_gate(
        selection_file(tmp_path)[0],
        outcomes,
        verification_plan_path=tmp_path / "missing-plan.json",
    )
    assert result["verdict"] == "failure"


@pytest.mark.parametrize("outcome", ["failure", "cancelled", "skipped", "timed_out", "neutral"])
def test_marker_requires_every_selected_component(outcome: str) -> None:
    successful = {name: "success" for name in SCHEDULE_COMPONENTS}
    assert marker_gate(successful)
    assert not marker_gate({**successful, "django": outcome})


def test_scheduled_gate_accepts_only_intentional_skip_shape(tmp_path: Path) -> None:
    skipped = {
        "selector": "success",
        **{name: "skipped" for name in SCHEDULE_COMPONENTS},
        "full-regression": "skipped",
    }
    path = decision_file(tmp_path, skip=True)
    assert scheduled_gate(path, skipped)["verdict"] == "success"
    assert scheduled_gate(path, {**skipped, "django": "success"})["verdict"] == "failure"


def test_scheduled_gate_retries_failed_selected_component(tmp_path: Path) -> None:
    selected = {
        "selector": "success",
        **{name: "success" for name in SCHEDULE_COMPONENTS},
        "full-regression": "success",
    }
    path = decision_file(tmp_path, skip=False)
    assert scheduled_gate(path, selected)["verdict"] == "success"
    assert scheduled_gate(path, {**selected, "django": "failure"})["verdict"] == "failure"
    assert scheduled_gate(path, {**selected, "full-regression": "skipped"})["verdict"] == "failure"
