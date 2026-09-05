from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ci.evidence import (
    ALLOWED_COMPONENT_COMMANDS,
    EVIDENCE_SCHEMA_VERSION,
    OUTPUT_FORMATS,
    EvidenceError,
    artifact_records,
    build_envelope,
    choose_reusable_evidence,
    digest_manifest,
    environment_fingerprint,
    environment_matches_plan,
    machine_output_claim,
    validate_envelope,
    validate_machine_output_files,
)
from ci.ownership import sha256_json
from ci.verification import PLAN_SCHEMA_VERSION, build_plan, create_report, dump_json
from tests_ci.helpers import component_output, git, repository_with_change, selection_for

GOLDEN_MANIFEST = [
    {"mode": "100644", "object_id": "1" * 40, "object_type": "blob", "path": "a.txt"},
    {"mode": "100755", "object_id": "2" * 40, "object_type": "blob", "path": "dir/b.py"},
]
ROOT = Path(__file__).resolve().parents[1]


def test_evidence_schema_and_selector_artifact_version_follow_the_plan_contract() -> None:
    schema = json.loads((ROOT / "ci" / "evidence.schema.json").read_text(encoding="utf-8"))

    assert EVIDENCE_SCHEMA_VERSION == 3
    assert schema["$id"].endswith("evidence-v3.schema.json")
    assert schema["properties"]["schema_version"] == {"const": EVIDENCE_SCHEMA_VERSION}
    assert OUTPUT_FORMATS["selector"] == frozenset({f"verification-plan-v{PLAN_SCHEMA_VERSION}"})
    assert (
        f"verification-plan-v{PLAN_SCHEMA_VERSION}"
        in schema["properties"]["output"]["properties"]["format"]["enum"]
    )


def plan_for_api(tmp_path: Path):
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
    return repository, plan


def plan_for_render_change(tmp_path: Path):
    repository, base, head = repository_with_change(
        tmp_path,
        {"api/templates/api/page.html": "changed\n"},
        initial={"api/templates/api/page.html": "initial\n"},
    )
    selection, records = selection_for(("api/templates/api/page.html",), base=base, head=head)
    plan = build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=base,
        head=head,
        selection=selection,
        records=records,
        now=datetime(2026, 8, 9, 12, tzinfo=UTC),
    )
    return repository, plan


def test_full_django_evidence_uses_the_compatibility_free_ci_command(
    tmp_path: Path,
) -> None:
    _repository, plan = plan_for_render_change(tmp_path)

    assert plan["components"]["django"]["command"] == "make test-django-full"
    assert ALLOWED_COMPONENT_COMMANDS["django"] == frozenset(
        {"make test", "make test-django-full", "make test-ci-focused"}
    )

    with pytest.raises(EvidenceError, match="evidence command does not match"):
        build_envelope(
            plan=plan,
            component="django",
            result="success",
            origin=local_origin(),
            command="make test",
            execution_environment=plan["components"]["django"]["environment"],
            completed_at=datetime(2026, 8, 9, 12, tzinfo=UTC),
        )


def local_origin(role: str = "tester") -> dict[str, object]:
    return {"issue": 113, "kind": "local", "producer_role": role, "worktree": "issue-113"}


def test_manifest_digest_has_a_fixed_golden_vector_and_rejects_reordering() -> None:
    assert digest_manifest(GOLDEN_MANIFEST) == (
        "147f87658c5ca8157427b01473281ba9bbaf7921ba6094ff7e800782051e143c"
    )
    with pytest.raises(EvidenceError, match="sorted"):
        digest_manifest(list(reversed(GOLDEN_MANIFEST)))


def test_environment_fingerprint_allowlists_non_secret_configuration() -> None:
    environment = environment_fingerprint(
        {
            "DJANGO_SETTINGS_MODULE": "website.settings.test",
            "DTC_ENVIRONMENT": "development",
            "DATABASE_URL": "secret",
            "TOKEN": "secret",
        }
    )
    assert environment["allowlisted_config"] == {
        "DJANGO_SETTINGS_MODULE": "website.settings.test",
        "DTC_ENVIRONMENT": "development",
    }
    assert "secret" not in json.dumps(environment)


def test_concrete_github_runner_image_version_changes_environment_identity() -> None:
    old = environment_fingerprint({"ImageOS": "ubuntu24", "ImageVersion": "20260801.1"})
    new = environment_fingerprint({"ImageOS": "ubuntu24", "ImageVersion": "20260808.1"})

    assert old["runner_image"] == "ubuntu24@20260801.1"
    assert new["runner_image"] == "ubuntu24@20260808.1"
    assert old["runner_image_version"] == "20260801.1"
    assert new["runner_image_version"] == "20260808.1"
    assert old["sha256"] != new["sha256"]

    with pytest.raises(EvidenceError, match="ImageVersion"):
        environment_fingerprint({"ImageOS": "ubuntu24"})


