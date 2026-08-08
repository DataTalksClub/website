"""Strict, independently approved target expectations for the parity gate.

The legacy manifest is observation evidence, not an approval ledger.  This
module deliberately keeps target expectations in a separate, digest-bound
artifact and never infers approval from a source or production capture.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urldefrag, urlsplit, urlunsplit

from compatibility.models import (
    Capture,
    ManifestValidationError,
    ObservationOrigin,
    PageMetadata,
    Reference,
    ReferenceKind,
    ReviewState,
    SitemapEntry,
    SitemapState,
    StructuredData,
)
from compatibility.redaction import text_contains_unredacted_private_data
from compatibility.schema import RecordSchemaError, load_schema, validate_record

EXPECTATION_SCHEMA_VERSION = 1
DEFAULT_EXPECTATION_SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "_docs"
    / "compatibility"
    / "approved-expectation.schema.json"
)

MAX_EXPECTATIONS = 10_000
MAX_METADATA_ITEMS = 100_000
MAX_RESPONSE_TEXT_BYTES = 8 * 1024 * 1024
MAX_URL_LENGTH = 8_192
MAX_TEXT_LENGTH = 16_384

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STABLE_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_EXPECTATION_ID = re.compile(r"^expectation-[0-9a-f]{24}$")
_TEST_REFERENCE = re.compile(r"^[A-Za-z0-9_.:/\[\]-]{1,512}$")


class ExpectationValidationError(ValueError):
    """An approved target expectation is unsafe, ambiguous, or inconsistent."""


class ExpectationDecodeError(ExpectationValidationError):
    """Serialized expectation data is malformed or noncanonical."""


class Disposition(StrEnum):
    PRESERVE = "preserve"
    REDIRECT = "redirect"
    RETIRE = "retire"


class QueryPolicy(StrEnum):
    """How an approved redirect constructs its query component."""

    EXACT = "exact"
    PRESERVE_RAW = "preserve_raw"


def approved_expectation_id(source_scope: str, public_url: str) -> str:
    """Return the stable ID for one scope and exact public URL."""

    identity = json.dumps(
        [EXPECTATION_SCHEMA_VERSION, source_scope, public_url],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"expectation-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"


def _bounded_text(
    value: object,
    field: str,
    *,
    maximum: int = MAX_TEXT_LENGTH,
    allow_empty: bool = True,
    private: bool = True,
) -> str:
    if type(value) is not str:
        raise ExpectationValidationError(f"{field}_must_be_string")
    if not allow_empty and not value:
        raise ExpectationValidationError(f"{field}_must_not_be_empty")
    if value != value.strip():
        raise ExpectationValidationError(f"{field}_must_not_have_outer_whitespace")
    if len(value) > maximum:
        raise ExpectationValidationError(f"{field}_is_too_long")
    if any(ord(character) < 0x20 for character in value):
        raise ExpectationValidationError(f"{field}_contains_control_character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ExpectationValidationError(f"{field}_contains_invalid_unicode") from exc
    if private and text_contains_unredacted_private_data(value):
        raise ExpectationValidationError(f"{field}_contains_private_data")
    return value


def _digest(value: object, field: str) -> str:
    digest = _bounded_text(value, field, maximum=64, allow_empty=False)
    if _SHA256.fullmatch(digest) is None:
        raise ExpectationValidationError(f"{field}_must_be_sha256")
    return digest


def _stable_id(value: object, field: str) -> str:
    identifier = _bounded_text(value, field, maximum=128, allow_empty=False)
    if _STABLE_ID.fullmatch(identifier) is None:
        raise ExpectationValidationError(f"{field}_must_be_stable_identifier")
    return identifier


def _safe_https_url(value: object, field: str, *, origin_only: bool = False) -> str:
    url = _bounded_text(
        value,
        field,
        maximum=MAX_URL_LENGTH,
        allow_empty=False,
        private=False,
    )
    if any(character.isspace() for character in url):
        raise ExpectationValidationError(f"{field}_contains_raw_whitespace")
    try:
        Reference(ReferenceKind.INTERNAL_LINK, url)
        parsed = urlsplit(url)
        if parsed.port is not None and not 1 <= parsed.port <= 65_535:
            raise ValueError
    except (ManifestValidationError, ValueError) as exc:
        raise ExpectationValidationError(f"{field}_must_be_safe_https_url") from exc
    if parsed.scheme != "https" or not parsed.hostname:
        raise ExpectationValidationError(f"{field}_must_be_safe_https_url")
    if origin_only and (parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ExpectationValidationError(f"{field}_must_be_origin_only")
    return url


def _bounded_collection(value: tuple[object, ...], field: str) -> None:
    if len(value) > MAX_METADATA_ITEMS:
        raise ExpectationValidationError(f"{field}_has_too_many_items")


def _metadata_strings(metadata: PageMetadata) -> tuple[str, ...]:
    values = [
        metadata.title,
        metadata.description,
        metadata.first_heading,
        metadata.language,
        metadata.canonical_url,
        metadata.client_redirect_url,
        metadata.main_content_fingerprint,
        *metadata.robots,
        *metadata.fragments,
    ]
    values.extend(item for pair in metadata.alternates for item in pair)
    values.extend(item for pair in metadata.social_metadata for item in pair)
    values.extend(
        item for entry in metadata.structured_data for item in (entry.type, entry.identifier)
    )
    values.extend(entry.url for entry in metadata.references)
    return tuple(values)


def validate_response_metadata_bounds(metadata: PageMetadata, sitemap: SitemapState) -> None:
    for field, values in (
        ("metadata_robots", metadata.robots),
        ("metadata_alternates", metadata.alternates),
        ("metadata_social", metadata.social_metadata),
        ("metadata_structured", metadata.structured_data),
        ("metadata_fragments", metadata.fragments),
        ("metadata_references", metadata.references),
        ("sitemap_entries", sitemap.entries),
    ):
        _bounded_collection(values, field)  # type: ignore[arg-type]

    strings = [*_metadata_strings(metadata)]
    strings.extend(item for entry in sitemap.entries for item in (entry.url, entry.lastmod))
    total_bytes = 0
    for index, value in enumerate(strings):
        validated = _bounded_text(
            value,
            f"expected_response_text_{index}",
            allow_empty=True,
            private=True,
        )
        total_bytes += len(validated.encode("utf-8"))
        if total_bytes > MAX_RESPONSE_TEXT_BYTES:
            raise ExpectationValidationError("expected_response_metadata_is_too_large")


@dataclass(frozen=True, slots=True)
class ExpectedResponse:
    """Approved terminal response using the #34 metadata/sitemap vocabulary."""

    status: int
    final_url: str
    content_type: str = ""
    response_last_modified: str = ""
    response_content_language: str = ""
    response_robots: tuple[str, ...] = ()
    body_sha256: str = ""
    metadata: PageMetadata = PageMetadata()
    sitemap: SitemapState = SitemapState()

    def __post_init__(self) -> None:
        if type(self.metadata) is not PageMetadata:
            raise ExpectationValidationError("expected_response_metadata_must_be_page_metadata")
        if type(self.sitemap) is not SitemapState:
            raise ExpectationValidationError("expected_response_sitemap_must_be_sitemap_state")
        if type(self.response_robots) is not tuple:
            raise ExpectationValidationError("expected_response_robots_must_be_tuple")
        _safe_https_url(self.final_url, "expected_response_final_url")
        _bounded_text(self.content_type, "expected_response_content_type", maximum=255)
        _bounded_text(
            self.response_last_modified,
            "expected_response_last_modified",
            maximum=256,
        )
        _bounded_text(
            self.response_content_language,
            "expected_response_content_language",
            maximum=128,
        )
        _bounded_text(self.body_sha256, "expected_response_body_sha256", maximum=64)
        validate_response_metadata_bounds(self.metadata, self.sitemap)
        try:
            Capture(
                origin=ObservationOrigin.PRODUCTION,
                requested_url=self.final_url,
                status=self.status,
                final_url=self.final_url,
                response_count=1,
                transfer_bytes=0,
                content_type=self.content_type,
                response_last_modified=self.response_last_modified,
                response_content_language=self.response_content_language,
                response_robots=self.response_robots,
                body_sha256=self.body_sha256,
                metadata=self.metadata,
                sitemap=self.sitemap,
            )
        except ManifestValidationError as exc:
            raise ExpectationValidationError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class ApprovedExpectation:
    """One independently reviewed target behavior, separate from legacy evidence."""

    expectation_id: str
    source_scope: str
    public_url: str
    disposition: Disposition
    review_state: ReviewState
    expected_response: ExpectedResponse | None = None
    redirect_status: int | None = None
    redirect_target: str = ""
    query_policy: QueryPolicy | None = None
    owner: str = ""
    reason: str = ""
    test_reference: str = ""

    def __post_init__(self) -> None:
        scope = _stable_id(self.source_scope, "expectation_source_scope")
        public_url = _safe_https_url(self.public_url, "expectation_public_url")
        if urlsplit(public_url).fragment:
            raise ExpectationValidationError("expectation_public_url_must_not_have_fragment")
        identifier = _bounded_text(
            self.expectation_id,
            "expectation_id",
            maximum=36,
            allow_empty=False,
        )
        if _EXPECTATION_ID.fullmatch(identifier) is None:
            raise ExpectationValidationError("expectation_id_is_invalid")
        if identifier != approved_expectation_id(scope, public_url):
            raise ExpectationValidationError("expectation_id_is_not_canonical")
        if type(self.disposition) is not Disposition:
            raise ExpectationValidationError("expectation_disposition_must_be_enum")
        if type(self.review_state) is not ReviewState:
            raise ExpectationValidationError("expectation_review_state_must_be_enum")
        if (
            self.expected_response is not None
            and type(self.expected_response) is not ExpectedResponse
        ):
            raise ExpectationValidationError(
                "expectation_response_must_be_expected_response_or_none"
            )
        owner = _bounded_text(self.owner, "expectation_owner", maximum=128)
        reason = _bounded_text(self.reason, "expectation_reason", maximum=1_024)
        test_reference = _bounded_text(
            self.test_reference,
            "expectation_test_reference",
            maximum=512,
            private=True,
        )

        if self.disposition is Disposition.PRESERVE:
            if self.review_state is not ReviewState.APPROVED_PARITY:
                raise ExpectationValidationError("preserve_expectation_requires_approved_parity")
            if self.expected_response is None:
                raise ExpectationValidationError("preserve_expectation_requires_response")
            if (
                self.redirect_status is not None
                or self.redirect_target
                or self.query_policy is not None
                or owner
                or reason
                or test_reference
            ):
                raise ExpectationValidationError("preserve_expectation_has_exception_fields")
            if self.expected_response.final_url != public_url:
                raise ExpectationValidationError("preserve_response_must_end_at_public_url")
            if not 200 <= self.expected_response.status <= 499 or (
                300 <= self.expected_response.status <= 399
                or self.expected_response.status in {404, 410}
            ):
                raise ExpectationValidationError("preserve_response_status_is_not_approvable")
            self._validate_non_client_response()
            return

        if self.review_state is not ReviewState.APPROVED_EXCEPTION:
            raise ExpectationValidationError("exception_expectation_requires_approved_exception")
        if not owner or not reason or not test_reference:
            raise ExpectationValidationError("exception_expectation_requires_evidence")
        if _STABLE_ID.fullmatch(owner) is None:
            raise ExpectationValidationError("expectation_owner_must_be_stable_identifier")
        if _TEST_REFERENCE.fullmatch(test_reference) is None or ".." in test_reference:
            raise ExpectationValidationError("expectation_test_reference_is_invalid")

        if self.disposition is Disposition.REDIRECT:
            if self.redirect_status not in {301, 308} or type(self.redirect_status) is not int:
                raise ExpectationValidationError("redirect_expectation_requires_301_or_308")
            target = _safe_https_url(self.redirect_target, "expectation_redirect_target")
            if type(self.query_policy) is not QueryPolicy:
                raise ExpectationValidationError("redirect_expectation_requires_query_policy")
            if self.expected_response is None:
                raise ExpectationValidationError("redirect_expectation_requires_target_response")
            if self.query_policy is QueryPolicy.PRESERVE_RAW and urlsplit(target).query:
                raise ExpectationValidationError(
                    "preserve_raw_redirect_target_must_not_contain_query"
                )
            resolved_target = self.resolved_redirect_target
            if resolved_target == public_url:
                raise ExpectationValidationError("redirect_target_must_differ_from_public_url")
            source_path = urlsplit(public_url).path
            target_path = urlsplit(resolved_target).path
            if source_path not in {"", "/"} and target_path in {"", "/", "/index.html"}:
                raise ExpectationValidationError("redirect_to_homepage_is_forbidden")
            if self.expected_response.final_url != urldefrag(resolved_target)[0]:
                raise ExpectationValidationError("redirect_response_must_end_at_exact_target")
            if not 200 <= self.expected_response.status <= 299:
                raise ExpectationValidationError("redirect_target_response_must_be_successful")
            self._validate_non_client_response()
            return

        if (
            self.expected_response is not None
            or self.redirect_status is not None
            or self.redirect_target
            or self.query_policy is not None
        ):
            raise ExpectationValidationError("retire_expectation_must_be_direct_410")

    def _validate_non_client_response(self) -> None:
        assert self.expected_response is not None
        if self.expected_response.metadata.client_redirect_url:
            raise ExpectationValidationError("approved_response_must_not_use_client_redirect")
        if self.expected_response.metadata.soft_404:
            raise ExpectationValidationError("approved_response_must_not_be_soft_404")

    @property
    def resolved_redirect_target(self) -> str:
        if self.disposition is not Disposition.REDIRECT:
            return ""
        if self.query_policy is QueryPolicy.EXACT:
            return self.redirect_target
        source_query = urlsplit(self.public_url).query
        target = urlsplit(self.redirect_target)
        return urlunsplit(
            (target.scheme, target.netloc, target.path, source_query, target.fragment)
        )

    @classmethod
    def preserve(
        cls,
        *,
        source_scope: str,
        public_url: str,
        expected_response: ExpectedResponse,
    ) -> ApprovedExpectation:
        return cls(
            expectation_id=approved_expectation_id(source_scope, public_url),
            source_scope=source_scope,
            public_url=public_url,
            disposition=Disposition.PRESERVE,
            review_state=ReviewState.APPROVED_PARITY,
            expected_response=expected_response,
        )

    @classmethod
    def redirect(
        cls,
        *,
        source_scope: str,
        public_url: str,
        redirect_status: int,
        redirect_target: str,
        query_policy: QueryPolicy,
        expected_response: ExpectedResponse,
        owner: str,
        reason: str,
        test_reference: str,
    ) -> ApprovedExpectation:
        return cls(
            expectation_id=approved_expectation_id(source_scope, public_url),
            source_scope=source_scope,
            public_url=public_url,
            disposition=Disposition.REDIRECT,
            review_state=ReviewState.APPROVED_EXCEPTION,
            expected_response=expected_response,
            redirect_status=redirect_status,
            redirect_target=redirect_target,
            query_policy=query_policy,
            owner=owner,
            reason=reason,
            test_reference=test_reference,
        )

    @classmethod
    def retire(
        cls,
        *,
        source_scope: str,
        public_url: str,
        owner: str,
        reason: str,
        test_reference: str,
    ) -> ApprovedExpectation:
        return cls(
            expectation_id=approved_expectation_id(source_scope, public_url),
            source_scope=source_scope,
            public_url=public_url,
            disposition=Disposition.RETIRE,
            review_state=ReviewState.APPROVED_EXCEPTION,
            owner=owner,
            reason=reason,
            test_reference=test_reference,
        )


