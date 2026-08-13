from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ci.content_invariants import build_invariant_artifact
from ci.evidence import build_envelope
from ci.ownership import sha256_json
from ci.selection import ChangeRecord, classify_records
from ci.verification import (
    VerificationError,
    build_plan,
    build_scheduled_state_envelope,
    create_report,
    materialize_reused_evidence,
    read_worktree_change_records,
    report_summary,
    repository_state,
    validate_plan,
    validate_report,
)
from tests_ci.helpers import component_output, repository_with_change, selection_for

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)


def make_plan(tmp_path: Path, changed: dict[str, str]):
    repository, base, head = repository_with_change(tmp_path, changed)
    paths = tuple(changed)
    selection, records = selection_for(paths, base=base, head=head)
    return repository, build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=base,
        head=head,
        selection=selection,
        records=records,
        now=NOW,
    )


def scheduled_state_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    planned_image: tuple[str, str] = ("ubuntu24", "20260801.1"),
    actual_image: tuple[str, str] | None = None,
):
    monkeypatch.setenv("ImageOS", planned_image[0])
    monkeypatch.setenv("ImageVersion", planned_image[1])
    repository, plan = make_plan(tmp_path, {"api/service.py": "changed\n"})
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    origin = {"issue": 143, "kind": "local", "producer_role": "engineer", "worktree": "143"}
    for component, item in plan["components"].items():
        if item["disposition"] != "rerun":
            continue
        records, output = component_output(evidence, plan, component)
        envelope = build_envelope(
            plan=plan,
            component=component,
            result="success",
            origin=origin,
            command=item["command"],
            execution_environment=item["environment"],
            artifacts=records,
            machine_output=output,
            completed_at=NOW,
        )
        (evidence / f"{component}-evidence.json").write_text(
            __import__("json").dumps(envelope, sort_keys=True), encoding="utf-8"
        )
    report = create_report(plan=plan, result_directory=evidence, phase="engineer")
    if actual_image is not None:
        monkeypatch.setenv("ImageOS", actual_image[0])
        monkeypatch.setenv("ImageVersion", actual_image[1])
    state = repository_state(repository, plan["head"])
    return plan, report, state


def test_single_app_plan_reruns_affected_closure_and_preserves_baseline_without_evidence(
    tmp_path: Path,
) -> None:
    _repository, plan = make_plan(tmp_path, {"courses/service.py": "changed\n"})
    assert plan["profile"] == "focused"
    assert plan["test_labels"] == [
        "accounts",
        "api",
        "content.tests",
        "core",
        "courses",
        "data",
        "studio_courses",
    ]
    assert plan["components"]["django"]["disposition"] == "rerun"
    assert plan["components"]["quality"]["disposition"] == "rerun"
    assert plan["components"]["playwright"]["disposition"] == "rerun"
    assert plan["components"]["container"]["disposition"] == "rerun"
    assert plan["components"]["screenshots"]["disposition"] == "not_applicable"


@pytest.mark.parametrize(
    ("changed", "reason"),
    [
        ({"api/templates/api/page.html": "changed\n"}, "render_impact"),
        ({"accounts/services.py": "changed\n"}, "auth_security_privacy"),
        ({"api/migrations/0002_x.py": "changed\n"}, "schema_migration"),
        ({"tests_ci/test_new.py": "changed\n"}, "test_infrastructure"),
        ({"new_app/module.py": "changed\n"}, "unknown_impact"),
    ],
)
def test_force_full_matrix_is_fresh_and_fail_closed(
    tmp_path: Path, changed: dict[str, str], reason: str
) -> None:
    _repository, plan = make_plan(tmp_path, changed)
    assert plan["profile"] == "full"
    assert plan["reason"] == reason
    for component in ("container", "django", "playwright", "quality"):
        assert plan["components"][component]["disposition"] == "rerun"
    if reason == "render_impact":
        assert plan["browser_profile"] == "full"
        assert plan["components"]["screenshots"]["disposition"] == "rerun"