def test_same_family_hosted_runner_drift_is_explicitly_compatible_for_fresh_execution() -> None:
    planned = environment_fingerprint({"ImageOS": "ubuntu24", "ImageVersion": "20260801.1"})
    actual = environment_fingerprint({"ImageOS": "ubuntu24", "ImageVersion": "20260808.1"})

    assert not environment_matches_plan(actual, planned)
    assert environment_matches_plan(actual, planned, allow_hosted_runner_drift=True)
    windows = environment_fingerprint({"ImageOS": "windows2025", "ImageVersion": "20260808.1"})
    assert not environment_matches_plan(windows, planned, allow_hosted_runner_drift=True)


def test_declared_runner_image_binds_the_plan_to_the_authorized_hosted_family() -> None:
    """A component planned for another runner records that runner's image family.

    The revision stays the planner's, because it cannot know the other pool's,
    so the declaration only moves the *family* -- a hosted execution on a
    different family, or a local plan with no concrete revision at all, is still
    refused.
    """

    planned = environment_fingerprint(
        {"ImageOS": "ubuntu24", "ImageVersion": "20260801.1"}, runner_image="ubuntu24-arm64"
    )
    assert planned["runner_image"] == "ubuntu24-arm64@20260801.1"

    actual = environment_fingerprint({"ImageOS": "ubuntu24-arm64", "ImageVersion": "20260808.1"})
    assert environment_matches_plan(actual, planned, allow_hosted_runner_drift=True)

    intel = environment_fingerprint({"ImageOS": "ubuntu24", "ImageVersion": "20260808.1"})
    assert not environment_matches_plan(intel, planned, allow_hosted_runner_drift=True)

    local_plan = environment_fingerprint({}, runner_image="ubuntu24-arm64")
    assert local_plan["runner_image"] == "ubuntu24-arm64@local"
    assert not environment_matches_plan(actual, local_plan, allow_hosted_runner_drift=True)


@pytest.mark.parametrize(
    "component, actual_environment",
    [
        (
            "container",
            {"ImageOS": "ubuntu24", "ImageVersion": "20260808.1"},
        ),
        (
            "django",
            {
                "DJANGO_SETTINGS_MODULE": "website.settings.production",
                "ImageOS": "ubuntu24",
                "ImageVersion": "20260801.1",
            },
        ),
    ],
    ids=["runner-image", "allowlisted-configuration"],
)
def test_component_rejects_execution_environment_different_from_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    component: str,
    actual_environment: dict[str, str],
) -> None:
    monkeypatch.setenv("ImageOS", "ubuntu24")
    monkeypatch.setenv("ImageVersion", "20260801.1")
    _repository, plan = plan_for_api(tmp_path)
    root = tmp_path / "evidence"
    root.mkdir()
    records, output = component_output(root, plan, component)

    with pytest.raises(EvidenceError, match="executing component environment"):
        build_envelope(
            plan=plan,
            component=component,
            result="success",
            origin=local_origin(),
            command=plan["components"][component]["command"],
            execution_environment=environment_fingerprint(actual_environment),
            artifacts=records,
            machine_output=output,
            completed_at=datetime(2026, 8, 9, 12, tzinfo=UTC),
        )