@dataclass(frozen=True, slots=True)
class ApprovedExpectationSet:
    """Canonical expectations bound to the exact three #34 input artifacts."""

    manifest_sha256: str
    differences_sha256: str
    public_contracts_sha256: str
    expectations: tuple[ApprovedExpectation, ...] = ()
    schema_version: int = EXPECTATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != EXPECTATION_SCHEMA_VERSION
        ):
            raise ExpectationValidationError("unsupported_expectation_schema_version")
        _digest(self.manifest_sha256, "expectation_manifest_sha256")
        _digest(self.differences_sha256, "expectation_differences_sha256")
        _digest(self.public_contracts_sha256, "expectation_public_contracts_sha256")
        if type(self.expectations) is not tuple:
            raise ExpectationValidationError("expectations_must_be_tuple")
        if len(self.expectations) > MAX_EXPECTATIONS:
            raise ExpectationValidationError("expectation_set_has_too_many_expectations")
        if any(type(item) is not ApprovedExpectation for item in self.expectations):
            raise ExpectationValidationError("expectations_must_contain_approved_expectations")
        ordered = tuple(
            sorted(
                self.expectations,
                key=lambda item: (item.source_scope, item.public_url, item.expectation_id),
            )
        )
        if self.expectations != ordered:
            raise ExpectationValidationError("expectations_must_be_canonically_sorted")
        ids = [item.expectation_id for item in self.expectations]
        urls = [item.public_url for item in self.expectations]
        if len(ids) != len(set(ids)):
            raise ExpectationValidationError("expectation_ids_must_be_unique")
        if len(urls) != len(set(urls)):
            raise ExpectationValidationError("expectation_public_urls_must_be_unique")

    @classmethod
    def create(
        cls,
        *,
        manifest_sha256: str,
        differences_sha256: str,
        public_contracts_sha256: str,
        expectations: tuple[ApprovedExpectation, ...] = (),
    ) -> ApprovedExpectationSet:
        return cls(
            manifest_sha256=manifest_sha256,
            differences_sha256=differences_sha256,
            public_contracts_sha256=public_contracts_sha256,
            expectations=tuple(
                sorted(
                    expectations,
                    key=lambda item: (item.source_scope, item.public_url, item.expectation_id),
                )
            ),
        )

    @property
    def sha256(self) -> str:
        return hashlib.sha256(dumps_expectations(self).encode()).hexdigest()

    @property
    def scopes(self) -> tuple[str, ...]:
        return tuple(sorted({item.source_scope for item in self.expectations}))

    def by_public_url(self) -> dict[str, ApprovedExpectation]:
        return {item.public_url: item for item in self.expectations}


def dumps_expectations(expectation_set: ApprovedExpectationSet) -> str:
    """Serialize one expectation set as canonical, schema-validated JSON."""

    if type(expectation_set) is not ApprovedExpectationSet:
        raise ExpectationValidationError("expectation_set_must_be_approved_expectation_set")
    record = _encode_expectation_set(expectation_set)
    try:
        validate_record(record, load_schema(DEFAULT_EXPECTATION_SCHEMA))
    except (OSError, json.JSONDecodeError, RecordSchemaError) as exc:
        raise ExpectationValidationError("expectation_set_failed_schema_validation") from exc
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


def loads_expectations(text: str) -> ApprovedExpectationSet:
    """Decode strict canonical JSON without accepting coercion or unknown data."""

    if type(text) is not str or not text or not text.endswith("\n") or "\r" in text:
        raise ExpectationDecodeError("expectation_json_is_not_canonical")
    try:
        encoded_size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ExpectationDecodeError("expectation_json_contains_invalid_unicode") from exc
    if encoded_size > 64 * 1024 * 1024 or len(text.splitlines()) != 1:
        raise ExpectationDecodeError("expectation_json_is_not_canonical")
    try:
        record = json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_strict_json_object,
        )
        if type(record) is not dict:
            raise ExpectationDecodeError("expectation_document_must_be_object")
        validate_record(record, load_schema(DEFAULT_EXPECTATION_SCHEMA))
        expectation_set = _decode_expectation_set(record)
    except ExpectationDecodeError:
        raise
    except (json.JSONDecodeError, OSError, RecordSchemaError, ExpectationValidationError) as exc:
        raise ExpectationDecodeError("expectation_document_is_invalid") from exc
    if dumps_expectations(expectation_set) != text:
        raise ExpectationDecodeError("expectation_json_is_not_canonical")
    return expectation_set


