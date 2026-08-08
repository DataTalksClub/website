from __future__ import annotations

import copy
import io
import json
import re
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from compatibility.models import (
    MANIFEST_SCHEMA_VERSION,
    Capture,
    Classification,
    CompatibilityRow,
    ManifestProvenance,
    ObservationOrigin,
    PageMetadata,
    RedirectHop,
    Reference,
    ReferenceKind,
    SitemapEntry,
    SitemapState,
    SourceRevision,
    StructuredData,
    dumps_jsonl,
    loads_jsonl,
)
from compatibility.schema import validate_jsonl_records, validate_record

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "_docs" / "compatibility" / "legacy-manifest.schema.json"
)
REPOSITORY = "https://github.com/DataTalksClub/data-engineering-zoomcamp.git"
REVISION = "0123456789abcdef0123456789abcdef01234567"
SHA256 = "a" * 64


class SchemaValidationError(AssertionError):
    """The dependency-free test validator found a JSON Schema violation."""


def _json_equal(left: object, right: object) -> bool:
    """JSON Schema treats booleans and numbers as distinct JSON values."""

    return type(left) is type(right) and left == right


def _matches(value: object, schema: object, root: Mapping[str, Any]) -> bool:
    try:
        _validate(value, schema, root)
    except SchemaValidationError:
        return False
    return True


def _validate(
    value: object,
    raw_schema: object,
    root: Mapping[str, Any],
    path: str = "$",
) -> None:
    """Validate the draft-2020-12 keywords used by the committed record schema."""

    if raw_schema is False:
        raise SchemaValidationError(f"{path}: false schema")
    if raw_schema is True:
        return
    if not isinstance(raw_schema, Mapping):
        raise SchemaValidationError(f"{path}: schema must be an object")
    schema = raw_schema

    reference = schema.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            raise SchemaValidationError(f"{path}: unsupported reference")
        definition_name = reference.removeprefix("#/$defs/")
        definitions = root.get("$defs")
        if not isinstance(definitions, Mapping) or definition_name not in definitions:
            raise SchemaValidationError(f"{path}: unknown reference")
        _validate(value, definitions[definition_name], root, path)

    alternatives = schema.get("oneOf")
    if alternatives is not None:
        assert isinstance(alternatives, list)
        match_count = sum(_matches(value, candidate, root) for candidate in alternatives)
        if match_count != 1:
            raise SchemaValidationError(f"{path}: expected exactly one matching schema")

    combined = schema.get("allOf")
    if combined is not None:
        assert isinstance(combined, list)
        for candidate in combined:
            _validate(value, candidate, root, path)

    excluded = schema.get("not")
    if excluded is not None and _matches(value, excluded, root):
        raise SchemaValidationError(f"{path}: matched excluded schema")

    condition = schema.get("if")
    if condition is not None:
        selected = schema.get("then") if _matches(value, condition, root) else schema.get("else")
        if selected is not None:
            _validate(value, selected, root, path)

    if "const" in schema and not _json_equal(value, schema["const"]):
        raise SchemaValidationError(f"{path}: unexpected constant")
    if "enum" in schema:
        enum = schema["enum"]
        assert isinstance(enum, list)
        if not any(_json_equal(value, item) for item in enum):
            raise SchemaValidationError(f"{path}: value is not in enum")

    expected_type = schema.get("type")
    type_matches = {
        "array": isinstance(value, list),
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "null": value is None,
        "object": isinstance(value, dict),
        "string": isinstance(value, str),
    }
    if isinstance(expected_type, str) and not type_matches[expected_type]:
        raise SchemaValidationError(f"{path}: expected {expected_type}")

    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            raise SchemaValidationError(f"{path}: string is too short")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise SchemaValidationError(f"{path}: string does not match pattern")

    if type(value) is int:
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int) and value < minimum:
            raise SchemaValidationError(f"{path}: number is too small")
        if isinstance(maximum, int) and value > maximum:
            raise SchemaValidationError(f"{path}: number is too large")

    if isinstance(value, dict):
        required = schema.get("required", [])
        assert isinstance(required, list)
        missing = set(required) - set(value)
        if missing:
            raise SchemaValidationError(f"{path}: missing {sorted(missing)}")
        properties = schema.get("properties", {})
        assert isinstance(properties, Mapping)
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(properties)
            if extra:
                raise SchemaValidationError(f"{path}: extra {sorted(extra)}")
        for key, child_schema in properties.items():
            if key in value:
                _validate(value[key], child_schema, root, f"{path}.{key}")

    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        maximum_items = schema.get("maxItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            raise SchemaValidationError(f"{path}: array is too short")
        if isinstance(maximum_items, int) and len(value) > maximum_items:
            raise SchemaValidationError(f"{path}: array is too long")
        if schema.get("uniqueItems") is True:
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                raise SchemaValidationError(f"{path}: array items are not unique")
        prefix_items = schema.get("prefixItems", [])
        assert isinstance(prefix_items, list)
        for index, child_schema in enumerate(prefix_items):
            if index < len(value):
                _validate(value[index], child_schema, root, f"{path}[{index}]")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value[len(prefix_items) :], start=len(prefix_items)):
                _validate(item, item_schema, root, f"{path}[{index}]")