def test_cross_app_and_test_rename_or_delete_force_full(tmp_path: Path) -> None:
    repository, base, head = repository_with_change(
        tmp_path,
        {"api/service.py": "changed\n", "studio/service.py": "changed\n"},
    )
    records = (
        ChangeRecord("M", ("api/service.py",)),
        ChangeRecord("M", ("studio/service.py",)),
    )
    selection = classify_records(records, event="push", base=base, head=head)
    plan = build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=base,
        head=head,
        selection=selection,
        records=records,
        now=NOW,
    )
    assert plan["reason"] == "cross_application"

    renamed = (ChangeRecord("R100", ("api/tests/test_old.py", "api/tests/test_new.py")),)
    plan = build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=base,
        head=head,
        selection=selection,
        records=renamed,
        now=NOW,
    )
    assert plan["profile"] == "full"
    assert "test_infrastructure" in plan["risk_flags"]


def test_prose_only_docs_are_explicitly_not_applicable(tmp_path: Path) -> None:
    _repository, plan = make_plan(tmp_path, {"README.md": "changed\n"})
    assert plan["profile"] == "documentation"
    assert plan["reason"] == "documentation_only"
    assert plan["components"]["selector"]["disposition"] == "rerun"
    for component in (
        "compatibility",
        "container",
        "django",
        "playwright",
        "quality",
        "screenshots",
    ):
        assert plan["components"][component]["disposition"] == "not_applicable"


@pytest.mark.parametrize("path", ["api/views.py", "api/urls.py", "api/context_processors.py"])
def test_data_shape_and_navigation_changes_require_full_browser_and_screenshots(
    tmp_path: Path, path: str
) -> None:
    _repository, plan = make_plan(tmp_path, {path: "changed\n"})
    assert plan["profile"] == "full"
    assert plan["reason"] == "render_impact"
    assert plan["browser_profile"] == "full"
    assert plan["components"]["playwright"]["disposition"] == "rerun"
    assert plan["components"]["screenshots"]["disposition"] == "rerun"


def test_ci_render_plan_fails_closed_without_screenshot_evidence(tmp_path: Path) -> None:
    _repository, plan = make_plan(tmp_path, {"api/templates/api/page.html": "changed\n"})
    report = create_report(plan=plan, phase="ci")
    screenshot_entry = next(
        entry for entry in report["buckets"]["skipped"] if entry["component"] == "screenshots"
    )
    assert report["verdict"] == "failure"
    assert screenshot_entry["reason"] == "required_result_missing"


def test_screenshot_routes_are_derived_from_impacted_graph_nodes(tmp_path: Path) -> None:
    _repository, course_plan = make_plan(
        tmp_path, {"courses/templates/courses/catalog.html": "changed\n"}
    )
    assert {
        (capture["route"], capture["route_state"])
        for capture in course_plan["render"]["required_captures"]
    } == {("/courses", "courses")}
    assert {capture["viewport"] for capture in course_plan["render"]["required_captures"]} == {
        "desktop",
        "mobile",
    }


def test_empty_canonical_diff_uses_shared_planner_and_fails_closed_to_full(
    tmp_path: Path,
) -> None:
    repository, _base, head = repository_with_change(tmp_path, {"README.md": "changed\n"})
    selection = classify_records((), event="push", base=head, head=head)
    plan = build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=head,
        head=head,
        selection=selection,
        now=NOW,
    )
    assert plan["profile"] == "full"
    assert plan["reason"] == "diff_empty"
    assert plan["changed_paths"] == []
    assert plan["components"]["playwright"]["command"] == "make test-playwright-core"


