from __future__ import annotations

import dataclasses
import json
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest

from core import backup_verification
from core.backup_verification import (
    ALLOWED_PROVIDER_KEYS,
    MAX_DEVELOPMENT_RECOVERY_POINT_AGE_SECONDS,
    MAX_PRODUCTION_RECOVERY_POINT_AGE_SECONDS,
    BackupVerificationError,
    BackupVerificationOutcome,
    BackupVerificationReceipt,
    BackupVerificationRequirements,
    EnvironmentClass,
    ErrorCode,
    MigrationNode,
    RuntimeReleaseIdentity,
    SnapshotState,
    VerificationState,
    build_database_schema_identity,
    build_snapshot_identity_digest,
    contract_self_check,
    decode_backup_snapshot_result,
    decode_backup_verification_outcome,
    decode_backup_verification_receipt,
    decode_backup_verification_requirements,
    encode_backup_snapshot_result,
    encode_backup_verification_outcome,
    encode_backup_verification_receipt,
    encode_backup_verification_requirements,
    evaluate_backup_receipt,
    safe_manifest_digest,
    verify_backup,
    verify_backup_result,
)
from core.tests.backup_verification_fixtures import (
    NOW,
    RaisingSyntheticProvider,
    SyntheticProvider,
    corrupt_result,
    digest,
    requirements,
    result,
    runtime,
    schema,
)


def assert_blocked(outcome: BackupVerificationOutcome, code: ErrorCode) -> None:
    assert outcome == BackupVerificationOutcome(VerificationState.BLOCKED, code, None)
    assert "provider detail" not in repr(outcome)


def verified_receipt() -> tuple[BackupVerificationRequirements, BackupVerificationReceipt]:
    expected = requirements()
    outcome = verify_backup(SyntheticProvider(result(expected)), expected, verified_at=NOW)
    assert outcome.state is VerificationState.VERIFIED
    assert outcome.receipt is not None
    return expected, outcome.receipt


def test_frozen_schema_one_dtos_and_distinct_domain_digests() -> None:
    expected, receipt = verified_receipt()
    snapshot = result(expected)
    values = (
        expected.runtime_identity.runtime_identity_digest,
        expected.database_schema_identity.migration_graph_digest,
        expected.database_schema_identity.applied_migration_digest,
        expected.database_schema_identity.database_schema_identity_digest,
        expected.requirements_digest,
        snapshot.snapshot_identity_digest,
        snapshot.result_digest,
        receipt.receipt_digest,
    )
    assert len(set(values)) == len(values)
    assert all(len(value) == 64 for value in values)
    assert contract_self_check() == (
        "schema:1",
        "verifier:1",
        "providers:website_database_snapshot",
        "max_resources:10000",
        "max_migrations:10000",
    )
    for value in (
        expected,
        snapshot,
        receipt,
        expected.runtime_identity,
        expected.database_schema_identity,
    ):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(value, next(iter(value.__dataclass_fields__)), 2)


