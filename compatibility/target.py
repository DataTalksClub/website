"""Strict observations of one target Django application's public response."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from compatibility.expectations import validate_response_metadata_bounds
from compatibility.models import (
    Capture,
    ManifestValidationError,
    ObservationOrigin,
    PageMetadata,
    RedirectHop,
    Reference,
    ReferenceKind,
    SitemapState,
    decode_page_metadata,
    decode_sitemap_state,
    encode_page_metadata,
    encode_sitemap_state,
)
from compatibility.redaction import url_contains_unredacted_sensitive_value
from compatibility.schema import RecordSchemaError, load_schema, validate_record

TARGET_OBSERVATION_SCHEMA_VERSION = 1
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_TARGET_OBSERVATION_SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "_docs"
    / "compatibility"
    / "target-observation.schema.json"
)
MAX_TARGET_OBSERVATIONS = 10_000
MAX_URL_LENGTH = 8_192
MAX_METADATA_ITEMS = 100_000


def _bounded_text(value: object, field: str, maximum: int) -> str:
    if type(value) is not str:
        raise TargetObservationError(f"{field}_must_be_string")
    if len(value) > maximum:
        raise TargetObservationError(f"{field}_is_too_long")
    if any(ord(character) < 0x20 for character in value):
        raise TargetObservationError(f"{field}_contains_control_character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise TargetObservationError(f"{field}_contains_invalid_unicode") from exc
    return value


class TargetObservationError(ValueError):
    """A target observation is unsafe, ambiguous, or malformed."""


@dataclass(frozen=True, slots=True)
class TargetObservation:
    """One bounded in-process HTTP observation without approval semantics.

    ``raw_network_reference`` retains the exact path and query spelling supplied to
    Django.  ``request.path_info`` cannot prove percent-escape identity because WSGI
    exposes a decoded path.
    """

    requested_url: str
    raw_network_reference: str
    status: int
    final_url: str
    response_count: int
    transfer_bytes: int
    content_type: str = ""
    response_last_modified: str = ""
    response_content_language: str = ""
    response_robots: tuple[str, ...] = ()
    response_location: str = ""
    body_sha256: str = ""
    redirect_chain: tuple[RedirectHop, ...] = ()
    metadata: PageMetadata = PageMetadata()
    sitemap: SitemapState = SitemapState()
    capture_error: str = ""
    elapsed_ms: int = 0
    schema_version: int = TARGET_OBSERVATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise TargetObservationError("unsupported_target_observation_schema_version")
        _bounded_text(self.requested_url, "requested_url", MAX_URL_LENGTH)
        _bounded_text(self.final_url, "final_url", MAX_URL_LENGTH)
        _bounded_text(self.raw_network_reference, "raw_network_reference", MAX_URL_LENGTH)
        _bounded_text(self.content_type, "content_type", 255)
        _bounded_text(self.response_last_modified, "response_last_modified", 256)
        _bounded_text(self.response_content_language, "response_content_language", 128)
        _bounded_text(self.response_location, "response_location", MAX_URL_LENGTH)
        _bounded_text(self.body_sha256, "body_sha256", 64)
        if (
            not self.raw_network_reference.startswith("/")
            or self.raw_network_reference.startswith("//")
            or "#" in self.raw_network_reference
            or any(character.isspace() for character in self.raw_network_reference)
            or any(ord(character) < 0x20 for character in self.raw_network_reference)
        ):
            raise TargetObservationError("raw_network_reference_is_invalid")
        requested = urlsplit(self.requested_url)
        if requested.fragment:
            raise TargetObservationError("requested_url_must_not_have_fragment")
        expected_reference = requested.path or "/"
        if requested.query:
            expected_reference = f"{expected_reference}?{requested.query}"
        if self.raw_network_reference != expected_reference:
            raise TargetObservationError("raw_network_reference_does_not_match_requested_url")
        try:
            contains_private_data = url_contains_unredacted_sensitive_value(self.requested_url)
        except ValueError as exc:
            raise TargetObservationError("requested_url_contains_credentials") from exc
        if contains_private_data:
            raise TargetObservationError("requested_url_contains_private_data")
        if type(self.capture_error) is not str or (
            self.capture_error and _ERROR_CODE.fullmatch(self.capture_error) is None
        ):
            raise TargetObservationError("capture_error_is_invalid")
        if type(self.elapsed_ms) is not int or self.elapsed_ms < 0:
            raise TargetObservationError("elapsed_ms_must_be_nonnegative_integer")
        if type(self.response_count) is not int or self.response_count < 1:
            raise TargetObservationError("response_count_must_be_positive_integer")
        if type(self.transfer_bytes) is not int or self.transfer_bytes < 0:
            raise TargetObservationError("transfer_bytes_must_be_nonnegative_integer")
        if self.body_sha256 and _SHA256.fullmatch(self.body_sha256) is None:
            raise TargetObservationError("body_sha256_must_be_sha256")
        if self.response_location:
            try:
                Reference(
                    ReferenceKind.INTERNAL_LINK,
                    urljoin(self.requested_url, self.response_location),
                )
                contains_private_location = url_contains_unredacted_sensitive_value(
                    self.response_location
                )
            except (ManifestValidationError, ValueError) as exc:
                raise TargetObservationError("response_location_contains_credentials") from exc
            if contains_private_location:
                raise TargetObservationError("response_location_contains_private_data")
        if type(self.redirect_chain) is not tuple or any(
            type(hop) is not RedirectHop for hop in self.redirect_chain
        ):
            raise TargetObservationError("redirect_chain_must_contain_redirect_hops")
        if len(self.redirect_chain) > 16:
            raise TargetObservationError("redirect_chain_has_too_many_hops")
        if type(self.response_robots) is not tuple:
            raise TargetObservationError("response_robots_must_be_tuple")
        if len(self.response_robots) > MAX_METADATA_ITEMS:
            raise TargetObservationError("response_robots_has_too_many_items")
        for directive in self.response_robots:
            _bounded_text(directive, "response_robots_item", 16_384)
        if type(self.metadata) is not PageMetadata:
            raise TargetObservationError("metadata_must_be_page_metadata")
        if type(self.sitemap) is not SitemapState:
            raise TargetObservationError("sitemap_must_be_sitemap_state")
        try:
            validate_response_metadata_bounds(self.metadata, self.sitemap)
        except ValueError as exc:
            raise TargetObservationError(str(exc)) from exc

        # Reuse the #34 response vocabulary validation. A target capture is a public
        # response observation, not a source-tree observation.
        try:
            Capture(
                origin=ObservationOrigin.PRODUCTION,
                requested_url=self.requested_url,
                status=self.status,
                final_url=self.final_url,
                response_count=self.response_count,
                transfer_bytes=self.transfer_bytes,
                error_code="",
                content_type=self.content_type,
                response_last_modified=self.response_last_modified,
                response_content_language=self.response_content_language,
                response_robots=self.response_robots,
                body_sha256=self.body_sha256,
                redirect_chain=self.redirect_chain,
                metadata=self.metadata,
                sitemap=self.sitemap,
            )
        except ManifestValidationError as exc:
            raise TargetObservationError(str(exc)) from exc

    @property
    def public_url(self) -> str:
        return self.requested_url

    def as_capture(self) -> Capture:
        """Return the normalized #34 capture vocabulary used by comparisons."""

        if self.capture_error:
            raise TargetObservationError("failed_observation_cannot_become_capture")

        return Capture(
            origin=ObservationOrigin.PRODUCTION,
            requested_url=self.requested_url,
            status=self.status,
            final_url=self.final_url,
            response_count=self.response_count,
            transfer_bytes=self.transfer_bytes,
            content_type=self.content_type,
            response_last_modified=self.response_last_modified,
            response_content_language=self.response_content_language,
            response_robots=self.response_robots,
            body_sha256=self.body_sha256,
            redirect_chain=self.redirect_chain,
            metadata=self.metadata,
            sitemap=self.sitemap,
        )