def _encode_expectation_set(value: ApprovedExpectationSet) -> dict[str, object]:
    return {
        "record_kind": "approved_expectation_set",
        "schema_version": value.schema_version,
        "manifest_sha256": value.manifest_sha256,
        "differences_sha256": value.differences_sha256,
        "public_contracts_sha256": value.public_contracts_sha256,
        "expectations": [_encode_expectation(item) for item in value.expectations],
    }


def _encode_expectation(value: ApprovedExpectation) -> dict[str, object]:
    return {
        "expectation_id": value.expectation_id,
        "source_scope": value.source_scope,
        "public_url": value.public_url,
        "disposition": value.disposition.value,
        "review_state": value.review_state.value,
        "expected_response": (
            _encode_expected_response(value.expected_response) if value.expected_response else None
        ),
        "redirect_status": value.redirect_status,
        "redirect_target": value.redirect_target,
        "query_policy": value.query_policy.value if value.query_policy else None,
        "owner": value.owner,
        "reason": value.reason,
        "test_reference": value.test_reference,
    }


def _encode_expected_response(value: ExpectedResponse) -> dict[str, object]:
    return {
        "status": value.status,
        "final_url": value.final_url,
        "content_type": value.content_type,
        "response_last_modified": value.response_last_modified,
        "response_content_language": value.response_content_language,
        "response_robots": list(value.response_robots),
        "body_sha256": value.body_sha256,
        "metadata": _encode_metadata(value.metadata),
        "sitemap": _encode_sitemap(value.sitemap),
    }