@pytest.mark.parametrize(
    ("encode", "decode", "value_factory"),
    [
        (
            encode_backup_verification_requirements,
            decode_backup_verification_requirements,
            requirements,
        ),
        (
            encode_backup_snapshot_result,
            decode_backup_snapshot_result,
            lambda: result(requirements()),
        ),
        (
            encode_backup_verification_receipt,
            decode_backup_verification_receipt,
            lambda: verified_receipt()[1],
        ),
    ],
)
def test_strict_canonical_json_round_trips_byte_for_byte(encode, decode, value_factory) -> None:
    value = value_factory()
    encoded = encode(value)
    assert encoded == encode(decode(encoded))
    assert encoded == json.dumps(
        json.loads(encoded), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    assert not encoded.endswith(b"\n")


def test_outcome_canonical_codec_uses_no_nullable_receipt() -> None:
    expected, receipt = verified_receipt()
    del expected
    verified = BackupVerificationOutcome(VerificationState.VERIFIED, ErrorCode.OK, receipt)
    blocked = BackupVerificationOutcome(VerificationState.BLOCKED, ErrorCode.MISSING_EVIDENCE, None)
    for outcome in (verified, blocked):
        encoded = encode_backup_verification_outcome(outcome)
        assert b"null" not in encoded
        assert decode_backup_verification_outcome(encoded) == outcome
        assert (
            encode_backup_verification_outcome(decode_backup_verification_outcome(encoded))
            == encoded
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: b" " + raw,
        lambda raw: raw + b"\n",
        lambda raw: b"\xef\xbb\xbf" + raw,
        lambda raw: raw.replace(b'"schema_version":1', b'"schema_version":1.0', 1),
        lambda raw: raw.replace(b'"schema_version":1', b'"schema_version":true', 1),
        lambda raw: raw.replace(b'"schema_version":1', b'"schema_version":null', 1),
        lambda raw: raw.replace(b'"schema_version":1', b'"schema_version":NaN', 1),
        lambda raw: raw.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1', 1),
        lambda raw: raw.replace(b'"schema_version":1', b'"schema_version":1,"unknown":1', 1),
        lambda raw: raw.replace(
            b'"environment_class":"synthetic_test"',
            b'"environment_class":"synthetic\\u005ftest"',
            1,
        ),
    ],
)
def test_strict_decoder_rejects_noncanonical_or_unsafe_json(mutate) -> None:
    raw = encode_backup_verification_requirements(requirements())
    with pytest.raises(BackupVerificationError):
        decode_backup_verification_requirements(mutate(raw))


def test_strict_decoder_maps_oversized_json_integer_to_typed_error() -> None:
    raw = encode_backup_verification_requirements(requirements())
    oversized_integer = raw.replace(b'"schema_version":1', b'"schema_version":' + b"9" * 5000, 1)

    with pytest.raises(BackupVerificationError) as captured:
        decode_backup_verification_requirements(oversized_integer)

    assert captured.value.code is ErrorCode.NONCANONICAL_EVIDENCE


def test_runtime_reuses_canonical_schema_two_version_validator() -> None:
    canonical = runtime()
    assert canonical.version.endswith(canonical.source_sha[:7])
    for values in (
        {
            "version": "local-development-build-version-not-configured",
            "source_sha": canonical.source_sha,
        },
        {"version": "20260230-115000-0123456", "source_sha": canonical.source_sha},
        {"version": canonical.version, "source_sha": canonical.source_sha.upper()},
        {"version": canonical.version, "source_sha": canonical.source_sha[:-1]},
    ):
        with pytest.raises(BackupVerificationError):
            RuntimeReleaseIdentity.create(
                image_digest=canonical.image_digest,
                **values,
            )


def test_migration_identity_is_order_independent_pending_aware_and_fail_closed() -> None:
    nodes = (
        MigrationNode("core/0002_second", ("core/0001_initial",)),
        MigrationNode("core/0001_initial"),
    )
    first = build_database_schema_identity(nodes, ("core/0002_second", "core/0001_initial"))
    second = build_database_schema_identity(
        reversed(nodes), ("core/0001_initial", "core/0002_second")
    )
    assert first == second
    assert first.pending_migration_count == 0
    pending = build_database_schema_identity(nodes, ("core/0001_initial",))
    assert pending.pending_migration_count == 1
    bad_inputs = (
        (nodes + (nodes[0],), ("core/0001_initial",)),
        (nodes, ("core/0001_initial", "core/0001_initial")),
        (nodes, ("other/0001_unknown",)),
        ((MigrationNode("core/0003_bad", ("other/0001_unknown",)),), ()),
    )
    for manifest, applied in bad_inputs:
        with pytest.raises(BackupVerificationError):
            build_database_schema_identity(manifest, applied)


@pytest.mark.parametrize("malformed_node", [object(), object.__new__(MigrationNode)])
def test_malformed_migration_nodes_return_typed_errors(malformed_node: object) -> None:
    with pytest.raises(BackupVerificationError) as captured:
        build_database_schema_identity([malformed_node], [])  # type: ignore[list-item]

    assert captured.value.code is ErrorCode.UNSAFE_EVIDENCE


