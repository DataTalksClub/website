from __future__ import annotations

import shutil
import tempfile
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
from community_base.content_sync.convert.courses import convert_course_repository
from community_base.curriculum.parsers import course_collections, parse_course, read_courses

from content_sync.course_repository import parse_course_repository
from content_sync.dtc_flow_normalization import (
    PROJECT_ROOT,
    apply_normalization_plan,
    build_normalization_plan,
)
from content_sync.dtc_flow_source import DtcFlowValidationError, read_dtc_flow

FIXTURE = Path(__file__).parent / "fixtures/course_repository/llm_zoomcamp_2026"
COHORT_PATH = "cohorts/2026/cohort.yaml"
MODULE_ID = "21111111-1111-4111-8111-111111111111"


def _snapshot(root: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for path in root.rglob("*"):
        if path.is_file():
            found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found


@pytest.fixture
def parsed_source():
    scratch = PROJECT_ROOT / ".tmp"
    scratch.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="dtc-flow-source-", dir=scratch)) / "repository"
    snapshot = _snapshot(FIXTURE)
    plan = build_normalization_plan(snapshot, parse_course_repository(snapshot))
    apply_normalization_plan(plan, root)
    report = convert_course_repository(root)
    assert report.ok, report.render()
    result = read_courses(root)
    collection = course_collections(result)[0]
    yield result, parse_course(result, collection)
    shutil.rmtree(root.parent)


def _flow_mapping(result) -> dict:
    return result.by_path()[COHORT_PATH].values["extra"]["dtc_flow"]


def test_current_fixture_flow_builds_frozen_typed_state(parsed_source) -> None:
    result, parsed = parsed_source
    mapping = _flow_mapping(result)

    state = read_dtc_flow(result, parsed)
    mapping["items"][0]["content_id"] = "00000000-0000-4000-8000-000000000000"

    flow = state.cohorts[0]
    assert flow.cohort_content_id == "41111111-1111-4111-8111-111111111111"
    assert [(item.kind, item.identity) for item in flow.items] == [
        ("module", MODULE_ID),
        ("project", "project-01"),
    ]
    with pytest.raises(FrozenInstanceError):
        flow.cohort_slug = "changed"  # type: ignore[misc]


def test_absent_dtc_flow_leaves_default_package_course_unchanged(parsed_source) -> None:
    result, parsed = parsed_source
    del result.by_path()[COHORT_PATH].values["extra"]["dtc_flow"]

    assert read_dtc_flow(result, parsed).cohorts == ()
    assert parsed.course.modules[0].content_id == MODULE_ID


@pytest.mark.parametrize(
    ("replacement", "code", "pointer"),
    [
        ({"version": 2, "items": []}, "unsupported_flow_version", "/extra/dtc_flow/version"),
        (
            {"version": 1, "items": [{"kind": "project", "id": "secret", "title": "x"}]},
            "unknown_key",
            "/extra/dtc_flow/items/0/title",
        ),
        (
            {"version": 1, "items": [{"kind": "module", "content_id": "secret"}]},
            "invalid_module_content_id",
            "/extra/dtc_flow/items/0/content_id",
        ),
        (
            {
                "version": 1,
                "items": [
                    {"kind": "module", "content_id": MODULE_ID},
                    {"kind": "project", "id": "project-01"},
                    {"kind": "project", "id": "project-01"},
                ],
            },
            "duplicate_flow_item",
            "/extra/dtc_flow/items/2",
        ),
        (
            {"version": 1, "items": [{"kind": "project", "id": "project-01"}]},
            "module_coverage_or_order_mismatch",
            "/extra/dtc_flow/items",
        ),
    ],
)
def test_flow_contract_rejects_shape_identity_and_order_errors(
    parsed_source, replacement, code, pointer
) -> None:
    result, parsed = parsed_source
    result.by_path()[COHORT_PATH].values["extra"]["dtc_flow"] = replacement

    with pytest.raises(DtcFlowValidationError) as raised:
        read_dtc_flow(result, parsed)

    assert raised.value.diagnostic.code == code
    assert raised.value.diagnostic.source_path == COHORT_PATH
    assert raised.value.diagnostic.pointer == pointer
    assert "secret" not in str(raised.value)
    assert len(str(raised.value)) < 700


def test_module_order_must_match_the_package_graph(parsed_source) -> None:
    result, parsed = parsed_source
    second_id = "29999999-9999-4999-8999-999999999999"
    second = replace(parsed.course.modules[0], content_id=second_id, slug="second")
    cohort = replace(parsed.course.cohorts[1], module_refs=(MODULE_ID, second_id))
    parsed = replace(
        parsed,
        course=replace(
            parsed.course,
            modules=(*parsed.course.modules, second),
            cohorts=(parsed.course.cohorts[0], cohort),
        ),
    )
    _flow_mapping(result)["items"] = [
        {"kind": "module", "content_id": second_id},
        {"kind": "module", "content_id": MODULE_ID},
    ]

    with pytest.raises(DtcFlowValidationError) as raised:
        read_dtc_flow(result, parsed)

    assert raised.value.diagnostic.code == "module_coverage_or_order_mismatch"
