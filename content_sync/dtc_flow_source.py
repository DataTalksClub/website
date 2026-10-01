"""Pure reader for DataTalks.Club's ordered cohort flow extension."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Never
from uuid import UUID

from community_base.content_sync.documents import ReadResult
from community_base.curriculum.source import CohortGraph, ModuleGraph, ParsedCurriculum

_PROJECT_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_MAX_ITEMS = 500


@dataclass(frozen=True, slots=True)
class DtcFlowDiagnostic:
    code: str
    source_path: str
    pointer: str


class DtcFlowValidationError(ValueError):
    """One bounded error in a site-owned flow record."""

    def __init__(self, code: str, source_path: str, pointer: str) -> None:
        self.diagnostic = DtcFlowDiagnostic(code[:128], source_path[:512], pointer[:512])
        self.diagnostics = (self.diagnostic,)
        super().__init__(f"{self.diagnostic.source_path}{self.diagnostic.pointer}: {code[:128]}")


@dataclass(frozen=True, slots=True)
class DtcFlowItem:
    kind: str
    identity: str


@dataclass(frozen=True, slots=True)
class DtcCohortFlow:
    cohort_content_id: str
    cohort_slug: str
    source_path: str
    items: tuple[DtcFlowItem, ...]


@dataclass(frozen=True, slots=True)
class DtcFlowState:
    cohorts: tuple[DtcCohortFlow, ...] = ()


def _fail(code: str, path: str, pointer: str) -> Never:
    raise DtcFlowValidationError(code, path, pointer)


def _mapping(value: object, path: str, pointer: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("mapping_required", path, pointer)
    return value


def _exact_keys(value: dict[str, Any], expected: frozenset[str], path: str, pointer: str) -> None:
    unknown = sorted(set(value) - expected)
    if unknown:
        _fail("unknown_key", path, f"{pointer}/{unknown[0]}")
    missing = sorted(expected - set(value))
    if missing:
        _fail("required_key_missing", path, f"{pointer}/{missing[0]}")


def _uuid(value: object, path: str, pointer: str) -> str:
    if not isinstance(value, str) or len(value) > 36:
        _fail("invalid_module_content_id", path, pointer)
    try:
        parsed = UUID(value)
    except ValueError:
        _fail("invalid_module_content_id", path, pointer)
    if str(parsed) != value:
        _fail("invalid_module_content_id", path, pointer)
    return value


def _project_slug(value: object, path: str, pointer: str) -> str:
    if not isinstance(value, str) or len(value) > 100:
        _fail("invalid_project_id", path, pointer)
    if _PROJECT_SLUG.fullmatch(value) is None:
        _fail("invalid_project_id", path, pointer)
    return value


def _all_modules(modules: tuple[ModuleGraph, ...]) -> dict[str, ModuleGraph]:
    found: dict[str, ModuleGraph] = {}
    pending = list(modules)
    while pending:
        module = pending.pop(0)
        if module.content_id:
            found[module.content_id] = module
        pending.extend(module.children)
    return found


def _cohort_by_path(parsed: ParsedCurriculum) -> dict[str, CohortGraph]:
    found: dict[str, CohortGraph] = {}
    for cohort in parsed.course.cohorts:
        if cohort.source_path is not None:
            found[cohort.source_path] = cohort
    return found


def _read_item(value: object, path: str, pointer: str) -> DtcFlowItem:
    item = _mapping(value, path, pointer)
    kind = item.get("kind")
    if kind == "module":
        _exact_keys(item, frozenset({"kind", "content_id"}), path, pointer)
        identity = _uuid(item["content_id"], path, f"{pointer}/content_id")
        return DtcFlowItem("module", identity)
    if kind == "project":
        _exact_keys(item, frozenset({"kind", "id"}), path, pointer)
        identity = _project_slug(item["id"], path, f"{pointer}/id")
        return DtcFlowItem("project", identity)
    _fail("unknown_flow_item_kind", path, f"{pointer}/kind")


def _read_items(value: object, path: str) -> tuple[DtcFlowItem, ...]:
    if not isinstance(value, list) or not value or len(value) > _MAX_ITEMS:
        _fail("invalid_flow_items", path, "/extra/dtc_flow/items")
    items = tuple(
        _read_item(item, path, f"/extra/dtc_flow/items/{index}") for index, item in enumerate(value)
    )
    identities: set[tuple[str, str]] = set()
    for index, item in enumerate(items):
        key = (item.kind, item.identity)
        if key in identities:
            _fail("duplicate_flow_item", path, f"/extra/dtc_flow/items/{index}")
        identities.add(key)
    return items


def _placed_ids(cohort: CohortGraph, parsed: ParsedCurriculum) -> tuple[str, ...]:
    if cohort.module_refs is not None:
        return tuple(cohort.module_refs)
    found: list[str] = []
    for module in parsed.course.modules:
        if module.content_id:
            found.append(module.content_id)
    return tuple(found)


def _validate_coverage(
    items: tuple[DtcFlowItem, ...], cohort: CohortGraph, parsed: ParsedCurriculum, path: str
) -> None:
    modules = _all_modules(parsed.course.modules)
    placed: list[str] = []
    for index, item in enumerate(items):
        if item.kind == "module" and item.identity not in modules:
            _fail("unknown_module", path, f"/extra/dtc_flow/items/{index}/content_id")
        if item.kind == "module":
            placed.append(item.identity)
    if tuple(placed) != _placed_ids(cohort, parsed):
        _fail("module_coverage_or_order_mismatch", path, "/extra/dtc_flow/items")


def _read_flow(
    value: object, cohort: CohortGraph, parsed: ParsedCurriculum, path: str
) -> DtcCohortFlow:
    flow = _mapping(value, path, "/extra/dtc_flow")
    _exact_keys(flow, frozenset({"version", "items"}), path, "/extra/dtc_flow")
    if type(flow["version"]) is not int or flow["version"] != 1:
        _fail("unsupported_flow_version", path, "/extra/dtc_flow/version")
    items = _read_items(flow["items"], path)
    _validate_coverage(items, cohort, parsed, path)
    if not cohort.content_id:
        _fail("cohort_content_id_required", path, "/content_id")
    return DtcCohortFlow(cohort.content_id, cohort.slug, path, items)


def read_dtc_flow(result: ReadResult, parsed: ParsedCurriculum) -> DtcFlowState:
    """Validate DTC flow values already parsed by the package reader."""

    cohorts = _cohort_by_path(parsed)
    flows: list[DtcCohortFlow] = []
    for document in result.documents:
        if document.part.name != "cohort":
            continue
        extra = document.values.get("extra")
        if not isinstance(extra, dict) or "dtc_flow" not in extra:
            continue
        cohort = cohorts.get(document.source_path)
        if cohort is None:
            _fail("cohort_not_in_parsed_graph", document.source_path, "/extra/dtc_flow")
        flows.append(_read_flow(extra["dtc_flow"], cohort, parsed, document.source_path))
    return DtcFlowState(tuple(flows))


__all__ = [
    "DtcCohortFlow",
    "DtcFlowDiagnostic",
    "DtcFlowItem",
    "DtcFlowState",
    "DtcFlowValidationError",
    "read_dtc_flow",
]