def test_machine_output_claim_records_actual_pass_skip_counts_and_rejects_forgery(
    tmp_path: Path,
) -> None:
    _repository, plan = plan_for_api(tmp_path)
    root = tmp_path / "evidence"
    root.mkdir()
    output_path = root / "playwright-output.log"
    output_path.write_text(
        "193 passed, 7 skipped in 1.25s\n"
        "DTC_FLAKE_POLICY_V1 attempted=200 passed=193 failed=0 skipped=7 "
        "rerun=0 quarantined=0 complete=1\n"
        "Destroying test database for alias 'default'...\n"
        "make[1]: Leaving directory '/workspace'\n",
        encoding="utf-8",
    )
    records = artifact_records((output_path,), root=root)
    output = machine_output_claim(
        output_path,
        root=root,
        component="playwright",
        plan=plan,
        result="success",
    )
    envelope = build_envelope(
        plan=plan,
        component="playwright",
        result="success",
        origin=local_origin(),
        command=plan["components"]["playwright"]["command"],
        execution_environment=plan["components"]["playwright"]["environment"],
        artifacts=records,
        machine_output=output,
        completed_at=datetime(2026, 8, 9, 12, tzinfo=UTC),
    )
    assert {key: envelope["counts"][key] for key in ("tests", "passed", "failed", "skipped")} == {
        "tests": 200,
        "passed": 193,
        "failed": 0,
        "skipped": 7,
    }

    forged = deepcopy(envelope)
    forged["counts"]["tests"] = forged["counts"]["passed"] = 999
    forged["counts"]["skipped"] = 0
    forged["counts"]["assertions"] = 999
    forged["counts"]["attempted"] = 999
    forged["output"]["counts"] = {
        "assertions": 999,
        "attempted": 999,
        "failed": 0,
        "passed": 999,
        "quarantined": 0,
        "rerun": 0,
        "skipped": 0,
        "tests": 999,
    }
    identity = dict(forged)
    identity.pop("evidence_id")
    forged["evidence_id"] = sha256_json(identity)
    validate_envelope(forged)
    envelope_path = root / "playwright-evidence.json"
    dump_json(forged, envelope_path)
    chosen, reason = choose_reusable_evidence(
        plan=plan,
        component="playwright",
        candidates=((envelope_path, forged),),
        evidence_root=root,
        consumer="tester",
        now=datetime(2026, 8, 9, 13, tzinfo=UTC),
    )
    assert chosen is None
    assert reason == "machine_output_mismatch"


def test_failed_test_component_envelope_carries_the_counts_its_log_holds(
    tmp_path: Path,
) -> None:
    _repository, plan = plan_for_api(tmp_path)
    root = tmp_path / "evidence"
    root.mkdir()
    output_path = root / "playwright-output.log"
    output_path.write_text(
        "=================================== FAILURES ===================================\n"
        "=========================== short test summary info ============================\n"
        "FAILED playwright_tests/core/journey.spec.ts:34 - journey continues after failure\n"
        "=========== 4 failed, 204 passed, 3 deselected in 1405.63s (0:23:25) ===========\n",
        encoding="utf-8",
    )
    records = artifact_records((output_path,), root=root)
    output = machine_output_claim(
        output_path,
        root=root,
        component="playwright",
        plan=plan,
        result="failure",
    )
    assert output["counts"] == {
        "assertions": 208,
        "failed": 4,
        "passed": 204,
        "skipped": 0,
        "tests": 208,
    }
    envelope = build_envelope(
        plan=plan,
        component="playwright",
        result="failure",
        origin=local_origin(),
        command=plan["components"]["playwright"]["command"],
        execution_environment=plan["components"]["playwright"]["environment"],
        artifacts=records,
        machine_output=output,
        exit_code=2,
        completed_at=datetime(2026, 8, 9, 12, tzinfo=UTC),
    )
    assert validate_envelope(envelope) == envelope
    assert {key: envelope["counts"][key] for key in ("tests", "passed", "failed", "skipped")} == {
        "tests": 208,
        "passed": 204,
        "failed": 4,
        "skipped": 0,
    }
    envelope_path = root / "playwright-evidence.json"
    dump_json(envelope, envelope_path)
    validate_machine_output_files(
        envelope, evidence_root=root, envelope_path=envelope_path, plan=plan
    )
    chosen, reason = choose_reusable_evidence(
        plan=plan,
        component="playwright",
        candidates=((envelope_path, envelope),),
        evidence_root=root,
        consumer="tester",
        now=datetime(2026, 8, 9, 13, tzinfo=UTC),
    )
    assert chosen is None
    assert reason == "latest_result_failure"