def test_environment_recovery_bounds_are_exact_and_receipt_bound_is_required() -> None:
    base = requirements()
    for environment, maximum in (
        (EnvironmentClass.PRODUCTION, MAX_PRODUCTION_RECOVERY_POINT_AGE_SECONDS),
        (EnvironmentClass.DEVELOPMENT, MAX_DEVELOPMENT_RECOVERY_POINT_AGE_SECONDS),
    ):
        accepted = BackupVerificationRequirements.create(
            environment_class=environment,
            runtime_identity=base.runtime_identity,
            database_schema_identity=base.database_schema_identity,
            provider_key=base.provider_key,
            provider_contract_version=1,
            maximum_recovery_point_age_seconds=maximum,
            maximum_receipt_age_seconds=1,
        )
        assert accepted.maximum_recovery_point_age_seconds == maximum
        with pytest.raises(BackupVerificationError):
            BackupVerificationRequirements.create(
                environment_class=environment,
                runtime_identity=base.runtime_identity,
                database_schema_identity=base.database_schema_identity,
                provider_key=base.provider_key,
                provider_contract_version=1,
                maximum_recovery_point_age_seconds=maximum + 1,
                maximum_receipt_age_seconds=1,
            )
    with pytest.raises(BackupVerificationError):
        BackupVerificationRequirements.create(
            environment_class=EnvironmentClass.PRODUCTION,
            runtime_identity=base.runtime_identity,
            database_schema_identity=base.database_schema_identity,
            provider_key=base.provider_key,
            provider_contract_version=1,
            maximum_recovery_point_age_seconds=1,
            maximum_receipt_age_seconds=0,
        )


def test_complete_verification_and_provider_exception_containment() -> None:
    expected = requirements()
    outcome = verify_backup(SyntheticProvider(result(expected)), expected, verified_at=NOW)
    assert outcome.state is VerificationState.VERIFIED
    assert outcome.error_code is ErrorCode.OK
    assert outcome.receipt is not None
    assert outcome.receipt.result_digest == result(expected).result_digest
    assert_blocked(
        verify_backup(RaisingSyntheticProvider(), expected, verified_at=NOW),
        ErrorCode.PROVIDER_UNAVAILABLE,
    )


@pytest.mark.parametrize(
    ("supplied", "code"),
    [
        (lambda expected: None, ErrorCode.MISSING_EVIDENCE),
        (
            lambda expected: result(
                expected,
                state=SnapshotState.UNAVAILABLE,
                error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                counts=(0, 0, 0, 0),
            ),
            ErrorCode.PROVIDER_UNAVAILABLE,
        ),
        (
            lambda expected: result(
                expected,
                state=SnapshotState.UNAVAILABLE,
                error_code=ErrorCode.PROVIDER_AMBIGUOUS,
                counts=(0, 0, 0, 0),
            ),
            ErrorCode.PROVIDER_AMBIGUOUS,
        ),
        (
            lambda expected: result(
                expected,
                state=SnapshotState.PARTIAL,
                error_code=ErrorCode.PARTIAL_RESULT,
                counts=(2, 1, 1, 1),
            ),
            ErrorCode.PARTIAL_RESULT,
        ),
        (
            lambda expected: result(
                expected,
                state=SnapshotState.PARTIAL,
                error_code=ErrorCode.ENCRYPTION_UNVERIFIED,
                counts=(2, 2, 2, 1),
            ),
            ErrorCode.ENCRYPTION_UNVERIFIED,
        ),
        (
            lambda expected: result(
                expected,
                state=SnapshotState.PARTIAL,
                error_code=ErrorCode.COUNT_MISMATCH,
                counts=(2, 1, 1, 1),
            ),
            ErrorCode.COUNT_MISMATCH,
        ),
        (lambda expected: corrupt_result(result(expected)), ErrorCode.DIGEST_MISMATCH),
        (
            lambda expected: result(expected, result_runtime=runtime(alternate=True)),
            ErrorCode.RUNTIME_IDENTITY_MISMATCH,
        ),
        (
            lambda expected: result(expected, result_schema=schema(pending=True)),
            ErrorCode.SCHEMA_IDENTITY_MISMATCH,
        ),
        (
            lambda expected: result(expected, recovery_point_at=NOW + timedelta(seconds=1)),
            ErrorCode.RECOVERY_POINT_IN_FUTURE,
        ),
        (
            lambda expected: result(expected, recovery_point_at=NOW - timedelta(seconds=601)),
            ErrorCode.RECOVERY_POINT_STALE,
        ),
        (
            lambda expected: result(expected, provider_observed_at=NOW + timedelta(seconds=1)),
            ErrorCode.VERIFICATION_TIME_INVALID,
        ),
    ],
)
def test_all_result_failure_families_are_safe(supplied, code) -> None:
    expected = requirements()
    assert_blocked(verify_backup_result(supplied(expected), expected, verified_at=NOW), code)


