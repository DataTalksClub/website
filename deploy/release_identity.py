"""Sealed release source identities and immutable published-image records.

Only the ``construct`` CLI is allowed to create a new timestamped version.  Every
later release stage must deserialize the record produced here.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

IDENTITY_SCHEMA = 2
LEGACY_IDENTITY_SCHEMA = 1
LOCAL_VERSION = "local-development-build-version-not-configured"
SOURCE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
VERSION_PATTERN = re.compile(r"^(?P<stamp>[0-9]{8}-[0-9]{6})-(?P<suffix>[0-9a-f]{7})$")
RFC3339_UTC_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class IdentityError(ValueError):
    """Raised when release identity data is malformed or contradictory."""


def _validate_sha(value: object) -> str:
    if not isinstance(value, str) or SOURCE_SHA_PATTERN.fullmatch(value) is None:
        raise IdentityError("source_sha must be 40 lowercase hexadecimal characters")
    return value


def _validate_digest(value: object) -> str:
    if not isinstance(value, str) or IMAGE_DIGEST_PATTERN.fullmatch(value) is None:
        raise IdentityError("image_digest must be sha256 plus 64 lowercase hexadecimal characters")
    return value


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or RFC3339_UTC_PATTERN.fullmatch(value) is None:
        raise IdentityError("constructed_at must be an RFC3339 UTC second ending in Z")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise IdentityError("constructed_at is not a valid UTC calendar instant") from error
    return parsed.replace(tzinfo=UTC)


def validate_schema2_version(value: object, source_sha: object) -> str:
    """Validate and return one canonical schema-2 VERSION.

    This is the sole compact timestamp parser used by both the release controller
    and the deployed Django runtime.  The regex fixes the wire format while
    ``strptime`` rejects calendar- and clock-invalid values that still match it.
    """

    sha = _validate_sha(source_sha)
    if not isinstance(value, str):
        raise IdentityError("version must be UTC YYYYMMDD-HHMMSS plus a seven-character SHA")
    match = VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise IdentityError("version must be UTC YYYYMMDD-HHMMSS plus a seven-character SHA")
    try:
        datetime.strptime(match.group("stamp"), "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    except ValueError as error:
        raise IdentityError("version timestamp is not a valid UTC calendar instant") from error
    if match.group("suffix") != sha[:7]:
        raise IdentityError("version does not match source_sha")
    return value


def _validate_version(value: object, source_sha: str, constructed_at: str) -> str:
    version = validate_schema2_version(value, source_sha)
    instant = _parse_utc(constructed_at)
    expected = f"{instant:%Y%m%d-%H%M%S}-{source_sha[:7]}"
    if version != expected:
        raise IdentityError("version does not match constructed_at and source_sha")
    return version


def _strict_payload(payload: object, keys: set[str], *, context: str) -> Mapping[str, Any]:
    if not isinstance(payload, dict) or set(payload) != keys:
        raise IdentityError(f"{context} fields differ from the sealed schema")
    return payload


@dataclass(frozen=True)
class SourceIdentity:
    identity_schema: int
    version: str
    source_sha: str
    constructed_at: str

    def __post_init__(self) -> None:
        if self.identity_schema != IDENTITY_SCHEMA:
            raise IdentityError("new source identity must use identity_schema 2")
        _validate_sha(self.source_sha)
        _validate_version(self.version, self.source_sha, self.constructed_at)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n")

    @classmethod
    def from_dict(cls, payload: object) -> SourceIdentity:
        values = _strict_payload(
            payload,
            {"identity_schema", "version", "source_sha", "constructed_at"},
            context="source identity",
        )
        try:
            return cls(**values)
        except TypeError as error:
            raise IdentityError("source identity types differ from the sealed schema") from error

    @classmethod
    def read(cls, path: Path) -> SourceIdentity:
        try:
            return cls.from_dict(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError) as error:
            raise IdentityError(f"invalid source identity {path}: {error}") from error


class SourceIdentityConstructor:
    """One-shot constructor so one resolver cannot silently read the clock twice."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._used = False

    def construct(self, source_sha: str) -> SourceIdentity:
        if self._used:
            raise IdentityError("source identity constructor may be called only once")
        self._used = True
        sha = _validate_sha(source_sha)
        instant = self._clock()
        if not isinstance(instant, datetime) or instant.tzinfo is None:
            raise IdentityError("source identity clock must return an aware UTC instant")
        instant = instant.astimezone(UTC).replace(microsecond=0)
        constructed_at = instant.strftime("%Y-%m-%dT%H:%M:%SZ")
        version = f"{instant:%Y%m%d-%H%M%S}-{sha[:7]}"
        return SourceIdentity(IDENTITY_SCHEMA, version, sha, constructed_at)