@pytest.mark.parametrize(
    "body",
    [
        (
            b"============================= test session starts =============================\n"
            b"platform linux -- Python 3.13.5, pytest-8.4.2, pluggy-1.6.0\n"
            b"Killed\n"
        ),
        b"\xff\xfe\x00killed mid-write",
    ],
    ids=["truncated-without-summary", "invalid-utf8"],
)
def test_unparseable_failed_output_falls_back_to_zero_counts_without_blocking_the_report(
    tmp_path: Path,
    body: bytes,
) -> None:
    _repository, plan = plan_for_api(tmp_path)
    root = tmp_path / "evidence"
    root.mkdir()
    output_path = root / "playwright-output.log"
    output_path.write_bytes(body)
    output = machine_output_claim(
        output_path,
        root=root,
        component="playwright",
        plan=plan,
        result="failure",
    )
    assert output["counts"] == {
        "assertions": 0,
        "failed": 0,
        "passed": 0,
        "skipped": 0,
        "tests": 0,
    }
    with pytest.raises(EvidenceError, match="test output"):
        machine_output_claim(
            output_path, root=root, component="playwright", plan=plan, result="success"
        )

    for component, item in plan["components"].items():
        if item["disposition"] != "rerun":
            continue
        if component == "playwright":
            result = "failure"
            records = artifact_records((output_path,), root=root)
            machine_output = output
        else:
            result = "success"
            records, machine_output = component_output(root, plan, component)
        envelope = build_envelope(
            plan=plan,
            component=component,
            result=result,
            origin=local_origin("engineer"),
            command=item["command"],
            execution_environment=item["environment"],
            artifacts=records,
            machine_output=machine_output,
            exit_code=0 if result == "success" else 1,
            completed_at=datetime(2026, 8, 9, 12, tzinfo=UTC),
        )
        dump_json(envelope, root / f"{component}-evidence.json")

    report = create_report(plan=plan, result_directory=root, phase="engineer")
    assert report["verdict"] == "failure"
    assert report["buckets"]["skipped"] == []
    rerun = {entry["component"]: entry for entry in report["buckets"]["rerun"]}
    assert rerun["playwright"]["result"] == "failure"
    assert rerun["playwright"]["evidence"]["counts"]["tests"] == 0


def test_partial_pytest_output_cannot_validate_as_success(tmp_path: Path) -> None:
    _repository, plan = plan_for_api(tmp_path)
    root = tmp_path / "evidence"
    root.mkdir()
    output_path = root / "playwright-output.log"
    output_path.write_text(
        "11 passed in 19.00s\nplaywright_tests/test_hanging.py::test_synthetic_wedged_browser\n",
        encoding="utf-8",
    )

    with pytest.raises(EvidenceError, match="flake-policy"):
        machine_output_claim(
            output_path,
            root=root,
            component="playwright",
            plan=plan,
            result="success",
        )

    timed_out = machine_output_claim(
        output_path,
        root=root,
        component="playwright",
        plan=plan,
        result="timed_out",
    )
    assert timed_out["counts"] == {
        "assertions": 11,
        "failed": 0,
        "passed": 11,
        "skipped": 0,
        "tests": 11,
    }

    interrupted_path = root / "playwright-interrupted.log"
    interrupted_path.write_text(
        "+++++++++++++++++++++++++++++++++++ Timeout ++++++++++++++++++++++++++++++++++++\n"
        "11 passed in 120.00s\n",
        encoding="utf-8",
    )
    with pytest.raises(EvidenceError, match="interrupted pytest run"):
        machine_output_claim(
            interrupted_path,
            root=root,
            component="playwright",
            plan=plan,
            result="success",
        )


def test_make_timeout_wrapper_echo_is_not_an_interrupted_pytest_run(tmp_path: Path) -> None:
    _repository, plan = plan_for_api(tmp_path)
    root = tmp_path / "evidence"
    root.mkdir()
    output_path = root / "playwright-output.log"
    timeout_echo = (
        "\ttimeout --foreground --signal=TERM --kill-after=30s 600s "
        "uv run --frozen pytest playwright_tests \\\n"
    )
    policy = (
        "DTC_FLAKE_POLICY_V1 attempted=30 passed=30 failed=0 skipped=0 "
        "rerun=0 quarantined=0 complete=1\n"
    )
    output_path.write_text(
        timeout_echo
        + "============================= test session starts ==============================\n"
        + "collected 30 items\n"
        + policy
        + "================ 30 passed, 207 deselected in 207.83s (0:03:27) ================\n",
        encoding="utf-8",
    )
    claim = machine_output_claim(
        output_path,
        root=root,
        component="playwright",
        plan=plan,
        result="success",
    )
    assert claim["counts"]["passed"] == 30
    assert claim["counts"]["tests"] == 30


