"""Canonical, redacted parity findings and fail-closed compatibility guard."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from compatibility.models import ManifestValidationError, Reference, ReferenceKind
from compatibility.redaction import text_contains_unredacted_private_data
from compatibility.schema import RecordSchemaError, load_schema, validate_record

REPORT_SCHEMA_VERSION = 1
DEFAULT_REPORT_SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "_docs"
    / "compatibility"
    / "seo-parity-report.schema.json"
)
MAX_FINDINGS = 100_000

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FINDING_ID = re.compile(r"^finding-[0-9a-f]{24}$")
_STABLE_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_BINDING_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}$")
_FINDING_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_FIELD = re.compile(r"^[A-Za-z0-9_.$:\[\]-]{1,255}$")


class ReportValidationError(ValueError):
    """A parity report is unsafe or internally inconsistent."""


class ReportDecodeError(ReportValidationError):
    """Serialized parity report data is malformed or noncanonical."""


class FindingSeverity(StrEnum):
    WARNING = "warning"
    BLOCKING = "blocking"


class ParityStatus(StrEnum):
    PASS = "PASS"
    BLOCKED = "BLOCKED"


class GuardFailure(StrEnum):
    WRONG_TARGET = "wrong_target"
    WRONG_SCOPE = "wrong_scope"
    STALE_ARTIFACT_DIGEST = "stale_artifact_digest"
    STALE_EXPECTATION_DIGEST = "stale_expectation_digest"
    WRONG_EXPECTATION_COUNT = "wrong_expectation_count"
    INCOMPLETE_REPORT = "incomplete_report"
    BLOCKED_REPORT = "blocked_report"


class CompatibilityGuardError(RuntimeError):
    """Typed denial raised before an activation/cutover callback can run."""

    def __init__(self, code: GuardFailure) -> None:
        self.code = code
        super().__init__(code.value)


def _text(value: object, field: str, *, maximum: int, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise ReportValidationError(f"{field}_must_be_string")
    if not allow_empty and not value:
        raise ReportValidationError(f"{field}_must_not_be_empty")
    if value != value.strip():
        raise ReportValidationError(f"{field}_must_not_have_outer_whitespace")
    if len(value) > maximum:
        raise ReportValidationError(f"{field}_is_too_long")
    if any(ord(character) < 0x20 for character in value):
        raise ReportValidationError(f"{field}_contains_control_character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ReportValidationError(f"{field}_contains_invalid_unicode") from exc
    if text_contains_unredacted_private_data(value):
        raise ReportValidationError(f"{field}_contains_private_data")
    return value


def _digest(value: object, field: str) -> str:
    digest = _text(value, field, maximum=64)
    if _SHA256.fullmatch(digest) is None:
        raise ReportValidationError(f"{field}_must_be_sha256")
    return digest


def _stable_id(value: object, field: str) -> str:
    identifier = _text(value, field, maximum=128)
    if _STABLE_ID.fullmatch(identifier) is None:
        raise ReportValidationError(f"{field}_must_be_stable_identifier")
    return identifier


def _binding_id(value: object, field: str) -> str:
    identifier = _text(value, field, maximum=256)
    if _BINDING_ID.fullmatch(identifier) is None:
        raise ReportValidationError(f"{field}_must_be_stable_binding")
    return identifier


def _origin(value: object) -> str:
    origin = _text(value, "target_origin", maximum=512)
    try:
        Reference(ReferenceKind.INTERNAL_LINK, origin)
        parsed = urlsplit(origin)
        if parsed.port is not None and not 1 <= parsed.port <= 65_535:
            raise ValueError
    except (ManifestValidationError, ValueError) as exc:
        raise ReportValidationError("target_origin_must_be_safe_https_origin") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ReportValidationError("target_origin_must_be_safe_https_origin")
    return origin.rstrip("/")


def stable_finding_id(code: str, scope: str, subject_id: str, field: str) -> str:
    """Return a value-free stable identity for one semantic finding."""

    identity = json.dumps(
        [REPORT_SCHEMA_VERSION, code, scope, subject_id, field],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"finding-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"


@dataclass(frozen=True, slots=True)
class Finding:
    """One actionable finding containing identifiers, never observed raw values."""

    finding_id: str
    code: str
    severity: FindingSeverity
    scope: str
    subject_id: str
    field: str
    required_action: str

    def __post_init__(self) -> None:
        finding_id = _text(self.finding_id, "finding_id", maximum=32)
        if _FINDING_ID.fullmatch(finding_id) is None:
            raise ReportValidationError("finding_id_is_invalid")
        code = _text(self.code, "finding_code", maximum=128)
        if _FINDING_CODE.fullmatch(code) is None:
            raise ReportValidationError("finding_code_is_invalid")
        if type(self.severity) is not FindingSeverity:
            raise ReportValidationError("finding_severity_must_be_enum")
        scope = _stable_id(self.scope, "finding_scope")
        subject_id = _text(self.subject_id, "finding_subject_id", maximum=128)
        if _STABLE_ID.fullmatch(subject_id) is None and not re.fullmatch(
            r"^(?:expectation|contract)-[0-9a-f]{24}$", subject_id
        ):
            raise ReportValidationError("finding_subject_id_is_invalid")
        field = _text(self.field, "finding_field", maximum=256)
        if _FIELD.fullmatch(field) is None:
            raise ReportValidationError("finding_field_is_invalid")
        required_action = _text(self.required_action, "finding_required_action", maximum=128)
        if _FINDING_CODE.fullmatch(required_action) is None:
            raise ReportValidationError("finding_required_action_is_invalid")
        if finding_id != stable_finding_id(code, scope, subject_id, field):
            raise ReportValidationError("finding_id_is_not_canonical")

    @classmethod
    def create(
        cls,
        *,
        code: str,
        severity: FindingSeverity,
        scope: str,
        subject_id: str,
        field: str,
        required_action: str,
    ) -> Finding:
        return cls(
            finding_id=stable_finding_id(code, scope, subject_id, field),
            code=code,
            severity=severity,
            scope=scope,
            subject_id=subject_id,
            field=field,
            required_action=required_action,
        )


@dataclass(frozen=True, slots=True)
class TargetBinding:
    """Exact fixture/release and derived-state identity evaluated by the gate."""

    target_id: str
    target_origin: str
    release_id: str
    parser_version: str
    route_sha256: str
    asset_sha256: str
    projection_sha256: str

    def __post_init__(self) -> None:
        _stable_id(self.target_id, "target_id")
        canonical_origin = _origin(self.target_origin)
        if canonical_origin != self.target_origin:
            raise ReportValidationError("target_origin_is_not_canonical")
        _binding_id(self.release_id, "target_release_id")
        _binding_id(self.parser_version, "target_parser_version")
        _digest(self.route_sha256, "target_route_sha256")
        _digest(self.asset_sha256, "target_asset_sha256")
        _digest(self.projection_sha256, "target_projection_sha256")


def _finding_key(finding: Finding) -> tuple[str, str, str, str, str]:
    return (
        finding.scope,
        finding.subject_id,
        finding.code,
        finding.field,
        finding.finding_id,
    )


@dataclass(frozen=True, slots=True)
class ParityReport:
    """Deterministic gate result bound to inputs, scope, target, and counts."""

    status: ParityStatus
    target: TargetBinding
    manifest_sha256: str
    differences_sha256: str
    public_contracts_sha256: str
    expectation_set_sha256: str
    scope: tuple[str, ...]
    complete: bool
    expectation_count: int
    observation_count: int
    matched_count: int
    blocking_finding_count: int
    warning_finding_count: int
    findings: tuple[Finding, ...] = ()
    schema_version: int = REPORT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != REPORT_SCHEMA_VERSION:
            raise ReportValidationError("unsupported_report_schema_version")
        if type(self.status) is not ParityStatus:
            raise ReportValidationError("report_status_must_be_enum")
        if type(self.target) is not TargetBinding:
            raise ReportValidationError("report_target_must_be_target_binding")
        _digest(self.manifest_sha256, "report_manifest_sha256")
        _digest(self.differences_sha256, "report_differences_sha256")
        _digest(self.public_contracts_sha256, "report_public_contracts_sha256")
        _digest(self.expectation_set_sha256, "report_expectation_set_sha256")
        if type(self.scope) is not tuple or not self.scope:
            raise ReportValidationError("report_scope_must_be_nonempty_tuple")
        for item in self.scope:
            _stable_id(item, "report_scope_item")
        if self.scope != tuple(sorted(set(self.scope))):
            raise ReportValidationError("report_scope_must_be_unique_and_sorted")
        if type(self.complete) is not bool:
            raise ReportValidationError("report_complete_must_be_boolean")
        for name, value in (
            ("expectation_count", self.expectation_count),
            ("observation_count", self.observation_count),
            ("matched_count", self.matched_count),
            ("blocking_finding_count", self.blocking_finding_count),
            ("warning_finding_count", self.warning_finding_count),
        ):
            if type(value) is not int or value < 0:
                raise ReportValidationError(f"report_{name}_must_be_nonnegative_integer")
        if self.matched_count > min(self.expectation_count, self.observation_count):
            raise ReportValidationError("report_matched_count_exceeds_inputs")
        if type(self.findings) is not tuple:
            raise ReportValidationError("report_findings_must_be_tuple")
        if len(self.findings) > MAX_FINDINGS:
            raise ReportValidationError("report_has_too_many_findings")
        if any(type(item) is not Finding for item in self.findings):
            raise ReportValidationError("report_findings_must_contain_findings")
        if self.findings != tuple(sorted(self.findings, key=_finding_key)):
            raise ReportValidationError("report_findings_must_be_canonically_sorted")
        ids = [item.finding_id for item in self.findings]
        if len(ids) != len(set(ids)):
            raise ReportValidationError("report_finding_ids_must_be_unique")
        blocking = sum(item.severity is FindingSeverity.BLOCKING for item in self.findings)
        warnings = sum(item.severity is FindingSeverity.WARNING for item in self.findings)
        if blocking != self.blocking_finding_count or warnings != self.warning_finding_count:
            raise ReportValidationError("report_finding_counts_do_not_match")
        should_pass = (
            self.complete
            and self.expectation_count > 0
            and self.expectation_count == self.observation_count == self.matched_count
            and blocking == 0
        )
        expected_status = ParityStatus.PASS if should_pass else ParityStatus.BLOCKED
        if self.status is not expected_status:
            raise ReportValidationError("report_status_is_inconsistent")

    @classmethod
    def create(
        cls,
        *,
        target: TargetBinding,
        manifest_sha256: str,
        differences_sha256: str,
        public_contracts_sha256: str,
        expectation_set_sha256: str,
        scope: tuple[str, ...],
        complete: bool,
        expectation_count: int,
        observation_count: int,
        matched_count: int,
        findings: tuple[Finding, ...] = (),
    ) -> ParityReport:
        ordered = tuple(sorted(findings, key=_finding_key))
        blocking = sum(item.severity is FindingSeverity.BLOCKING for item in ordered)
        warnings = sum(item.severity is FindingSeverity.WARNING for item in ordered)
        should_pass = (
            complete
            and expectation_count > 0
            and expectation_count == observation_count == matched_count
            and blocking == 0
        )
        return cls(
            status=ParityStatus.PASS if should_pass else ParityStatus.BLOCKED,
            target=target,
            manifest_sha256=manifest_sha256,
            differences_sha256=differences_sha256,
            public_contracts_sha256=public_contracts_sha256,
            expectation_set_sha256=expectation_set_sha256,
            scope=tuple(sorted(set(scope))),
            complete=complete,
            expectation_count=expectation_count,
            observation_count=observation_count,
            matched_count=matched_count,
            blocking_finding_count=blocking,
            warning_finding_count=warnings,
            findings=ordered,
        )

    @property
    def sha256(self) -> str:
        return hashlib.sha256(dumps_report(self).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class GateRequirements:
    """Fresh bindings required before a side-effecting callback may run."""

    target: TargetBinding
    manifest_sha256: str
    differences_sha256: str
    public_contracts_sha256: str
    expectation_set_sha256: str
    scope: tuple[str, ...]
    expectation_count: int

    def __post_init__(self) -> None:
        if type(self.target) is not TargetBinding:
            raise ReportValidationError("requirements_target_must_be_target_binding")
        _digest(self.manifest_sha256, "requirements_manifest_sha256")
        _digest(self.differences_sha256, "requirements_differences_sha256")
        _digest(self.public_contracts_sha256, "requirements_public_contracts_sha256")
        _digest(self.expectation_set_sha256, "requirements_expectation_set_sha256")
        if type(self.scope) is not tuple or not self.scope:
            raise ReportValidationError("requirements_scope_must_be_nonempty_tuple")
        for item in self.scope:
            _stable_id(item, "requirements_scope_item")
        if self.scope != tuple(sorted(set(self.scope))):
            raise ReportValidationError("requirements_scope_must_be_unique_and_sorted")
        if type(self.expectation_count) is not int or self.expectation_count < 1:
            raise ReportValidationError("requirements_expectation_count_must_be_positive")


def require_compatible[T](
    report: ParityReport,
    requirements: GateRequirements,
    callback: Callable[[], T] | None = None,
) -> T | None:
    """Fail closed on any stale/incomplete report, then optionally run a callback."""

    if type(report) is not ParityReport or type(requirements) is not GateRequirements:
        raise TypeError("require_compatible_requires_typed_report_and_requirements")
    if report.target != requirements.target:
        raise CompatibilityGuardError(GuardFailure.WRONG_TARGET)
    if report.scope != requirements.scope:
        raise CompatibilityGuardError(GuardFailure.WRONG_SCOPE)
    if (
        report.manifest_sha256 != requirements.manifest_sha256
        or report.differences_sha256 != requirements.differences_sha256
        or report.public_contracts_sha256 != requirements.public_contracts_sha256
    ):
        raise CompatibilityGuardError(GuardFailure.STALE_ARTIFACT_DIGEST)
    if report.expectation_set_sha256 != requirements.expectation_set_sha256:
        raise CompatibilityGuardError(GuardFailure.STALE_EXPECTATION_DIGEST)
    if report.expectation_count != requirements.expectation_count:
        raise CompatibilityGuardError(GuardFailure.WRONG_EXPECTATION_COUNT)
    if (
        not report.complete
        or report.expectation_count < 1
        or report.expectation_count != report.observation_count
        or report.expectation_count != report.matched_count
    ):
        raise CompatibilityGuardError(GuardFailure.INCOMPLETE_REPORT)
    if report.status is not ParityStatus.PASS or report.blocking_finding_count:
        raise CompatibilityGuardError(GuardFailure.BLOCKED_REPORT)
    return callback() if callback is not None else None


def dumps_report(report: ParityReport) -> str:
    """Serialize a report as canonical, schema-validated JSON."""

    if type(report) is not ParityReport:
        raise ReportValidationError("report_must_be_parity_report")
    record = _encode_report(report)
    try:
        validate_record(record, load_schema(DEFAULT_REPORT_SCHEMA))
    except (OSError, json.JSONDecodeError, RecordSchemaError) as exc:
        raise ReportValidationError("report_failed_schema_validation") from exc
    return (
        json.dumps(
            record,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def loads_report(text: str) -> ParityReport:
    """Decode strict canonical JSON and reject duplicates or equivalent rewrites."""

    if type(text) is not str or not text or not text.endswith("\n") or "\r" in text:
        raise ReportDecodeError("report_json_is_not_canonical")
    try:
        encoded_size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ReportDecodeError("report_json_contains_invalid_unicode") from exc
    if encoded_size > 64 * 1024 * 1024 or len(text.splitlines()) != 1:
        raise ReportDecodeError("report_json_is_not_canonical")
    try:
        record = json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_strict_json_object,
        )
        if type(record) is not dict:
            raise ReportDecodeError("report_document_must_be_object")
        validate_record(record, load_schema(DEFAULT_REPORT_SCHEMA))
        report = _decode_report(record)
    except ReportDecodeError:
        raise
    except (json.JSONDecodeError, OSError, RecordSchemaError, ReportValidationError) as exc:
        raise ReportDecodeError("report_document_is_invalid") from exc
    if dumps_report(report) != text:
        raise ReportDecodeError("report_json_is_not_canonical")
    return report


def _encode_report(report: ParityReport) -> dict[str, object]:
    return {
        "record_kind": "seo_parity_report",
        "schema_version": report.schema_version,
        "status": report.status.value,
        "target": _encode_target(report.target),
        "manifest_sha256": report.manifest_sha256,
        "differences_sha256": report.differences_sha256,
        "public_contracts_sha256": report.public_contracts_sha256,
        "expectation_set_sha256": report.expectation_set_sha256,
        "scope": list(report.scope),
        "complete": report.complete,
        "expectation_count": report.expectation_count,
        "observation_count": report.observation_count,
        "matched_count": report.matched_count,
        "blocking_finding_count": report.blocking_finding_count,
        "warning_finding_count": report.warning_finding_count,
        "findings": [_encode_finding(item) for item in report.findings],
    }


def _encode_target(target: TargetBinding) -> dict[str, object]:
    return {
        "target_id": target.target_id,
        "target_origin": target.target_origin,
        "release_id": target.release_id,
        "parser_version": target.parser_version,
        "route_sha256": target.route_sha256,
        "asset_sha256": target.asset_sha256,
        "projection_sha256": target.projection_sha256,
    }


def _encode_finding(finding: Finding) -> dict[str, object]:
    return {
        "finding_id": finding.finding_id,
        "code": finding.code,
        "severity": finding.severity.value,
        "scope": finding.scope,
        "subject_id": finding.subject_id,
        "field": finding.field,
        "required_action": finding.required_action,
    }


def _decode_report(record: Mapping[str, object]) -> ParityReport:
    _expect_keys(
        record,
        {
            "record_kind",
            "schema_version",
            "status",
            "target",
            "manifest_sha256",
            "differences_sha256",
            "public_contracts_sha256",
            "expectation_set_sha256",
            "scope",
            "complete",
            "expectation_count",
            "observation_count",
            "matched_count",
            "blocking_finding_count",
            "warning_finding_count",
            "findings",
        },
        "report",
    )
    if record["record_kind"] != "seo_parity_report":
        raise ReportDecodeError("report_record_kind_is_invalid")
    return ParityReport(
        status=_enum(ParityStatus, record["status"], "report_status"),
        target=_decode_target(_object(record["target"], "target")),
        manifest_sha256=record["manifest_sha256"],  # type: ignore[arg-type]
        differences_sha256=record["differences_sha256"],  # type: ignore[arg-type]
        public_contracts_sha256=record["public_contracts_sha256"],  # type: ignore[arg-type]
        expectation_set_sha256=record["expectation_set_sha256"],  # type: ignore[arg-type]
        scope=tuple(_array(record["scope"], "scope")),  # type: ignore[arg-type]
        complete=record["complete"],  # type: ignore[arg-type]
        expectation_count=record["expectation_count"],  # type: ignore[arg-type]
        observation_count=record["observation_count"],  # type: ignore[arg-type]
        matched_count=record["matched_count"],  # type: ignore[arg-type]
        blocking_finding_count=record["blocking_finding_count"],  # type: ignore[arg-type]
        warning_finding_count=record["warning_finding_count"],  # type: ignore[arg-type]
        findings=tuple(
            _decode_finding(_object(item, "finding"))
            for item in _array(record["findings"], "findings")
        ),
        schema_version=record["schema_version"],  # type: ignore[arg-type]
    )


def _decode_target(record: Mapping[str, object]) -> TargetBinding:
    _expect_keys(
        record,
        {
            "target_id",
            "target_origin",
            "release_id",
            "parser_version",
            "route_sha256",
            "asset_sha256",
            "projection_sha256",
        },
        "target",
    )
    return TargetBinding(
        target_id=record["target_id"],  # type: ignore[arg-type]
        target_origin=record["target_origin"],  # type: ignore[arg-type]
        release_id=record["release_id"],  # type: ignore[arg-type]
        parser_version=record["parser_version"],  # type: ignore[arg-type]
        route_sha256=record["route_sha256"],  # type: ignore[arg-type]
        asset_sha256=record["asset_sha256"],  # type: ignore[arg-type]
        projection_sha256=record["projection_sha256"],  # type: ignore[arg-type]
    )


def _decode_finding(record: Mapping[str, object]) -> Finding:
    _expect_keys(
        record,
        {
            "finding_id",
            "code",
            "severity",
            "scope",
            "subject_id",
            "field",
            "required_action",
        },
        "finding",
    )
    return Finding(
        finding_id=record["finding_id"],  # type: ignore[arg-type]
        code=record["code"],  # type: ignore[arg-type]
        severity=_enum(FindingSeverity, record["severity"], "finding_severity"),
        scope=record["scope"],  # type: ignore[arg-type]
        subject_id=record["subject_id"],  # type: ignore[arg-type]
        field=record["field"],  # type: ignore[arg-type]
        required_action=record["required_action"],  # type: ignore[arg-type]
    )


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReportDecodeError("report_json_has_duplicate_key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ReportDecodeError(f"report_json_has_nonfinite_value:{value}")


def _expect_keys(record: Mapping[str, object], expected: set[str], field: str) -> None:
    if set(record) != expected:
        raise ReportDecodeError(f"{field}_has_unexpected_keys")


def _array(value: object, field: str) -> list[object]:
    if type(value) is not list:
        raise ReportDecodeError(f"{field}_must_be_array")
    return value


def _object(value: object, field: str) -> Mapping[str, object]:
    if type(value) is not dict:
        raise ReportDecodeError(f"{field}_must_be_object")
    return value


def _enum(enum_type: type[StrEnum], value: object, field: str):  # type: ignore[no-untyped-def]
    if type(value) is not str:
        raise ReportDecodeError(f"{field}_must_be_string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ReportDecodeError(f"{field}_is_unknown") from exc