def test_large_value_only_content_is_digest_exhaustive_without_visual_rerun(
    tmp_path: Path,
) -> None:
    records = [
        {"id": f"course-{index}", "title": f"Course {index}", "url": f"/courses/{index}"}
        for index in range(10_000)
    ]
    body = __import__("json").dumps(records)
    _repository, plan = make_plan(tmp_path, {"data/catalog.json": body})
    assert plan["profile"] == "focused"
    assert plan["browser_profile"] == "core"
    assert plan["components"]["django"]["disposition"] == "rerun"
    assert plan["components"]["content_invariants"]["disposition"] == "rerun"
    assert plan["components"]["screenshots"]["disposition"] == "not_applicable"
    manifest_entry = next(
        entry
        for entry in plan["components"]["django"]["inputs"]["manifest"]
        if entry["path"] == "data/catalog.json"
    )
    assert len(manifest_entry["object_id"]) == 40  # Exact Git blob identity covers the whole value.
    artifact = build_invariant_artifact(repository=_repository, plan=plan)
    assert artifact["record_count"] == 10_000
    assert artifact["files"][0]["identity_unique"] is True
    assert artifact["files"][0]["url_complete_count"] == 10_000
    assert artifact["files"][0]["metadata_complete_count"] == 10_000


def test_exact_evidence_reuses_only_unaffected_components(tmp_path: Path) -> None:
    repository, plan = make_plan(tmp_path, {"api/service.py": "changed\n"})
    root = tmp_path / "evidence"
    root.mkdir()
    origin = {"issue": 113, "kind": "local", "producer_role": "tester", "worktree": "qa"}
    for component in ("container", "playwright"):
        records, output = component_output(root, plan, component)
        envelope = build_envelope(
            plan=plan,
            component=component,
            result="success",
            origin=origin,
            command=plan["components"][component]["command"],
            execution_environment=plan["components"][component]["environment"],
            artifacts=records,
            machine_output=output,
            completed_at=NOW,
        )
        (root / f"{component}.json").write_text(
            __import__("json").dumps(envelope, sort_keys=True), encoding="utf-8"
        )
    selection, records = selection_for(("api/service.py",), base=plan["base"], head=plan["head"])
    reused = build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=plan["base"],
        head=plan["head"],
        selection=selection,
        records=records,
        evidence_directory=root,
        consumer="tester",
        now=NOW,
    )
    assert reused["components"]["container"]["disposition"] == "reused"
    assert reused["components"]["playwright"]["disposition"] == "reused"
    assert reused["components"]["django"]["disposition"] == "rerun"
    assert reused["components"]["quality"]["disposition"] == "rerun"
    materialized = tmp_path / "materialized"
    assert (
        materialize_reused_evidence(
            plan=reused,
            evidence_directory=root,
            output_directory=materialized,
        )
        == 2
    )
    assert (materialized / "container" / "container-evidence.json").is_file()
    assert (materialized / "container" / "container-output.log").is_file()


def test_release_plan_never_reuses_container_without_exact_candidate_image(
    tmp_path: Path,
) -> None:
    repository, plan = make_plan(tmp_path, {"api/service.py": "changed\n"})
    selection, records = selection_for(("api/service.py",), base=plan["base"], head=plan["head"])
    release_plan = build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=plan["base"],
        head=plan["head"],
        selection=selection,
        records=records,
        now=NOW,
        release_requires_image=True,
    )
    assert release_plan["release_requires_image"] is True
    assert release_plan["components"]["container"]["disposition"] == "rerun"
    assert release_plan["components"]["container"]["reason"] == "exact_release_image_required"


def test_report_buckets_are_exhaustive_disjoint_and_required_skips_fail(tmp_path: Path) -> None:
    _repository, plan = make_plan(tmp_path, {"README.md": "changed\n"})
    report = create_report(plan=plan, phase="engineer")
    assert report["verdict"] == "failure"
    # Always-fresh selector/evidence results have not been supplied.
    assert {entry["component"] for entry in report["buckets"]["skipped"]} == {
        "evidence_validation",
        "selector",
    }
    duplicate = deepcopy(report)
    duplicate["buckets"]["not_applicable"].append(deepcopy(duplicate["buckets"]["skipped"][0]))
    with pytest.raises(VerificationError, match="duplicated"):
        validate_report(duplicate, plan=plan, allow_pending=True)

    malformed = deepcopy(plan)
    malformed["components"].pop("quality")
    with pytest.raises(VerificationError, match="incomplete"):
        validate_plan(malformed)


