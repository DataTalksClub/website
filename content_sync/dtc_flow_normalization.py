"""Deterministic source normalization for DTC's legacy ordered course flow."""

from __future__ import annotations

import hashlib
import posixpath
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Never

import yaml
from community_base.content_sync.kinds import slug_from_name

from content_sync import course_repository as legacy

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DtcNormalizationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FileDisposition:
    source_path: str
    action: str
    target_path: str = ""


@dataclass(frozen=True, slots=True)
class PlannedFile:
    path: str
    content: bytes


@dataclass(frozen=True, slots=True)
class DtcNormalizationPlan:
    before: tuple[tuple[str, str], ...]
    files: tuple[PlannedFile, ...]
    dispositions: tuple[FileDisposition, ...]

    def snapshot(self) -> dict[str, bytes]:
        found: dict[str, bytes] = {}
        for item in self.files:
            if not isinstance(item.path, str) or not isinstance(item.content, bytes):
                _fail("invalid_planned_file")
            if item.path in found:
                _fail("invalid_planned_file")
            found[item.path] = item.content
        return _validated_snapshot(found)


def _fail(code: str) -> Never:
    raise DtcNormalizationError(code)


def _validated_snapshot(snapshot: Mapping[str, bytes]) -> dict[str, bytes]:
    if "." in snapshot:
        _fail("invalid_repository_path")
    try:
        return legacy._validated_snapshot(snapshot, limits=legacy.DEFAULT_LIMITS)
    except legacy.CourseRepositoryValidationError as error:
        _fail(error.diagnostics[0].code)


def _yaml_mapping(content: bytes, path: str) -> dict[str, Any]:
    return legacy._load_yaml_mapping(content, path=path, limits=legacy.DEFAULT_LIMITS)


def _dump(value: Mapping[str, Any]) -> bytes:
    return yaml.safe_dump(dict(value), sort_keys=False, allow_unicode=True).encode()


def _move_prefix(
    files: dict[str, bytes], origins: dict[str, str], source: str, target: str
) -> None:
    selected: list[str] = []
    for path in files:
        if path == source or path.startswith(f"{source}/"):
            selected.append(path)
    if not selected:
        _fail("module_subtree_missing")
    targets = {path: path.replace(source, target, 1) for path in selected}
    if len(set(targets.values())) != len(targets):
        _fail("duplicate_destination_path")
    for new in targets.values():
        if new in files and new not in selected:
            _fail("destination_collision")
    for old in sorted(selected):
        new = targets[old]
        files[new] = files.pop(old)
        origins[new] = origins.pop(old)


def _move_file(files: dict[str, bytes], origins: dict[str, str], source: str, target: str) -> None:
    if source not in files:
        _fail("referenced_file_missing")
    if target in files and target != source:
        _fail("destination_collision")
    files[target] = files.pop(source)
    origins[target] = origins.pop(source)


def _rewrite_unit(content: bytes, code_paths: tuple[tuple[str, str], ...], path: str) -> bytes:
    text = content.decode("utf-8")
    if not text.startswith("---\n"):
        return content
    head, body = text[4:].split("---", 1)
    values = _yaml_mapping(head.encode(), path)
    if code_paths:
        code = values.get("code")
        if not isinstance(code, list) or len(code) != len(code_paths):
            _fail("unit_code_contract_mismatch")
        for item, (label, path) in zip(code, code_paths, strict=True):
            if not isinstance(item, dict) or item.get("label") != label:
                _fail("unit_code_contract_mismatch")
            item["path"] = path
    return b"---\n" + _dump(values) + b"---" + body.encode()


def _flatten_units(files: dict[str, bytes], origins: dict[str, str], module, target: str) -> None:
    manifest = f"{target}/module.yaml"
    values = _yaml_mapping(files[manifest], manifest)
    declarations = values.get("units")
    if not isinstance(declarations, list) or len(declarations) != len(module.units):
        _fail("module_units_contract_mismatch")
    for declaration, unit in zip(declarations, module.units, strict=True):
        source = unit.source_path.replace(str(PurePosixPath(module.source_path).parent), target, 1)
        destination = f"{target}/{PurePosixPath(source).name}"
        _move_file(files, origins, source, destination)
        if not isinstance(declaration, dict):
            _fail("module_units_contract_mismatch")
        declaration["path"] = PurePosixPath(destination).name
        code_paths = tuple(
            (
                entry.label,
                posixpath.relpath(
                    entry.source_path.replace(
                        str(PurePosixPath(module.source_path).parent), target, 1
                    ),
                    target,
                ),
            )
            for entry in unit.metadata.code
        )
        files[destination] = _rewrite_unit(files[destination], code_paths, destination)
    files[manifest] = _dump(values)
    lesson_dir = f"{target}/lessons"
    if any(path.startswith(f"{lesson_dir}/") for path in files):
        _fail("unaccounted_lesson_file")


def _flow_items(flow) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for item in flow:
        if isinstance(item, legacy.ModuleFlowSource):
            items.append({"kind": "module", "content_id": item.module.content_id})
        elif isinstance(item, legacy.ProjectFlowSource):
            items.append({"kind": "project", "id": item.slug})
        else:
            _fail("unknown_typed_flow_item")
    return items