def _encode_metadata(value: PageMetadata) -> dict[str, object]:
    return {
        "title": value.title,
        "description": value.description,
        "first_heading": value.first_heading,
        "language": value.language,
        "robots": list(value.robots),
        "canonical_url": value.canonical_url,
        "client_redirect_url": value.client_redirect_url,
        "alternates": [list(item) for item in value.alternates],
        "social_metadata": [list(item) for item in value.social_metadata],
        "structured_data": [
            {"type": item.type, "identifier": item.identifier} for item in value.structured_data
        ],
        "fragments": list(value.fragments),
        "references": [{"kind": item.kind.value, "url": item.url} for item in value.references],
        "main_content_fingerprint": value.main_content_fingerprint,
        "soft_404": value.soft_404,
    }


def _encode_sitemap(value: SitemapState) -> dict[str, object]:
    return {"entries": [{"url": item.url, "lastmod": item.lastmod} for item in value.entries]}


def _decode_expectation_set(record: Mapping[str, object]) -> ApprovedExpectationSet:
    _expect_keys(
        record,
        {
            "record_kind",
            "schema_version",
            "manifest_sha256",
            "differences_sha256",
            "public_contracts_sha256",
            "expectations",
        },
        "expectation_set",
    )
    if record["record_kind"] != "approved_expectation_set":
        raise ExpectationDecodeError("expectation_record_kind_is_invalid")
    expectations = _array(record["expectations"], "expectations")
    return ApprovedExpectationSet(
        manifest_sha256=record["manifest_sha256"],  # type: ignore[arg-type]
        differences_sha256=record["differences_sha256"],  # type: ignore[arg-type]
        public_contracts_sha256=record["public_contracts_sha256"],  # type: ignore[arg-type]
        expectations=tuple(
            _decode_expectation(_object(item, "expectation")) for item in expectations
        ),
        schema_version=record["schema_version"],  # type: ignore[arg-type]
    )


