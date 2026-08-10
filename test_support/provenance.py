from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EMAIL_SHAPED_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PRIVATE_FORBIDDEN_FIELDS = frozenset(
    {
        "answer",
        "attendee",
        "credential",
        "email",
        "event_name",
        "filename",
        "name",
        "payload",
        "provider_digest",
        "provider_id",
        "provider_identifier",
        "provider_payload",
        "recipient",
        "registration",
        "registration_row",
        "secret",
        "source_digest",
        "source_filename",
        "source_path",
        "token",
    }
)
_PRIVATE_FORBIDDEN_TOKENS = frozenset(
    {
        "answer",
        "attendee",
        "credential",
        "email",
        "filename",
        "name",
        "payload",
        "recipient",
        "registration",
        "secret",
        "token",
    }
)
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


class FixtureProvenanceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PublicFixtureProvenance:
    artifact_url: str
    artifact_version: str
    repository: str
    commit: str
    source_path: str
    byte_sha256: str
    schema_version: int
    sanitization: tuple[str, ...]
    source_kind: str


def validate_public_fixture(fixture: Path, provenance_path: Path) -> PublicFixtureProvenance:
    _assert_sibling_files(fixture, provenance_path)
    try:
        raw = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise FixtureProvenanceError("public fixture provenance is malformed") from error
    expected = {
        "artifact_url",
        "artifact_version",
        "byte_sha256",
        "commit",
        "repository",
        "sanitization",
        "schema_version",
        "source_path",
        "source_kind",
    }
    if not isinstance(raw, dict) or set(raw) != expected:
        raise FixtureProvenanceError("public fixture provenance fields are not exact")
    source_path = raw["source_path"]
    if (
        not isinstance(source_path, str)
        or source_path.startswith(("/", "\\"))
        or any(part in {"", ".", ".."} for part in source_path.replace("\\", "/").split("/"))
    ):
        raise FixtureProvenanceError("public fixture source path is unsafe")
    if (
        not isinstance(raw["artifact_version"], str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", raw["artifact_version"])
        or not isinstance(raw["repository"], str)
        or not raw["repository"].startswith("https://github.com/")
        or not _SHA1_RE.fullmatch(raw["commit"] or "")
        or not _SHA256_RE.fullmatch(raw["byte_sha256"] or "")
        or raw["schema_version"] != 1
        or not isinstance(raw["sanitization"], list)
        or any(not isinstance(item, str) for item in raw["sanitization"])
        or raw["source_kind"] not in {"release_build", "repository_file"}
    ):
        raise FixtureProvenanceError("public fixture provenance values are invalid")
    artifact = _validate_artifact_url(raw["artifact_url"])
    if raw["artifact_version"] not in artifact.path:
        raise FixtureProvenanceError("public fixture artifact URL is not immutable by version")
    if raw["source_kind"] == "repository_file":
        expected_suffix = f"/{raw['commit']}/{source_path}"
        if artifact.hostname != "raw.githubusercontent.com" or not artifact.path.endswith(
            expected_suffix
        ):
            raise FixtureProvenanceError(
                "repository-file artifact URL does not name the exact commit and source path"
            )
    elif not raw["sanitization"]:
        raise FixtureProvenanceError("release-build fixtures must describe their build provenance")
    digest = hashlib.sha256(fixture.read_bytes()).hexdigest()
    if digest != raw["byte_sha256"]:
        raise FixtureProvenanceError("public fixture bytes do not match provenance")
    return PublicFixtureProvenance(
        artifact_url=raw["artifact_url"],
        artifact_version=raw["artifact_version"],
        repository=raw["repository"],
        commit=raw["commit"],
        source_path=source_path,
        byte_sha256=digest,
        schema_version=1,
        sanitization=tuple(raw["sanitization"]),
        source_kind=raw["source_kind"],
    )


def _validate_artifact_url(value: object):
    if not isinstance(value, str):
        raise FixtureProvenanceError("public fixture artifact URL is invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise FixtureProvenanceError("public fixture artifact URL is invalid") from error
    if (
        parsed.scheme != "https"
        or parsed.hostname
        not in {"cdn.tailwindcss.com", "cdnjs.cloudflare.com", "raw.githubusercontent.com"}
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/")
        or parsed.query
        or parsed.fragment
    ):
        raise FixtureProvenanceError("public fixture artifact URL is invalid")
    return parsed


def validate_private_fixture(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise FixtureProvenanceError("private fixture is malformed") from error
    if not isinstance(raw, dict) or set(raw) != {
        "generator_version",
        "records",
        "schema_family",
    }:
        raise FixtureProvenanceError("private fixture metadata must be synthetic and minimal")
    if (
        not isinstance(raw["generator_version"], str)
        or not raw["generator_version"].startswith("synthetic-")
        or not isinstance(raw["schema_family"], str)
        or not raw["schema_family"].startswith("provider-neutral-")
        or not isinstance(raw["records"], list)
    ):
        raise FixtureProvenanceError("private fixture requires a synthetic generator version")
    _validate_private_value(raw["records"])
    return raw


def _validate_private_value(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise FixtureProvenanceError("private fixture fields must be text")
            if _is_private_field(key):
                raise FixtureProvenanceError(
                    "private fixture retains forbidden source or identity data"
                )
            _validate_private_value(nested)
        return
    if isinstance(value, list):
        for nested in value:
            _validate_private_value(nested)
        return
    if isinstance(value, str) and _EMAIL_SHAPED_RE.fullmatch(value.strip()):
        raise FixtureProvenanceError("private fixture retains forbidden source or identity data")
    if value is not None and not isinstance(value, (bool, int, float, str)):
        raise FixtureProvenanceError("private fixture contains an unsupported value")


def _is_private_field(key: str) -> bool:
    """Classify semantic private-field categories after Unicode/case/style normalization."""

    compatible = unicodedata.normalize("NFKC", key)
    separated = _CAMEL_BOUNDARY_RE.sub("_", compatible).casefold()
    normalized = _NON_ALNUM_RE.sub("_", separated).strip("_")
    tokens = tuple(token for token in normalized.split("_") if token)
    collapsed = "".join(tokens)
    if normalized in _PRIVATE_FORBIDDEN_FIELDS:
        return True
    if any(token in _PRIVATE_FORBIDDEN_TOKENS for token in tokens):
        return True
    if collapsed in {field.replace("_", "") for field in _PRIVATE_FORBIDDEN_FIELDS}:
        return True
    token_set = set(tokens)
    if "provider" in token_set and token_set & {"digest", "id", "identifier"}:
        return True
    return "source" in token_set and bool(token_set & {"digest", "filename", "path"})


def _assert_sibling_files(fixture: Path, provenance: Path) -> None:
    if fixture.is_symlink() or provenance.is_symlink():
        raise FixtureProvenanceError("fixture and provenance cannot be symlinks")
    if not fixture.is_file() or not provenance.is_file():
        raise FixtureProvenanceError("fixture and provenance must both exist")
    if fixture.parent.resolve(strict=True) != provenance.parent.resolve(strict=True):
        raise FixtureProvenanceError("public fixture provenance must be a sibling file")