def test_report_and_actions_summary_bind_complete_plan_state_and_machine_evidence(
    tmp_path: Path,
) -> None:
    _repository, plan = make_plan(tmp_path, {"api/service.py": "changed\n"})
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    origin = {
        "issue": 113,
        "kind": "local",
        "producer_role": "engineer",
        "worktree": "issue-113",
    }
    for component, item in plan["components"].items():
        if item["disposition"] != "rerun":
            continue
        records, output = component_output(evidence, plan, component)
        envelope = build_envelope(
            plan=plan,
            component=component,
            result="success",
            origin=origin,
            command=item["command"],
            execution_environment=item["environment"],
            artifacts=records,
            machine_output=output,
            completed_at=NOW,
        )
        (evidence / f"{component}-evidence.json").write_text(
            __import__("json").dumps(envelope, sort_keys=True), encoding="utf-8"
        )

    report = create_report(plan=plan, result_directory=evidence, phase="engineer")
    assert report["verdict"] == "success"
    assert report["policy_version"] == plan["policy_version"]
    assert report["direct_nodes"] == plan["direct_nodes"]
    assert report["downstream_nodes"] == plan["downstream_nodes"]
    assert report["risk_flags"] == plan["risk_flags"]
    assert len(report["verification_state_sha256"]) == 64
    assert report["invalid_evidence"] == [
        {"component": "container", "reason": "no_matching_evidence"},
        {"component": "playwright", "reason": "no_matching_evidence"},
    ]
    for entries in report["buckets"].values():
        for entry in entries:
            assert entry["proof"]["command"] == plan["components"][entry["component"]]["command"]
            assert entry["proof"]["input_sha256"]
            assert entry["proof"]["state_sha256"] == report["verification_state_sha256"]
    summary = report_summary(report)
    rerun = report["buckets"]["rerun"][0]
    assert rerun["evidence"]["evidence_id"] in summary
    assert rerun["evidence"]["input_sha256"] in summary
    assert rerun["evidence"]["expires_at"] in summary
    assert rerun["evidence"]["output"]["artifact"]["sha256"] in summary
    assert '"producer_role":"engineer"' in summary
    assert "passed=" in summary and "skipped=" in summary

    wrong_policy = deepcopy(report)
    wrong_policy["policy_version"] += 1
    with pytest.raises(VerificationError, match="metadata"):
        validate_report(wrong_policy, plan=plan, evidence_directory=evidence)


def test_repository_state_changes_with_concrete_runner_image_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repository, _base, head = repository_with_change(tmp_path, {"api/service.py": "changed\n"})
    monkeypatch.setenv("ImageOS", "ubuntu24")
    monkeypatch.setenv("ImageVersion", "20260801.1")
    old = repository_state(repository, head)
    monkeypatch.setenv("ImageVersion", "20260808.1")
    new = repository_state(repository, head)

    assert old["source_manifest_sha256"] == new["source_manifest_sha256"]
    assert old["verification_state_sha256"] != new["verification_state_sha256"]


def test_repository_state_ignores_commit_metadata_but_changes_with_tree(tmp_path: Path) -> None:
    repository, base, head = repository_with_change(tmp_path, {"api/service.py": "changed\n"})
    base_state = repository_state(repository, base)
    head_state = repository_state(repository, head)
    assert base_state["verification_state_sha256"] != head_state["verification_state_sha256"]