def _encode_observation(value: TargetObservation) -> dict[str, object]:
    return {
        "schema_version": value.schema_version,
        "requested_url": value.requested_url,
        "raw_network_reference": value.raw_network_reference,
        "status": value.status,
        "final_url": value.final_url,
        "response_count": value.response_count,
        "transfer_bytes": value.transfer_bytes,
        "content_type": value.content_type,
        "response_last_modified": value.response_last_modified,
        "response_content_language": value.response_content_language,
        "response_robots": list(value.response_robots),
        "response_location": value.response_location,
        "body_sha256": value.body_sha256,
        "redirect_chain": [{"status": hop.status, "url": hop.url} for hop in value.redirect_chain],
        "metadata": encode_page_metadata(value.metadata),
        "sitemap": encode_sitemap_state(value.sitemap),
        "capture_error": value.capture_error,
        "elapsed_ms": value.elapsed_ms,
    }


def dumps_target_observations(observations: tuple[TargetObservation, ...]) -> str:
    """Serialize a canonically ordered, schema-validated observation document."""

    if type(observations) is not tuple or any(
        type(item) is not TargetObservation for item in observations
    ):
        raise TargetObservationError("observations_must_be_target_observation_tuple")
    if len(observations) > MAX_TARGET_OBSERVATIONS:
        raise TargetObservationError("target_observation_count_limit_exceeded")
    ordered = tuple(sorted(observations, key=lambda item: item.public_url))
    urls = [item.public_url for item in ordered]
    if len(urls) != len(set(urls)):
        raise TargetObservationError("target_observation_urls_must_be_unique")
    document = {
        "record_kind": "target_observation_set",
        "schema_version": TARGET_OBSERVATION_SCHEMA_VERSION,
        "observations": [_encode_observation(item) for item in ordered],
    }
    try:
        validate_record(document, load_schema(DEFAULT_TARGET_OBSERVATION_SCHEMA))
    except (OSError, json.JSONDecodeError, RecordSchemaError) as exc:
        raise TargetObservationError("target_observation_failed_schema_validation") from exc
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise TargetObservationError("target_observation_has_duplicate_key")
        result[key] = value
    return result


