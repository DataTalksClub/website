from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from ci.provenance import (
    EvidenceError,
    artifact_name,
    build_provenance,
    canonical_selection_bytes,
    dump_provenance,
    dump_resolution,
    load_resolution,
    resolve_artifact,
    selection_digest,
    validate_artifact,
)
from ci.selection import ChangeRecord, classify_records, dump_selection

BASE = "1" * 40
HEAD = "2" * 40
CONTROLLER = "3" * 40
RUN_ID = "1234"


def selection() -> dict[str, object]:
    return classify_records(
        (ChangeRecord("M", ("api/module.py",)),),
        event="push",
        base=BASE,
        head=HEAD,
    )


def write_artifact(directory: Path, *, run_id: str = RUN_ID, attempt: int = 1) -> None:
    payload = selection()
    dump_selection(payload, directory / "ci-selection.json")
    dump_provenance(
        build_provenance(
            payload,
            run_id=run_id,
            created_attempt=attempt,
            controller_sha=CONTROLLER,
            release_sha=HEAD,
            source_after_sha=HEAD,
            source_before_sha=BASE,
        ),
        directory / "ci-selection-provenance.json",
    )


def test_current_artifact_is_bound_to_run_attempt_and_digest(tmp_path: Path) -> None:
    directory = tmp_path / "current"
    directory.mkdir()
    write_artifact(directory)

    resolved = validate_artifact(
        directory,
        expected_run_id=RUN_ID,
        expected_attempt=1,
        controller_sha=CONTROLLER,
        release_sha=HEAD,
        event="push",
        source_after_sha=HEAD,
        source_before_sha=BASE,
        expected_selection_sha256=selection_digest((directory / "ci-selection.json").read_bytes()),
    )
    assert resolved.provenance["artifact_name"] == artifact_name(RUN_ID, 1)
    assert resolved.provenance["selection_sha256"] == selection_digest(
        (directory / "ci-selection.json").read_bytes()
    )
    with pytest.raises(EvidenceError, match="classifier_digest_mismatch"):
        validate_artifact(
            directory,
            expected_run_id=RUN_ID,
            expected_attempt=1,
            controller_sha=CONTROLLER,
            release_sha=HEAD,
            event="push",
            source_after_sha=HEAD,
            source_before_sha=BASE,
            expected_selection_sha256="0" * 64,
        )


def test_noncanonical_or_tampered_selection_fails_closed(tmp_path: Path) -> None:
    directory = tmp_path / "current"
    directory.mkdir()
    write_artifact(directory)
    selection_path = directory / "ci-selection.json"
    selection_path.write_text(
        json.dumps(json.loads(selection_path.read_text(encoding="utf-8")), sort_keys=True),
        encoding="utf-8",
    )
    with pytest.raises(EvidenceError, match="selection_bytes_not_canonical"):
        validate_artifact(
            directory,
            expected_run_id=RUN_ID,
            expected_attempt=1,
            controller_sha=CONTROLLER,
            release_sha=HEAD,
            event="push",
            source_after_sha=HEAD,
            source_before_sha=BASE,
        )

    write_artifact(directory)
    selection_path.write_bytes(selection_path.read_bytes().replace(b'"api"', b'"data"', 1))
    with pytest.raises(
        EvidenceError,
        match="selection_invalid|selection_bytes_not_canonical|selection_digest_mismatch",
    ):
        validate_artifact(
            directory,
            expected_run_id=RUN_ID,
            expected_attempt=1,
            controller_sha=CONTROLLER,
            release_sha=HEAD,
            event="push",
            source_after_sha=HEAD,
            source_before_sha=BASE,
        )


def test_wrong_controller_release_run_and_source_are_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "current"
    directory.mkdir()
    write_artifact(directory)
    kwargs = {
        "expected_run_id": RUN_ID,
        "expected_attempt": 1,
        "controller_sha": CONTROLLER,
        "release_sha": HEAD,
        "event": "push",
        "source_after_sha": HEAD,
        "source_before_sha": BASE,
    }
    for field, value, reason in (
        ("expected_run_id", "9999", "run_id_mismatch"),
        ("expected_attempt", 2, "created_attempt_mismatch"),
        ("controller_sha", "4" * 40, "controller_sha_mismatch"),
        ("release_sha", "4" * 40, "release_sha_mismatch"),
        ("source_after_sha", "4" * 40, "source_after_sha_mismatch"),
        ("source_before_sha", "4" * 40, "source_before_sha_mismatch"),
    ):
        with pytest.raises(EvidenceError, match=reason):
            validate_artifact(directory, **cast(Any, {**kwargs, field: value}))

    with pytest.raises(EvidenceError, match="classifier_profile_mismatch"):
        validate_artifact(directory, **cast(Any, {**kwargs, "expected_profile": "full"}))
    with pytest.raises(EvidenceError, match="classifier_reason_mismatch"):
        validate_artifact(directory, **cast(Any, {**kwargs, "expected_reason": "manual_dispatch"}))