def _schema() -> dict[str, Any]:
    value = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _manifest() -> str:
    requested_url = "https://datatalks.club/legacy/docs.html"
    final_url = "https://datatalks.club/docs/"
    metadata = PageMetadata(
        title="Legacy docs",
        description="Legacy documentation",
        first_heading="Documentation",
        language="en",
        robots=("follow", "index"),
        canonical_url=final_url,
        alternates=(("en", final_url), ("es", "https://datatalks.club/es/docs/")),
        social_metadata=(("og:title", "Legacy docs"), ("twitter:card", "summary")),
        structured_data=(StructuredData("Article", "legacy-docs"),),
        fragments=("intro", "reference"),
        references=(
            Reference(ReferenceKind.ASSET, "https://datatalks.club/assets/docs.css"),
            Reference(ReferenceKind.INTERNAL_LINK, final_url),
        ),
        main_content_fingerprint=SHA256,
        soft_404=False,
    )
    sitemap = SitemapState((SitemapEntry(requested_url, "2026-08-08T06:30:00Z"),))
    source = Capture.create(
        origin=ObservationOrigin.SOURCE,
        requested_url=requested_url,
        status=200,
        source_repository=REPOSITORY,
        source_path="_site/legacy/docs.html",
        content_type="text/html; charset=utf-8",
        body_sha256="b" * 64,
        metadata=metadata,
        sitemap=sitemap,
    )
    production = Capture.create(
        origin=ObservationOrigin.PRODUCTION,
        requested_url=requested_url,
        status=200,
        final_url=final_url,
        content_type="text/html; charset=utf-8",
        response_last_modified="Sat, 08 Aug 2026 06:30:00 GMT",
        response_content_language="en",
        response_robots=("follow", "index"),
        body_sha256="c" * 64,
        redirect_chain=(RedirectHop(301, f"{final_url}#intro"),),
        metadata=metadata,
        sitemap=sitemap,
    )
    failure_url = "https://datatalks.club/offline.html"
    failed = Capture.create(
        origin=ObservationOrigin.PRODUCTION,
        requested_url=failure_url,
        status=0,
        error_code="network_timeout",
    )
    provenance = ManifestProvenance.create(
        generated_at="2026-08-08T06:30:00Z",
        tool_version="legacy-manifest/1",
        source_revisions=(SourceRevision("data-engineering-zoomcamp", REPOSITORY, REVISION),),
        production_origins=("https://datatalks.club",),
        allowlisted_hosts=("datatalks.club",),
        crawl_policy_sha256="d" * 64,
    )
    return dumps_jsonl(
        provenance,
        (
            CompatibilityRow(
                Classification.redirect(
                    final_url,
                    owner="web-team",
                    reason="Canonical documentation location",
                    test_reference="compatibility/tests/test_redirects.py::test_legacy_docs",
                ),
                source_capture=source,
                production_capture=production,
            ),
            CompatibilityRow(Classification.preserve(), production_capture=failed),
        ),
    )


def _records() -> list[dict[str, Any]]:
    return [json.loads(line) for line in io.StringIO(_manifest())]


def _record(kind: str) -> dict[str, Any]:
    return next(record for record in _records() if record["record_kind"] == kind)


def _redirect_record() -> dict[str, Any]:
    return next(
        record
        for record in _records()
        if record["record_kind"] == "compatibility_row"
        and record["classification"]["kind"] == "redirect"
    )


def _failure_record() -> dict[str, Any]:
    return next(
        record
        for record in _records()
        if record["record_kind"] == "compatibility_row"
        and record["production_capture"]["status"] == 0
    )


def _assert_invalid(record: object) -> None:
    with pytest.raises(SchemaValidationError):
        _validate(record, _schema(), _schema())