def _decode_expectation(record: Mapping[str, object]) -> ApprovedExpectation:
    _expect_keys(
        record,
        {
            "expectation_id",
            "source_scope",
            "public_url",
            "disposition",
            "review_state",
            "expected_response",
            "redirect_status",
            "redirect_target",
            "query_policy",
            "owner",
            "reason",
            "test_reference",
        },
        "expectation",
    )
    response_record = record["expected_response"]
    query_policy = record["query_policy"]
    return ApprovedExpectation(
        expectation_id=record["expectation_id"],  # type: ignore[arg-type]
        source_scope=record["source_scope"],  # type: ignore[arg-type]
        public_url=record["public_url"],  # type: ignore[arg-type]
        disposition=_enum(Disposition, record["disposition"], "disposition"),
        review_state=_enum(ReviewState, record["review_state"], "review_state"),
        expected_response=(
            _decode_expected_response(_object(response_record, "expected_response"))
            if response_record is not None
            else None
        ),
        redirect_status=record["redirect_status"],  # type: ignore[arg-type]
        redirect_target=record["redirect_target"],  # type: ignore[arg-type]
        query_policy=(
            _enum(QueryPolicy, query_policy, "query_policy") if query_policy is not None else None
        ),
        owner=record["owner"],  # type: ignore[arg-type]
        reason=record["reason"],  # type: ignore[arg-type]
        test_reference=record["test_reference"],  # type: ignore[arg-type]
    )


def _decode_expected_response(record: Mapping[str, object]) -> ExpectedResponse:
    _expect_keys(
        record,
        {
            "status",
            "final_url",
            "content_type",
            "response_last_modified",
            "response_content_language",
            "response_robots",
            "body_sha256",
            "metadata",
            "sitemap",
        },
        "expected_response",
    )
    return ExpectedResponse(
        status=record["status"],  # type: ignore[arg-type]
        final_url=record["final_url"],  # type: ignore[arg-type]
        content_type=record["content_type"],  # type: ignore[arg-type]
        response_last_modified=record["response_last_modified"],  # type: ignore[arg-type]
        response_content_language=record["response_content_language"],  # type: ignore[arg-type]
        response_robots=tuple(_array(record["response_robots"], "response_robots")),  # type: ignore[arg-type]
        body_sha256=record["body_sha256"],  # type: ignore[arg-type]
        metadata=_decode_metadata(_object(record["metadata"], "metadata")),
        sitemap=_decode_sitemap(_object(record["sitemap"], "sitemap")),
    )


