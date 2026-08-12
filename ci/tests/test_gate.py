from __future__ import annotations

from pathlib import Path

import pytest

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
            "current_sha": HEAD,
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
    assert payload["verdict"] == "success"
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
