"""Deterministic source/production manifest merging and actionable differences."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from enum import Enum, StrEnum
from typing import Any
from urllib.parse import urlsplit

from compatibility.models import (
    Capture,
    Classification,
    CompatibilityRow,
    ObservationOrigin,
    ReferenceKind,
)
from compatibility.redaction import (
    is_redacted_value,
    redact_url,
    redacted_value,
    value_requires_redaction,
)

DIFFERENCE_SCHEMA_VERSION = 1
_DIFFERENCE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_CREDENTIAL_RE = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh(?:p|o|u|s|r)_[A-Za-z0-9]{20,}|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
    r"[A-Za-z0-9_-]{8,}|-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----)"
)


class DifferenceKind(StrEnum):
    """Machine-readable compatibility regressions and additions."""

    ROUTE_REMOVED = "route_removed"
    ROUTE_ADDED = "route_added"
    FRAGMENT_REMOVED = "fragment_removed"
    FRAGMENT_ADDED = "fragment_added"
    ASSET_REMOVED = "asset_removed"
    ASSET_ADDED = "asset_added"
    CANONICAL_CHANGED = "canonical_changed"
    FIELD_CHANGED = "field_changed"


_REQUIRED_ACTION: dict[DifferenceKind, str] = {
    DifferenceKind.ROUTE_REMOVED: "restore_or_classify_route",
    DifferenceKind.ROUTE_ADDED: "review_and_baseline_route",
    DifferenceKind.FRAGMENT_REMOVED: "restore_or_approve_fragment",
    DifferenceKind.FRAGMENT_ADDED: "review_and_baseline_fragment",
    DifferenceKind.ASSET_REMOVED: "restore_or_approve_asset",
    DifferenceKind.ASSET_ADDED: "review_and_baseline_asset",
    DifferenceKind.CANONICAL_CHANGED: "restore_or_approve_canonical",
    DifferenceKind.FIELD_CHANGED: "review_field_change",
}


@dataclass(frozen=True, slots=True)
class ManifestDifference:
    """One stable, granular compatibility change.

    ``difference_id`` deliberately excludes the before/after values. This keeps the
    identifier stable while an unresolved change evolves, while ``subject`` separates
    multiple fragment and asset changes at the same field.
    """

    difference_id: str
    kind: DifferenceKind
    public_url: str
    field: str
    subject: str
    before: object
    after: object
    required_action: str
    schema_version: int = DIFFERENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.kind) is not DifferenceKind:
            raise ValueError("difference_kind_must_be_enum")
        if type(self.schema_version) is not int or self.schema_version != DIFFERENCE_SCHEMA_VERSION:
            raise ValueError("unsupported_difference_schema_version")
        for name, value in (
            ("public_url", self.public_url),
            ("field", self.field),
            ("subject", self.subject),
            ("required_action", self.required_action),
        ):
            if type(value) is not str or not value or value != value.strip():
                raise ValueError(f"difference_{name}_must_be_nonempty_string")
            if any(ord(character) < 0x20 for character in value):
                raise ValueError(f"difference_{name}_contains_control_character")
        if self.required_action != _REQUIRED_ACTION[self.kind]:
            raise ValueError("difference_required_action_does_not_match_kind")
        url_parts = urlsplit(self.public_url)
        if (
            url_parts.scheme not in {"http", "https"}
            or not url_parts.hostname
            or url_parts.username is not None
            or url_parts.password is not None
        ):
            raise ValueError("difference_public_url_must_be_absolute_http_url")
        expected_id = _difference_id(self.kind, self.public_url, self.field, self.subject)
        if not _DIFFERENCE_ID_RE.fullmatch(self.difference_id) or self.difference_id != expected_id:
            raise ValueError("difference_id_is_not_canonical")
        _validate_json_value(self.before)
        _validate_json_value(self.after)
        if (
            _safe_string("public_url", self.public_url) != self.public_url
            or _safe_string("subject", self.subject) != self.subject
            or _safe_value(self.before, self.field) != self.before
            or _safe_value(self.after, self.field) != self.after
        ):
            raise ValueError("difference_contains_unredacted_private_value")


def merge_captures(
    source: Iterable[Capture],
    production: Iterable[Capture],
    classifications: Mapping[str, Classification] | None = None,
) -> tuple[CompatibilityRow, ...]:
    source_map = _capture_map(source, ObservationOrigin.SOURCE)
    production_map = _capture_map(production, ObservationOrigin.PRODUCTION)
    overrides = classifications or {}
    unknown = set(overrides) - (set(source_map) | set(production_map))
    if unknown:
        raise ValueError("classification_for_unknown_url")
    return tuple(
        CompatibilityRow(
            classification=overrides.get(url, Classification.preserve()),
            source_capture=source_map.get(url),
            production_capture=production_map.get(url),
        )
        for url in sorted(set(source_map) | set(production_map))
    )


def _capture_map(captures: Iterable[Capture], origin: ObservationOrigin) -> dict[str, Capture]:
    result: dict[str, Capture] = {}
    for capture in captures:
        if capture.origin is not origin:
            raise ValueError("capture_origin_mismatch")
        if capture.requested_url in result:
            raise ValueError("duplicate_capture_url")
        result[capture.requested_url] = capture
    return result


def diff_source_production(rows: Iterable[CompatibilityRow]) -> tuple[ManifestDifference, ...]:
    """Compare generated-source observations with production observations."""

    row_map = _row_map(rows)
    differences: list[ManifestDifference] = []
    for public_url, row in row_map.items():
        if row.source_capture is None:
            differences.append(
                _difference(
                    DifferenceKind.ROUTE_ADDED,
                    public_url,
                    "$route",
                    public_url,
                    None,
                    "present",
                )
            )
            continue
        if row.production_capture is None:
            differences.append(
                _difference(
                    DifferenceKind.ROUTE_REMOVED,
                    public_url,
                    "$route",
                    public_url,
                    "present",
                    None,
                )
            )
            continue
        source = _comparable_capture(row.source_capture)
        production = _comparable_capture(row.production_capture)
        _walk_difference(public_url, "", source, production, differences)
    return _ordered_differences(differences)


def diff_rows(
    before: Iterable[CompatibilityRow], after: Iterable[CompatibilityRow]
) -> tuple[ManifestDifference, ...]:
    """Compare two manifest versions without depending on their input ordering."""

    before_map = _row_map(before)
    after_map = _row_map(after)
    differences: list[ManifestDifference] = []
    for public_url in sorted(set(before_map) | set(after_map)):
        before_row = before_map.get(public_url)
        after_row = after_map.get(public_url)
        if before_row is None:
            differences.append(
                _difference(
                    DifferenceKind.ROUTE_ADDED,
                    public_url,
                    "$route",
                    public_url,
                    None,
                    "present",
                )
            )
            continue
        if after_row is None:
            differences.append(
                _difference(
                    DifferenceKind.ROUTE_REMOVED,
                    public_url,
                    "$route",
                    public_url,
                    "present",
                    None,
                )
            )
            continue
        _walk_difference(
            public_url,
            "",
            _primitive(asdict(before_row)),
            _primitive(asdict(after_row)),
            differences,
        )
    return _ordered_differences(differences)


def _row_map(rows: Iterable[CompatibilityRow]) -> dict[str, CompatibilityRow]:
    result: dict[str, CompatibilityRow] = {}
    for row in rows:
        if type(row) is not CompatibilityRow:
            raise ValueError("differences_require_compatibility_rows")
        if row.public_url in result:
            raise ValueError("duplicate_compatibility_row_url")
        result[row.public_url] = row
    return dict(sorted(result.items()))


def _comparable_capture(capture: Capture) -> dict[str, object]:
    value = _primitive(asdict(capture))
    assert isinstance(value, dict)
    # Source-only location and origin identify how an observation was made; they do
    # not describe a production compatibility difference.
    for key in (
        "origin",
        "source_repository",
        "source_path",
        "response_count",
        "transfer_bytes",
    ):
        value.pop(key, None)
    return value


def _primitive(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _primitive(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_primitive(item) for item in value]
    return value


def _walk_difference(
    public_url: str,
    field: str,
    before: object,
    after: object,
    output: list[ManifestDifference],
) -> None:
    if before == after:
        return
    if _is_field(field, "fragments"):
        _walk_set_difference(
            public_url,
            field,
            before,
            after,
            DifferenceKind.FRAGMENT_REMOVED,
            DifferenceKind.FRAGMENT_ADDED,
            output,
        )
        return
    if _is_field(field, "references"):
        _walk_reference_difference(public_url, field, before, after, output)
        return
    if _is_field(field, "canonical_url"):
        output.append(
            _difference(
                DifferenceKind.CANONICAL_CHANGED,
                public_url,
                field,
                public_url,
                before,
                after,
            )
        )
        return
    route_kind = _route_status_kind(field, before, after)
    if route_kind is not None:
        output.append(_difference(route_kind, public_url, field, public_url, before, after))
        return
    if (
        isinstance(before, dict)
        and (isinstance(after, dict) or after is None)
        or isinstance(after, dict)
        and before is None
    ):
        before_mapping = before if isinstance(before, dict) else {}
        after_mapping = after if isinstance(after, dict) else {}
        for key in sorted(set(before_mapping) | set(after_mapping)):
            child = f"{field}.{key}" if field else str(key)
            _walk_difference(
                public_url,
                child,
                before_mapping.get(key),
                after_mapping.get(key),
                output,
            )
        return
    changed_field = field or "$row"
    output.append(
        _difference(
            DifferenceKind.FIELD_CHANGED,
            public_url,
            changed_field,
            changed_field,
            before,
            after,
        )
    )


def _is_field(field: str, name: str) -> bool:
    return field == name or field.endswith(f".{name}")


def _route_status_kind(field: str, before: object, after: object) -> DifferenceKind | None:
    if not _is_field(field, "status") or type(before) is not int or type(after) is not int:
        return None
    before_removed = before in {404, 410}
    after_removed = after in {404, 410}
    if not before_removed and before != 0 and after_removed:
        return DifferenceKind.ROUTE_REMOVED
    if before_removed and not after_removed and after != 0:
        return DifferenceKind.ROUTE_ADDED
    return None


def _walk_set_difference(
    public_url: str,
    field: str,
    before: object,
    after: object,
    removed_kind: DifferenceKind,
    added_kind: DifferenceKind,
    output: list[ManifestDifference],
) -> None:
    before_items = _string_set(before)
    after_items = _string_set(after)
    if before_items is None or after_items is None:
        output.append(
            _difference(
                DifferenceKind.FIELD_CHANGED,
                public_url,
                field,
                field,
                before,
                after,
            )
        )
        return
    for item in sorted(before_items - after_items):
        output.append(_difference(removed_kind, public_url, field, item, item, None))
    for item in sorted(after_items - before_items):
        output.append(_difference(added_kind, public_url, field, item, None, item))


def _string_set(value: object) -> set[str] | None:
    if value is None:
        return set()
    if not isinstance(value, list) or any(type(item) is not str for item in value):
        return None
    return set(value)


def _walk_reference_difference(
    public_url: str,
    field: str,
    before: object,
    after: object,
    output: list[ManifestDifference],
) -> None:
    before_assets, before_other = _partition_references(before)
    after_assets, after_other = _partition_references(after)
    if before_assets is None or after_assets is None:
        output.append(
            _difference(
                DifferenceKind.FIELD_CHANGED,
                public_url,
                field,
                field,
                before,
                after,
            )
        )
        return
    for asset_url in sorted(before_assets - after_assets):
        output.append(
            _difference(
                DifferenceKind.ASSET_REMOVED,
                public_url,
                field,
                asset_url,
                asset_url,
                None,
            )
        )
    for asset_url in sorted(after_assets - before_assets):
        output.append(
            _difference(
                DifferenceKind.ASSET_ADDED,
                public_url,
                field,
                asset_url,
                None,
                asset_url,
            )
        )
    if before_other != after_other:
        output.append(
            _difference(
                DifferenceKind.FIELD_CHANGED,
                public_url,
                field,
                field,
                before_other,
                after_other,
            )
        )


def _partition_references(
    value: object,
) -> tuple[set[str] | None, list[object]]:
    if value is None:
        return set(), []
    if not isinstance(value, list):
        return None, []
    assets: set[str] = set()
    other: list[object] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"kind", "url"}:
            return None, []
        if item["kind"] == ReferenceKind.ASSET.value and type(item["url"]) is str:
            assets.add(item["url"])
        else:
            other.append(item)
    return assets, other


def _difference(
    kind: DifferenceKind,
    public_url: str,
    field: str,
    subject: str,
    before: object,
    after: object,
) -> ManifestDifference:
    safe_url = _safe_string("public_url", public_url)
    safe_field = _safe_string("field", field)
    safe_subject = _safe_string("subject", subject)
    safe_before = _safe_value(before, safe_field)
    safe_after = _safe_value(after, safe_field)
    return ManifestDifference(
        difference_id=_difference_id(kind, safe_url, safe_field, safe_subject),
        kind=kind,
        public_url=safe_url,
        field=safe_field,
        subject=safe_subject,
        before=safe_before,
        after=safe_after,
        required_action=_REQUIRED_ACTION[kind],
    )


def _difference_id(kind: DifferenceKind, public_url: str, field: str, subject: str) -> str:
    identity = json.dumps(
        [DIFFERENCE_SCHEMA_VERSION, kind.value, public_url, field, subject],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(identity.encode()).hexdigest()}"


def _safe_value(value: object, field: str) -> object:
    primitive = _primitive(value)
    if isinstance(primitive, dict):
        return {
            str(key): _safe_value(item, f"{field}.{key}" if field else str(key))
            for key, item in sorted(primitive.items())
        }
    if isinstance(primitive, list):
        return [_safe_value(item, field) for item in primitive]
    if type(primitive) is str:
        return _safe_string(field, primitive)
    _validate_json_value(primitive)
    return primitive


def _safe_string(field: str, value: str) -> str:
    if is_redacted_value(value):
        return value
    parts = urlsplit(value)
    if parts.scheme in {"http", "https"} and parts.hostname:
        # URL redaction understands query keys, fragments, and userinfo. Returning
        # here is intentional: applying the plain-text email heuristic afterwards
        # misclassifies ordinary image-density names such as ``logo@3x.png``.
        return redact_url(value)
    if (
        _EMAIL_RE.search(value)
        or _CREDENTIAL_RE.search(value)
        or value_requires_redaction(field.rsplit(".", 1)[-1], value)
    ):
        return redacted_value(value)
    return value


def _validate_json_value(value: object) -> None:
    if value is None or type(value) in {str, int, bool}:
        return
    if type(value) is float:
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("difference_value_must_be_finite")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise ValueError("difference_object_keys_must_be_strings")
        for item in value.values():
            _validate_json_value(item)
        return
    raise ValueError("difference_value_must_be_json")


def _ordered_differences(
    differences: Iterable[ManifestDifference],
) -> tuple[ManifestDifference, ...]:
    ordered = tuple(
        sorted(
            differences,
            key=lambda item: (
                item.public_url,
                item.kind.value,
                item.field,
                item.subject,
                item.difference_id,
            ),
        )
    )
    ids = [item.difference_id for item in ordered]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate_difference_id")
    return ordered


def dumps_differences(differences: Iterable[ManifestDifference]) -> str:
    """Serialize a canonical, versioned difference document."""

    ordered = _ordered_differences(differences)
    document = {
        "differences": [_primitive(asdict(item)) for item in ordered],
        "record_kind": "legacy_manifest_differences",
        "schema_version": DIFFERENCE_SCHEMA_VERSION,
    }
    _validate_json_value(document)
    return (
        json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )
