from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from community_base import __version__ as community_base_version
from community_base.content_sync.convert.courses import convert_course_repository
from community_base.curriculum.parsers import course_collections, parse_course, read_courses

from content_sync.course_repository import ModuleFlowSource, parse_course_repository
from content_sync.dtc_flow_normalization import (
    PROJECT_ROOT,
    DtcNormalizationError,
    DtcNormalizationPlan,
    PlannedFile,
    apply_normalization_plan,
    build_normalization_plan,
)
from content_sync.dtc_flow_source import read_dtc_flow

FIXTURE = Path(__file__).parent / "fixtures/course_repository/llm_zoomcamp_2026"
MODULE_SOURCE = "cohorts/2026/01-agentic-rag/module.yaml"
MODULE_TARGET = "01-agentic-rag/module.yaml"
COHORT = "cohorts/2026/cohort.yaml"


def _snapshot(root: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for path in root.rglob("*"):
        if path.is_file():
            found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


def _scratch() -> Path:
    parent = PROJECT_ROOT / ".tmp"
    parent.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="dtc-flow-normalization-", dir=parent))


def _plan(snapshot: dict[str, bytes]):
    source = parse_course_repository(snapshot)
    return build_normalization_plan(snapshot, source)


def _converted(root: Path):
    report = convert_course_repository(root)
    assert report.ok, report.render()
    assert report.verify() == []
    result = read_courses(root)
    assert result.ok, [item.render() for item in result.diagnostics]
    parsed = parse_course(result, course_collections(result)[0])
    return report, result, parsed


def _digests(root: Path) -> dict[str, str]:
    return {path: hashlib.sha256(body).hexdigest() for path, body in _snapshot(root).items()}


def test_normalization_plan_preserves_current_fixture_identity_and_bytes() -> None:
    before = _snapshot(FIXTURE)
    plan = _plan(before)
    after = plan.snapshot()
    dispositions = {item.source_path: item for item in plan.dispositions}

    assert len(dispositions) == len(before) == 13
    assert dispositions[MODULE_SOURCE].target_path == MODULE_TARGET
    assert dispositions["README.md"].action == "unchanged"
    assert after["README.md"] == before["README.md"]
    assert (
        after["01-agentic-rag/code/notebook.ipynb"]
        == before["cohorts/2026/01-agentic-rag/code/notebook.ipynb"]
    )
    assert MODULE_SOURCE not in after
    assert all(item.action in {"unchanged", "moved", "rewritten"} for item in plan.dispositions)


def test_invalid_or_colliding_normalization_writes_nothing() -> None:
    before = _snapshot(FIXTURE)
    source = parse_course_repository(before)
    missing = dict(before)
    missing.pop(MODULE_SOURCE)
    collision = dict(before)
    collision[MODULE_TARGET] = b"collision"
    module_item = source.cohorts[2].flow[0]
    assert isinstance(module_item, ModuleFlowSource)
    bad_module = replace(module_item.module, source_path="cohorts/2026/bad.txt")
    bad_cohort = replace(source.cohorts[2], flow=(replace(module_item, module=bad_module),))
    bad_source = replace(source, cohorts=(*source.cohorts[:2], bad_cohort))
    scratch = _scratch()

    with pytest.raises(DtcNormalizationError, match="referenced_file_missing"):
        build_normalization_plan(missing, source)
    with pytest.raises(DtcNormalizationError, match="destination_collision"):
        build_normalization_plan(collision, source)
    with pytest.raises(DtcNormalizationError, match="extension_invalid"):
        build_normalization_plan(before, bad_source)
    with pytest.raises(DtcNormalizationError, match="project_scratch"):
        apply_normalization_plan(_plan(before), scratch.parent.parent / "outside")

    assert list(scratch.iterdir()) == []
    shutil.rmtree(scratch)


@pytest.mark.parametrize("unsafe", ["/outside-sentinel", "../../traversal-sentinel", ".", ""])
def test_source_snapshot_paths_are_validated_before_planning(unsafe: str) -> None:
    before = _snapshot(FIXTURE)
    source = parse_course_repository(before)
    before[unsafe] = b"must not be planned"

    with pytest.raises(DtcNormalizationError):
        build_normalization_plan(before, source)


