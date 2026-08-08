from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from compatibility.report import (
    DEFAULT_REPORT_SCHEMA,
    CompatibilityGuardError,
    Finding,
    FindingSeverity,
    GateRequirements,
    GuardFailure,
    ParityReport,
    ParityStatus,
    ReportDecodeError,
    ReportValidationError,
    TargetBinding,
    dumps_report,
    loads_report,
    require_compatible,
    stable_finding_id,
)
from compatibility.schema import load_schema, validate_record

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
EXPECTATION_ID = "expectation-0123456789abcdef01234567"


def target(
    *,
    target_id: str = "django-fixture",
    release_id: str = "fixture-v1",
) -> TargetBinding:
    return TargetBinding(
        target_id=target_id,
        target_origin="https://fixture.invalid",
        release_id=release_id,
        parser_version="compatibility-extractor/1",
        route_sha256=SHA_A,
        asset_sha256=SHA_B,
        projection_sha256=SHA_C,
    )


def warning() -> Finding:
    return Finding.create(
        code="optional_external_unreachable",
        severity=FindingSeverity.WARNING,
        scope="docs",
        subject_id=EXPECTATION_ID,
        field="metadata.references",
        required_action="review_external_warning",
    )


def blocker() -> Finding:
    return Finding.create(
        code="canonical_mismatch",
        severity=FindingSeverity.BLOCKING,
        scope="docs",
        subject_id=EXPECTATION_ID,
        field="metadata.canonical_url",
        required_action="restore_approved_canonical",
    )


def report(
    *,
    binding: TargetBinding | None = None,
    complete: bool = True,
    expectation_count: int = 1,
    observation_count: int = 1,
    matched_count: int = 1,
    findings: tuple[Finding, ...] = (),
    scope: tuple[str, ...] = ("docs",),
    manifest_sha256: str = SHA_A,
    expectation_set_sha256: str = SHA_D,
) -> ParityReport:
    return ParityReport.create(
        target=binding or target(),
        manifest_sha256=manifest_sha256,
        differences_sha256=SHA_B,
        public_contracts_sha256=SHA_C,
        expectation_set_sha256=expectation_set_sha256,
        scope=scope,
        complete=complete,
        expectation_count=expectation_count,
        observation_count=observation_count,
        matched_count=matched_count,
        findings=findings,
    )


def requirements(
    *,
    binding: TargetBinding | None = None,
    scope: tuple[str, ...] = ("docs",),
    manifest_sha256: str = SHA_A,
    expectation_set_sha256: str = SHA_D,
    expectation_count: int = 1,
) -> GateRequirements:
    return GateRequirements(
        target=binding or target(),
        manifest_sha256=manifest_sha256,
        differences_sha256=SHA_B,
        public_contracts_sha256=SHA_C,
        expectation_set_sha256=expectation_set_sha256,
        scope=scope,
        expectation_count=expectation_count,
    )


def test_pass_report_round_trips_canonically_with_nonblocking_warning() -> None:
    parity = report(findings=(warning(),))
    encoded = dumps_report(parity)
    record = json.loads(encoded)

    validate_record(record, load_schema(DEFAULT_REPORT_SCHEMA))
    assert parity.status is ParityStatus.PASS
    assert parity.warning_finding_count == 1
    assert parity.blocking_finding_count == 0
    assert loads_report(encoded) == parity
    assert dumps_report(loads_report(encoded)) == encoded
    assert len(parity.sha256) == 64
    with pytest.raises(FrozenInstanceError):
        parity.complete = False  # type: ignore[misc]


def test_blocking_finding_and_incomplete_or_empty_coverage_are_blocked() -> None:
    blocked = report(findings=(blocker(),))
    incomplete = report(complete=False, observation_count=0, matched_count=0)
    empty = report(
        complete=True,
        expectation_count=0,
        observation_count=0,
        matched_count=0,
    )

    assert blocked.status is ParityStatus.BLOCKED
    assert blocked.blocking_finding_count == 1
    assert incomplete.status is ParityStatus.BLOCKED
    assert empty.status is ParityStatus.BLOCKED


def test_direct_report_constructor_rejects_inconsistent_status_counts_and_order() -> None:
    passing = report(findings=(warning(),))
    first = blocker()
    second = warning()

    with pytest.raises(ReportValidationError, match="status_is_inconsistent"):
        replace(passing, status=ParityStatus.BLOCKED)
    with pytest.raises(ReportValidationError, match="finding_counts"):
        replace(passing, warning_finding_count=0)
    with pytest.raises(ReportValidationError, match="canonically_sorted"):
        replace(
            report(findings=(first, second)),
            findings=tuple(reversed(report(findings=(first, second)).findings)),
        )
    with pytest.raises(ReportValidationError, match="exceeds_inputs"):
        report(expectation_count=1, observation_count=1, matched_count=2)