def test_receipt_evaluator_exact_recovery_and_receipt_freshness_boundaries() -> None:
    expected, receipt = verified_receipt()
    for evaluated in (
        NOW,
        NOW + timedelta(seconds=299),
        NOW + timedelta(seconds=300),
    ):
        outcome = evaluate_backup_receipt(
            receipt,
            expected,
            expected.runtime_identity,
            expected.database_schema_identity,
            evaluated_at=evaluated,
        )
        assert outcome.state is VerificationState.VERIFIED
    assert_blocked(
        evaluate_backup_receipt(
            receipt,
            expected,
            expected.runtime_identity,
            expected.database_schema_identity,
            evaluated_at=NOW + timedelta(seconds=301),
        ),
        ErrorCode.RECEIPT_STALE,
    )
    recovery_edge = result(expected, recovery_point_at=NOW - timedelta(seconds=600))
    edge_receipt = verify_backup_result(recovery_edge, expected, verified_at=NOW).receipt
    assert edge_receipt is not None
    assert (
        evaluate_backup_receipt(
            edge_receipt,
            expected,
            expected.runtime_identity,
            expected.database_schema_identity,
            evaluated_at=NOW,
        ).state
        is VerificationState.VERIFIED
    )
    assert_blocked(
        evaluate_backup_receipt(
            edge_receipt,
            expected,
            expected.runtime_identity,
            expected.database_schema_identity,
            evaluated_at=NOW + timedelta(seconds=1),
        ),
        ErrorCode.RECOVERY_POINT_STALE,
    )


def test_receipt_evaluator_rejects_requirements_runtime_schema_and_corruption_drift() -> None:
    expected, receipt = verified_receipt()
    changed_requirements = BackupVerificationRequirements.create(
        environment_class=expected.environment_class,
        runtime_identity=expected.runtime_identity,
        database_schema_identity=expected.database_schema_identity,
        provider_key=expected.provider_key,
        provider_contract_version=expected.provider_contract_version,
        maximum_recovery_point_age_seconds=599,
        maximum_receipt_age_seconds=expected.maximum_receipt_age_seconds,
    )
    assert_blocked(
        evaluate_backup_receipt(
            receipt,
            changed_requirements,
            expected.runtime_identity,
            expected.database_schema_identity,
            evaluated_at=NOW,
        ),
        ErrorCode.REQUIREMENTS_MISMATCH,
    )
    assert_blocked(
        evaluate_backup_receipt(
            receipt,
            expected,
            runtime(alternate=True),
            expected.database_schema_identity,
            evaluated_at=NOW,
        ),
        ErrorCode.RUNTIME_IDENTITY_MISMATCH,
    )
    assert_blocked(
        evaluate_backup_receipt(
            receipt, expected, expected.runtime_identity, schema(pending=True), evaluated_at=NOW
        ),
        ErrorCode.SCHEMA_IDENTITY_MISMATCH,
    )
    corrupted = dataclasses.replace(receipt)
    object.__setattr__(corrupted, "receipt_digest", digest("bad-receipt"))
    assert_blocked(
        evaluate_backup_receipt(
            corrupted,
            expected,
            expected.runtime_identity,
            expected.database_schema_identity,
            evaluated_at=NOW,
        ),
        ErrorCode.DIGEST_MISMATCH,
    )


