"""Strict, deterministic records for the legacy compatibility manifest.

The manifest is a long-lived interface: later parity tests and redirect-map
generation consume it.  Its records therefore reject implicit coercion, unknown
JSON members, ambiguous classifications, and values that look like credentials or
personal data.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Self
from urllib.parse import parse_qsl, unquote, urlsplit

from compatibility.redaction import (
    fragment_requires_redaction,
    is_url_valued_social_key,
    url_contains_unredacted_sensitive_value,
)

MANIFEST_SCHEMA_VERSION = 2
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REDACTED_DIGEST_RE = re.compile(r"^redacted-sha256-[0-9a-f]{64}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{7,64}$")
_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_SOURCE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$")
_EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ASSET_SCALE_SUFFIX_RE = re.compile(r"@\d+(?:\.\d+)?x\.(?:avif|gif|jpe?g|png|svg|webp)\b", re.I)
_JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
    r"[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
)
_AWS_ACCESS_KEY_RE = re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")
_GITHUB_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])gh(?:p|o|u|s|r)_[A-Za-z0-9]{20,}(?![A-Za-z0-9])")
_BEARER_RE = re.compile(r"(?i)(?:^|\s)bearer\s+[A-Za-z0-9._~+/=-]{8,}")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_SENSITIVE_QUERY_KEY_RE = re.compile(
    r"(?i)(?:^|[_-])(?:access[_-]?token|api[_-]?key|auth|authorization|code|credential|"
    r"jwt|password|refresh[_-]?token|secret|session|signature|token)(?:$|[_-])"
)
_ISO_LASTMOD_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))?$"
)
_CANONICAL_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class ManifestValidationError(ValueError):
    """A manifest value does not satisfy the stable schema."""


class ManifestDecodeError(ManifestValidationError):
    """Serialized manifest data is malformed or does not match the schema."""


class ObservationOrigin(StrEnum):
    SOURCE = "source"
    PRODUCTION = "production"


class ReferenceKind(StrEnum):
    INTERNAL_LINK = "internal_link"
    EXTERNAL_LINK = "external_link"
    FORM_ACTION = "form_action"
    ASSET = "asset"


class ClassificationKind(StrEnum):
    PRESERVE = "preserve"
    REDIRECT = "redirect"
    RETIRE = "retire"


class ReviewState(StrEnum):
    """Explicit review authority for compatibility classifications."""

    PROPOSED_PRESERVE = "proposed_preserve"
    APPROVED_PARITY = "approved_parity"
    APPROVED_EXCEPTION = "approved_exception"


def _require_exact_string(value: object, field_name: str, *, allow_empty: bool = True) -> str:
    if type(value) is not str:
        raise ManifestValidationError(f"{field_name}_must_be_string")
    if not allow_empty and not value:
        raise ManifestValidationError(f"{field_name}_must_not_be_empty")
    if value != value.strip():
        raise ManifestValidationError(f"{field_name}_must_not_have_outer_whitespace")
    if any(ord(character) < 0x20 for character in value):
        raise ManifestValidationError(f"{field_name}_contains_control_character")
    _reject_private_value(value, field_name)
    return value


def _require_exact_tuple(value: object, field_name: str) -> tuple[Any, ...]:
    if type(value) is not tuple:
        raise ManifestValidationError(f"{field_name}_must_be_tuple")
    return value


def _reject_private_value(value: str, field_name: str) -> None:
    if not value:
        return
    # Asset naming conventions such as ``logo@2x.png`` are not email addresses.
    email_candidate = _ASSET_SCALE_SUFFIX_RE.sub("", value)
    if _EMAIL_RE.search(email_candidate):
        raise ManifestValidationError(f"{field_name}_contains_personal_data")
    if (
        _JWT_RE.search(value)
        or _AWS_ACCESS_KEY_RE.search(value)
        or _GITHUB_TOKEN_RE.search(value)
        or _BEARER_RE.search(value)
        or _PRIVATE_KEY_RE.search(value)
    ):
        raise ManifestValidationError(f"{field_name}_contains_credential")


def _is_sensitive_query_key(value: str) -> bool:
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value).lower()
    compact = re.sub(r"[^a-z0-9]", "", separated)
    protected = {
        "accesstoken",
        "apikey",
        "auth",
        "authorization",
        "code",
        "credential",
        "jwt",
        "password",
        "refreshtoken",
        "secret",
        "session",
        "sig",
        "signature",
        "token",
    }
    return bool(_SENSITIVE_QUERY_KEY_RE.search(separated)) or compact in protected


def _validate_http_url(value: object, field_name: str, *, allow_empty: bool = False) -> str:
    url = _require_exact_string(value, field_name, allow_empty=allow_empty)
    if not url and allow_empty:
        return url
    if any(character.isspace() for character in url):
        raise ManifestValidationError(f"{field_name}_contains_raw_whitespace")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ManifestValidationError(f"{field_name}_must_be_absolute_http_url")
    if parts.username is not None or parts.password is not None:
        raise ManifestValidationError(f"{field_name}_contains_credentials")
    try:
        port = parts.port
    except ValueError as exc:
        raise ManifestValidationError(f"{field_name}_has_invalid_port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ManifestValidationError(f"{field_name}_has_invalid_port")
    try:
        decoded_url = unquote(url)
    except UnicodeError as exc:
        raise ManifestValidationError(f"{field_name}_has_invalid_encoding") from exc
    if any(ord(character) < 0x20 for character in decoded_url):
        raise ManifestValidationError(f"{field_name}_contains_control_character")
    _reject_private_value(decoded_url, field_name)
    for query_key, query_value in parse_qsl(parts.query, keep_blank_values=True):
        if _is_sensitive_query_key(query_key) and not _REDACTED_DIGEST_RE.fullmatch(query_value):
            raise ManifestValidationError(f"{field_name}_contains_sensitive_query_key")
        _reject_private_value(query_value, field_name)
    try:
        has_sensitive_value = url_contains_unredacted_sensitive_value(url)
    except ValueError as exc:
        raise ManifestValidationError(f"{field_name}_contains_credentials") from exc
    if has_sensitive_value:
        raise ManifestValidationError(f"{field_name}_contains_unredacted_sensitive_value")
    return url


def _validate_reference_url(value: object, field_name: str) -> str:
    # Extractors resolve relative and root-relative references before constructing
    # records.  Only network URLs are durable enough for cross-host comparison.
    return _validate_http_url(value, field_name)


def _without_fragment(value: str) -> str:
    parts = urlsplit(value)
    return parts._replace(fragment="").geturl()


def _validate_pair_tuple(
    values: object,
    field_name: str,
    *,
    second_is_url: bool = False,
) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for index, item in enumerate(_require_exact_tuple(values, field_name)):
        if type(item) is not tuple or len(item) != 2:
            raise ManifestValidationError(f"{field_name}_{index}_must_be_string_pair")
        key = _require_exact_string(item[0], f"{field_name}_{index}_key", allow_empty=False)
        if second_is_url:
            value = _validate_http_url(item[1], f"{field_name}_{index}_value")
        else:
            value = _require_exact_string(item[1], f"{field_name}_{index}_value")
        result.append((key, value))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class Reference:
    kind: ReferenceKind
    url: str

    def __post_init__(self) -> None:
        if type(self.kind) is not ReferenceKind:
            raise ManifestValidationError("reference_kind_must_be_enum")
        _validate_reference_url(self.url, "reference_url")


@dataclass(frozen=True, slots=True)
class StructuredData:
    type: str
    identifier: str = ""

    def __post_init__(self) -> None:
        type_name = _require_exact_string(self.type, "structured_data_type", allow_empty=False)
        identifier = _require_exact_string(self.identifier, "structured_data_identifier")
        for field_name, value in (
            ("structured_data_type", type_name),
            ("structured_data_identifier", identifier),
        ):
            try:
                has_sensitive_value = url_contains_unredacted_sensitive_value(value)
            except ValueError as exc:
                raise ManifestValidationError(f"{field_name}_contains_credentials") from exc
            if has_sensitive_value:
                raise ManifestValidationError(f"{field_name}_contains_unredacted_sensitive_value")


@dataclass(frozen=True, slots=True)
class SitemapEntry:
    url: str
    lastmod: str = ""

    def __post_init__(self) -> None:
        _validate_http_url(self.url, "sitemap_url")
        lastmod = _require_exact_string(self.lastmod, "sitemap_lastmod")
        if lastmod:
            _validate_lastmod(lastmod)


def _validate_lastmod(value: str) -> None:
    if not _ISO_LASTMOD_RE.fullmatch(value):
        raise ManifestValidationError("sitemap_lastmod_must_be_iso_8601")
    try:
        if "T" in value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError
        else:
            date.fromisoformat(value)
    except ValueError as exc:
        raise ManifestValidationError("sitemap_lastmod_must_be_iso_8601") from exc


@dataclass(frozen=True, slots=True)
class SitemapState:
    entries: tuple[SitemapEntry, ...] = ()

    def __post_init__(self) -> None:
        entries = _require_exact_tuple(self.entries, "sitemap_entries")
        if any(type(entry) is not SitemapEntry for entry in entries):
            raise ManifestValidationError("sitemap_entries_must_contain_sitemap_entries")
        urls = [entry.url for entry in entries]
        if len(urls) != len(set(urls)):
            raise ManifestValidationError("sitemap_entries_must_have_unique_urls")
        if list(entries) != sorted(entries, key=lambda entry: (entry.url, entry.lastmod)):
            raise ManifestValidationError("sitemap_entries_must_be_sorted")


@dataclass(frozen=True, slots=True)
class RedirectHop:
    status: int
    url: str

    def __post_init__(self) -> None:
        if type(self.status) is not int or self.status not in {301, 302, 303, 307, 308}:
            raise ManifestValidationError("redirect_hop_status_must_be_redirect_status")
        _validate_http_url(self.url, "redirect_hop_url")


@dataclass(frozen=True, slots=True)
class PageMetadata:
    title: str = ""
    description: str = ""
    first_heading: str = ""
    language: str = ""
    robots: tuple[str, ...] = ()
    canonical_url: str = ""
    client_redirect_url: str = ""
    alternates: tuple[tuple[str, str], ...] = ()
    social_metadata: tuple[tuple[str, str], ...] = ()
    structured_data: tuple[StructuredData, ...] = ()
    fragments: tuple[str, ...] = ()
    references: tuple[Reference, ...] = ()
    main_content_fingerprint: str = ""
    soft_404: bool = False

    def __post_init__(self) -> None:
        _require_exact_string(self.title, "page_title")
        _require_exact_string(self.description, "page_description")
        _require_exact_string(self.first_heading, "page_first_heading")
        _require_exact_string(self.language, "page_language")
        for index, directive in enumerate(_require_exact_tuple(self.robots, "page_robots")):
            _require_exact_string(directive, f"page_robots_{index}", allow_empty=False)
        if list(self.robots) != sorted(set(self.robots)):
            raise ManifestValidationError("page_robots_must_be_unique_and_sorted")
        _validate_http_url(self.canonical_url, "page_canonical_url", allow_empty=True)
        _validate_http_url(
            self.client_redirect_url,
            "page_client_redirect_url",
            allow_empty=True,
        )
        alternates = _validate_pair_tuple(self.alternates, "page_alternates", second_is_url=True)
        if list(alternates) != sorted(set(alternates)):
            raise ManifestValidationError("page_alternates_must_be_unique_and_sorted")
        social_metadata = _validate_pair_tuple(self.social_metadata, "page_social_metadata")
        for key, value in social_metadata:
            if not is_url_valued_social_key(key):
                continue
            try:
                has_sensitive_value = url_contains_unredacted_sensitive_value(value)
            except ValueError as exc:
                raise ManifestValidationError("page_social_metadata_contains_credentials") from exc
            if has_sensitive_value:
                raise ManifestValidationError(
                    "page_social_metadata_contains_unredacted_sensitive_value"
                )
        if list(social_metadata) != sorted(set(social_metadata)):
            raise ManifestValidationError("page_social_metadata_must_be_unique_and_sorted")
        social_keys = [key for key, _value in social_metadata]
        if len(social_keys) != len(set(social_keys)):
            raise ManifestValidationError("page_social_metadata_keys_must_be_unique")
        structured_data = _require_exact_tuple(self.structured_data, "page_structured_data")
        if any(type(item) is not StructuredData for item in structured_data):
            raise ManifestValidationError(
                "page_structured_data_must_contain_structured_data_records"
            )
        if list(structured_data) != sorted(
            set(structured_data), key=lambda item: (item.type, item.identifier)
        ):
            raise ManifestValidationError("page_structured_data_must_be_unique_and_sorted")
        for index, fragment in enumerate(_require_exact_tuple(self.fragments, "page_fragments")):
            fragment = _require_exact_string(fragment, f"page_fragments_{index}", allow_empty=False)
            if fragment_requires_redaction(fragment):
                raise ManifestValidationError("page_fragment_contains_unredacted_sensitive_value")
        if list(self.fragments) != sorted(set(self.fragments)):
            raise ManifestValidationError("page_fragments_must_be_unique_and_sorted")
        references = _require_exact_tuple(self.references, "page_references")
        if any(type(item) is not Reference for item in references):
            raise ManifestValidationError("page_references_must_contain_reference_records")
        if list(references) != sorted(
            set(references), key=lambda item: (item.kind.value, item.url)
        ):
            raise ManifestValidationError("page_references_must_be_unique_and_sorted")
        fingerprint = _require_exact_string(
            self.main_content_fingerprint,
            "page_main_content_fingerprint",
        )
        if fingerprint and not _SHA256_RE.fullmatch(fingerprint):
            raise ManifestValidationError("page_main_content_fingerprint_must_be_sha256")
        if type(self.soft_404) is not bool:
            raise ManifestValidationError("page_soft_404_must_be_boolean")


@dataclass(frozen=True, slots=True)
class Capture:
    origin: ObservationOrigin
    requested_url: str
    status: int
    final_url: str
    response_count: int = 0
    transfer_bytes: int = 0
    error_code: str = ""
    source_repository: str = ""
    source_path: str = ""
    content_type: str = ""
    response_last_modified: str = ""
    response_content_language: str = ""
    response_robots: tuple[str, ...] = ()
    body_sha256: str = ""
    redirect_chain: tuple[RedirectHop, ...] = ()
    metadata: PageMetadata = PageMetadata()
    sitemap: SitemapState = SitemapState()

    def __post_init__(self) -> None:
        if type(self.origin) is not ObservationOrigin:
            raise ManifestValidationError("capture_origin_must_be_enum")
        _validate_http_url(self.requested_url, "capture_requested_url")
        error_code = _require_exact_string(self.error_code, "capture_error_code")
        if error_code and not _ERROR_CODE_RE.fullmatch(error_code):
            raise ManifestValidationError("capture_error_code_is_invalid")
        if type(self.status) is not int or (self.status != 0 and not 100 <= self.status <= 599):
            raise ManifestValidationError("capture_status_must_be_http_status")
        if (self.status == 0) != bool(error_code):
            raise ManifestValidationError("capture_failure_requires_status_zero_and_error_code")
        if type(self.response_count) is not int or self.response_count < 0:
            raise ManifestValidationError("capture_response_count_must_be_nonnegative_integer")
        if type(self.transfer_bytes) is not int or self.transfer_bytes < 0:
            raise ManifestValidationError("capture_transfer_bytes_must_be_nonnegative_integer")
        _validate_http_url(self.final_url, "capture_final_url")
        repository = _require_exact_string(self.source_repository, "capture_source_repository")
        source_path = _require_exact_string(self.source_path, "capture_source_path")
        if self.origin is ObservationOrigin.SOURCE:
            _validate_http_url(repository, "capture_source_repository")
            _validate_source_path(source_path)
        elif repository or source_path:
            raise ManifestValidationError("production_capture_must_not_claim_source_location")
        _require_exact_string(self.content_type, "capture_content_type")
        _require_exact_string(self.response_last_modified, "capture_response_last_modified")
        _require_exact_string(self.response_content_language, "capture_response_content_language")
        for index, directive in enumerate(
            _require_exact_tuple(self.response_robots, "capture_response_robots")
        ):
            _require_exact_string(
                directive,
                f"capture_response_robots_{index}",
                allow_empty=False,
            )
        if list(self.response_robots) != sorted(set(self.response_robots)):
            raise ManifestValidationError("capture_response_robots_must_be_unique_and_sorted")
        body_sha256 = _require_exact_string(self.body_sha256, "capture_body_sha256")
        if body_sha256 and not _SHA256_RE.fullmatch(body_sha256):
            raise ManifestValidationError("capture_body_sha256_must_be_sha256")
        redirect_chain = _require_exact_tuple(self.redirect_chain, "capture_redirect_chain")
        if any(type(hop) is not RedirectHop for hop in redirect_chain):
            raise ManifestValidationError("capture_redirect_chain_must_contain_redirect_hops")
        if redirect_chain and _without_fragment(redirect_chain[-1].url) != self.final_url:
            raise ManifestValidationError("capture_redirect_chain_must_end_at_final_url")
        if type(self.metadata) is not PageMetadata:
            raise ManifestValidationError("capture_metadata_must_be_page_metadata")
        if type(self.sitemap) is not SitemapState:
            raise ManifestValidationError("capture_sitemap_must_be_sitemap_state")
        if self.status == 0 and (
            self.final_url != self.requested_url
            or self.content_type
            or self.response_last_modified
            or self.response_content_language
            or self.response_robots
            or self.body_sha256
            or self.redirect_chain
            or self.metadata != PageMetadata()
            or self.sitemap != SitemapState()
        ):
            raise ManifestValidationError("failed_capture_must_not_claim_response_data")

    @classmethod
    def create(
        cls,
        *,
        origin: ObservationOrigin,
        requested_url: str,
        status: int,
        final_url: str = "",
        response_count: int = 0,
        transfer_bytes: int = 0,
        error_code: str = "",
        source_repository: str = "",
        source_path: str = "",
        content_type: str = "",
        response_last_modified: str = "",
        response_content_language: str = "",
        response_robots: Sequence[str] = (),
        body_sha256: str = "",
        redirect_chain: Sequence[RedirectHop] = (),
        metadata: PageMetadata | None = None,
        sitemap: SitemapState | None = None,
    ) -> Self:
        """Build a validated capture without allowing mutable collection fields."""

        if isinstance(redirect_chain, (str, bytes)):
            raise ManifestValidationError("capture_redirect_chain_must_be_sequence_of_hops")
        resolved_final_url = final_url or requested_url
        return cls(
            origin=origin,
            requested_url=requested_url,
            status=status,
            final_url=resolved_final_url,
            response_count=response_count,
            transfer_bytes=transfer_bytes,
            error_code=error_code,
            source_repository=source_repository,
            source_path=source_path,
            content_type=content_type,
            response_last_modified=response_last_modified,
            response_content_language=response_content_language,
            response_robots=tuple(response_robots),
            body_sha256=body_sha256,
            redirect_chain=tuple(redirect_chain),
            metadata=metadata if metadata is not None else PageMetadata(),
            sitemap=sitemap if sitemap is not None else SitemapState(),
        )


def _validate_source_path(value: str) -> None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ManifestValidationError("capture_source_path_must_be_safe_relative_path")


@dataclass(frozen=True, slots=True)
class SourceRevision:
    source_id: str
    repository: str
    revision: str

    def __post_init__(self) -> None:
        source_id = _require_exact_string(
            self.source_id,
            "source_revision_source_id",
            allow_empty=False,
        )
        if not _SOURCE_ID_RE.fullmatch(source_id):
            raise ManifestValidationError("source_revision_source_id_is_invalid")
        _validate_http_url(self.repository, "source_revision_repository")
        revision = _require_exact_string(
            self.revision,
            "source_revision_revision",
            allow_empty=False,
        )
        if not _REVISION_RE.fullmatch(revision):
            raise ManifestValidationError("source_revision_revision_must_be_git_hash")


@dataclass(frozen=True, slots=True)
class ManifestProvenance:
    generated_at: str
    tool_version: str
    source_revisions: tuple[SourceRevision, ...] = ()
    production_origins: tuple[str, ...] = ()
    allowlisted_hosts: tuple[str, ...] = ()
    crawl_policy_sha256: str = ""
    schema_version: int = MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        generated_at = _require_exact_string(
            self.generated_at,
            "manifest_generated_at",
            allow_empty=False,
        )
        if not _CANONICAL_TIMESTAMP_RE.fullmatch(generated_at):
            raise ManifestValidationError("manifest_generated_at_must_be_utc_rfc3339")
        try:
            datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ManifestValidationError("manifest_generated_at_must_be_utc_rfc3339") from exc
        _require_exact_string(self.tool_version, "manifest_tool_version", allow_empty=False)
        source_revisions = _require_exact_tuple(
            self.source_revisions,
            "manifest_source_revisions",
        )
        if any(type(item) is not SourceRevision for item in source_revisions):
            raise ManifestValidationError(
                "manifest_source_revisions_must_contain_source_revision_records"
            )
        source_ids = [item.source_id for item in source_revisions]
        if source_ids != sorted(source_ids) or len(source_ids) != len(set(source_ids)):
            raise ManifestValidationError("manifest_source_revisions_must_be_unique_and_sorted")
        origins = _require_exact_tuple(self.production_origins, "manifest_production_origins")
        for index, origin in enumerate(origins):
            validated = _validate_http_url(origin, f"manifest_production_origins_{index}")
            parts = urlsplit(validated)
            if parts.path not in {"", "/"} or parts.query or parts.fragment:
                raise ManifestValidationError("manifest_production_origin_must_be_origin_only")
        if list(origins) != sorted(origins) or len(origins) != len(set(origins)):
            raise ManifestValidationError("manifest_production_origins_must_be_unique_and_sorted")
        hosts = _require_exact_tuple(self.allowlisted_hosts, "manifest_allowlisted_hosts")
        for index, host in enumerate(hosts):
            host = _require_exact_string(
                host,
                f"manifest_allowlisted_hosts_{index}",
                allow_empty=False,
            )
            if host != host.lower() or urlsplit(f"//{host}").hostname != host:
                raise ManifestValidationError("manifest_allowlisted_host_is_invalid")
        if list(hosts) != sorted(hosts) or len(hosts) != len(set(hosts)):
            raise ManifestValidationError("manifest_allowlisted_hosts_must_be_unique_and_sorted")
        policy_hash = _require_exact_string(
            self.crawl_policy_sha256,
            "manifest_crawl_policy_sha256",
        )
        if policy_hash and not _SHA256_RE.fullmatch(policy_hash):
            raise ManifestValidationError("manifest_crawl_policy_sha256_must_be_sha256")
        if type(self.schema_version) is not int or self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise ManifestValidationError("unsupported_manifest_schema_version")

    @classmethod
    def create(
        cls,
        *,
        generated_at: datetime | str,
        tool_version: str,
        source_revisions: Iterable[SourceRevision] = (),
        production_origins: Iterable[str] = (),
        allowlisted_hosts: Iterable[str] = (),
        crawl_policy_sha256: str = "",
    ) -> Self:
        if isinstance(generated_at, datetime):
            if generated_at.tzinfo is None or generated_at.utcoffset() is None:
                raise ManifestValidationError("manifest_generated_at_must_be_timezone_aware")
            timestamp = generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        elif type(generated_at) is str:
            timestamp = generated_at
        else:
            raise ManifestValidationError("manifest_generated_at_must_be_datetime_or_string")
        revision_values = tuple(source_revisions)
        if any(type(item) is not SourceRevision for item in revision_values):
            raise ManifestValidationError(
                "manifest_source_revisions_must_contain_source_revision_records"
            )
        origin_values = tuple(production_origins)
        if any(type(item) is not str for item in origin_values):
            raise ManifestValidationError("manifest_production_origins_must_contain_strings")
        host_values = tuple(allowlisted_hosts)
        if any(type(item) is not str for item in host_values):
            raise ManifestValidationError("manifest_allowlisted_hosts_must_contain_strings")
        revisions = tuple(sorted(revision_values, key=lambda item: item.source_id))
        return cls(
            generated_at=timestamp,
            tool_version=tool_version,
            source_revisions=revisions,
            production_origins=tuple(sorted(origin_values)),
            allowlisted_hosts=tuple(sorted(host_values)),
            crawl_policy_sha256=crawl_policy_sha256,
        )


@dataclass(frozen=True, slots=True)
class Classification:
    kind: ClassificationKind
    review_state: ReviewState = ReviewState.PROPOSED_PRESERVE
    redirect_target: str = ""
    owner: str = ""
    reason: str = ""
    test_reference: str = ""

    def __post_init__(self) -> None:
        if type(self.kind) is not ClassificationKind:
            raise ManifestValidationError("classification_kind_must_be_enum")
        if type(self.review_state) is not ReviewState:
            raise ManifestValidationError("classification_review_state_must_be_enum")
        target = _require_exact_string(self.redirect_target, "classification_redirect_target")
        owner = _require_exact_string(self.owner, "classification_owner")
        reason = _require_exact_string(self.reason, "classification_reason")
        test_reference = _require_exact_string(
            self.test_reference,
            "classification_test_reference",
        )
        if self.kind is ClassificationKind.PRESERVE:
            if self.review_state not in {
                ReviewState.PROPOSED_PRESERVE,
                ReviewState.APPROVED_PARITY,
            }:
                raise ManifestValidationError("preserve_classification_has_invalid_review_state")
            if target or owner or reason or test_reference:
                raise ManifestValidationError(
                    "preserve_classification_must_not_have_exception_fields"
                )
            return
        if self.review_state is not ReviewState.APPROVED_EXCEPTION:
            raise ManifestValidationError("exception_classification_must_be_explicitly_approved")
        if not owner or not reason or not test_reference:
            raise ManifestValidationError("exception_classification_requires_owner_reason_and_test")
        if self.kind is ClassificationKind.REDIRECT:
            _validate_http_url(target, "classification_redirect_target")
        elif target:
            raise ManifestValidationError("retire_classification_must_not_have_redirect_target")

    @classmethod
    def preserve(cls, *, approved: bool = False) -> Self:
        return cls(
            ClassificationKind.PRESERVE,
            review_state=(
                ReviewState.APPROVED_PARITY if approved else ReviewState.PROPOSED_PRESERVE
            ),
        )

    @classmethod
    def redirect(
        cls,
        target: str,
        *,
        owner: str,
        reason: str,
        test_reference: str,
    ) -> Self:
        return cls(
            ClassificationKind.REDIRECT,
            review_state=ReviewState.APPROVED_EXCEPTION,
            redirect_target=target,
            owner=owner,
            reason=reason,
            test_reference=test_reference,
        )

    @classmethod
    def retire(cls, *, owner: str, reason: str, test_reference: str) -> Self:
        return cls(
            ClassificationKind.RETIRE,
            review_state=ReviewState.APPROVED_EXCEPTION,
            owner=owner,
            reason=reason,
            test_reference=test_reference,
        )


@dataclass(frozen=True, slots=True)
class CompatibilityRow:
    classification: Classification
    source_capture: Capture | None = None
    production_capture: Capture | None = None

    def __post_init__(self) -> None:
        if type(self.classification) is not Classification:
            raise ManifestValidationError("row_classification_must_be_classification")
        if self.source_capture is None and self.production_capture is None:
            raise ManifestValidationError("row_requires_a_capture")
        if self.source_capture is not None:
            if type(self.source_capture) is not Capture:
                raise ManifestValidationError("row_source_capture_must_be_capture")
            if self.source_capture.origin is not ObservationOrigin.SOURCE:
                raise ManifestValidationError("row_source_capture_has_wrong_origin")
        if self.production_capture is not None:
            if type(self.production_capture) is not Capture:
                raise ManifestValidationError("row_production_capture_must_be_capture")
            if self.production_capture.origin is not ObservationOrigin.PRODUCTION:
                raise ManifestValidationError("row_production_capture_has_wrong_origin")
        if (
            self.source_capture is not None
            and self.production_capture is not None
            and self.source_capture.requested_url != self.production_capture.requested_url
        ):
            raise ManifestValidationError("row_capture_urls_must_match_exactly")
        if self.classification.kind is ClassificationKind.REDIRECT:
            target = self.classification.redirect_target
            if target == self.public_url:
                raise ManifestValidationError("redirect_target_must_differ_from_source")
            source_path = urlsplit(self.public_url).path
            target_parts = urlsplit(target)
            if source_path not in {"", "/"} and target_parts.path in {"", "/", "/index.html"}:
                raise ManifestValidationError("redirect_to_homepage_is_forbidden")
            production = self.production_capture
            if production is None:
                raise ManifestValidationError("redirect_classification_requires_production_capture")
            if len(production.redirect_chain) != 1 or production.final_url != target:
                raise ManifestValidationError("redirect_classification_requires_exactly_one_hop")
            if production.redirect_chain[0].status not in {301, 308}:
                raise ManifestValidationError("redirect_classification_requires_permanent_status")
            if not 200 <= production.status <= 299:
                raise ManifestValidationError("redirect_classification_requires_successful_target")
        elif self.classification.kind is ClassificationKind.RETIRE:
            production = self.production_capture
            if production is None or production.status != 410:
                raise ManifestValidationError("retire_classification_requires_production_410")
            if production.redirect_chain:
                raise ManifestValidationError("retire_classification_must_not_redirect")

    @property
    def public_url(self) -> str:
        capture = self.source_capture or self.production_capture
        assert capture is not None
        return capture.requested_url


def dumps_jsonl(provenance: ManifestProvenance, rows: Iterable[CompatibilityRow]) -> str:
    """Serialize one manifest to canonical, line-oriented JSON.

    The provenance record is always first. Rows are ordered by exact public URL,
    making equivalent runs byte-for-byte comparable regardless of input order.
    """

    if type(provenance) is not ManifestProvenance:
        raise ManifestValidationError("manifest_provenance_must_be_provenance")
    row_tuple = tuple(rows)
    if any(type(row) is not CompatibilityRow for row in row_tuple):
        raise ManifestValidationError("manifest_rows_must_contain_compatibility_rows")
    ordered_rows = tuple(sorted(row_tuple, key=lambda row: row.public_url))
    _validate_manifest_redirect_targets(ordered_rows)
    urls = [row.public_url for row in ordered_rows]
    if len(urls) != len(set(urls)):
        raise ManifestValidationError("manifest_rows_must_have_unique_public_urls")
    records = [_encode_provenance(provenance), *(_encode_row(row) for row in ordered_rows)]
    _validate_json_values(records)
    return "".join(
        json.dumps(
            record,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
        for record in records
    )


def loads_jsonl(text: str) -> tuple[ManifestProvenance, tuple[CompatibilityRow, ...]]:
    """Parse the exact schema emitted by :func:`dumps_jsonl`."""

    if type(text) is not str:
        raise ManifestDecodeError("manifest_jsonl_must_be_string")
    if not text:
        raise ManifestDecodeError("manifest_jsonl_must_not_be_empty")
    if not text.endswith("\n") or "\r" in text:
        raise ManifestDecodeError("manifest_jsonl_is_not_canonical")
    lines = text.splitlines()
    if not lines or any(not line for line in lines):
        raise ManifestDecodeError("manifest_jsonl_must_not_contain_blank_lines")
    decoded: list[dict[str, object]] = []
    for line_number, line in enumerate(lines, start=1):
        try:
            value = json.loads(
                line,
                parse_constant=_reject_json_constant,
                object_pairs_hook=_strict_json_object,
            )
        except (json.JSONDecodeError, ManifestDecodeError) as exc:
            raise ManifestDecodeError(f"manifest_jsonl_line_{line_number}_is_invalid") from exc
        if type(value) is not dict:
            raise ManifestDecodeError(f"manifest_jsonl_line_{line_number}_must_be_object")
        decoded.append(value)
    if decoded[0].get("record_kind") != "provenance":
        raise ManifestDecodeError("manifest_jsonl_first_record_must_be_provenance")
    if any(record.get("record_kind") == "provenance" for record in decoded[1:]):
        raise ManifestDecodeError("manifest_jsonl_must_have_one_provenance_record")
    try:
        provenance = _decode_provenance(decoded[0])
        rows = tuple(_decode_row(record) for record in decoded[1:])
    except ManifestDecodeError:
        raise
    except ManifestValidationError as exc:
        raise ManifestDecodeError(str(exc)) from exc
    urls = [row.public_url for row in rows]
    if urls != sorted(urls) or len(urls) != len(set(urls)):
        raise ManifestDecodeError("manifest_rows_must_be_unique_and_sorted")
    # Re-encoding is a final canonicality check: semantically equivalent but
    # unstable representations are rejected instead of silently normalized.
    canonical = dumps_jsonl(provenance, rows)
    if text != canonical:
        raise ManifestDecodeError("manifest_jsonl_is_not_canonical")
    return provenance, rows


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestDecodeError("manifest_json_object_has_duplicate_key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ManifestDecodeError(f"manifest_json_contains_non_finite_number:{value}")


def _expect_keys(record: Mapping[str, object], expected: set[str], record_name: str) -> None:
    actual = set(record)
    if actual != expected:
        raise ManifestDecodeError(f"{record_name}_has_unexpected_keys")


def _enum_value(enum_type: type[StrEnum], value: object, field_name: str) -> StrEnum:
    if type(value) is not str:
        raise ManifestDecodeError(f"{field_name}_must_be_string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ManifestDecodeError(f"{field_name}_is_unknown") from exc


def _decode_tuple(value: object, field_name: str) -> tuple[object, ...]:
    if type(value) is not list:
        raise ManifestDecodeError(f"{field_name}_must_be_array")
    return tuple(value)


def _encode_provenance(provenance: ManifestProvenance) -> dict[str, object]:
    return {
        "record_kind": "provenance",
        "schema_version": provenance.schema_version,
        "generated_at": provenance.generated_at,
        "tool_version": provenance.tool_version,
        "crawl_policy_sha256": provenance.crawl_policy_sha256,
        "production_origins": list(provenance.production_origins),
        "allowlisted_hosts": list(provenance.allowlisted_hosts),
        "source_revisions": [
            {
                "source_id": revision.source_id,
                "repository": revision.repository,
                "revision": revision.revision,
            }
            for revision in provenance.source_revisions
        ],
    }


def _decode_provenance(record: Mapping[str, object]) -> ManifestProvenance:
    _expect_keys(
        record,
        {
            "record_kind",
            "schema_version",
            "generated_at",
            "tool_version",
            "crawl_policy_sha256",
            "production_origins",
            "allowlisted_hosts",
            "source_revisions",
        },
        "provenance_record",
    )
    if record["record_kind"] != "provenance":
        raise ManifestDecodeError("provenance_record_has_wrong_kind")
    revisions: list[SourceRevision] = []
    for item in _decode_tuple(record["source_revisions"], "provenance_source_revisions"):
        if type(item) is not dict:
            raise ManifestDecodeError("source_revision_must_be_object")
        _expect_keys(item, {"source_id", "repository", "revision"}, "source_revision")
        revisions.append(
            SourceRevision(
                source_id=item["source_id"],  # type: ignore[arg-type]
                repository=item["repository"],  # type: ignore[arg-type]
                revision=item["revision"],  # type: ignore[arg-type]
            )
        )
    return ManifestProvenance(
        generated_at=record["generated_at"],  # type: ignore[arg-type]
        tool_version=record["tool_version"],  # type: ignore[arg-type]
        source_revisions=tuple(revisions),
        production_origins=_decode_tuple(  # type: ignore[arg-type]
            record["production_origins"],
            "manifest_production_origins",
        ),
        allowlisted_hosts=_decode_tuple(  # type: ignore[arg-type]
            record["allowlisted_hosts"],
            "manifest_allowlisted_hosts",
        ),
        crawl_policy_sha256=record["crawl_policy_sha256"],  # type: ignore[arg-type]
        schema_version=record["schema_version"],  # type: ignore[arg-type]
    )


def _encode_row(row: CompatibilityRow) -> dict[str, object]:
    return {
        "record_kind": "compatibility_row",
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "classification": _encode_classification(row.classification),
        "source_capture": _encode_capture(row.source_capture) if row.source_capture else None,
        "production_capture": (
            _encode_capture(row.production_capture) if row.production_capture else None
        ),
    }


def _decode_row(record: Mapping[str, object]) -> CompatibilityRow:
    _expect_keys(
        record,
        {
            "record_kind",
            "schema_version",
            "classification",
            "source_capture",
            "production_capture",
        },
        "compatibility_row",
    )
    if record["record_kind"] != "compatibility_row":
        raise ManifestDecodeError("compatibility_row_has_wrong_kind")
    if (
        record["schema_version"] != MANIFEST_SCHEMA_VERSION
        or type(record["schema_version"]) is not int
    ):
        raise ManifestDecodeError("unsupported_manifest_schema_version")
    classification_record = record["classification"]
    if type(classification_record) is not dict:
        raise ManifestDecodeError("classification_must_be_object")
    source_record = record["source_capture"]
    production_record = record["production_capture"]
    if source_record is not None and type(source_record) is not dict:
        raise ManifestDecodeError("source_capture_must_be_object_or_null")
    if production_record is not None and type(production_record) is not dict:
        raise ManifestDecodeError("production_capture_must_be_object_or_null")
    return CompatibilityRow(
        classification=_decode_classification(classification_record),
        source_capture=_decode_capture(source_record) if source_record is not None else None,
        production_capture=(
            _decode_capture(production_record) if production_record is not None else None
        ),
    )


def _encode_classification(classification: Classification) -> dict[str, object]:
    return {
        "kind": classification.kind.value,
        "review_state": classification.review_state.value,
        "redirect_target": classification.redirect_target,
        "owner": classification.owner,
        "reason": classification.reason,
        "test_reference": classification.test_reference,
    }


def _decode_classification(record: Mapping[str, object]) -> Classification:
    _expect_keys(
        record,
        {"kind", "review_state", "redirect_target", "owner", "reason", "test_reference"},
        "classification",
    )
    return Classification(
        kind=_enum_value(  # type: ignore[arg-type]
            ClassificationKind,
            record["kind"],
            "classification_kind",
        ),
        review_state=_enum_value(  # type: ignore[arg-type]
            ReviewState,
            record["review_state"],
            "classification_review_state",
        ),
        redirect_target=record["redirect_target"],  # type: ignore[arg-type]
        owner=record["owner"],  # type: ignore[arg-type]
        reason=record["reason"],  # type: ignore[arg-type]
        test_reference=record["test_reference"],  # type: ignore[arg-type]
    )


def _validate_manifest_redirect_targets(rows: Sequence[CompatibilityRow]) -> None:
    homepage_targets: dict[str, list[str]] = {}
    for row in rows:
        if row.classification.kind is not ClassificationKind.REDIRECT:
            continue
        target = urlsplit(row.classification.redirect_target)
        if target.path not in {"", "/", "/index.html"}:
            continue
        origin = f"{target.scheme}://{target.netloc}"
        homepage_targets.setdefault(origin, []).append(row.public_url)
    if any(len(urls) > 1 for urls in homepage_targets.values()):
        raise ManifestValidationError("manifest_redirects_must_not_collapse_to_homepage")


def _encode_capture(capture: Capture) -> dict[str, object]:
    return {
        "origin": capture.origin.value,
        "requested_url": capture.requested_url,
        "status": capture.status,
        "final_url": capture.final_url,
        "response_count": capture.response_count,
        "transfer_bytes": capture.transfer_bytes,
        "error_code": capture.error_code,
        "source_repository": capture.source_repository,
        "source_path": capture.source_path,
        "content_type": capture.content_type,
        "response_last_modified": capture.response_last_modified,
        "response_content_language": capture.response_content_language,
        "response_robots": list(capture.response_robots),
        "body_sha256": capture.body_sha256,
        "redirect_chain": [
            {"status": hop.status, "url": hop.url} for hop in capture.redirect_chain
        ],
        "metadata": _encode_metadata(capture.metadata),
        "sitemap": _encode_sitemap(capture.sitemap),
    }


def _decode_capture(record: Mapping[str, object]) -> Capture:
    expected = {field.name for field in fields(Capture)}
    _expect_keys(record, expected, "capture")
    metadata_record = record["metadata"]
    sitemap_record = record["sitemap"]
    if type(metadata_record) is not dict:
        raise ManifestDecodeError("capture_metadata_must_be_object")
    if type(sitemap_record) is not dict:
        raise ManifestDecodeError("capture_sitemap_must_be_object")
    redirect_chain: list[RedirectHop] = []
    for item in _decode_tuple(record["redirect_chain"], "capture_redirect_chain"):
        if type(item) is not dict:
            raise ManifestDecodeError("redirect_hop_must_be_object")
        _expect_keys(item, {"status", "url"}, "redirect_hop")
        redirect_chain.append(
            RedirectHop(
                status=item["status"],  # type: ignore[arg-type]
                url=item["url"],  # type: ignore[arg-type]
            )
        )
    return Capture(
        origin=_enum_value(ObservationOrigin, record["origin"], "capture_origin"),  # type: ignore[arg-type]
        requested_url=record["requested_url"],  # type: ignore[arg-type]
        status=record["status"],  # type: ignore[arg-type]
        final_url=record["final_url"],  # type: ignore[arg-type]
        response_count=record["response_count"],  # type: ignore[arg-type]
        transfer_bytes=record["transfer_bytes"],  # type: ignore[arg-type]
        error_code=record["error_code"],  # type: ignore[arg-type]
        source_repository=record["source_repository"],  # type: ignore[arg-type]
        source_path=record["source_path"],  # type: ignore[arg-type]
        content_type=record["content_type"],  # type: ignore[arg-type]
        response_last_modified=record["response_last_modified"],  # type: ignore[arg-type]
        response_content_language=record["response_content_language"],  # type: ignore[arg-type]
        response_robots=_decode_tuple(  # type: ignore[arg-type]
            record["response_robots"],
            "capture_response_robots",
        ),
        body_sha256=record["body_sha256"],  # type: ignore[arg-type]
        redirect_chain=tuple(redirect_chain),
        metadata=_decode_metadata(metadata_record),
        sitemap=_decode_sitemap(sitemap_record),
    )


def _encode_metadata(metadata: PageMetadata) -> dict[str, object]:
    return {
        "title": metadata.title,
        "description": metadata.description,
        "first_heading": metadata.first_heading,
        "language": metadata.language,
        "robots": list(metadata.robots),
        "canonical_url": metadata.canonical_url,
        "client_redirect_url": metadata.client_redirect_url,
        "alternates": [list(item) for item in metadata.alternates],
        "social_metadata": [list(item) for item in metadata.social_metadata],
        "structured_data": [
            {"type": item.type, "identifier": item.identifier} for item in metadata.structured_data
        ],
        "fragments": list(metadata.fragments),
        "references": [{"kind": item.kind.value, "url": item.url} for item in metadata.references],
        "main_content_fingerprint": metadata.main_content_fingerprint,
        "soft_404": metadata.soft_404,
    }


def _decode_metadata(record: Mapping[str, object]) -> PageMetadata:
    expected = {field.name for field in fields(PageMetadata)}
    _expect_keys(record, expected, "page_metadata")
    alternates = _decode_string_pairs(record["alternates"], "page_alternates")
    social_metadata = _decode_string_pairs(
        record["social_metadata"],
        "page_social_metadata",
    )
    structured: list[StructuredData] = []
    for item in _decode_tuple(record["structured_data"], "page_structured_data"):
        if type(item) is not dict:
            raise ManifestDecodeError("structured_data_must_be_object")
        _expect_keys(item, {"type", "identifier"}, "structured_data")
        structured.append(
            StructuredData(
                type=item["type"],  # type: ignore[arg-type]
                identifier=item["identifier"],  # type: ignore[arg-type]
            )
        )
    references: list[Reference] = []
    for item in _decode_tuple(record["references"], "page_references"):
        if type(item) is not dict:
            raise ManifestDecodeError("reference_must_be_object")
        _expect_keys(item, {"kind", "url"}, "reference")
        references.append(
            Reference(
                kind=_enum_value(ReferenceKind, item["kind"], "reference_kind"),  # type: ignore[arg-type]
                url=item["url"],  # type: ignore[arg-type]
            )
        )
    return PageMetadata(
        title=record["title"],  # type: ignore[arg-type]
        description=record["description"],  # type: ignore[arg-type]
        first_heading=record["first_heading"],  # type: ignore[arg-type]
        language=record["language"],  # type: ignore[arg-type]
        robots=_decode_tuple(record["robots"], "page_robots"),  # type: ignore[arg-type]
        canonical_url=record["canonical_url"],  # type: ignore[arg-type]
        client_redirect_url=record["client_redirect_url"],  # type: ignore[arg-type]
        alternates=alternates,
        social_metadata=social_metadata,
        structured_data=tuple(structured),
        fragments=_decode_tuple(record["fragments"], "page_fragments"),  # type: ignore[arg-type]
        references=tuple(references),
        main_content_fingerprint=record["main_content_fingerprint"],  # type: ignore[arg-type]
        soft_404=record["soft_404"],  # type: ignore[arg-type]
    )


def _decode_string_pairs(value: object, field_name: str) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for item in _decode_tuple(value, field_name):
        if type(item) is not list or len(item) != 2:
            raise ManifestDecodeError(f"{field_name}_must_contain_string_pairs")
        result.append((item[0], item[1]))  # type: ignore[arg-type]
    return tuple(result)


def _encode_sitemap(sitemap: SitemapState) -> dict[str, object]:
    return {"entries": [{"url": entry.url, "lastmod": entry.lastmod} for entry in sitemap.entries]}


def _decode_sitemap(record: Mapping[str, object]) -> SitemapState:
    _expect_keys(record, {"entries"}, "sitemap")
    entries: list[SitemapEntry] = []
    for item in _decode_tuple(record["entries"], "sitemap_entries"):
        if type(item) is not dict:
            raise ManifestDecodeError("sitemap_entry_must_be_object")
        _expect_keys(item, {"url", "lastmod"}, "sitemap_entry")
        entries.append(
            SitemapEntry(
                url=item["url"],  # type: ignore[arg-type]
                lastmod=item["lastmod"],  # type: ignore[arg-type]
            )
        )
    return SitemapState(tuple(entries))


def _validate_json_values(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ManifestValidationError("manifest_contains_non_finite_number")
    if isinstance(value, str):
        _reject_private_value(value, "manifest")
    elif isinstance(value, Mapping):
        for key, nested in value.items():
            _reject_private_value(key, "manifest_key")
            _validate_json_values(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            _validate_json_values(nested)


__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "Capture",
    "Classification",
    "ClassificationKind",
    "CompatibilityRow",
    "ManifestDecodeError",
    "ManifestProvenance",
    "ManifestValidationError",
    "ObservationOrigin",
    "PageMetadata",
    "Reference",
    "ReferenceKind",
    "RedirectHop",
    "SitemapEntry",
    "SitemapState",
    "SourceRevision",
    "StructuredData",
    "dumps_jsonl",
    "loads_jsonl",
]