def test_timed_out_partial_playwright_output_is_a_terminal_failure_report(
    tmp_path: Path,
) -> None:
    _repository, plan = plan_for_api(tmp_path)
    root = tmp_path / "evidence"
    root.mkdir()
    output_path = root / "playwright-output.log"
    output_path.write_text(
        "============================= test session starts =============================\n"
        "collected 220 items\n"
        "...........\n"
        "playwright_tests/test_hanging.py::test_synthetic_wedged_browser\n",
        encoding="utf-8",
    )
    records = artifact_records((output_path,), root=root)
    output = machine_output_claim(
        output_path,
        root=root,
        component="playwright",
        plan=plan,
        result="timed_out",
    )
    envelope = build_envelope(
        plan=plan,
        component="playwright",
        result="timed_out",
        origin=local_origin(),
        command=plan["components"]["playwright"]["command"],
        execution_environment=plan["components"]["playwright"]["environment"],
        artifacts=records,
        machine_output=output,
        exit_code=124,
        completed_at=datetime(2026, 8, 9, 12, tzinfo=UTC),
    )
    envelope_path = root / "playwright-evidence.json"
    dump_json(envelope, envelope_path)
    validate_machine_output_files(
        envelope, evidence_root=root, envelope_path=envelope_path, plan=plan
    )

    report = create_report(plan=plan, result_directory=root, phase="engineer")
    assert report["verdict"] == "failure"
    rerun = next(
        entry for entry in report["buckets"]["rerun"] if entry["component"] == "playwright"
    )
    assert rerun["result"] == "timed_out"
    assert rerun["evidence"]["counts"]["tests"] == 0


def test_success_envelope_is_digest_bound_and_strictly_validated(tmp_path: Path) -> None:
    _repository, plan = plan_for_api(tmp_path)
    root = tmp_path / "evidence"
    root.mkdir()
    artifact = root / "result.json"
    artifact.write_text('{"status":"success"}\n', encoding="utf-8")
    records, output = component_output(root, plan, "container", path=artifact)
    completed = datetime(2026, 8, 9, 12, 5, tzinfo=UTC)
    envelope = build_envelope(
        plan=plan,
        component="container",
        result="success",
        origin=local_origin(),
        command="make verification-container",
        execution_environment=plan["components"]["container"]["environment"],
        artifacts=records,
        machine_output=output,
        started_at=completed - timedelta(minutes=2),
        completed_at=completed,
    )
    assert validate_envelope(envelope) == envelope
    assert envelope["expires_at"] == "2026-08-10T12:05:00Z"
    assert envelope["exit_code"] == 0
    assert envelope["counts"] == {
        "artifacts": 1,
        "assertions": 1,
        "commands": 1,
        "failed": 0,
        "input_files": len(plan["components"]["container"]["inputs"]["manifest"]),
        "passed": 0,
        "skipped": 0,
        "tests": 0,
    }
    assert envelope["source_tree"]["manifest_sha256"] == plan["source_tree"]["manifest_sha256"]
    assert set(envelope["input_manifest"][0]) == {"mode", "object_id", "object_type", "path"}

    mutations = []
    changed_input = deepcopy(envelope)
    changed_input["input_manifest"][0]["object_id"] = "f" * 40
    mutations.append(changed_input)
    changed_environment = deepcopy(envelope)
    changed_environment["environment"]["python"] = "0"
    mutations.append(changed_environment)
    changed_policy = deepcopy(envelope)
    changed_policy["policy"]["graph_sha256"] = "x" * 64
    mutations.append(changed_policy)
    changed_artifact = deepcopy(envelope)
    changed_artifact["artifacts"][0]["sha256"] = "x" * 64
    mutations.append(changed_artifact)
    unknown_field = deepcopy(envelope)
    unknown_field["unknown"] = True
    mutations.append(unknown_field)
    missing_count = deepcopy(envelope)
    missing_count["counts"].pop("assertions")
    identity = dict(missing_count)
    identity.pop("evidence_id")
    missing_count["evidence_id"] = sha256_json(identity)
    mutations.append(missing_count)
    false_exit = deepcopy(envelope)
    false_exit["exit_code"] = 1
    identity = dict(false_exit)
    identity.pop("evidence_id")
    false_exit["evidence_id"] = sha256_json(identity)
    mutations.append(false_exit)
    for mutation in mutations:
        with pytest.raises(EvidenceError):
            validate_envelope(mutation)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command", "python -c 'print(1)'"),
        ("validity_class", "visual"),
        ("validity_seconds", 1),
        ("expires_at", "2099-01-01T00:00:00Z"),
    ],
)
def test_recomputed_forged_contract_fields_are_rejected(
    tmp_path: Path, field: str, value: object
) -> None:
    _repository, plan = plan_for_api(tmp_path)
    root = tmp_path / "evidence"
    root.mkdir()
    artifact = root / "result.json"
    artifact.write_text("success\n", encoding="utf-8")
    records, output = component_output(root, plan, "container", path=artifact)
    envelope = build_envelope(
        plan=plan,
        component="container",
        result="success",
        origin=local_origin(),
        command=plan["components"]["container"]["command"],
        execution_environment=plan["components"]["container"]["environment"],
        artifacts=records,
        machine_output=output,
        completed_at=datetime(2026, 8, 9, 12, tzinfo=UTC),
    )
    envelope[field] = value
    identity = dict(envelope)
    identity.pop("evidence_id")
    envelope["evidence_id"] = sha256_json(identity)
    with pytest.raises(EvidenceError):
        validate_envelope(envelope)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("result",), []),
        (("input_sha256",), 1),
        (("input_manifest",), {}),
        (("origin", "kind"), []),
        (("selection", "direct_nodes"), [{}]),
        (("source_tree", "commit"), 1),
        (("artifacts", 0, "sha256"), 1),
    ],
)
def test_malformed_json_types_fail_as_invalid_evidence(
    tmp_path: Path, path: tuple[object, ...], value: object
) -> None:
    _repository, plan = plan_for_api(tmp_path)
    root = tmp_path / "evidence"
    root.mkdir()
    artifact = root / "result.json"
    artifact.write_text("success\n", encoding="utf-8")
    records, output = component_output(root, plan, "container", path=artifact)
    envelope = build_envelope(
        plan=plan,
        component="container",
        result="success",
        origin=local_origin(),
        command=plan["components"]["container"]["command"],
        execution_environment=plan["components"]["container"]["environment"],
        artifacts=records,
        machine_output=output,
        completed_at=datetime(2026, 8, 9, 12, tzinfo=UTC),
    )
    target: object = envelope
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]
    identity = dict(envelope)
    identity.pop("evidence_id")
    envelope["evidence_id"] = sha256_json(identity)
    with pytest.raises(EvidenceError):
        validate_envelope(envelope)