@dataclass(frozen=True)
class PublishedImageRecord:
    identity_schema: int
    version: str
    source_sha: str
    constructed_at: str | None
    repository_uri: str
    image_digest: str
    image_config_digest: str
    platform: str
    user: str

    def __post_init__(self) -> None:
        _validate_sha(self.source_sha)
        _validate_digest(self.image_digest)
        _validate_digest(self.image_config_digest)
        if not self.repository_uri or "@" in self.repository_uri or "://" in self.repository_uri:
            raise IdentityError("repository_uri must be an untagged repository URI")
        if not self.platform or not self.user:
            raise IdentityError("published image platform and user are required")
        if self.identity_schema == IDENTITY_SCHEMA:
            if self.constructed_at is None:
                raise IdentityError("schema-2 published image requires constructed_at")
            _validate_version(self.version, self.source_sha, self.constructed_at)
        elif self.identity_schema == LEGACY_IDENTITY_SCHEMA:
            if self.version != self.source_sha or self.constructed_at is not None:
                raise IdentityError("legacy published image version must be its source SHA")
        else:
            raise IdentityError("published image identity_schema is unsupported")

    @classmethod
    def publish(
        cls,
        source: SourceIdentity,
        *,
        repository_uri: str,
        image_digest: str,
        image_config_digest: str,
        platform: str,
        user: str,
    ) -> PublishedImageRecord:
        if type(source) is not SourceIdentity:
            raise IdentityError("publisher requires a sealed schema-2 source identity")
        return cls(
            source.identity_schema,
            source.version,
            source.source_sha,
            source.constructed_at,
            repository_uri,
            image_digest,
            image_config_digest,
            platform,
            user,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def write(self, path: Path) -> None:
        if self.identity_schema != IDENTITY_SCHEMA:
            raise IdentityError("new published image records must use identity_schema 2")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n")

    @classmethod
    def from_dict(cls, payload: object, *, allow_legacy: bool = False) -> PublishedImageRecord:
        schema2 = {
            "identity_schema",
            "version",
            "source_sha",
            "constructed_at",
            "repository_uri",
            "image_digest",
            "image_config_digest",
            "platform",
            "user",
        }
        if isinstance(payload, dict) and set(payload) == schema2:
            try:
                record = cls(**payload)
            except TypeError as error:
                raise IdentityError("published image types differ from schema 2") from error
            if record.identity_schema != IDENTITY_SCHEMA:
                raise IdentityError("schema-2 published image record must declare schema 2")
            return record
        legacy = {
            "source_sha",
            "repository_uri",
            "image_digest",
            "image_config_digest",
            "platform",
            "user",
        }
        if allow_legacy and isinstance(payload, dict) and set(payload) == legacy:
            return cls(
                LEGACY_IDENTITY_SCHEMA,
                _validate_sha(payload["source_sha"]),
                payload["source_sha"],
                None,
                payload["repository_uri"],
                payload["image_digest"],
                payload["image_config_digest"],
                payload["platform"],
                payload["user"],
            )
        raise IdentityError("published image fields differ from the sealed schema")

    @classmethod
    def read(cls, path: Path, *, allow_legacy: bool = False) -> PublishedImageRecord:
        try:
            return cls.from_dict(json.loads(path.read_text()), allow_legacy=allow_legacy)
        except (OSError, json.JSONDecodeError) as error:
            raise IdentityError(f"invalid published image record {path}: {error}") from error


def _construct(arguments: argparse.Namespace) -> None:
    SourceIdentityConstructor().construct(arguments.source_sha).write(arguments.output)


def _publish(arguments: argparse.Namespace) -> None:
    PublishedImageRecord.publish(
        SourceIdentity.read(arguments.source_identity),
        repository_uri=arguments.repository_uri,
        image_digest=arguments.image_digest,
        image_config_digest=arguments.image_config_digest,
        platform=arguments.platform,
        user=arguments.user,
    ).write(arguments.output)


def _inspect_published(arguments: argparse.Namespace) -> None:
    record = PublishedImageRecord.read(arguments.record, allow_legacy=arguments.allow_legacy)
    print(json.dumps(record.to_dict(), sort_keys=True, separators=(",", ":")))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Construct and validate sealed release identities")
    subparsers = root.add_subparsers(dest="command", required=True)
    construct = subparsers.add_parser("construct")
    construct.add_argument("--source-sha", required=True)
    construct.add_argument("--output", required=True, type=Path)
    construct.set_defaults(func=_construct)
    publish = subparsers.add_parser("publish")
    publish.add_argument("--source-identity", required=True, type=Path)
    publish.add_argument("--repository-uri", required=True)
    publish.add_argument("--image-digest", required=True)
    publish.add_argument("--image-config-digest", required=True)
    publish.add_argument("--platform", required=True)
    publish.add_argument("--user", required=True)
    publish.add_argument("--output", required=True, type=Path)
    publish.set_defaults(func=_publish)
    inspect = subparsers.add_parser("inspect-published")
    inspect.add_argument("--record", required=True, type=Path)
    inspect.add_argument("--allow-legacy", action="store_true")
    inspect.set_defaults(func=_inspect_published)
    return root


def main() -> int:
    arguments = parser().parse_args()
    try:
        arguments.func(arguments)
    except IdentityError as error:
        raise SystemExit(str(error)) from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
