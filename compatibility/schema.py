"""Dependency-free validation for the JSON Schema subset used by manifest records."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

DEFAULT_MANIFEST_SCHEMA = (
    Path(__file__).resolve().parents[1] / "_docs" / "compatibility" / "legacy-manifest.schema.json"
)


class RecordSchemaError(ValueError):
    """A JSON record does not satisfy the checked-in schema."""


def load_schema(path: Path = DEFAULT_MANIFEST_SCHEMA) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RecordSchemaError("record_schema_must_be_object")
    return value


def validate_record(
    value: object,
    schema: Mapping[str, Any],
    *,
    root: Mapping[str, Any] | None = None,
    path: str = "$",
) -> None:
    """Validate the draft-2020-12 keywords used by the committed schema."""

    selected_root = schema if root is None else root
    _validate(value, schema, selected_root, path)


def validate_jsonl_records(text: str, schema: Mapping[str, Any]) -> int:
    """Stream-validate every nonblank JSONL record independently."""

    count = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line:
            raise RecordSchemaError(f"line_{line_number}_is_blank")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RecordSchemaError(f"line_{line_number}_is_invalid_json") from exc
        try:
            validate_record(record, schema)
        except RecordSchemaError as exc:
            raise RecordSchemaError(f"line_{line_number}:{exc}") from exc
        count += 1
    if count == 0:
        raise RecordSchemaError("manifest_has_no_records")
    return count


def _matches(value: object, schema: object, root: Mapping[str, Any]) -> bool:
    try:
        _validate(value, schema, root, "$")
    except RecordSchemaError:
        return False
    return True


def _json_equal(left: object, right: object) -> bool:
    return type(left) is type(right) and left == right


def _validate(
    value: object,
    raw_schema: object,
    root: Mapping[str, Any],
    path: str,
) -> None:
    if raw_schema is False:
        raise RecordSchemaError(f"{path}:false_schema")
    if raw_schema is True:
        return
    if not isinstance(raw_schema, Mapping):
        raise RecordSchemaError(f"{path}:schema_must_be_object")
    schema = raw_schema

    reference = schema.get("$ref")
    if reference is not None:
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            raise RecordSchemaError(f"{path}:unsupported_reference")
        definitions = root.get("$defs")
        name = reference.removeprefix("#/$defs/")
        if not isinstance(definitions, Mapping) or name not in definitions:
            raise RecordSchemaError(f"{path}:unknown_reference")
        _validate(value, definitions[name], root, path)

    one_of = schema.get("oneOf")
    if one_of is not None:
        alternatives = _schema_list(one_of, path)
        if sum(_matches(value, candidate, root) for candidate in alternatives) != 1:
            raise RecordSchemaError(f"{path}:one_of_mismatch")

    all_of = schema.get("allOf")
    if all_of is not None:
        for candidate in _schema_list(all_of, path):
            _validate(value, candidate, root, path)

    excluded = schema.get("not")
    if excluded is not None and _matches(value, excluded, root):
        raise RecordSchemaError(f"{path}:excluded_schema_match")

    condition = schema.get("if")
    if condition is not None:
        branch = schema.get("then") if _matches(value, condition, root) else schema.get("else")
        if branch is not None:
            _validate(value, branch, root, path)

    if "const" in schema and not _json_equal(value, schema["const"]):
        raise RecordSchemaError(f"{path}:const_mismatch")
    enum = schema.get("enum")
    if enum is not None:
        choices = _schema_list(enum, path)
        if not any(_json_equal(value, choice) for choice in choices):
            raise RecordSchemaError(f"{path}:enum_mismatch")

    expected_type = schema.get("type")
    type_matches = {
        "array": isinstance(value, list),
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "null": value is None,
        "object": isinstance(value, dict),
        "string": isinstance(value, str),
    }
    if isinstance(expected_type, str):
        if expected_type not in type_matches or not type_matches[expected_type]:
            raise RecordSchemaError(f"{path}:type_mismatch")

    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        if isinstance(minimum_length, int) and len(value) < minimum_length:
            raise RecordSchemaError(f"{path}:string_too_short")
        maximum_length = schema.get("maxLength")
        if isinstance(maximum_length, int) and len(value) > maximum_length:
            raise RecordSchemaError(f"{path}:string_too_long")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise RecordSchemaError(f"{path}:pattern_mismatch")

    if type(value) is int:
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int) and value < minimum:
            raise RecordSchemaError(f"{path}:number_too_small")
        if isinstance(maximum, int) and value > maximum:
            raise RecordSchemaError(f"{path}:number_too_large")

    if isinstance(value, dict):
        required = _string_list(schema.get("required", []), path)
        missing = set(required) - set(value)
        if missing:
            raise RecordSchemaError(f"{path}:missing_fields")
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise RecordSchemaError(f"{path}:invalid_properties_schema")
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            raise RecordSchemaError(f"{path}:extra_fields")
        for key, child_schema in properties.items():
            if key in value:
                _validate(value[key], child_schema, root, f"{path}.{key}")

    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        maximum_items = schema.get("maxItems")
        if isinstance(minimum_items, int) and len(value) < minimum_items:
            raise RecordSchemaError(f"{path}:array_too_short")
        if isinstance(maximum_items, int) and len(value) > maximum_items:
            raise RecordSchemaError(f"{path}:array_too_long")
        if schema.get("uniqueItems") is True:
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                raise RecordSchemaError(f"{path}:array_not_unique")
        prefix_items = _schema_list(schema.get("prefixItems", []), path)
        for index, child_schema in enumerate(prefix_items):
            if index < len(value):
                _validate(value[index], child_schema, root, f"{path}[{index}]")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(value[len(prefix_items) :], start=len(prefix_items)):
                _validate(item, item_schema, root, f"{path}[{index}]")


def _schema_list(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise RecordSchemaError(f"{path}:schema_keyword_must_be_array")
    return value


def _string_list(value: object, path: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RecordSchemaError(f"{path}:schema_keyword_must_be_string_array")
    return value