def test_reuse_requires_success_trust_freshness_artifact_and_no_later_failure(
    tmp_path: Path,
) -> None:
    _repository, plan = plan_for_api(tmp_path)
    root = tmp_path / "evidence"
    root.mkdir()
    artifact = root / "container-result.json"
    artifact.write_text('{"status":"success"}\n', encoding="utf-8")
    records, output = component_output(root, plan, "container", path=artifact)
    completed = datetime(2026, 8, 9, 12, 5, tzinfo=UTC)
    success = build_envelope(
        plan=plan,
        component="container",
        result="success",
        origin=local_origin(),
        command="make verification-container",
        execution_environment=plan["components"]["container"]["environment"],
        artifacts=records,
        machine_output=output,
        completed_at=completed,
    )
    envelope_path = root / "success.json"
    dump_json(success, envelope_path)

    reused, reason = choose_reusable_evidence(
        plan=plan,
        component="container",
        candidates=((envelope_path, success),),
        evidence_root=root,
        consumer="tester",
        now=completed + timedelta(hours=1),
    )
    assert reused == success
    assert reason == "exact_digest_match"

    rejected, reason = choose_reusable_evidence(
        plan=plan,
        component="container",
        candidates=((envelope_path, success),),
        evidence_root=root,
        consumer="ci",
        now=completed + timedelta(hours=1),
    )
    assert rejected is None and reason == "untrusted_origin"

    expired, reason = choose_reusable_evidence(
        plan=plan,
        component="container",
        candidates=((envelope_path, success),),
        evidence_root=root,
        consumer="tester",
        now=completed + timedelta(days=2),
    )
    assert expired is None and reason == "expired"

    artifact.write_text("tampered\n", encoding="utf-8")
    tampered, reason = choose_reusable_evidence(
        plan=plan,
        component="container",
        candidates=((envelope_path, success),),
        evidence_root=root,
        consumer="tester",
        now=completed + timedelta(hours=1),
    )
    assert tampered is None and reason == "artifact_digest_mismatch"

    artifact.unlink()
    missing, reason = choose_reusable_evidence(
        plan=plan,
        component="container",
        candidates=((envelope_path, success),),
        evidence_root=root,
        consumer="tester",
        now=completed + timedelta(hours=1),
    )
    assert missing is None and reason == "missing_artifact"