def _decode_metadata(record: Mapping[str, object]) -> PageMetadata:
    _expect_keys(
        record,
        {
            "title",
            "description",
            "first_heading",
            "language",
            "robots",
            "canonical_url",
            "client_redirect_url",
            "alternates",
            "social_metadata",
            "structured_data",
            "fragments",
            "references",
            "main_content_fingerprint",
            "soft_404",
        },
        "metadata",
    )
    structured_items: list[StructuredData] = []
    for value in _array(record["structured_data"], "structured_data"):
        item = _object(value, "structured_data_item")
        _expect_keys(item, {"type", "identifier"}, "structured_data_item")
        structured_items.append(
            StructuredData(
                type=item["type"],  # type: ignore[arg-type]
                identifier=item["identifier"],  # type: ignore[arg-type]
            )
        )
    reference_items: list[Reference] = []
    for value in _array(record["references"], "references"):
        item = _object(value, "reference")
        _expect_keys(item, {"kind", "url"}, "reference")
        reference_items.append(
            Reference(
                kind=_enum(ReferenceKind, item["kind"], "reference_kind"),
                url=item["url"],  # type: ignore[arg-type]
            )
        )
    return PageMetadata(
        title=record["title"],  # type: ignore[arg-type]
        description=record["description"],  # type: ignore[arg-type]
        first_heading=record["first_heading"],  # type: ignore[arg-type]
        language=record["language"],  # type: ignore[arg-type]
        robots=tuple(_array(record["robots"], "robots")),  # type: ignore[arg-type]
        canonical_url=record["canonical_url"],  # type: ignore[arg-type]
        client_redirect_url=record["client_redirect_url"],  # type: ignore[arg-type]
        alternates=_string_pairs(record["alternates"], "alternates"),
        social_metadata=_string_pairs(record["social_metadata"], "social_metadata"),
        structured_data=tuple(structured_items),
        fragments=tuple(_array(record["fragments"], "fragments")),  # type: ignore[arg-type]
        references=tuple(reference_items),
        main_content_fingerprint=record["main_content_fingerprint"],  # type: ignore[arg-type]
        soft_404=record["soft_404"],  # type: ignore[arg-type]
    )


def _decode_sitemap(record: Mapping[str, object]) -> SitemapState:
    _expect_keys(record, {"entries"}, "sitemap")
    entries: list[SitemapEntry] = []
    for value in _array(record["entries"], "sitemap_entries"):
        item = _object(value, "sitemap_entry")
        _expect_keys(item, {"url", "lastmod"}, "sitemap_entry")
        entries.append(
            SitemapEntry(
                url=item["url"],  # type: ignore[arg-type]
                lastmod=item["lastmod"],  # type: ignore[arg-type]
            )
        )
    return SitemapState(tuple(entries))


def _string_pairs(value: object, field: str) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for item in _array(value, field):
        if type(item) is not list or len(item) != 2 or any(type(part) is not str for part in item):
            raise ExpectationDecodeError(f"{field}_must_contain_string_pairs")
        result.append((item[0], item[1]))
    return tuple(result)


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ExpectationDecodeError("expectation_json_has_duplicate_key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ExpectationDecodeError(f"expectation_json_has_nonfinite_value:{value}")


def _expect_keys(record: Mapping[str, object], expected: set[str], field: str) -> None:
    if set(record) != expected:
        raise ExpectationDecodeError(f"{field}_has_unexpected_keys")


def _array(value: object, field: str) -> list[object]:
    if type(value) is not list:
        raise ExpectationDecodeError(f"{field}_must_be_array")
    return value


def _object(value: object, field: str) -> Mapping[str, object]:
    if type(value) is not dict:
        raise ExpectationDecodeError(f"{field}_must_be_object")
    return value


def _enum(enum_type: type[StrEnum], value: object, field: str):  # type: ignore[no-untyped-def]
    if type(value) is not str:
        raise ExpectationDecodeError(f"{field}_must_be_string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ExpectationDecodeError(f"{field}_is_unknown") from exc