def test_schema_exactly_covers_every_serialized_model_field() -> None:
    definitions = _schema()["$defs"]
    expected = {
        "capture": {field.name for field in fields(Capture)},
        "classification": {field.name for field in fields(Classification)},
        "metadata": {field.name for field in fields(PageMetadata)},
        "redirectHop": {field.name for field in fields(RedirectHop)},
        "reference": {field.name for field in fields(Reference)},
        "sitemap": {field.name for field in fields(SitemapState)},
        "sitemapEntry": {field.name for field in fields(SitemapEntry)},
        "sourceRevision": {field.name for field in fields(SourceRevision)},
        "structuredData": {field.name for field in fields(StructuredData)},
        "provenance": {
            "record_kind",
            *(field.name for field in fields(ManifestProvenance)),
        },
        "row": {
            "record_kind",
            "schema_version",
            *(field.name for field in fields(CompatibilityRow)),
        },
    }

    for definition_name, field_names in expected.items():
        definition = definitions[definition_name]
        assert set(definition["properties"]) == field_names
        assert set(definition["required"]) == field_names
        assert definition["additionalProperties"] is False
    assert definitions["provenance"]["properties"]["schema_version"]["const"] == (
        MANIFEST_SCHEMA_VERSION
    )
    assert definitions["row"]["properties"]["schema_version"]["const"] == (MANIFEST_SCHEMA_VERSION)


def test_each_jsonl_record_is_independently_stream_validatable() -> None:
    schema = _schema()
    stream = io.StringIO(_manifest())
    records_seen = 0

    for line in stream:
        record = json.loads(line)
        _validate(record, schema, schema)
        validate_record(record, schema)
        records_seen += 1

    provenance, rows = loads_jsonl(_manifest())
    assert records_seen == 3
    assert validate_jsonl_records(_manifest(), schema) == records_seen
    assert provenance.schema_version == MANIFEST_SCHEMA_VERSION
    assert len(rows) == 2
    redirect = next(row for row in rows if row.classification.kind.value == "redirect")
    assert redirect.production_capture is not None
    assert redirect.production_capture.redirect_chain[0].url.endswith("#intro")


def test_every_object_level_rejects_extra_fields() -> None:
    cases: tuple[tuple[dict[str, Any], tuple[str | int, ...]], ...] = (
        (_record("provenance"), ()),
        (_record("provenance"), ("source_revisions", 0)),
        (_redirect_record(), ()),
        (_redirect_record(), ("classification",)),
        (_redirect_record(), ("production_capture",)),
        (_redirect_record(), ("production_capture", "redirect_chain", 0)),
        (_redirect_record(), ("production_capture", "metadata")),
        (_redirect_record(), ("production_capture", "metadata", "structured_data", 0)),
        (_redirect_record(), ("production_capture", "metadata", "references", 0)),
        (_redirect_record(), ("production_capture", "sitemap")),
        (_redirect_record(), ("production_capture", "sitemap", "entries", 0)),
    )

    for original, path in cases:
        record = copy.deepcopy(original)
        target: Any = record
        for part in path:
            target = target[part]
        target["unexpected"] = True
        _assert_invalid(record)


def test_capture_requires_every_current_field() -> None:
    capture = _redirect_record()["production_capture"]
    assert isinstance(capture, dict)

    for field in fields(Capture):
        record = _redirect_record()
        del record["production_capture"][field.name]
        _assert_invalid(record)


def test_schema_rejects_malformed_failures_responses_and_provenance() -> None:
    invalid_records: list[dict[str, Any]] = []

    record = _failure_record()
    record["production_capture"]["error_code"] = ""
    invalid_records.append(record)

    record = _failure_record()
    record["production_capture"]["response_content_language"] = "en"
    invalid_records.append(record)

    record = _redirect_record()
    record["production_capture"]["status"] = 42
    invalid_records.append(record)

    record = _redirect_record()
    record["production_capture"]["error_code"] = "network_timeout"
    invalid_records.append(record)

    record = _redirect_record()
    record["production_capture"]["redirect_chain"][0]["status"] = 302
    invalid_records.append(record)

    record = _redirect_record()
    record["production_capture"]["status"] = 404
    invalid_records.append(record)

    record = _redirect_record()
    record["source_capture"]["source_path"] = "../secrets.txt"
    invalid_records.append(record)

    record = _redirect_record()
    record["production_capture"]["source_repository"] = REPOSITORY
    invalid_records.append(record)

    record = _redirect_record()
    record["production_capture"]["sitemap"]["entries"][0]["lastmod"] = "08/08/2026"
    invalid_records.append(record)

    record = _redirect_record()
    record["classification"]["owner"] = ""
    invalid_records.append(record)

    record = _redirect_record()
    record["source_capture"] = None
    record["production_capture"] = None
    invalid_records.append(record)

    provenance = _record("provenance")
    provenance["generated_at"] = "2026-08-08 06:30:00+00:00"
    invalid_records.append(provenance)

    for invalid_record in invalid_records:
        _assert_invalid(invalid_record)