@pytest.mark.parametrize(
    "latest_result", ["failure", "cancelled", "stale", "timed_out", "action_required"]
)
def test_latest_non_success_never_falls_back_to_older_pass(
    tmp_path: Path, latest_result: str
) -> None:
    _repository, plan = plan_for_api(tmp_path)
    root = tmp_path / "evidence"
    root.mkdir()
    artifact = root / "result.json"
    artifact.write_text("result\n", encoding="utf-8")
    records, success_output = component_output(root, plan, "playwright", path=artifact)
    first_time = datetime(2026, 8, 9, 12, tzinfo=UTC)
    success = build_envelope(
        plan=plan,
        component="playwright",
        result="success",
        origin=local_origin(),
        command="make test-playwright-smoke",
        execution_environment=plan["components"]["playwright"]["environment"],
        artifacts=records,
        machine_output=success_output,
        completed_at=first_time,
    )
    _failure_records, failure_output = component_output(
        root, plan, "playwright", result=latest_result, path=artifact
    )
    failure = build_envelope(
        plan=plan,
        component="playwright",
        result=latest_result,
        origin=local_origin(),
        command="make test-playwright-smoke",
        execution_environment=plan["components"]["playwright"]["environment"],
        artifacts=records,
        machine_output=failure_output,
        completed_at=first_time + timedelta(minutes=1),
    )
    chosen, reason = choose_reusable_evidence(
        plan=plan,
        component="playwright",
        candidates=((root / "old.json", success), (root / "new.json", failure)),
        evidence_root=root,
        consumer="tester",
        now=first_time + timedelta(minutes=2),
    )
    assert chosen is None
    assert reason == f"latest_result_{latest_result}"


def test_later_failed_actions_run_invalidates_older_success(tmp_path: Path) -> None:
    _repository, plan = plan_for_api(tmp_path)
    root = tmp_path / "evidence"
    root.mkdir()
    artifact = root / "result.json"
    artifact.write_text("result\n", encoding="utf-8")
    origin = {
        "artifact_id": "verification-component-container-10-attempt-1",
        "job_id": "container",
        "kind": "github_actions",
        "ref": "refs/heads/main",
        "repository": "DataTalksClub/website",
        "run_attempt": 1,
        "run_id": 10,
        "workflow": ".github/workflows/ci.yml",
    }
    records, output = component_output(root, plan, "container", path=artifact)
    envelope = build_envelope(
        plan=plan,
        component="container",
        result="success",
        origin=origin,
        command="make verification-container",
        execution_environment=plan["components"]["container"]["environment"],
        artifacts=records,
        machine_output=output,
        completed_at=datetime(2026, 8, 9, 12, tzinfo=UTC),
    )
    chosen, reason = choose_reusable_evidence(
        plan=plan,
        component="container",
        candidates=((root / "ci.json", envelope),),
        evidence_root=root,
        consumer="ci",
        now=datetime(2026, 8, 9, 13, tzinfo=UTC),
        history=({"id": 11, "conclusion": "cancelled"},),
    )
    assert chosen is None and reason == "later_non_successful_run"


