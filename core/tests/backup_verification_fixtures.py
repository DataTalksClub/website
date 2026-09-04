"""Explicit deterministic test-only providers for the schema-1 contract."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from core.backup_verification import (
    DATABASE_SNAPSHOT_PROVIDER_KEY,
    BackupSnapshotProvider,
    BackupSnapshotResult,
    BackupVerificationRequirements,
    DatabaseSchemaIdentity,
    EnvironmentClass,
    ErrorCode,
    MigrationNode,
    RuntimeReleaseIdentity,
    SnapshotState,
    build_database_schema_identity,
    build_snapshot_identity_digest,
    safe_manifest_digest,
)

NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)


def digest(label: str) -> str:
    return hashlib.sha256(f"synthetic-fixture:{label}".encode()).hexdigest()


def runtime(*, alternate: bool = False) -> RuntimeReleaseIdentity:
    sha = ("1" if alternate else "0") + "123456789abcdef0123456789abcdef01234567"[:39]
    return RuntimeReleaseIdentity.create(
        version=f"20260830-115000-{sha[:7]}",
        source_sha=sha,
        image_digest=f"sha256:{digest('alternate-image' if alternate else 'image')}",
    )


def schema(*, pending: bool = False) -> DatabaseSchemaIdentity:
    nodes = (
        MigrationNode("core/0001_initial"),
        MigrationNode("core/0002_safe", ("core/0001_initial",)),
    )
    applied = ("core/0001_initial",) if pending else tuple(node.identity for node in nodes)
    return build_database_schema_identity(nodes, applied)


def requirements(
    *,
    environment: EnvironmentClass = EnvironmentClass.SYNTHETIC_TEST,
    current_schema: DatabaseSchemaIdentity | None = None,
) -> BackupVerificationRequirements:
    return BackupVerificationRequirements.create(
        environment_class=environment,
        runtime_identity=runtime(),
        database_schema_identity=current_schema or schema(),
        provider_key=DATABASE_SNAPSHOT_PROVIDER_KEY,
        provider_contract_version=1,
        maximum_recovery_point_age_seconds=600,
        maximum_receipt_age_seconds=300,
    )


def result(
    expected: BackupVerificationRequirements,
    *,
    state: SnapshotState = SnapshotState.COMPLETE,
    error_code: ErrorCode = ErrorCode.OK,
    recovery_point_at: datetime | None = None,
    provider_observed_at: datetime | None = None,
    counts: tuple[int, int, int, int] = (2, 2, 2, 2),
    result_runtime: RuntimeReleaseIdentity | None = None,
    result_schema: DatabaseSchemaIdentity | None = None,
) -> BackupSnapshotResult:
    recovery = recovery_point_at or NOW - timedelta(minutes=4)
    observed = provider_observed_at or NOW - timedelta(minutes=1)
    return BackupSnapshotResult.create(
        schema_version=1,
        provider_key=expected.provider_key,
        provider_contract_version=expected.provider_contract_version,
        environment_class=expected.environment_class,
        runtime_identity=result_runtime or expected.runtime_identity,
        database_schema_identity=result_schema or expected.database_schema_identity,
        snapshot_identity_digest=build_snapshot_identity_digest(
            {"generation": 7, "kind": "synthetic"}
        ),
        snapshot_manifest_digest=safe_manifest_digest({"resources": 2, "kind": "synthetic"}),
        recovery_point_at=recovery.strftime("%Y-%m-%dT%H:%M:%SZ"),
        provider_observed_at=observed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expected_resource_count=counts[0],
        present_resource_count=counts[1],
        verified_resource_count=counts[2],
        encrypted_resource_count=counts[3],
        state=state,
        error_code=error_code,
    )


class SyntheticProvider(BackupSnapshotProvider):
    def __init__(self, supplied_result: BackupSnapshotResult) -> None:
        self.supplied_result = supplied_result

    def __call__(self, expected: BackupVerificationRequirements) -> BackupSnapshotResult:
        assert expected.requirements_digest
        return self.supplied_result


class RaisingSyntheticProvider(BackupSnapshotProvider):
    def __call__(self, expected: BackupVerificationRequirements) -> BackupSnapshotResult:
        del expected
        raise RuntimeError("provider detail that must never escape")


def corrupt_result(value: BackupSnapshotResult) -> BackupSnapshotResult:
    corrupted = replace(value)
    object.__setattr__(corrupted, "result_digest", digest("corrupt"))
    return corrupted
