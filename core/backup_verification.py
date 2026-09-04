"""Provider-neutral, non-authorizing backup verification contracts.

This module deliberately contains no provider adapter, settings lookup, database
query, command, route, or restore operation.  It validates only bounded safe
facts supplied by an adapter and exposes the one-way receipt seam consumed by
future recovery coordination.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, Self, cast

from deploy.release_identity import IdentityError, validate_schema2_version

SCHEMA_VERSION = 1
VERIFIER_CONTRACT_VERSION = 1
MAX_RESOURCE_COUNT = 10_000
MAX_MIGRATION_COUNT = 10_000
MAX_INTEGER_SECONDS = 31_536_000
MAX_PRODUCTION_RECOVERY_POINT_AGE_SECONDS = 900
MAX_DEVELOPMENT_RECOVERY_POINT_AGE_SECONDS = 86_400
MAX_SYNTHETIC_RECOVERY_POINT_AGE_SECONDS = 86_400
MAX_CANONICAL_JSON_BYTES = 65_536
MAX_CANONICAL_JSON_ITEMS = 512
MAX_CANONICAL_JSON_DEPTH = 6

DATABASE_SNAPSHOT_PROVIDER_KEY = "website_database_snapshot"
ALLOWED_PROVIDER_KEYS = frozenset({DATABASE_SNAPSHOT_PROVIDER_KEY})

_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_MIGRATION_IDENTITY = re.compile(r"^[a-z][a-z0-9_]*/[0-9A-Za-z_]+$")
_UTC_SECOND = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_UNSAFE_TEXT = re.compile(
    r"(?i)(?:@|https?://|arn:|(?:postgres(?:ql)?|mysql)://|\b(?:select|insert|update|delete)\b|"
    r"\b(?:password|passwd|secret|token|credential|authorization|cookie|payload|message|content)\b|"
    r"\b(?:localhost|database|hostname|account[_ -]?id|domain[_ -]?id|subject[_ -]?hash)\b|"
    r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b)"
)


class EnvironmentClass(StrEnum):
    SYNTHETIC_TEST = "synthetic_test"
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class SnapshotState(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class VerificationState(StrEnum):
    VERIFIED = "verified"
    BLOCKED = "blocked"


class ErrorCode(StrEnum):
    OK = "ok"
    MISSING_EVIDENCE = "missing_evidence"
    UNSAFE_EVIDENCE = "unsafe_evidence"
    NONCANONICAL_EVIDENCE = "noncanonical_evidence"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    UNSUPPORTED_PROVIDER_CONTRACT = "unsupported_provider_contract"
    UNSUPPORTED_VERIFIER_CONTRACT = "unsupported_verifier_contract"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_AMBIGUOUS = "provider_ambiguous"
    PARTIAL_RESULT = "partial_result"
    ENCRYPTION_UNVERIFIED = "encryption_unverified"
    COUNT_MISMATCH = "count_mismatch"
    DIGEST_MISMATCH = "digest_mismatch"
    ENVIRONMENT_MISMATCH = "environment_mismatch"
    RUNTIME_IDENTITY_MISMATCH = "runtime_identity_mismatch"
    SCHEMA_IDENTITY_MISMATCH = "schema_identity_mismatch"
    RECOVERY_POINT_IN_FUTURE = "recovery_point_in_future"
    RECOVERY_POINT_STALE = "recovery_point_stale"
    VERIFICATION_TIME_INVALID = "verification_time_invalid"
    RECEIPT_STALE = "receipt_stale"
    REQUIREMENTS_MISMATCH = "requirements_mismatch"


class BackupVerificationError(ValueError):
    """A safe, allowlisted validation failure without input echoing."""

    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


_DOMAINS = {
    "requirements": b"dtc:backup-verification:requirements:v1",
    "runtime": b"dtc:backup-verification:runtime-identity:v1",
    "migration_graph": b"dtc:backup-verification:migration-graph:v1",
    "applied_migrations": b"dtc:backup-verification:applied-migrations:v1",
    "database_schema": b"dtc:backup-verification:database-schema-identity:v1",
    "snapshot_identity": b"dtc:backup-verification:snapshot-identity:v1",
    "snapshot_result": b"dtc:backup-verification:snapshot-result:v1",
    "receipt": b"dtc:backup-verification:receipt:v1",
}


def _canonical_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as error:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE) from error
    if len(encoded) > MAX_CANONICAL_JSON_BYTES:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    return encoded


def _domain_digest(kind: str, payload: object) -> str:
    return hashlib.sha256(_DOMAINS[kind] + b"\0" + _canonical_bytes(payload)).hexdigest()


def _plain_digest(payload: object) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _validate_int(value: object, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    return value


def _validate_digest(value: object, *, image: bool = False) -> str:
    pattern = _IMAGE_DIGEST if image else _HEX_DIGEST
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    raw = value.removeprefix("sha256:")
    if len(set(raw)) == 1 or raw == raw[:8] * 8:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    return value


def _validate_ascii_string(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    try:
        value.encode("ascii")
    except UnicodeEncodeError as error:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE) from error
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    if _UNSAFE_TEXT.search(value):
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    return value


def _validate_safe_projection_string(value: object) -> str:
    safe = _validate_ascii_string(value)
    if re.fullmatch(r"[0-9a-fA-F]{16,}", safe) or re.fullmatch(
        r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", safe
    ):
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    return safe


def _parse_utc_second(value: object) -> datetime:
    if not isinstance(value, str) or _UTC_SECOND.fullmatch(value) is None:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE) from error


def _utc_second(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise BackupVerificationError(ErrorCode.VERIFICATION_TIME_INVALID)
    if value.utcoffset() != datetime.min.replace(tzinfo=UTC).utcoffset() or value.microsecond:
        raise BackupVerificationError(ErrorCode.VERIFICATION_TIME_INVALID)
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _enum(enum_type: type[StrEnum], value: object) -> StrEnum:
    if not isinstance(value, str):
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    try:
        return enum_type(value)
    except ValueError as error:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE) from error


@dataclass(frozen=True, slots=True)
class RuntimeReleaseIdentity:
    identity_schema: int
    version: str
    source_sha: str
    image_digest: str
    runtime_identity_digest: str

    def __post_init__(self) -> None:
        if type(self.identity_schema) is not int or self.identity_schema != 2:
            raise BackupVerificationError(ErrorCode.UNSUPPORTED_SCHEMA)
        _validate_ascii_string(self.version)
        _validate_ascii_string(self.source_sha)
        _validate_digest(self.image_digest, image=True)
        try:
            validate_schema2_version(self.version, self.source_sha)
        except IdentityError as error:
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE) from error
        expected = _domain_digest("runtime", self.payload(include_digest=False))
        if _validate_digest(self.runtime_identity_digest) != expected:
            raise BackupVerificationError(ErrorCode.DIGEST_MISMATCH)

    @classmethod
    def create(cls, *, version: str, source_sha: str, image_digest: str) -> Self:
        payload = {
            "identity_schema": 2,
            "image_digest": image_digest,
            "source_sha": source_sha,
            "version": version,
        }
        return cls(2, version, source_sha, image_digest, _domain_digest("runtime", payload))

    def payload(self, *, include_digest: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "identity_schema": self.identity_schema,
            "image_digest": self.image_digest,
            "source_sha": self.source_sha,
            "version": self.version,
        }
        if include_digest:
            value["runtime_identity_digest"] = self.runtime_identity_digest
        return value


@dataclass(frozen=True, slots=True)
class MigrationNode:
    identity: str
    dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.identity, str)
            or _MIGRATION_IDENTITY.fullmatch(self.identity) is None
        ):
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        if not isinstance(self.dependencies, tuple):
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        for dependency in self.dependencies:
            if not isinstance(dependency, str) or _MIGRATION_IDENTITY.fullmatch(dependency) is None:
                raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)


@dataclass(frozen=True, slots=True)
class DatabaseSchemaIdentity:
    schema_version: int
    migration_graph_digest: str
    applied_migration_digest: str
    applied_migration_count: int
    pending_migration_count: int
    database_schema_identity_digest: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise BackupVerificationError(ErrorCode.UNSUPPORTED_SCHEMA)
        _validate_digest(self.migration_graph_digest)
        _validate_digest(self.applied_migration_digest)
        _validate_int(self.applied_migration_count, minimum=0, maximum=MAX_MIGRATION_COUNT)
        _validate_int(self.pending_migration_count, minimum=0, maximum=MAX_MIGRATION_COUNT)
        if self.applied_migration_count + self.pending_migration_count > MAX_MIGRATION_COUNT:
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        expected = _domain_digest("database_schema", self.payload(include_digest=False))
        if _validate_digest(self.database_schema_identity_digest) != expected:
            raise BackupVerificationError(ErrorCode.DIGEST_MISMATCH)

    def payload(self, *, include_digest: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "applied_migration_count": self.applied_migration_count,
            "applied_migration_digest": self.applied_migration_digest,
            "migration_graph_digest": self.migration_graph_digest,
            "pending_migration_count": self.pending_migration_count,
            "schema_version": self.schema_version,
        }
        if include_digest:
            value["database_schema_identity_digest"] = self.database_schema_identity_digest
        return value


def build_database_schema_identity(
    manifest: Iterable[MigrationNode], applied: Iterable[str]
) -> DatabaseSchemaIdentity:
    nodes = tuple(manifest)
    applied_nodes = tuple(applied)
    if len(nodes) > MAX_MIGRATION_COUNT or len(applied_nodes) > MAX_MIGRATION_COUNT:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    for node in nodes:
        node_error = _revalidate(node, MigrationNode)
        if node_error:
            raise BackupVerificationError(node_error)
    identities = [node.identity for node in nodes]
    if len(set(identities)) != len(identities):
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    known = set(identities)
    graph_payload: list[dict[str, object]] = []
    for node in nodes:
        if (
            len(set(node.dependencies)) != len(node.dependencies)
            or not set(node.dependencies) <= known
        ):
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        graph_payload.append(
            {"dependencies": sorted(node.dependencies), "migration": node.identity}
        )
    graph_payload.sort(key=lambda item: cast(str, item["migration"]))
    for identity in applied_nodes:
        if not isinstance(identity, str) or _MIGRATION_IDENTITY.fullmatch(identity) is None:
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    if len(set(applied_nodes)) != len(applied_nodes) or not set(applied_nodes) <= known:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    applied_payload = sorted(applied_nodes)
    payload = {
        "applied_migration_count": len(applied_payload),
        "applied_migration_digest": _domain_digest("applied_migrations", applied_payload),
        "migration_graph_digest": _domain_digest("migration_graph", graph_payload),
        "pending_migration_count": len(nodes) - len(applied_payload),
        "schema_version": SCHEMA_VERSION,
    }
    return DatabaseSchemaIdentity(
        **payload,  # type: ignore[arg-type]
        database_schema_identity_digest=_domain_digest("database_schema", payload),
    )


@dataclass(frozen=True, slots=True)
class BackupVerificationRequirements:
    schema_version: int
    environment_class: EnvironmentClass
    runtime_identity: RuntimeReleaseIdentity
    database_schema_identity: DatabaseSchemaIdentity
    provider_key: str
    provider_contract_version: int
    maximum_recovery_point_age_seconds: int
    maximum_receipt_age_seconds: int
    requirements_digest: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise BackupVerificationError(ErrorCode.UNSUPPORTED_SCHEMA)
        if type(self.environment_class) is not EnvironmentClass:
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        if type(self.runtime_identity) is not RuntimeReleaseIdentity:
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        if type(self.database_schema_identity) is not DatabaseSchemaIdentity:
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        if self.provider_key not in ALLOWED_PROVIDER_KEYS:
            raise BackupVerificationError(ErrorCode.UNSUPPORTED_PROVIDER_CONTRACT)
        _validate_int(self.provider_contract_version, minimum=1, maximum=255)
        recovery_maximum = {
            EnvironmentClass.PRODUCTION: MAX_PRODUCTION_RECOVERY_POINT_AGE_SECONDS,
            EnvironmentClass.DEVELOPMENT: MAX_DEVELOPMENT_RECOVERY_POINT_AGE_SECONDS,
            EnvironmentClass.SYNTHETIC_TEST: MAX_SYNTHETIC_RECOVERY_POINT_AGE_SECONDS,
        }[self.environment_class]
        _validate_int(
            self.maximum_recovery_point_age_seconds,
            minimum=1,
            maximum=recovery_maximum,
        )
        _validate_int(self.maximum_receipt_age_seconds, minimum=1, maximum=MAX_INTEGER_SECONDS)
        expected = _domain_digest("requirements", self.payload(include_digest=False))
        if _validate_digest(self.requirements_digest) != expected:
            raise BackupVerificationError(ErrorCode.DIGEST_MISMATCH)

    @classmethod
    def create(
        cls,
        *,
        environment_class: EnvironmentClass,
        runtime_identity: RuntimeReleaseIdentity,
        database_schema_identity: DatabaseSchemaIdentity,
        provider_key: str,
        provider_contract_version: int,
        maximum_recovery_point_age_seconds: int,
        maximum_receipt_age_seconds: int,
    ) -> Self:
        payload = {
            "database_schema_identity": database_schema_identity.payload(),
            "environment_class": environment_class.value,
            "maximum_receipt_age_seconds": maximum_receipt_age_seconds,
            "maximum_recovery_point_age_seconds": maximum_recovery_point_age_seconds,
            "provider_contract_version": provider_contract_version,
            "provider_key": provider_key,
            "runtime_identity": runtime_identity.payload(),
            "schema_version": SCHEMA_VERSION,
        }
        return cls(
            SCHEMA_VERSION,
            environment_class,
            runtime_identity,
            database_schema_identity,
            provider_key,
            provider_contract_version,
            maximum_recovery_point_age_seconds,
            maximum_receipt_age_seconds,
            _domain_digest("requirements", payload),
        )

    def payload(self, *, include_digest: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "database_schema_identity": self.database_schema_identity.payload(),
            "environment_class": self.environment_class.value,
            "maximum_receipt_age_seconds": self.maximum_receipt_age_seconds,
            "maximum_recovery_point_age_seconds": self.maximum_recovery_point_age_seconds,
            "provider_contract_version": self.provider_contract_version,
            "provider_key": self.provider_key,
            "runtime_identity": self.runtime_identity.payload(),
            "schema_version": self.schema_version,
        }
        if include_digest:
            value["requirements_digest"] = self.requirements_digest
        return value


def build_snapshot_identity_digest(safe_projection: Mapping[str, int | str]) -> str:
    if not isinstance(safe_projection, Mapping) or not safe_projection or len(safe_projection) > 16:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    normalized: dict[str, int | str] = {}
    for key, value in safe_projection.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        if _UNSAFE_TEXT.search(key):
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        if type(value) is int:
            normalized[key] = _validate_int(value, minimum=0, maximum=MAX_INTEGER_SECONDS)
        elif isinstance(value, str):
            normalized[key] = _validate_safe_projection_string(value)
        else:
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    return _domain_digest("snapshot_identity", normalized)


@dataclass(frozen=True, slots=True)
class BackupSnapshotResult:
    schema_version: int
    provider_key: str
    provider_contract_version: int
    environment_class: EnvironmentClass
    runtime_identity: RuntimeReleaseIdentity
    database_schema_identity: DatabaseSchemaIdentity
    snapshot_identity_digest: str
    snapshot_manifest_digest: str
    recovery_point_at: str
    provider_observed_at: str
    expected_resource_count: int
    present_resource_count: int
    verified_resource_count: int
    encrypted_resource_count: int
    state: SnapshotState
    error_code: ErrorCode
    result_digest: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise BackupVerificationError(ErrorCode.UNSUPPORTED_SCHEMA)
        if self.provider_key not in ALLOWED_PROVIDER_KEYS:
            raise BackupVerificationError(ErrorCode.UNSUPPORTED_PROVIDER_CONTRACT)
        _validate_int(self.provider_contract_version, minimum=1, maximum=255)
        if type(self.environment_class) is not EnvironmentClass:
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        if (
            type(self.runtime_identity) is not RuntimeReleaseIdentity
            or type(self.database_schema_identity) is not DatabaseSchemaIdentity
        ):
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        _validate_digest(self.snapshot_identity_digest)
        _validate_digest(self.snapshot_manifest_digest)
        _parse_utc_second(self.recovery_point_at)
        _parse_utc_second(self.provider_observed_at)
        counts = (
            self.expected_resource_count,
            self.present_resource_count,
            self.verified_resource_count,
            self.encrypted_resource_count,
        )
        for count in counts:
            _validate_int(count, minimum=0, maximum=MAX_RESOURCE_COUNT)
        if type(self.state) is not SnapshotState or type(self.error_code) is not ErrorCode:
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        if self.state is SnapshotState.COMPLETE:
            if (
                self.error_code is not ErrorCode.OK
                or self.expected_resource_count < 1
                or len(set(counts)) != 1
            ):
                raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        elif self.state is SnapshotState.UNAVAILABLE:
            if self.error_code not in {
                ErrorCode.PROVIDER_UNAVAILABLE,
                ErrorCode.PROVIDER_AMBIGUOUS,
            }:
                raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        elif self.error_code not in {
            ErrorCode.PARTIAL_RESULT,
            ErrorCode.ENCRYPTION_UNVERIFIED,
            ErrorCode.COUNT_MISMATCH,
        }:
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        expected = _domain_digest("snapshot_result", self.payload(include_digest=False))
        if _validate_digest(self.result_digest) != expected:
            raise BackupVerificationError(ErrorCode.DIGEST_MISMATCH)

    @classmethod
    def create(cls, **values: object) -> Self:
        payload = _snapshot_result_payload(values)
        return cls(
            **values,  # type: ignore[arg-type]
            result_digest=_domain_digest("snapshot_result", payload),
        )

    def payload(self, *, include_digest: bool = True) -> dict[str, object]:
        value = _snapshot_result_payload(
            {
                field: getattr(self, field)
                for field in _SNAPSHOT_RESULT_FIELDS
                if field != "result_digest"
            }
        )
        if include_digest:
            value["result_digest"] = self.result_digest
        return value


_SNAPSHOT_RESULT_FIELDS = (
    "schema_version",
    "provider_key",
    "provider_contract_version",
    "environment_class",
    "runtime_identity",
    "database_schema_identity",
    "snapshot_identity_digest",
    "snapshot_manifest_digest",
    "recovery_point_at",
    "provider_observed_at",
    "expected_resource_count",
    "present_resource_count",
    "verified_resource_count",
    "encrypted_resource_count",
    "state",
    "error_code",
    "result_digest",
)


def _snapshot_result_payload(values: Mapping[str, object]) -> dict[str, object]:
    runtime = cast(RuntimeReleaseIdentity, values["runtime_identity"])
    schema = cast(DatabaseSchemaIdentity, values["database_schema_identity"])
    environment = cast(EnvironmentClass, values["environment_class"])
    state = cast(SnapshotState, values["state"])
    code = cast(ErrorCode, values["error_code"])
    return {
        "database_schema_identity": schema.payload(),
        "encrypted_resource_count": values["encrypted_resource_count"],
        "environment_class": environment.value,
        "error_code": code.value,
        "expected_resource_count": values["expected_resource_count"],
        "present_resource_count": values["present_resource_count"],
        "provider_contract_version": values["provider_contract_version"],
        "provider_key": values["provider_key"],
        "provider_observed_at": values["provider_observed_at"],
        "recovery_point_at": values["recovery_point_at"],
        "runtime_identity": runtime.payload(),
        "schema_version": values["schema_version"],
        "snapshot_identity_digest": values["snapshot_identity_digest"],
        "snapshot_manifest_digest": values["snapshot_manifest_digest"],
        "state": state.value,
        "verified_resource_count": values["verified_resource_count"],
    }


@dataclass(frozen=True, slots=True)
class BackupVerificationReceipt:
    schema_version: int
    verifier_contract_version: int
    requirements_digest: str
    provider_key: str
    provider_contract_version: int
    environment_class: EnvironmentClass
    runtime_identity: RuntimeReleaseIdentity
    database_schema_identity: DatabaseSchemaIdentity
    snapshot_identity_digest: str
    snapshot_manifest_digest: str
    result_digest: str
    recovery_point_at: str
    provider_observed_at: str
    verified_at: str
    expected_resource_count: int
    present_resource_count: int
    verified_resource_count: int
    encrypted_resource_count: int
    state: VerificationState
    error_code: ErrorCode
    receipt_digest: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != SCHEMA_VERSION:
            raise BackupVerificationError(ErrorCode.UNSUPPORTED_SCHEMA)
        if (
            type(self.verifier_contract_version) is not int
            or self.verifier_contract_version != VERIFIER_CONTRACT_VERSION
        ):
            raise BackupVerificationError(ErrorCode.UNSUPPORTED_VERIFIER_CONTRACT)
        _validate_digest(self.requirements_digest)
        if self.provider_key not in ALLOWED_PROVIDER_KEYS:
            raise BackupVerificationError(ErrorCode.UNSUPPORTED_PROVIDER_CONTRACT)
        _validate_int(self.provider_contract_version, minimum=1, maximum=255)
        if type(self.environment_class) is not EnvironmentClass:
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        if (
            type(self.runtime_identity) is not RuntimeReleaseIdentity
            or type(self.database_schema_identity) is not DatabaseSchemaIdentity
        ):
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        for digest in (
            self.snapshot_identity_digest,
            self.snapshot_manifest_digest,
            self.result_digest,
        ):
            _validate_digest(digest)
        for instant in (self.recovery_point_at, self.provider_observed_at, self.verified_at):
            _parse_utc_second(instant)
        counts = (
            self.expected_resource_count,
            self.present_resource_count,
            self.verified_resource_count,
            self.encrypted_resource_count,
        )
        for count in counts:
            _validate_int(count, minimum=0, maximum=MAX_RESOURCE_COUNT)
        if (
            self.expected_resource_count < 1
            or len(set(counts)) != 1
            or self.state is not VerificationState.VERIFIED
            or self.error_code is not ErrorCode.OK
        ):
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        expected = _domain_digest("receipt", self.payload(include_digest=False))
        if _validate_digest(self.receipt_digest) != expected:
            raise BackupVerificationError(ErrorCode.DIGEST_MISMATCH)

    def payload(self, *, include_digest: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "database_schema_identity": self.database_schema_identity.payload(),
            "encrypted_resource_count": self.encrypted_resource_count,
            "environment_class": self.environment_class.value,
            "error_code": self.error_code.value,
            "expected_resource_count": self.expected_resource_count,
            "present_resource_count": self.present_resource_count,
            "provider_contract_version": self.provider_contract_version,
            "provider_key": self.provider_key,
            "provider_observed_at": self.provider_observed_at,
            "recovery_point_at": self.recovery_point_at,
            "requirements_digest": self.requirements_digest,
            "result_digest": self.result_digest,
            "runtime_identity": self.runtime_identity.payload(),
            "schema_version": self.schema_version,
            "snapshot_identity_digest": self.snapshot_identity_digest,
            "snapshot_manifest_digest": self.snapshot_manifest_digest,
            "state": self.state.value,
            "verified_at": self.verified_at,
            "verified_resource_count": self.verified_resource_count,
            "verifier_contract_version": self.verifier_contract_version,
        }
        if include_digest:
            value["receipt_digest"] = self.receipt_digest
        return value


@dataclass(frozen=True, slots=True)
class BackupVerificationOutcome:
    state: VerificationState
    error_code: ErrorCode
    receipt: BackupVerificationReceipt | None

    def __post_init__(self) -> None:
        if type(self.state) is not VerificationState or type(self.error_code) is not ErrorCode:
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        if self.state is VerificationState.VERIFIED:
            if (
                self.error_code is not ErrorCode.OK
                or type(self.receipt) is not BackupVerificationReceipt
            ):
                raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
        elif (
            self.state is not VerificationState.BLOCKED
            or self.error_code in {ErrorCode.OK}
            or self.receipt is not None
        ):
            raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)


class BackupSnapshotProvider(Protocol):
    def __call__(self, requirements: BackupVerificationRequirements) -> BackupSnapshotResult: ...


def _blocked(code: ErrorCode) -> BackupVerificationOutcome:
    return BackupVerificationOutcome(VerificationState.BLOCKED, code, None)


def _revalidate(value: object, expected_type: type[object]) -> ErrorCode | None:
    if type(value) is not expected_type:
        return ErrorCode.UNSAFE_EVIDENCE
    try:
        value.__post_init__()  # type: ignore[attr-defined]
    except BackupVerificationError as error:
        return error.code
    except Exception:
        return ErrorCode.UNSAFE_EVIDENCE
    return None


def verify_backup_result(
    result: BackupSnapshotResult | None,
    requirements: BackupVerificationRequirements,
    *,
    verified_at: datetime,
) -> BackupVerificationOutcome:
    requirement_error = _revalidate(requirements, BackupVerificationRequirements)
    if requirement_error:
        return _blocked(requirement_error)
    if result is None:
        return _blocked(ErrorCode.MISSING_EVIDENCE)
    result_error = _revalidate(result, BackupSnapshotResult)
    if result_error:
        return _blocked(result_error)
    try:
        verified_text = _utc_second(verified_at)
    except BackupVerificationError as error:
        return _blocked(error.code)
    if result.provider_contract_version != requirements.provider_contract_version:
        return _blocked(ErrorCode.UNSUPPORTED_PROVIDER_CONTRACT)
    if result.provider_key != requirements.provider_key:
        return _blocked(ErrorCode.REQUIREMENTS_MISMATCH)
    if result.environment_class is not requirements.environment_class:
        return _blocked(ErrorCode.ENVIRONMENT_MISMATCH)
    if result.runtime_identity != requirements.runtime_identity:
        return _blocked(ErrorCode.RUNTIME_IDENTITY_MISMATCH)
    if (
        result.database_schema_identity != requirements.database_schema_identity
        or result.database_schema_identity.pending_migration_count != 0
    ):
        return _blocked(ErrorCode.SCHEMA_IDENTITY_MISMATCH)
    if result.state is SnapshotState.UNAVAILABLE:
        return _blocked(result.error_code)
    if result.state is SnapshotState.PARTIAL:
        return _blocked(result.error_code)
    counts = (
        result.expected_resource_count,
        result.present_resource_count,
        result.verified_resource_count,
        result.encrypted_resource_count,
    )
    if len(set(counts[:3])) != 1:
        return _blocked(ErrorCode.COUNT_MISMATCH)
    if result.encrypted_resource_count != result.expected_resource_count:
        return _blocked(ErrorCode.ENCRYPTION_UNVERIFIED)
    recovery = _parse_utc_second(result.recovery_point_at)
    observed = _parse_utc_second(result.provider_observed_at)
    verified = _parse_utc_second(verified_text)
    if recovery > observed:
        return _blocked(ErrorCode.RECOVERY_POINT_IN_FUTURE)
    if observed > verified:
        return _blocked(ErrorCode.VERIFICATION_TIME_INVALID)
    if int((verified - recovery).total_seconds()) > requirements.maximum_recovery_point_age_seconds:
        return _blocked(ErrorCode.RECOVERY_POINT_STALE)
    receipt_values: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "verifier_contract_version": VERIFIER_CONTRACT_VERSION,
        "requirements_digest": requirements.requirements_digest,
        "provider_key": result.provider_key,
        "provider_contract_version": result.provider_contract_version,
        "environment_class": result.environment_class,
        "runtime_identity": result.runtime_identity,
        "database_schema_identity": result.database_schema_identity,
        "snapshot_identity_digest": result.snapshot_identity_digest,
        "snapshot_manifest_digest": result.snapshot_manifest_digest,
        "result_digest": result.result_digest,
        "recovery_point_at": result.recovery_point_at,
        "provider_observed_at": result.provider_observed_at,
        "verified_at": verified_text,
        "expected_resource_count": result.expected_resource_count,
        "present_resource_count": result.present_resource_count,
        "verified_resource_count": result.verified_resource_count,
        "encrypted_resource_count": result.encrypted_resource_count,
        "state": VerificationState.VERIFIED,
        "error_code": ErrorCode.OK,
    }
    payload = _receipt_payload(receipt_values)
    receipt = BackupVerificationReceipt(
        **receipt_values,  # type: ignore[arg-type]
        receipt_digest=_domain_digest("receipt", payload),
    )
    return BackupVerificationOutcome(VerificationState.VERIFIED, ErrorCode.OK, receipt)


def verify_backup(
    provider: BackupSnapshotProvider,
    requirements: BackupVerificationRequirements,
    *,
    verified_at: datetime,
) -> BackupVerificationOutcome:
    try:
        result = provider(requirements)
    except Exception:
        return _blocked(ErrorCode.PROVIDER_UNAVAILABLE)
    return verify_backup_result(result, requirements, verified_at=verified_at)


def _receipt_payload(values: Mapping[str, object]) -> dict[str, object]:
    runtime = cast(RuntimeReleaseIdentity, values["runtime_identity"])
    schema = cast(DatabaseSchemaIdentity, values["database_schema_identity"])
    environment = cast(EnvironmentClass, values["environment_class"])
    state = cast(VerificationState, values["state"])
    code = cast(ErrorCode, values["error_code"])
    return {
        "database_schema_identity": schema.payload(),
        "encrypted_resource_count": values["encrypted_resource_count"],
        "environment_class": environment.value,
        "error_code": code.value,
        "expected_resource_count": values["expected_resource_count"],
        "present_resource_count": values["present_resource_count"],
        "provider_contract_version": values["provider_contract_version"],
        "provider_key": values["provider_key"],
        "provider_observed_at": values["provider_observed_at"],
        "recovery_point_at": values["recovery_point_at"],
        "requirements_digest": values["requirements_digest"],
        "result_digest": values["result_digest"],
        "runtime_identity": runtime.payload(),
        "schema_version": values["schema_version"],
        "snapshot_identity_digest": values["snapshot_identity_digest"],
        "snapshot_manifest_digest": values["snapshot_manifest_digest"],
        "state": state.value,
        "verified_at": values["verified_at"],
        "verified_resource_count": values["verified_resource_count"],
        "verifier_contract_version": values["verifier_contract_version"],
    } | ({"present_resource_count": values["present_resource_count"]})


def evaluate_backup_receipt(
    receipt: BackupVerificationReceipt | None,
    requirements: BackupVerificationRequirements,
    current_runtime: RuntimeReleaseIdentity,
    current_schema: DatabaseSchemaIdentity,
    *,
    evaluated_at: datetime,
) -> BackupVerificationOutcome:
    for value, expected in (
        (requirements, BackupVerificationRequirements),
        (current_runtime, RuntimeReleaseIdentity),
        (current_schema, DatabaseSchemaIdentity),
    ):
        error = _revalidate(value, expected)
        if error:
            return _blocked(error)
    if receipt is None:
        return _blocked(ErrorCode.MISSING_EVIDENCE)
    receipt_error = _revalidate(receipt, BackupVerificationReceipt)
    if receipt_error:
        return _blocked(receipt_error)
    # The receipt copies the result fields so downstream consumers do not need
    # to retain the provider result.  Validate that copied projection against
    # the original result digest before trusting any of those fields.  The
    # outer receipt digest authenticates the receipt envelope, but cannot by
    # itself prove that the copied result fields are the fields that the
    # provider originally signed.
    nested_result_payload = _snapshot_result_payload(
        {
            "schema_version": SCHEMA_VERSION,
            "provider_key": receipt.provider_key,
            "provider_contract_version": receipt.provider_contract_version,
            "environment_class": receipt.environment_class,
            "runtime_identity": receipt.runtime_identity,
            "database_schema_identity": receipt.database_schema_identity,
            "snapshot_identity_digest": receipt.snapshot_identity_digest,
            "snapshot_manifest_digest": receipt.snapshot_manifest_digest,
            "recovery_point_at": receipt.recovery_point_at,
            "provider_observed_at": receipt.provider_observed_at,
            "expected_resource_count": receipt.expected_resource_count,
            "present_resource_count": receipt.present_resource_count,
            "verified_resource_count": receipt.verified_resource_count,
            "encrypted_resource_count": receipt.encrypted_resource_count,
            "state": SnapshotState.COMPLETE,
            "error_code": ErrorCode.OK,
        }
    )
    if _domain_digest("snapshot_result", nested_result_payload) != receipt.result_digest:
        return _blocked(ErrorCode.DIGEST_MISMATCH)
    try:
        evaluated_text = _utc_second(evaluated_at)
    except BackupVerificationError as error:
        return _blocked(error.code)
    if receipt.verifier_contract_version != VERIFIER_CONTRACT_VERSION:
        return _blocked(ErrorCode.UNSUPPORTED_VERIFIER_CONTRACT)
    if receipt.requirements_digest != requirements.requirements_digest:
        return _blocked(ErrorCode.REQUIREMENTS_MISMATCH)
    if (
        receipt.provider_key != requirements.provider_key
        or receipt.provider_contract_version != requirements.provider_contract_version
    ):
        return _blocked(ErrorCode.UNSUPPORTED_PROVIDER_CONTRACT)
    if receipt.environment_class is not requirements.environment_class:
        return _blocked(ErrorCode.ENVIRONMENT_MISMATCH)
    if (
        receipt.runtime_identity != requirements.runtime_identity
        or receipt.runtime_identity != current_runtime
    ):
        return _blocked(ErrorCode.RUNTIME_IDENTITY_MISMATCH)
    if (
        receipt.database_schema_identity != requirements.database_schema_identity
        or receipt.database_schema_identity != current_schema
        or current_schema.pending_migration_count != 0
    ):
        return _blocked(ErrorCode.SCHEMA_IDENTITY_MISMATCH)
    evaluated = _parse_utc_second(evaluated_text)
    recovery = _parse_utc_second(receipt.recovery_point_at)
    observed = _parse_utc_second(receipt.provider_observed_at)
    verified = _parse_utc_second(receipt.verified_at)
    if recovery > observed:
        return _blocked(ErrorCode.RECOVERY_POINT_IN_FUTURE)
    if observed > verified or verified > evaluated:
        return _blocked(ErrorCode.VERIFICATION_TIME_INVALID)
    if (
        int((evaluated - recovery).total_seconds())
        > requirements.maximum_recovery_point_age_seconds
    ):
        return _blocked(ErrorCode.RECOVERY_POINT_STALE)
    if int((evaluated - verified).total_seconds()) > requirements.maximum_receipt_age_seconds:
        return _blocked(ErrorCode.RECEIPT_STALE)
    return BackupVerificationOutcome(VerificationState.VERIFIED, ErrorCode.OK, receipt)


def encode_backup_verification_requirements(value: BackupVerificationRequirements) -> bytes:
    _revalidate_or_raise(value, BackupVerificationRequirements)
    return _canonical_bytes(value.payload())


def encode_backup_snapshot_result(value: BackupSnapshotResult) -> bytes:
    _revalidate_or_raise(value, BackupSnapshotResult)
    return _canonical_bytes(value.payload())


def encode_backup_verification_receipt(value: BackupVerificationReceipt) -> bytes:
    _revalidate_or_raise(value, BackupVerificationReceipt)
    return _canonical_bytes(value.payload())


def encode_backup_verification_outcome(value: BackupVerificationOutcome) -> bytes:
    _revalidate_or_raise(value, BackupVerificationOutcome)
    payload: dict[str, object] = {
        "error_code": value.error_code.value,
        "state": value.state.value,
    }
    if value.receipt is not None:
        payload["receipt"] = value.receipt.payload()
    return _canonical_bytes(payload)


def _revalidate_or_raise(value: object, expected: type[object]) -> None:
    error = _revalidate(value, expected)
    if error:
        raise BackupVerificationError(error)


def _strict_json(data: bytes) -> Mapping[str, object]:
    if not isinstance(data, bytes) or not data or len(data) > MAX_CANONICAL_JSON_BYTES:
        raise BackupVerificationError(ErrorCode.NONCANONICAL_EVIDENCE)
    if data.startswith(b"\xef\xbb\xbf"):
        raise BackupVerificationError(ErrorCode.NONCANONICAL_EVIDENCE)
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as error:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE) from error

    def strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise BackupVerificationError(ErrorCode.NONCANONICAL_EVIDENCE)
            result[key] = value
        return result

    try:
        value = json.loads(
            text,
            object_pairs_hook=strict_object,
            parse_float=lambda _: (_ for _ in ()).throw(
                BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
            ),
            parse_constant=lambda _: (_ for _ in ()).throw(
                BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
            ),
        )
    except BackupVerificationError:
        raise
    except (ValueError, UnicodeError) as error:
        raise BackupVerificationError(ErrorCode.NONCANONICAL_EVIDENCE) from error
    _bounded_json(value, depth=0, budget=[MAX_CANONICAL_JSON_ITEMS])
    if _canonical_bytes(value) != data or not isinstance(value, dict):
        raise BackupVerificationError(ErrorCode.NONCANONICAL_EVIDENCE)
    return value


def _bounded_json(value: object, *, depth: int, budget: list[int]) -> None:
    budget[0] -= 1
    if depth > MAX_CANONICAL_JSON_DEPTH or budget[0] < 0:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    if value is None or isinstance(value, (bool, float)):
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    if type(value) is int:
        return
    if isinstance(value, str):
        _validate_ascii_string(value)
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _validate_ascii_string(key)
            _bounded_json(child, depth=depth + 1, budget=budget)
        return
    if isinstance(value, list):
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)


def _exact(payload: Mapping[str, object], keys: set[str]) -> None:
    if set(payload) != keys:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)


def _decode_runtime(payload: object) -> RuntimeReleaseIdentity:
    if not isinstance(payload, dict):
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    _exact(
        payload,
        {"identity_schema", "version", "source_sha", "image_digest", "runtime_identity_digest"},
    )
    try:
        return RuntimeReleaseIdentity(**payload)
    except TypeError as error:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE) from error


def _decode_schema(payload: object) -> DatabaseSchemaIdentity:
    if not isinstance(payload, dict):
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE)
    _exact(
        payload,
        {
            "schema_version",
            "migration_graph_digest",
            "applied_migration_digest",
            "applied_migration_count",
            "pending_migration_count",
            "database_schema_identity_digest",
        },
    )
    try:
        return DatabaseSchemaIdentity(**payload)  # type: ignore[arg-type]
    except TypeError as error:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE) from error


def decode_backup_verification_requirements(data: bytes) -> BackupVerificationRequirements:
    payload = _strict_json(data)
    _exact(
        payload,
        {
            "schema_version",
            "environment_class",
            "runtime_identity",
            "database_schema_identity",
            "provider_key",
            "provider_contract_version",
            "maximum_recovery_point_age_seconds",
            "maximum_receipt_age_seconds",
            "requirements_digest",
        },
    )
    values = dict(payload)
    values["environment_class"] = _enum(EnvironmentClass, values["environment_class"])
    values["runtime_identity"] = _decode_runtime(values["runtime_identity"])
    values["database_schema_identity"] = _decode_schema(values["database_schema_identity"])
    try:
        return BackupVerificationRequirements(**values)  # type: ignore[arg-type]
    except TypeError as error:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE) from error


def decode_backup_snapshot_result(data: bytes) -> BackupSnapshotResult:
    payload = _strict_json(data)
    _exact(payload, set(_SNAPSHOT_RESULT_FIELDS))
    values = dict(payload)
    values["environment_class"] = _enum(EnvironmentClass, values["environment_class"])
    values["state"] = _enum(SnapshotState, values["state"])
    values["error_code"] = _enum(ErrorCode, values["error_code"])
    values["runtime_identity"] = _decode_runtime(values["runtime_identity"])
    values["database_schema_identity"] = _decode_schema(values["database_schema_identity"])
    try:
        return BackupSnapshotResult(**values)  # type: ignore[arg-type]
    except TypeError as error:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE) from error


def decode_backup_verification_receipt(data: bytes) -> BackupVerificationReceipt:
    payload = _strict_json(data)
    keys = set(BackupVerificationReceipt.__dataclass_fields__)
    _exact(payload, keys)
    values = dict(payload)
    values["environment_class"] = _enum(EnvironmentClass, values["environment_class"])
    values["state"] = _enum(VerificationState, values["state"])
    values["error_code"] = _enum(ErrorCode, values["error_code"])
    values["runtime_identity"] = _decode_runtime(values["runtime_identity"])
    values["database_schema_identity"] = _decode_schema(values["database_schema_identity"])
    try:
        return BackupVerificationReceipt(**values)  # type: ignore[arg-type]
    except TypeError as error:
        raise BackupVerificationError(ErrorCode.UNSAFE_EVIDENCE) from error


def decode_backup_verification_outcome(data: bytes) -> BackupVerificationOutcome:
    payload = _strict_json(data)
    state = _enum(VerificationState, payload.get("state"))
    expected_keys = (
        {"state", "error_code", "receipt"}
        if state is VerificationState.VERIFIED
        else {
            "state",
            "error_code",
        }
    )
    _exact(payload, expected_keys)
    code = _enum(ErrorCode, payload["error_code"])
    receipt = None
    if state is VerificationState.VERIFIED:
        receipt = decode_backup_verification_receipt(_canonical_bytes(payload["receipt"]))
    return BackupVerificationOutcome(cast(VerificationState, state), cast(ErrorCode, code), receipt)


def safe_manifest_digest(manifest: Mapping[str, int | str]) -> str:
    """Hash a bounded safe manifest after applying the same sensitive-value policy."""

    build_snapshot_identity_digest(manifest)
    return _plain_digest(dict(manifest))


def contract_self_check() -> tuple[str, ...]:
    """Return stable non-secret contract facts for repository/system tests."""

    return (
        f"schema:{SCHEMA_VERSION}",
        f"verifier:{VERIFIER_CONTRACT_VERSION}",
        f"providers:{','.join(sorted(ALLOWED_PROVIDER_KEYS))}",
        f"max_resources:{MAX_RESOURCE_COUNT}",
        f"max_migrations:{MAX_MIGRATION_COUNT}",
    )