@pytest.mark.parametrize(
    "path",
    [
        "api/service.py",
        "api/tests/test_service.py",
        "api/fixtures/example.json",
        "Makefile",
        "ci/policy.py",
    ],
    ids=["source", "tests", "fixtures", "tools", "configuration"],
)
def test_one_bit_change_in_each_relevant_input_class_rejects_reuse(
    tmp_path: Path, path: str
) -> None:
    repository, base, head = repository_with_change(
        tmp_path,
        {"api/service.py": "candidate\n"},
        initial={
            "api/service.py": "initial\n",
            "api/tests/test_service.py": "initial\n",
            "api/fixtures/example.json": "{}\n",
            "Makefile": "test:\n\t@true\n",
            "ci/policy.py": "VALUE = 0\n",
        },
    )
    selection, records = selection_for(("api/service.py",), base=base, head=head)
    first_plan = build_plan(
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
    artifact = root / "django-result.json"
    artifact.write_text("success\n", encoding="utf-8")
    records, output = component_output(root, first_plan, "django", path=artifact)
    envelope = build_envelope(
        plan=first_plan,
        component="django",
        result="success",
        origin=local_origin(),
        command=first_plan["components"]["django"]["command"],
        execution_environment=first_plan["components"]["django"]["environment"],
        artifacts=records,
        machine_output=output,
        completed_at=datetime(2026, 8, 9, 12, tzinfo=UTC),
    )

    changed = repository / path
    body = changed.read_bytes()
    changed.write_bytes(body[:-1] + bytes([body[-1] ^ 1]))
    git(repository, "add", "-A")
    git(repository, "commit", "-m", "one-bit change")
    next_head = git(repository, "rev-parse", "HEAD")
    next_selection, next_records = selection_for((path,), base=head, head=next_head)
    next_plan = build_plan(
        repository=repository,
        repository_id="DataTalksClub/website",
        base=head,
        head=next_head,
        selection=next_selection,
        records=next_records,
        now=datetime(2026, 8, 9, 12, 1, tzinfo=UTC),
    )
    chosen, reason = choose_reusable_evidence(
        plan=next_plan,
        component="django",
        candidates=((root / "django-evidence.json", envelope),),
        evidence_root=root,
        consumer="tester",
        now=datetime(2026, 8, 9, 12, 2, tzinfo=UTC),
    )
    assert chosen is None
    assert reason == "relevant_inputs_changed"

    _failure_records, failure_output = component_output(
        root, next_plan, "django", result="failure", path=artifact
    )
    later_failure = build_envelope(
        plan=next_plan,
        component="django",
        result="failure",
        origin=local_origin(),
        command=next_plan["components"]["django"]["command"],
        execution_environment=next_plan["components"]["django"]["environment"],
        artifacts=records,
        machine_output=failure_output,
        completed_at=datetime(2026, 8, 9, 12, 3, tzinfo=UTC),
    )
    chosen, reason = choose_reusable_evidence(
        plan=first_plan,
        component="django",
        candidates=(
            (root / "old-evidence.json", envelope),
            (root / "new-failure-evidence.json", later_failure),
        ),
        evidence_root=root,
        consumer="tester",
        now=datetime(2026, 8, 9, 12, 4, tzinfo=UTC),
    )
    assert chosen is None and reason == "latest_result_failure"


def test_screenshot_reuse_requires_independent_inspection_and_exact_render_digest(
    tmp_path: Path,
) -> None:
    _repository, plan = plan_for_render_change(tmp_path)
    root = tmp_path / "evidence"
    root.mkdir()
    images = []
    captures = []
    captured_at = "2026-08-09T12:00:00Z"
    for index, _required in enumerate(plan["render"]["required_captures"]):
        image = root / f"capture-{index}.png"
        image.write_bytes(f"deterministic screenshot artifact {index}".encode())
        images.append(image)
    records = artifact_records(images, root=root)
    by_path = {record["path"]: record for record in records}
    for index, required in enumerate(plan["render"]["required_captures"]):
        artifact_path = f"capture-{index}.png"
        captures.append(
            {
                **required,
                "artifact_path": artifact_path,
                "captured_at": captured_at,
                "image_sha256": by_path[artifact_path]["sha256"],
                "independent_inspection": True,
                "render_sha256": plan["render"]["sha256"],
                "verdict": "pass",
            }
        )
    screenshot = {"captures": captures, "reviewer": "independent tester"}
    output = machine_output_claim(
        images[0],
        root=root,
        component="screenshots",
        plan=plan,
        result="success",
        screenshot=screenshot,
    )
    envelope = build_envelope(
        plan=plan,
        component="screenshots",
        result="success",
        origin=local_origin(),
        command="independent tester desktop/mobile capture and inspection",
        execution_environment=plan["components"]["screenshots"]["environment"],
        artifacts=records,
        machine_output=output,
        screenshot=screenshot,
        completed_at=datetime(2026, 8, 9, 12, tzinfo=UTC),
    )
    uninspected = deepcopy(envelope)
    uninspected["screenshot"]["captures"][0]["independent_inspection"] = False
    identity = dict(uninspected)
    identity.pop("evidence_id")
    uninspected["evidence_id"] = sha256_json(identity)
    with pytest.raises(EvidenceError, match="independent pass"):
        validate_envelope(uninspected)

    partial = deepcopy(envelope)
    partial["screenshot"]["captures"].pop()
    partial["counts"]["assertions"] -= 1
    partial["counts"]["captures"] -= 1
    partial["output"]["counts"]["assertions"] -= 1
    partial["output"]["counts"]["captures"] -= 1
    identity = dict(partial)
    identity.pop("evidence_id")
    partial["evidence_id"] = sha256_json(identity)
    chosen, reason = choose_reusable_evidence(
        plan=plan,
        component="screenshots",
        candidates=((root / "screenshot-evidence.json", partial),),
        evidence_root=root,
        consumer="tester",
        now=datetime(2026, 8, 9, 13, tzinfo=UTC),
    )
    assert chosen is None and reason == "screenshot_coverage_incomplete"

    changed_render_plan = deepcopy(plan)
    changed_render_plan["render"]["sha256"] = "f" * 64
    chosen, reason = choose_reusable_evidence(
        plan=changed_render_plan,
        component="screenshots",
        candidates=((root / "screenshot-evidence.json", envelope),),
        evidence_root=root,
        consumer="tester",
        now=datetime(2026, 8, 9, 13, tzinfo=UTC),
    )
    assert chosen is None and reason == "render_inputs_changed"