def _normalize_course(files: dict[str, bytes], source: legacy.CourseRepositorySource) -> None:
    values = _yaml_mapping(files["course.yaml"], "course.yaml")
    if source.course.description is None:
        _fail("course_description_required")
    values["description"] = source.course.description
    files["course.yaml"] = _dump(values)


def _normalize_cohort_manifest(
    files: dict[str, bytes], cohort, modules: list[str], bindings: list[dict[str, str]]
) -> None:
    if cohort.source_path is None:
        return
    values = _yaml_mapping(files[cohort.source_path], cohort.source_path)
    values["delivery"] = "live"
    if cohort.flow:
        values.pop("flow", None)
        values["modules"] = modules
        values["homework"] = bindings
        extra = values.get("extra") or {}
        if not isinstance(extra, dict) or "dtc_flow" in extra:
            _fail("dtc_flow_destination_collision")
        extra["dtc_flow"] = {"version": 1, "items": _flow_items(cohort.flow)}
        values["extra"] = extra
    files[cohort.source_path] = _dump(values)


def _normalize_flow(files: dict[str, bytes], origins: dict[str, str], cohort) -> None:
    modules: list[str] = []
    bindings: list[dict[str, str]] = []
    for item in cohort.flow:
        if not isinstance(item, legacy.ModuleFlowSource):
            continue
        if PurePosixPath(item.module.source_path).suffix != ".yaml":
            _fail("module_manifest_extension_invalid")
        if PurePosixPath(item.homework.source_path).suffix != ".yaml":
            _fail("homework_manifest_extension_invalid")
        source_dir = str(PurePosixPath(item.module.source_path).parent)
        if item.module.source_path not in files:
            _fail("referenced_file_missing")
        target_dir = PurePosixPath(source_dir).name
        _move_prefix(files, origins, source_dir, target_dir)
        _flatten_units(files, origins, item.module, target_dir)
        homework_dir = f"cohorts/{cohort.identifier}/homework/{target_dir}"
        for source_path in (item.homework.source_path, item.homework.instructions_source_path):
            moved = source_path.replace(source_dir, target_dir, 1)
            _move_file(files, origins, moved, f"{homework_dir}/{PurePosixPath(moved).name}")
        module_slug = slug_from_name(target_dir)
        modules.append(module_slug)
        bindings.append({"module": module_slug, "source": f"{homework_dir}/homework.yaml"})
    _normalize_cohort_manifest(files, cohort, modules, bindings)


def _dispositions(
    before: Mapping[str, bytes], after: Mapping[str, bytes], origins: Mapping[str, str]
) -> tuple[FileDisposition, ...]:
    by_source = {source: target for target, source in origins.items()}
    result: list[FileDisposition] = []
    for path, content in sorted(before.items()):
        target = by_source.get(path)
        if target is None:
            result.append(FileDisposition(path, "intentionally_removed"))
        elif target != path:
            result.append(FileDisposition(path, "moved", target))
        elif after[path] == content:
            result.append(FileDisposition(path, "unchanged"))
        else:
            result.append(FileDisposition(path, "rewritten"))
    return tuple(result)


def build_normalization_plan(
    snapshot: Mapping[str, bytes], source: legacy.CourseRepositorySource
) -> DtcNormalizationPlan:
    """Build the complete output snapshot without performing filesystem writes."""
    validated = _validated_snapshot(snapshot)
    before = tuple(
        (path, hashlib.sha256(content).hexdigest()) for path, content in sorted(validated.items())
    )
    if "content.yaml" in validated:
        planned = tuple(PlannedFile(path, content) for path, content in sorted(validated.items()))
        dispositions = tuple(FileDisposition(path, "unchanged") for path in sorted(validated))
        return DtcNormalizationPlan(before, planned, dispositions)
    files = dict(validated)
    origins = {path: path for path in files}
    _normalize_course(files, source)
    for cohort in source.cohorts:
        if cohort.flow:
            _normalize_flow(files, origins, cohort)
        else:
            _normalize_cohort_manifest(files, cohort, [], [])
    planned = tuple(PlannedFile(path, content) for path, content in sorted(files.items()))
    plan = DtcNormalizationPlan(before, planned, _dispositions(validated, files, origins))
    plan.snapshot()
    return plan


def _directory_snapshot(root: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for path in root.rglob("*"):
        if path.is_file():
            snapshot[path.relative_to(root).as_posix()] = path.read_bytes()
    return snapshot


def apply_normalization_plan(plan: DtcNormalizationPlan, destination: Path) -> bool:
    """Atomically materialize a complete plan below this repository's ``.tmp``."""
    target = destination.resolve()
    scratch = (PROJECT_ROOT / ".tmp").resolve()
    if not target.is_relative_to(scratch) or target == scratch:
        _fail("destination_must_be_project_scratch")
    expected = plan.snapshot()
    if target.exists():
        if target.is_dir() and _directory_snapshot(target) == expected:
            return False
        _fail("destination_not_empty_or_identical")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.staging"
    if staging.exists():
        _fail("staging_collision")
    try:
        staging.mkdir()
        for path, content in expected.items():
            output = staging / path
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
        staging.rename(target)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return True