def test_extra_artifact_entries_are_ambiguous(tmp_path: Path) -> None:
    directory = tmp_path / "current"
    directory.mkdir()
    write_artifact(directory)
    (directory / "unexpected.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(EvidenceError, match="artifact_files_ambiguous"):
        validate_artifact(
            directory,
            expected_run_id=RUN_ID,
            expected_attempt=1,
            controller_sha=CONTROLLER,
            release_sha=HEAD,
            event="push",
            source_after_sha=HEAD,
            source_before_sha=BASE,
        )


def test_reused_classifier_can_only_fallback_to_same_run_attempt_one(tmp_path: Path) -> None:
    fallback = tmp_path / "attempt-1"
    fallback.mkdir()
    write_artifact(fallback, attempt=1)

    resolved = resolve_artifact(
        current_directory=tmp_path / "missing-current",
        fallback_directory=fallback,
        expected_run_id=RUN_ID,
        current_attempt=2,
        classifier_created_attempt=1,
        controller_sha=CONTROLLER,
        release_sha=HEAD,
        event="push",
        source_after_sha=HEAD,
        source_before_sha=BASE,
        expected_selection_sha256=selection_digest((fallback / "ci-selection.json").read_bytes()),
    )
    assert resolved.mode == "reused_attempt_1"
    assert resolved.resolved_attempt == 1

    with pytest.raises(EvidenceError, match="current_attempt_evidence_missing"):
        resolve_artifact(
            current_directory=tmp_path / "missing-current",
            fallback_directory=fallback,
            expected_run_id=RUN_ID,
            current_attempt=2,
            classifier_created_attempt=2,
            controller_sha=CONTROLLER,
            release_sha=HEAD,
            event="push",
            source_after_sha=HEAD,
            source_before_sha=BASE,
        )


def test_malformed_current_artifact_never_falls_back(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir()
    (current / "ci-selection.json").write_text("{}\n", encoding="utf-8")
    fallback = tmp_path / "attempt-1"
    fallback.mkdir()
    write_artifact(fallback, attempt=1)

    with pytest.raises(EvidenceError, match="artifact_files_missing|selection_invalid"):
        resolve_artifact(
            current_directory=current,
            fallback_directory=fallback,
            expected_run_id=RUN_ID,
            current_attempt=2,
            classifier_created_attempt=1,
            controller_sha=CONTROLLER,
            release_sha=HEAD,
            event="push",
            source_after_sha=HEAD,
            source_before_sha=BASE,
        )


def test_resolution_is_bounded_and_records_mode(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir()
    write_artifact(current)
    resolved = resolve_artifact(
        current_directory=current,
        fallback_directory=tmp_path / "attempt-1",
        expected_run_id=RUN_ID,
        current_attempt=1,
        classifier_created_attempt=1,
        controller_sha=CONTROLLER,
        release_sha=HEAD,
        event="push",
        source_after_sha=HEAD,
        source_before_sha=BASE,
    )
    output = tmp_path / "resolved"
    dump_resolution(resolved, output)
    assert load_resolution(output / "ci-selection-resolution.json")["mode"] == "current_attempt"
    assert (output / "ci-selection.json").read_bytes() == canonical_selection_bytes(selection())


def test_manual_selection_has_no_push_source_identity(tmp_path: Path) -> None:
    payload: dict[str, object] = {
        "application_roots": [],
        "base": None,
        "changed_path_count": 0,
        "event": "workflow_dispatch",
        "head": HEAD,
        "profile": "full",
        "reason": "manual_dispatch",
        "schema_version": 1,
        "test_labels": [],
    }
    provenance = build_provenance(
        payload,
        run_id=RUN_ID,
        created_attempt=1,
        controller_sha=CONTROLLER,
        release_sha=HEAD,
        source_after_sha=None,
        source_before_sha=None,
    )
    assert provenance["source_after_sha"] is None
    with pytest.raises(EvidenceError, match="manual_source_identity_unexpected"):
        build_provenance(
            payload,
            run_id=RUN_ID,
            created_attempt=1,
            controller_sha=CONTROLLER,
            release_sha=HEAD,
            source_after_sha=HEAD,
            source_before_sha=None,
        )