@pytest.mark.parametrize(
    "field_value",
    [
        {
            "expected_resource_count": 3,
            "present_resource_count": 3,
            "verified_resource_count": 3,
            "encrypted_resource_count": 3,
        },
        {"recovery_point_at": "2026-08-30T11:58:00Z"},
        {"snapshot_manifest_digest": digest("tampered-manifest")},
    ],
)
def test_receipt_evaluator_validates_nested_result_digest(field_value: dict[str, object]) -> None:
    expected, receipt = verified_receipt()
    tampered = dataclasses.replace(receipt)
    for field, value in field_value.items():
        object.__setattr__(tampered, field, value)
    # Keep the outer envelope internally consistent.  The original nested
    # result digest must still detect the copied-field substitution.
    object.__setattr__(
        tampered,
        "receipt_digest",
        backup_verification._domain_digest("receipt", tampered.payload(include_digest=False)),
    )
    assert_blocked(
        evaluate_backup_receipt(
            tampered,
            expected,
            expected.runtime_identity,
            expected.database_schema_identity,
            evaluated_at=NOW,
        ),
        ErrorCode.DIGEST_MISMATCH,
    )


@pytest.mark.parametrize(
    "canary",
    [
        "person@example.invalid",
        "account_id=12345",
        "domain-id-12345",
        "192.0.2.42",
        "https://example.invalid/private",
        "arn:aws:rds:eu-west-1:123456789012:snapshot:private",
        "database.internal",
        "hostname.internal",
        "SELECT * FROM private_table",
        "token credential secret",
        "provider payload",
        "private content message",
        "subject_hash=abcd",
        digest("low-entropy-subject"),
    ],
)
def test_sensitive_canaries_rejected_before_digest_or_serialization(canary: str) -> None:
    with pytest.raises(BackupVerificationError) as captured:
        build_snapshot_identity_digest({"kind": canary})
    assert captured.value.code is ErrorCode.UNSAFE_EVIDENCE
    assert canary not in str(captured.value)
    with pytest.raises(BackupVerificationError):
        safe_manifest_digest({"kind": canary})


def test_contract_has_one_way_dependency_and_no_activation_or_provider_path() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "core" / "backup_verification.py").read_text()
    forbidden = (
        "import privacy",
        "from privacy",
        "import email_app",
        "from email_app",
        "import content",
        "from content",
        "import jobs",
        "from jobs",
        "deploy.release import",
        "boto3",
        "botocore",
        "os.environ",
        "django.conf",
        "management.commands",
        "urlpatterns",
        "requests.",
        "socket.",
        "subprocess.",
    )
    assert not any(token in source for token in forbidden)
    assert ALLOWED_PROVIDER_KEYS == frozenset({"website_database_snapshot"})
    assert "BackupSnapshotProvider" in source
    assert "evaluate_backup_receipt" in source
    assert "RestoreSnapshotProviderDefinition" not in source


def test_synthetic_provider_does_not_consult_environment_network_or_filesystem() -> None:
    expected = requirements()
    provider = SyntheticProvider(result(expected))
    with (
        mock.patch("os.getenv", side_effect=AssertionError("environment access")),
        mock.patch("socket.socket", side_effect=AssertionError("network access")),
        mock.patch("subprocess.run", side_effect=AssertionError("subprocess access")),
        mock.patch.object(Path, "read_bytes", side_effect=AssertionError("filesystem access")),
    ):
        outcome = verify_backup(provider, expected, verified_at=NOW)
    assert outcome.state is VerificationState.VERIFIED