def _object(value: object, field: str) -> Mapping[str, object]:
    if type(value) is not dict:
        raise TargetObservationError(f"{field}_must_be_object")
    return value


def _array(value: object, field: str) -> list[object]:
    if type(value) is not list:
        raise TargetObservationError(f"{field}_must_be_array")
    return value


def _expect_keys(record: Mapping[str, object], keys: set[str], field: str) -> None:
    if set(record) != keys:
        raise TargetObservationError(f"{field}_has_wrong_fields")


def _decode_observation(record: Mapping[str, object]) -> TargetObservation:
    keys = {
        "schema_version",
        "requested_url",
        "raw_network_reference",
        "status",
        "final_url",
        "response_count",
        "transfer_bytes",
        "content_type",
        "response_last_modified",
        "response_content_language",
        "response_robots",
        "response_location",
        "body_sha256",
        "redirect_chain",
        "metadata",
        "sitemap",
        "capture_error",
        "elapsed_ms",
    }
    _expect_keys(record, keys, "target_observation")
    redirect_chain: list[RedirectHop] = []
    for item in _array(record["redirect_chain"], "redirect_chain"):
        hop = _object(item, "redirect_hop")
        _expect_keys(hop, {"status", "url"}, "redirect_hop")
        redirect_chain.append(
            RedirectHop(status=hop["status"], url=hop["url"])  # type: ignore[arg-type]
        )
    return TargetObservation(
        requested_url=record["requested_url"],  # type: ignore[arg-type]
        raw_network_reference=record["raw_network_reference"],  # type: ignore[arg-type]
        status=record["status"],  # type: ignore[arg-type]
        final_url=record["final_url"],  # type: ignore[arg-type]
        response_count=record["response_count"],  # type: ignore[arg-type]
        transfer_bytes=record["transfer_bytes"],  # type: ignore[arg-type]
        content_type=record["content_type"],  # type: ignore[arg-type]
        response_last_modified=record["response_last_modified"],  # type: ignore[arg-type]
        response_content_language=record["response_content_language"],  # type: ignore[arg-type]
        response_robots=tuple(_array(record["response_robots"], "response_robots")),  # type: ignore[arg-type]
        response_location=record["response_location"],  # type: ignore[arg-type]
        body_sha256=record["body_sha256"],  # type: ignore[arg-type]
        redirect_chain=tuple(redirect_chain),
        metadata=decode_page_metadata(_object(record["metadata"], "metadata")),
        sitemap=decode_sitemap_state(_object(record["sitemap"], "sitemap")),
        capture_error=record["capture_error"],  # type: ignore[arg-type]
        elapsed_ms=record["elapsed_ms"],  # type: ignore[arg-type]
        schema_version=record["schema_version"],  # type: ignore[arg-type]
    )


def loads_target_observations(text: str) -> tuple[TargetObservation, ...]:
    """Decode only the exact canonical observation document."""

    if type(text) is not str or not text.endswith("\n") or "\r" in text:
        raise TargetObservationError("target_observation_json_is_not_canonical")
    try:
        encoded_size = len(text.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise TargetObservationError("target_observation_json_contains_invalid_unicode") from exc
    if encoded_size > 128 * 1024 * 1024 or len(text.splitlines()) != 1:
        raise TargetObservationError("target_observation_json_is_not_canonical")
    try:
        value = json.loads(text, object_pairs_hook=_strict_object)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise TargetObservationError("target_observation_json_is_invalid") from exc
    document = _object(value, "target_observation_document")
    _expect_keys(
        document,
        {"record_kind", "schema_version", "observations"},
        "target_observation_document",
    )
    try:
        validate_record(document, load_schema(DEFAULT_TARGET_OBSERVATION_SCHEMA))
    except (OSError, json.JSONDecodeError, RecordSchemaError) as exc:
        raise TargetObservationError("target_observation_failed_schema_validation") from exc
    if document["record_kind"] != "target_observation_set":
        raise TargetObservationError("target_observation_record_kind_is_invalid")
    try:
        observations = tuple(
            _decode_observation(_object(item, "target_observation"))
            for item in _array(document["observations"], "observations")
        )
    except ManifestValidationError as exc:
        raise TargetObservationError(str(exc)) from exc
    if dumps_target_observations(observations) != text:
        raise TargetObservationError("target_observation_json_is_not_canonical")
    return observations