def test_source_snapshot_case_collisions_are_rejected_before_planning() -> None:
    before = _snapshot(FIXTURE)
    source = parse_course_repository(before)
    before["01-AGENTIC-RAG/MODULE.YAML"] = b"must not be planned"

    with pytest.raises(DtcNormalizationError, match="case_colliding_paths"):
        build_normalization_plan(before, source)


@pytest.mark.parametrize(
    "case", ["absolute", "traversal", "dot", "empty", "duplicate", "case_collision"]
)
def test_forged_plans_cannot_escape_or_overwrite(case: str) -> None:
    scratch = _scratch()
    destination = scratch / "destination"
    sentinel = scratch / "sentinel"
    path = "safe.txt"
    files: tuple[PlannedFile, ...] = (PlannedFile(path, b"first"),)
    if case == "absolute":
        files = (PlannedFile(str(sentinel), b"escape"),)
    elif case == "traversal":
        files = (PlannedFile("../sentinel", b"escape"),)
    elif case == "dot":
        files = (PlannedFile(".", b"escape"),)
    elif case == "empty":
        files = (PlannedFile("", b"escape"),)
    elif case == "duplicate":
        files = (*files, PlannedFile(path, b"last"))
    elif case == "case_collision":
        files = (*files, PlannedFile("SAFE.TXT", b"last"))
    plan = DtcNormalizationPlan((), files, ())

    with pytest.raises(DtcNormalizationError):
        apply_normalization_plan(plan, destination)

    assert not destination.exists() and not sentinel.exists()
    shutil.rmtree(scratch)


def test_normalized_fixture_passes_v021_converter_parser_and_flow_contract() -> None:
    before = _snapshot(FIXTURE)
    scratch = _scratch()
    root = scratch / "repository"
    assert apply_normalization_plan(_plan(before), root)

    _report, result, parsed = _converted(root)
    state = read_dtc_flow(result, parsed)
    output = b"\n".join(_snapshot(root).values())

    assert [(item.kind, item.identity) for item in state.cohorts[0].items] == [
        ("module", "21111111-1111-4111-8111-111111111111"),
        ("project", "project-01"),
    ]
    assert parsed.course.cohorts[1].module_refs == ("21111111-1111-4111-8111-111111111111",)
    assert parsed.course.cohorts[1].homework_bindings[0]["module"] == "agentic-rag"
    for identity in _stable_identities():
        assert identity.encode() in output
    assert b"pages-24" in output and b"pages-72" in output
    assert set(result.by_path()[COHORT].values["extra"]["dtc_flow"]["items"][1]) == {"kind", "id"}
    assert _snapshot(FIXTURE) == before
    shutil.rmtree(scratch)


def _stable_identities() -> tuple[str, ...]:
    return (
        "11111111-1111-4111-8111-111111111111",
        "41111111-1111-4111-8111-111111111111",
        "42222222-2222-4222-8222-222222222222",
        "21111111-1111-4111-8111-111111111111",
        "31111111-1111-4111-8111-111111111111",
        "32222222-2222-4222-8222-222222222222",
        "51111111-1111-4111-8111-111111111111",
        "61111111-1111-4111-8111-111111111111",
        "62222222-2222-4222-8222-222222222222",
    )


def test_normalization_and_conversion_are_byte_idempotent() -> None:
    scratch = _scratch()
    root = scratch / "repository"
    apply_normalization_plan(_plan(_snapshot(FIXTURE)), root)
    _converted(root)
    first = _digests(root)

    repeated = build_normalization_plan(
        _snapshot(root), parse_course_repository(_snapshot(FIXTURE))
    )
    assert not apply_normalization_plan(repeated, root)
    report, _result, _parsed = _converted(root)

    assert report.converted == 0
    assert _digests(root) == first
    assert all(item.action == "unchanged" for item in repeated.dispositions)
    shutil.rmtree(scratch)


def test_generic_converter_still_refuses_unpreprocessed_nonempty_flow() -> None:
    scratch = _scratch()
    root = scratch / "repository"
    shutil.copytree(FIXTURE, root)
    before = _snapshot(root)

    report = convert_course_repository(root)

    if community_base_version == "0.5.21":
        refusals = {(item.path, item.rule) for item in report.refusals}
        assert (COHORT, "3.8") in refusals
        for path, body in before.items():
            if path.startswith("cohorts/2026/"):
                assert (root / path).read_bytes() == body
    else:
        assert community_base_version == "0.5.10"
        assert not report.ok
    shutil.rmtree(scratch)