def test_scheduled_state_accepts_exact_aggregate_environment_without_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, report, state = scheduled_state_fixture(tmp_path, monkeypatch)

    assert set(state["component_environment"]) == set(plan["components"])
    assert state["environment_sha256"] == state["environment"]["sha256"]
    assert state["component_environment_sha256"] == {
        component: fingerprint["sha256"]
        for component, fingerprint in state["component_environment"].items()
    }

    envelope = build_scheduled_state_envelope(
        plan=plan,
        report=report,
        state=state,
        run_id=143,
        run_attempt=1,
    )

    assert envelope["verification_state_sha256"] == state["verification_state_sha256"]


def test_scheduled_state_requires_explicit_opt_in_for_same_family_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, report, state = scheduled_state_fixture(
        tmp_path,
        monkeypatch,
        actual_image=("ubuntu24", "20260808.1"),
    )

    with pytest.raises(VerificationError, match="scheduled repository state"):
        build_scheduled_state_envelope(
            plan=plan,
            report=report,
            state=state,
            run_id=143,
            run_attempt=1,
        )

    envelope = build_scheduled_state_envelope(
        plan=plan,
        report=report,
        state=state,
        run_id=143,
        run_attempt=1,
        allow_hosted_runner_drift=True,
    )
    assert envelope["verification_state_sha256"] == state["verification_state_sha256"]


def test_scheduled_state_rejects_different_runner_family_even_with_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, report, state = scheduled_state_fixture(
        tmp_path,
        monkeypatch,
        actual_image=("windows2025", "20260808.1"),
    )

    with pytest.raises(VerificationError, match="scheduled repository state"):
        build_scheduled_state_envelope(
            plan=plan,
            report=report,
            state=state,
            run_id=143,
            run_attempt=1,
            allow_hosted_runner_drift=True,
        )


@pytest.mark.parametrize("field", ["environment", "component_environment"])
def test_scheduled_state_rejects_tampered_raw_environment_fingerprints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    plan, report, state = scheduled_state_fixture(tmp_path, monkeypatch)
    tampered = deepcopy(state)
    if field == "environment":
        tampered["environment"]["browser"] = "firefox"
    else:
        tampered["component_environment"]["django"]["browser"] = "firefox"

    with pytest.raises(VerificationError, match="repository state"):
        build_scheduled_state_envelope(
            plan=plan,
            report=report,
            state=tampered,
            run_id=143,
            run_attempt=1,
            allow_hosted_runner_drift=True,
        )


def test_scheduled_state_rejects_recomputed_tree_identity_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, report, state = scheduled_state_fixture(tmp_path, monkeypatch)
    tampered = deepcopy(state)
    tampered["tree_oid"] = "f" * len(state["tree_oid"])
    identity = dict(tampered)
    identity.pop("verification_state_sha256")
    tampered["verification_state_sha256"] = sha256_json(identity)

    with pytest.raises(VerificationError, match="scheduled repository state"):
        build_scheduled_state_envelope(
            plan=plan,
            report=report,
            state=tampered,
            run_id=143,
            run_attempt=1,
        )


def test_worktree_plan_includes_dirty_tracked_and_untracked_candidate_files(
    tmp_path: Path,
) -> None:
    repository, base, head = repository_with_change(tmp_path, {"api/service.py": "committed\n"})
    (repository / "api" / "service.py").write_text("dirty\n", encoding="utf-8")
    untracked = repository / "tests_ci" / "test_candidate.py"
    untracked.parent.mkdir()
    untracked.write_text("def test_candidate(): pass\n", encoding="utf-8")
    records = read_worktree_change_records(repository, base, head)
    selection, _ignored = selection_for(("api/service.py",), base=base, head=head)
    plan = build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=base,
        head=head,
        selection=selection,
        records=records,
        now=NOW,
        include_worktree=True,
    )
    assert plan["source_mode"] == "worktree"
    assert plan["source_tree"]["git_object_algorithm"] == "sha1"
    assert len(plan["source_tree"]["tree_oid"]) == 40
    assert {item["path"] for item in plan["changed_paths"]} >= {
        "api/service.py",
        "tests_ci/test_candidate.py",
    }
    assert plan["profile"] == "full"