def test_finding_id_is_stable_and_report_contains_no_raw_observed_value_field() -> None:
    finding = blocker()

    assert finding.finding_id == stable_finding_id(
        finding.code,
        finding.scope,
        finding.subject_id,
        finding.field,
    )
    assert set(json.loads(dumps_report(report(findings=(finding,))))["findings"][0]) == {
        "code",
        "field",
        "finding_id",
        "required_action",
        "scope",
        "severity",
        "subject_id",
    }
    with pytest.raises(ReportValidationError, match="not_canonical"):
        replace(finding, finding_id="finding-" + "0" * 24)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"code": "contains space"},
        {"subject_id": "private@example.com"},
        {"field": "https://private.example/path?token=secret"},
        {"required_action": "Review manually"},
    ],
)
def test_findings_reject_free_form_or_private_payloads(kwargs: dict[str, str]) -> None:
    values = {
        "code": "canonical_mismatch",
        "severity": FindingSeverity.BLOCKING,
        "scope": "docs",
        "subject_id": EXPECTATION_ID,
        "field": "metadata.canonical_url",
        "required_action": "restore_approved_canonical",
        **kwargs,
    }

    with pytest.raises(ReportValidationError):
        Finding.create(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: replace(value, target_id="target with spaces"),
        lambda value: replace(value, target_origin="http://fixture.invalid"),
        lambda value: replace(value, target_origin="https://user:password@fixture.invalid"),
        lambda value: replace(value, target_origin="https://fixture.invalid/path"),
        lambda value: replace(value, release_id="private@example.com"),
        lambda value: replace(value, release_id="ghp_" + "a" * 30),
        lambda value: replace(value, parser_version="password:hunter2"),
        lambda value: replace(value, release_id="\ud800"),
    ],
)
def test_target_binding_factories_reject_unsafe_identity(mutation) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ReportValidationError):
        mutation(target())


def test_guard_invokes_callback_once_only_after_exact_pass() -> None:
    calls: list[str] = []

    def activate() -> str:
        calls.append("activated")
        return "done"

    result = require_compatible(
        report(findings=(warning(),)),
        requirements(),
        activate,
    )

    assert result == "done"
    assert calls == ["activated"]
    assert require_compatible(report(), requirements()) is None


@pytest.mark.parametrize(
    ("parity", "required", "failure"),
    [
        (
            report(binding=target(release_id="fixture-v2")),
            requirements(),
            GuardFailure.WRONG_TARGET,
        ),
        (report(scope=("main",)), requirements(), GuardFailure.WRONG_SCOPE),
        (
            report(manifest_sha256="e" * 64),
            requirements(),
            GuardFailure.STALE_ARTIFACT_DIGEST,
        ),
        (
            report(expectation_set_sha256="e" * 64),
            requirements(),
            GuardFailure.STALE_EXPECTATION_DIGEST,
        ),
        (report(), requirements(expectation_count=2), GuardFailure.WRONG_EXPECTATION_COUNT),
        (
            report(complete=False, observation_count=0, matched_count=0),
            requirements(),
            GuardFailure.INCOMPLETE_REPORT,
        ),
        (report(findings=(blocker(),)), requirements(), GuardFailure.BLOCKED_REPORT),
    ],
)
def test_guard_fails_closed_with_typed_reason(
    parity: ParityReport,
    required: GateRequirements,
    failure: GuardFailure,
) -> None:
    calls: list[str] = []

    with pytest.raises(CompatibilityGuardError) as error:
        require_compatible(parity, required, lambda: calls.append("unsafe"))

    assert error.value.code is failure
    assert calls == []


def test_guard_and_requirements_reject_untyped_or_empty_scope() -> None:
    with pytest.raises(TypeError, match="typed"):
        require_compatible(report(), object())  # type: ignore[arg-type]
    with pytest.raises(ReportValidationError, match="nonempty"):
        replace(requirements(), scope=())
    with pytest.raises(ReportValidationError, match="positive"):
        replace(requirements(), expectation_count=0)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update({"unknown": True}),
        lambda value: value.update({"schema_version": True}),
        lambda value: value["findings"][0].update({"severity": "notice"}),
    ],
)
def test_report_decoder_rejects_unknown_boolean_and_unsupported_values(mutate) -> None:  # type: ignore[no-untyped-def]
    record = json.loads(dumps_report(report(findings=(warning(),))))
    mutate(record)
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"

    with pytest.raises(ReportDecodeError, match="invalid"):
        loads_report(encoded)


def test_report_decoder_rejects_duplicate_keys_and_noncanonical_encoding() -> None:
    canonical = dumps_report(report())
    duplicate = canonical.replace(
        '"schema_version":1',
        '"schema_version":1,"schema_version":1',
        1,
    )

    with pytest.raises(ReportDecodeError, match="duplicate_key"):
        loads_report(duplicate)
    with pytest.raises(ReportDecodeError, match="not_canonical"):
        loads_report(json.dumps(json.loads(canonical), indent=2) + "\n")


def test_schema_file_is_committed_at_the_declared_location() -> None:
    assert DEFAULT_REPORT_SCHEMA == (
        Path(__file__).resolve().parents[2]
        / "_docs"
        / "compatibility"
        / "seo-parity-report.schema.json"
    )
    assert load_schema(DEFAULT_REPORT_SCHEMA)["additionalProperties"] is False
