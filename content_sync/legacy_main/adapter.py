"""Pure parser for the pinned tools and conferences in the legacy main site.

The adapter deliberately has no Django, Git, network, subprocess, or persistence dependency.  A
caller supplies the result of an independently verified checkout check (origin, revision, tree,
and clean status); this module only reads the fifteen contract-selected files from that checkout.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

import yaml
from yaml.events import (
    AliasEvent,
    CollectionEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceStartEvent,
)
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from .contract import (
    LEGACY_MAIN_COMMIT,
    LEGACY_MAIN_CONTRACT,
    LEGACY_MAIN_REPOSITORY,
    LEGACY_MAIN_TREE,
    LegacyMainAdapterContract,
    LegacySelectedFile,
)

_HEX_40 = re.compile(r"^[0-9a-f]{40}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_STABLE_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{0,199}$")
_PERSON_KEY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._()\-]{0,199}$")
_DATE_LEXEM = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
_TIME_LEXEM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_DECIMAL_ID = re.compile(r"^[1-9][0-9]*$")
_HTTPS_URL = re.compile(r"^https://")
_FRAGMENT_TAG = re.compile(r"<(?:/ul|/li|ul|li|br|br/)>|</?[^>]*>")
_ALLOWED_FRAGMENT_TAGS = frozenset({"<ul>", "</ul>", "<li>", "</li>", "<br>", "<br/>"})
_YAML_STR = "tag:yaml.org,2002:str"
_YAML_INT = "tag:yaml.org,2002:int"
_YAML_TIMESTAMP = "tag:yaml.org,2002:timestamp"
_YAML_NULL = "tag:yaml.org,2002:null"
_YAML_BOOL = "tag:yaml.org,2002:bool"
_YAML_FLOAT = "tag:yaml.org,2002:float"
_YAML_MERGE = "tag:yaml.org,2002:merge"
_YAML_MAP = "tag:yaml.org,2002:map"
_YAML_SEQ = "tag:yaml.org,2002:seq"
_DANGEROUS_TEXT_URL = re.compile(r"(?i)(?:javascript|data):|(?:^|[\s(])//[^\s]")


@dataclass(frozen=True, slots=True)
class LegacyMainDiagnostic:
    """A bounded, source-safe parser diagnostic."""

    code: str
    source_path: str
    pointer: str = "/"

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "source_path": self.source_path, "pointer": self.pointer}


class LegacyMainValidationError(ValueError):
    """Raised when a complete candidate cannot be accepted atomically."""

    def __init__(self, diagnostics: Sequence[LegacyMainDiagnostic]) -> None:
        self.diagnostics = tuple(diagnostics)
        first = (
            self.diagnostics[0]
            if self.diagnostics
            else LegacyMainDiagnostic("legacy_main_parser_failed", ".", "/")
        )
        self.code = first.code
        # Do not include source values, exception text, or an absolute checkout path in errors.
        super().__init__(f"{first.source_path}{first.pointer}: {first.code}")


@dataclass(frozen=True, slots=True)
class AssetReference:
    record_kind: str
    record_key: str
    pointer: str

    def as_dict(self) -> dict[str, str]:
        return {
            "record_kind": self.record_kind,
            "record_key": self.record_key,
            "pointer": self.pointer,
        }


@dataclass(frozen=True, slots=True)
class ToolSourceRecord:
    key: str
    source_path: str
    git_blob_sha: str
    byte_size: int
    sha256: str
    title: str
    name: str
    description: str
    github_url: str
    categories: tuple[str, ...]
    demo_url: str
    maintainer_keys: tuple[str, ...]

    @property
    def source_blob_sha(self) -> str:
        return self.git_blob_sha

    @property
    def source_sha256(self) -> str:
        return self.sha256

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "source_path": self.source_path,
            "git_blob_sha": self.git_blob_sha,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "title": self.title,
            "name": self.name,
            "description": self.description,
            "github_url": self.github_url,
            "categories": list(self.categories),
            "demo_url": self.demo_url,
            "maintainer_keys": list(self.maintainer_keys),
        }


@dataclass(frozen=True, slots=True)
class TalkSourceRecord:
    name: str
    abstract_fragment: str
    speaker_key: str
    speaker_company: str | None
    schedule_variant: str
    time: str | None = None
    date: str | None = None
    eventbrite: str | None = None
    youtube: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "abstract_fragment": self.abstract_fragment,
            "speaker_key": self.speaker_key,
            "schedule_variant": self.schedule_variant,
        }
        if self.speaker_company is not None:
            result["speaker_company"] = self.speaker_company
        if self.schedule_variant == "track_date_talk_time":
            if self.time is not None:
                result["time"] = self.time
            if self.youtube is not None:
                result["youtube"] = self.youtube
        else:
            if self.date is not None:
                result["date"] = self.date
            if self.eventbrite is not None:
                result["eventbrite"] = self.eventbrite
            if self.youtube is not None:
                result["youtube"] = self.youtube
        return result


@dataclass(frozen=True, slots=True)
class TrackSourceRecord:
    name: str
    schedule_variant: str
    eventbrite: str
    talks: tuple[TalkSourceRecord, ...]
    date: str | None = None
    youtube: str | None = None
    start: str | None = None
    end: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "schedule_variant": self.schedule_variant,
            "eventbrite": self.eventbrite,
            "talks": [talk.as_dict() for talk in self.talks],
        }
        if self.schedule_variant == "track_date_talk_time":
            if self.date is not None:
                result["date"] = self.date
            if self.youtube is not None:
                result["youtube"] = self.youtube
        else:
            if self.start is not None:
                result["start"] = self.start
            if self.end is not None:
                result["end"] = self.end
        return result


@dataclass(frozen=True, slots=True)
class PartnerSourceRecord:
    name: str
    link: str
    image_asset_path: str

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "link": self.link,
            "image_asset_path": self.image_asset_path,
        }


@dataclass(frozen=True, slots=True)
class ConferenceSourceRecord:
    key: str
    source_path: str
    git_blob_sha: str
    byte_size: int
    sha256: str
    title: str
    description: str
    cover_asset_path: str
    layout: str
    tracks: tuple[TrackSourceRecord, ...]
    partners: tuple[PartnerSourceRecord, ...]
    legacy_body_size: int
    legacy_body_sha256: str

    @property
    def source_blob_sha(self) -> str:
        return self.git_blob_sha

    @property
    def source_sha256(self) -> str:
        return self.sha256

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "source_path": self.source_path,
            "git_blob_sha": self.git_blob_sha,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "title": self.title,
            "description": self.description,
            "cover_asset_path": self.cover_asset_path,
            "layout": self.layout,
            "tracks": [track.as_dict() for track in self.tracks],
            "partners": [partner.as_dict() for partner in self.partners],
            "legacy_body_size": self.legacy_body_size,
            "legacy_body_sha256": self.legacy_body_sha256,
        }


@dataclass(frozen=True, slots=True)
class AssetSourceRecord:
    source_path: str
    git_blob_sha: str
    byte_size: int
    sha256: str
    mime_family: str
    references: tuple[AssetReference, ...]

    @property
    def path(self) -> str:
        return self.source_path

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "git_blob_sha": self.git_blob_sha,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "mime_family": self.mime_family,
            "references": [reference.as_dict() for reference in self.references],
        }


@dataclass(frozen=True, slots=True)
class LegacyMainProvenance:
    repository: str
    commit: str
    tree: str
    parser_version: str
    schema_version: int
    selected_manifest_sha256: str
    counts: Mapping[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "commit": self.commit,
            "tree": self.tree,
            "parser_version": self.parser_version,
            "schema_version": self.schema_version,
            "selected_manifest_sha256": self.selected_manifest_sha256,
            "counts": dict(self.counts),
        }


@dataclass(frozen=True, slots=True)
class LegacyMainBundle:
    provenance: LegacyMainProvenance
    tools: tuple[ToolSourceRecord, ...]
    conferences: tuple[ConferenceSourceRecord, ...]
    assets: tuple[AssetSourceRecord, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provenance": self.provenance.as_dict(),
            "tools": [tool.as_dict() for tool in self.tools],
            "conferences": [conference.as_dict() for conference in self.conferences],
            "assets": [asset.as_dict() for asset in self.assets],
        }

    @property
    def canonical_bytes(self) -> bytes:
        return (
            json.dumps(
                self.as_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )

    @property
    def bundle_sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    @property
    def evidence_bytes(self) -> bytes:
        return self.canonical_bytes


class _Diagnostics:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._items: list[LegacyMainDiagnostic] = []

    def add(self, code: str, source_path: str, pointer: str = "/") -> None:
        # All call sites pass contract paths/pointers. Keep this guard so an accidental exception
        # or attacker-controlled value can never become an operator-visible diagnostic field.
        if not source_path or source_path.startswith("/") or "\\" in source_path:
            source_path = "."
        if not pointer.startswith("/") or "\\" in pointer:
            pointer = "/"
        self._items.append(LegacyMainDiagnostic(code, source_path[:256], pointer[:256]))

    def finish(self) -> tuple[LegacyMainDiagnostic, ...]:
        ordered = sorted(
            set(self._items), key=lambda item: (item.source_path, item.pointer, item.code)
        )
        if len(ordered) <= self._limit:
            return tuple(ordered)
        marker = LegacyMainDiagnostic("diagnostics_overflow", ".", "/")
        return tuple((*ordered[: max(0, self._limit - 1)], marker))

    def raise_if_any(self) -> None:
        diagnostics = self.finish()
        if diagnostics:
            raise LegacyMainValidationError(diagnostics)


def _pointer_join(pointer: str, part: str | int) -> str:
    if isinstance(part, int):
        value = str(part)
    else:
        value = (
            part
            if part
            in {
                "title",
                "name",
                "description",
                "image",
                "layout",
                "tracks",
                "talks",
                "partners",
                "date",
                "start",
                "end",
                "eventbrite",
                "youtube",
                "time",
                "speaker",
                "abstract",
                "github",
                "categories",
                "demo",
                "who",
                "link",
                "company",
                "id",
            }
            else "unknown"
        )
    return f"{pointer.rstrip('/')}/{value}"


def _json_pointer(part: str | int) -> str:
    return _pointer_join("/", part)


def _is_known_yaml_tag(tag: str | None) -> bool:
    return tag in {
        _YAML_STR,
        _YAML_INT,
        _YAML_TIMESTAMP,
        _YAML_NULL,
        _YAML_BOOL,
        _YAML_FLOAT,
        _YAML_MAP,
        _YAML_SEQ,
        _YAML_MERGE,
    }


def _yaml_preflight(
    text: str, *, source_path: str, diagnostics: _Diagnostics, contract: LegacyMainAdapterContract
) -> Node | None:
    """Parse one mapping into nodes while retaining scalar styles and lexemes."""

    depth = 0
    nodes = 0
    try:
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, AliasEvent) or getattr(event, "anchor", None) is not None:
                diagnostics.add("yaml_alias_forbidden", source_path, "/")
            if isinstance(event, (MappingStartEvent, SequenceStartEvent)):
                depth += 1
                nodes += 1
                if depth > contract.max_yaml_depth:
                    diagnostics.add("yaml_depth_exceeded", source_path, "/")
            elif isinstance(event, ScalarEvent):
                nodes += 1
                if depth + 1 > contract.max_yaml_depth:
                    diagnostics.add("yaml_depth_exceeded", source_path, "/")
            elif isinstance(event, CollectionEndEvent):
                depth = max(0, depth - 1)
            if nodes > contract.max_yaml_nodes:
                diagnostics.add("yaml_node_limit_exceeded", source_path, "/")
            if getattr(event, "tag", None) is not None and not _is_known_yaml_tag(event.tag):
                diagnostics.add("schema_value_type_invalid", source_path, "/")
    except yaml.YAMLError:
        diagnostics.add("frontmatter_invalid", source_path, "/")
        return None

    try:
        node = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        diagnostics.add("frontmatter_invalid", source_path, "/")
        return None
    if node is None or not isinstance(node, MappingNode):
        diagnostics.add("schema_value_type_invalid", source_path, "/")
        return None
    return node


def _mapping(
    node: Node | None,
    *,
    source_path: str,
    pointer: str,
    diagnostics: _Diagnostics,
) -> dict[str, Node] | None:
    if not isinstance(node, MappingNode) or node.tag != _YAML_MAP:
        diagnostics.add("schema_value_type_invalid", source_path, pointer)
        return None
    result: dict[str, Node] = {}
    for key_node, value_node in node.value:
        if not isinstance(key_node, ScalarNode) or key_node.tag != _YAML_STR:
            diagnostics.add("schema_value_type_invalid", source_path, pointer)
            continue
        key = key_node.value
        if key == "<<" or key_node.tag == _YAML_MERGE:
            diagnostics.add("yaml_alias_forbidden", source_path, pointer)
            continue
        if key in result:
            diagnostics.add("yaml_duplicate_key", source_path, _pointer_join(pointer, key))
            continue
        result[key] = value_node
    return result


def _fields(
    node: Node | None,
    *,
    allowed: frozenset[str],
    source_path: str,
    pointer: str,
    diagnostics: _Diagnostics,
) -> dict[str, Node]:
    mapping = _mapping(node, source_path=source_path, pointer=pointer, diagnostics=diagnostics)
    if mapping is None:
        return {}
    for key in mapping:
        if key not in allowed:
            # Do not echo an arbitrary attacker-controlled mapping key in a diagnostic pointer.
            diagnostics.add("schema_field_unknown", source_path, _pointer_join(pointer, "unknown"))
    return mapping


def _required_string(
    mapping: Mapping[str, Node],
    key: str,
    *,
    source_path: str,
    pointer: str,
    diagnostics: _Diagnostics,
    maximum: int,
    allow_timestamp: bool = False,
    nonempty: bool = True,
) -> str:
    child_pointer = _pointer_join(pointer, key)
    node = mapping.get(key)
    if node is None:
        diagnostics.add("schema_required_value_missing", source_path, child_pointer)
        return ""
    if not isinstance(node, ScalarNode) or node.tag == _YAML_NULL:
        diagnostics.add(
            "schema_required_value_missing"
            if isinstance(node, ScalarNode) and node.tag == _YAML_NULL
            else "schema_value_type_invalid",
            source_path,
            child_pointer,
        )
        return ""
    accepted_tags = {_YAML_STR}
    if allow_timestamp:
        accepted_tags.add(_YAML_TIMESTAMP)
    if node.tag not in accepted_tags:
        diagnostics.add("schema_value_type_invalid", source_path, child_pointer)
        return ""
    value = node.value
    if not isinstance(value, str):
        diagnostics.add("schema_value_type_invalid", source_path, child_pointer)
        return ""
    if len(value) > maximum:
        diagnostics.add("schema_limit_exceeded", source_path, child_pointer)
    if nonempty and not value:
        diagnostics.add("schema_required_value_missing", source_path, child_pointer)
    return value


def _optional_string(
    mapping: Mapping[str, Node],
    key: str,
    *,
    source_path: str,
    pointer: str,
    diagnostics: _Diagnostics,
    maximum: int,
    allow_timestamp: bool = False,
) -> str | None:
    if key not in mapping:
        return None
    value = _required_string(
        mapping,
        key,
        source_path=source_path,
        pointer=pointer,
        diagnostics=diagnostics,
        maximum=maximum,
        allow_timestamp=allow_timestamp,
    )
    return value


def _sequence(
    mapping: Mapping[str, Node],
    key: str,
    *,
    source_path: str,
    pointer: str,
    diagnostics: _Diagnostics,
    maximum: int,
) -> tuple[Node, ...]:
    child_pointer = _pointer_join(pointer, key)
    node = mapping.get(key)
    if node is None:
        diagnostics.add("schema_required_value_missing", source_path, child_pointer)
        return ()
    if not isinstance(node, SequenceNode) or node.tag != _YAML_SEQ:
        diagnostics.add("schema_value_type_invalid", source_path, child_pointer)
        return ()
    if len(node.value) > maximum:
        diagnostics.add("schema_limit_exceeded", source_path, child_pointer)
    return tuple(node.value)


def _validate_date(
    value: str,
    *,
    source_path: str,
    pointer: str,
    diagnostics: _Diagnostics,
) -> str:
    if _DATE_LEXEM.fullmatch(value) is None:
        diagnostics.add("schedule_value_invalid", source_path, pointer)
        return ""
    year, month, day = (int(part) for part in value.split(" ", 1)[0].split("-"))
    hour, minute, second = (int(part) for part in value.split(" ", 1)[1].split(":"))
    try:
        from datetime import datetime

        datetime(year, month, day, hour, minute, second)
    except ValueError:
        diagnostics.add("schedule_value_invalid", source_path, pointer)
        return ""
    return value


def _date(
    mapping: Mapping[str, Node],
    key: str,
    *,
    source_path: str,
    pointer: str,
    diagnostics: _Diagnostics,
) -> str:
    value = _required_string(
        mapping,
        key,
        source_path=source_path,
        pointer=pointer,
        diagnostics=diagnostics,
        maximum=64,
        allow_timestamp=True,
    )
    return (
        _validate_date(
            value,
            source_path=source_path,
            pointer=_pointer_join(pointer, key),
            diagnostics=diagnostics,
        )
        if value
        else value
    )


def _time(
    mapping: Mapping[str, Node],
    key: str,
    *,
    source_path: str,
    pointer: str,
    diagnostics: _Diagnostics,
) -> str:
    child_pointer = _pointer_join(pointer, key)
    node = mapping.get(key)
    value = _required_string(
        mapping,
        key,
        source_path=source_path,
        pointer=pointer,
        diagnostics=diagnostics,
        maximum=16,
    )
    if node is not None and (not isinstance(node, ScalarNode) or node.style not in {"'", '"'}):
        diagnostics.add("schedule_value_invalid", source_path, child_pointer)
    if value and _TIME_LEXEM.fullmatch(value) is None:
        diagnostics.add("schedule_value_invalid", source_path, child_pointer)
    return value


def _eventbrite(
    mapping: Mapping[str, Node],
    key: str,
    *,
    source_path: str,
    pointer: str,
    diagnostics: _Diagnostics,
) -> str:
    child_pointer = _pointer_join(pointer, key)
    node = mapping.get(key)
    if (
        node is None
        or not isinstance(node, ScalarNode)
        or node.tag in {_YAML_NULL, _YAML_BOOL, _YAML_FLOAT}
    ):
        diagnostics.add("eventbrite_id_invalid", source_path, child_pointer)
        return ""
    if node.tag not in {_YAML_INT, _YAML_STR}:
        diagnostics.add("eventbrite_id_invalid", source_path, child_pointer)
        return ""
    raw = node.value
    if not isinstance(raw, str) or len(raw) > 64 or _DECIMAL_ID.fullmatch(raw) is None:
        diagnostics.add("eventbrite_id_invalid", source_path, child_pointer)
        return ""
    return str(int(raw, 10))


def _url(
    mapping: Mapping[str, Node],
    key: str,
    *,
    source_path: str,
    pointer: str,
    diagnostics: _Diagnostics,
) -> str:
    child_pointer = _pointer_join(pointer, key)
    value = _required_string(
        mapping,
        key,
        source_path=source_path,
        pointer=pointer,
        diagnostics=diagnostics,
        maximum=2_048,
    )
    if not value:
        return value
    if any(ord(character) < 0x20 or character.isspace() for character in value) or "\\" in value:
        diagnostics.add("url_invalid", source_path, child_pointer)
        return value
    if value.startswith("//"):
        diagnostics.add("url_scheme_forbidden", source_path, child_pointer)
        return value
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
    except ValueError:
        diagnostics.add("url_invalid", source_path, child_pointer)
        return value
    if (
        parts.scheme.lower() != "https"
        or not hostname
        or parts.username is not None
        or parts.password is not None
    ):
        diagnostics.add(
            "url_scheme_forbidden" if parts.scheme.lower() != "https" else "url_invalid",
            source_path,
            child_pointer,
        )
    if parts.fragment:
        diagnostics.add("url_invalid", source_path, child_pointer)
    return value


def _person(
    node: Node | None,
    *,
    source_path: str,
    pointer: str,
    diagnostics: _Diagnostics,
    contract: LegacyMainAdapterContract,
) -> str:
    if not isinstance(node, ScalarNode) or node.tag != _YAML_STR or not node.value:
        diagnostics.add("person_key_invalid", source_path, pointer)
        return ""
    value = node.value
    if (
        len(value) > contract.max_opaque_key_length
        or _PERSON_KEY.fullmatch(value) is None
        or ".." in value
    ):
        diagnostics.add("person_key_invalid", source_path, pointer)
        return ""
    return value


def _person_list(
    mapping: Mapping[str, Node],
    key: str,
    *,
    source_path: str,
    pointer: str,
    diagnostics: _Diagnostics,
    contract: LegacyMainAdapterContract,
) -> tuple[str, ...]:
    nodes = _sequence(
        mapping,
        key,
        source_path=source_path,
        pointer=pointer,
        diagnostics=diagnostics,
        maximum=64,
    )
    return tuple(
        _person(
            node,
            source_path=source_path,
            pointer=f"{_pointer_join(pointer, key)}/{index}",
            diagnostics=diagnostics,
            contract=contract,
        )
        for index, node in enumerate(nodes)
    )


def _asset_path(
    value: str,
    *,
    source_path: str,
    pointer: str,
    diagnostics: _Diagnostics,
    family: str,
) -> str:
    if (
        not value
        or "\\" in value
        or "?" in value
        or "#" in value
        or value.startswith("//")
        or any(character.isspace() or ord(character) < 0x20 for character in value)
    ):
        diagnostics.add("asset_path_invalid", source_path, pointer)
        return ""
    relative = value.removeprefix("/")
    pure = PurePosixPath(relative)
    if (
        relative != pure.as_posix()
        or not relative
        or any(part in {"", ".", ".."} for part in pure.parts)
        or not relative.startswith(f"images/{family}/")
    ):
        diagnostics.add("asset_path_invalid", source_path, pointer)
        return ""
    return f"/{relative}"


def _abstract(
    mapping: Mapping[str, Node],
    key: str,
    *,
    source_path: str,
    pointer: str,
    diagnostics: _Diagnostics,
    contract: LegacyMainAdapterContract,
) -> str:
    value = _required_string(
        mapping,
        key,
        source_path=source_path,
        pointer=pointer,
        diagnostics=diagnostics,
        maximum=contract.max_abstract_length,
    )
    if not value:
        return value
    if (
        "{%" in value
        or "%}" in value
        or "{{" in value
        or "}}" in value
        or _DANGEROUS_TEXT_URL.search(value) is not None
    ):
        diagnostics.add("abstract_fragment_unsafe", source_path, _pointer_join(pointer, key))
        return value
    position = 0
    while position < len(value):
        start = value.find("<", position)
        end_marker = value.find(">", position)
        if start < 0 and end_marker < 0:
            break
        if start < 0 or end_marker < start:
            diagnostics.add("abstract_fragment_unsafe", source_path, _pointer_join(pointer, key))
            break
        end = value.find(">", start + 1)
        if end < 0:
            diagnostics.add("abstract_fragment_unsafe", source_path, _pointer_join(pointer, key))
            break
        tag = value[start : end + 1]
        if tag not in _ALLOWED_FRAGMENT_TAGS:
            diagnostics.add("abstract_fragment_unsafe", source_path, _pointer_join(pointer, key))
        position = end + 1
    stack: list[str] = []
    for match in re.finditer(r"</?(?:ul|li)>", value):
        tag = match.group(0)
        if tag.startswith("</"):
            expected = tag[2:-1]
            if not stack or stack.pop() != expected:
                diagnostics.add(
                    "abstract_fragment_unsafe", source_path, _pointer_join(pointer, key)
                )
                break
        else:
            stack.append(tag[1:-1])
    if stack:
        diagnostics.add("abstract_fragment_unsafe", source_path, _pointer_join(pointer, key))
    return value


def _check_directory(root: Path, relative: str, diagnostics: _Diagnostics) -> bool:
    path = root / relative
    try:
        mode = path.lstat().st_mode
    except OSError:
        diagnostics.add("source_path_missing", relative, "/")
        return False
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        diagnostics.add("source_path_type_invalid", relative, "/")
        return False
    return True


def _check_collection_paths(
    root: Path,
    *,
    relative_directory: str,
    allowed: frozenset[str],
    diagnostics: _Diagnostics,
) -> None:
    if not _check_directory(root, relative_directory, diagnostics):
        return
    try:
        entries = tuple(os.scandir(root / relative_directory))
    except OSError:
        diagnostics.add("source_path_missing", relative_directory, "/")
        return
    for entry in entries:
        # Only names are inspected here. Content outside the exact selected records is never read.
        if entry.name not in allowed:
            diagnostics.add("source_path_unexpected", f"{relative_directory}/<unexpected>", "/")
        elif entry.name.endswith("/") or entry.name in {".", ".."}:
            diagnostics.add("source_path_escape", relative_directory, "/")


def _check_parent_directories(root: Path, relative: str, diagnostics: _Diagnostics) -> bool:
    current = root
    parts = PurePosixPath(relative).parts[:-1]
    for part in parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError:
            diagnostics.add("source_path_missing", relative, "/")
            return False
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            diagnostics.add("source_path_type_invalid", relative, "/")
            return False
    return True


def _read_selected(
    root: Path,
    selected: LegacySelectedFile,
    *,
    contract: LegacyMainAdapterContract,
    diagnostics: _Diagnostics,
) -> bytes | None:
    relative = selected.path
    # Never let a caller-supplied/internal selection turn the checkout root into a path traversal
    # primitive.  The public ``contract=`` boundary accepts only the canonical contract, but this
    # guard also keeps the private reader fail closed if it is reused by future adapter code.
    posix = PurePosixPath(relative) if isinstance(relative, str) else PurePosixPath(".")
    if (
        not isinstance(relative, str)
        or not relative
        or relative != posix.as_posix()
        or posix.is_absolute()
        or any(part in {"", ".", ".."} for part in posix.parts)
        or "\\" in relative
        or "\x00" in relative
    ):
        diagnostics.add("source_path_escape", ".", "/")
        return None
    if not _check_parent_directories(root, relative, diagnostics):
        return None
    path = root / relative
    try:
        mode = path.lstat().st_mode
    except OSError:
        diagnostics.add("source_path_missing", relative, "/")
        return None
    # A checkout's Git mode is 100644; the working-tree umask may add a harmless group-write
    # bit, so reject executable/non-regular files without requiring one exact filesystem mode.
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o111:
        diagnostics.add("source_path_type_invalid", relative, "/")
        return None
    try:
        size = path.stat().st_size
    except OSError:
        diagnostics.add("source_path_missing", relative, "/")
        return None
    maximum = contract.max_asset_bytes if selected.kind == "asset" else contract.max_record_bytes
    if size < 0 or size > maximum or size != selected.byte_size:
        diagnostics.add("source_file_size_invalid", relative, "/")
        return None
    try:
        raw = path.read_bytes()
    except (OSError, ValueError):
        diagnostics.add("source_path_missing", relative, "/")
        return None
    if len(raw) != selected.byte_size:
        diagnostics.add("source_file_size_invalid", relative, "/")
        return None
    source_sha = hashlib.sha256(raw).hexdigest()
    git_header = f"blob {len(raw)}\0".encode("ascii")
    git_blob_sha = hashlib.sha1(git_header + raw, usedforsecurity=False).hexdigest()
    if source_sha != selected.sha256 or git_blob_sha != selected.git_blob_sha:
        diagnostics.add(
            "asset_digest_mismatch" if selected.kind == "asset" else "source_digest_mismatch",
            relative,
            "/",
        )
        return None
    return raw


def _frontmatter_and_body(
    raw: bytes,
    *,
    selected: LegacySelectedFile,
    contract: LegacyMainAdapterContract,
    diagnostics: _Diagnostics,
) -> tuple[str, bytes] | None:
    path = selected.path
    if len(raw) > contract.max_record_bytes:
        diagnostics.add("source_file_size_invalid", path, "/")
        return None
    if not raw.startswith(b"---\n"):
        diagnostics.add("frontmatter_invalid", path, "/")
        return None
    first_end = 4
    delimiter = re.compile(rb"^---[ \t]*\r?\n", re.MULTILINE)
    match = delimiter.search(raw, first_end)
    if match is None:
        diagnostics.add("frontmatter_invalid", path, "/")
        return None
    frontmatter = raw[first_end : match.start()]
    body = raw[match.end() :]
    if (
        len(frontmatter) > contract.max_frontmatter_bytes
        or len(body) > contract.max_legacy_body_bytes
    ):
        diagnostics.add("schema_limit_exceeded", path, "/")
        return None
    try:
        text = frontmatter.decode("utf-8")
    except UnicodeDecodeError:
        diagnostics.add("source_utf8_invalid", path, "/")
        return None
    # The legacy Jekyll files have a separating blank line before the presentation body. It is
    # framing, not semantic evidence; exact file and body digests still bind every other byte.
    return text, body.lstrip(b"\r\n")


def _body_ok(
    key: str,
    body: bytes,
    *,
    selected: LegacySelectedFile,
    contract: LegacyMainAdapterContract,
    diagnostics: _Diagnostics,
) -> tuple[int, str] | None:
    expected = next(
        ((size, digest) for body_key, size, digest in contract.body_evidence if body_key == key),
        None,
    )
    if selected.kind == "tool":
        if body:
            diagnostics.add("legacy_body_digest_mismatch", selected.path, "/")
            return None
        return (0, hashlib.sha256(b"").hexdigest())
    if expected is None:
        diagnostics.add("legacy_body_digest_mismatch", selected.path, "/")
        return None
    expected_size, expected_sha = expected
    actual_sha = hashlib.sha256(body).hexdigest()
    if len(body) != expected_size or actual_sha != expected_sha:
        diagnostics.add("legacy_body_digest_mismatch", selected.path, "/")
        return None
    return expected_size, expected_sha


def _parse_tool(
    selected: LegacySelectedFile,
    raw: bytes,
    *,
    contract: LegacyMainAdapterContract,
    diagnostics: _Diagnostics,
) -> ToolSourceRecord | None:
    framed = _frontmatter_and_body(
        raw, selected=selected, contract=contract, diagnostics=diagnostics
    )
    if framed is None:
        return None
    text, body = framed
    key = PurePosixPath(selected.path).stem
    if _STABLE_KEY.fullmatch(key) is None:
        diagnostics.add("stable_key_invalid", selected.path, "/")
    _body_ok(key, body, selected=selected, contract=contract, diagnostics=diagnostics)
    node = _yaml_preflight(
        text, source_path=selected.path, diagnostics=diagnostics, contract=contract
    )
    mapping = _fields(
        node,
        allowed=frozenset({"title", "name", "description", "github", "categories", "demo", "who"}),
        source_path=selected.path,
        pointer="/",
        diagnostics=diagnostics,
    )
    title = _required_string(
        mapping,
        "title",
        source_path=selected.path,
        pointer="/",
        diagnostics=diagnostics,
        maximum=contract.max_title_length,
    )
    name = _required_string(
        mapping,
        "name",
        source_path=selected.path,
        pointer="/",
        diagnostics=diagnostics,
        maximum=contract.max_name_length,
    )
    description = _required_string(
        mapping,
        "description",
        source_path=selected.path,
        pointer="/",
        diagnostics=diagnostics,
        maximum=contract.max_description_length,
    )
    github = _url(
        mapping,
        "github",
        source_path=selected.path,
        pointer="/",
        diagnostics=diagnostics,
    )
    category_nodes = _sequence(
        mapping,
        "categories",
        source_path=selected.path,
        pointer="/",
        diagnostics=diagnostics,
        maximum=64,
    )
    categories = tuple(
        _node_string(
            node_value,
            source_path=selected.path,
            pointer=f"/categories/{index}",
            diagnostics=diagnostics,
            maximum=contract.max_category_length,
        )
        for index, node_value in enumerate(category_nodes)
    )
    demo = _url(
        mapping,
        "demo",
        source_path=selected.path,
        pointer="/",
        diagnostics=diagnostics,
    )
    who = _person_list(
        mapping,
        "who",
        source_path=selected.path,
        pointer="/",
        diagnostics=diagnostics,
        contract=contract,
    )
    if _STABLE_KEY.fullmatch(key) is None:
        return None
    return ToolSourceRecord(
        key=key,
        source_path=selected.path,
        git_blob_sha=selected.git_blob_sha,
        byte_size=selected.byte_size,
        sha256=selected.sha256,
        title=title,
        name=name,
        description=description,
        github_url=github,
        categories=categories,
        demo_url=demo,
        maintainer_keys=who,
    )


def _node_string(
    node: Node,
    *,
    source_path: str,
    pointer: str,
    diagnostics: _Diagnostics,
    maximum: int,
    allow_timestamp: bool = False,
    nonempty: bool = True,
) -> str:
    if not isinstance(node, ScalarNode) or node.tag == _YAML_NULL:
        diagnostics.add(
            "schema_required_value_missing"
            if isinstance(node, ScalarNode) and node.tag == _YAML_NULL
            else "schema_value_type_invalid",
            source_path,
            pointer,
        )
        return ""
    accepted_tags = {_YAML_STR}
    if allow_timestamp:
        accepted_tags.add(_YAML_TIMESTAMP)
    if node.tag not in accepted_tags or not isinstance(node.value, str):
        diagnostics.add("schema_value_type_invalid", source_path, pointer)
        return ""
    value = node.value
    if len(value) > maximum:
        diagnostics.add("schema_limit_exceeded", source_path, pointer)
    if nonempty and not value:
        diagnostics.add("schema_required_value_missing", source_path, pointer)
    return value


def _node_url(
    node: Node | None,
    *,
    source_path: str,
    pointer: str,
    diagnostics: _Diagnostics,
    contract: LegacyMainAdapterContract,
) -> str | None:
    if node is None:
        return None
    value = _node_string(
        node,
        source_path=source_path,
        pointer=pointer,
        diagnostics=diagnostics,
        maximum=contract.max_url_length,
    )
    if not value:
        return value
    if any(ord(character) < 0x20 or character.isspace() for character in value) or "\\" in value:
        diagnostics.add("url_invalid", source_path, pointer)
        return value
    if value.startswith("//"):
        diagnostics.add("url_scheme_forbidden", source_path, pointer)
        return value
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
    except ValueError:
        diagnostics.add("url_invalid", source_path, pointer)
        return value
    if parts.scheme.lower() != "https":
        diagnostics.add("url_scheme_forbidden", source_path, pointer)
    if not hostname or parts.username is not None or parts.password is not None:
        diagnostics.add("url_invalid", source_path, pointer)
    if parts.fragment:
        diagnostics.add("url_invalid", source_path, pointer)
    return value


def _node_asset_path(
    node: Node | None,
    *,
    source_path: str,
    pointer: str,
    family: str,
    diagnostics: _Diagnostics,
    contract: LegacyMainAdapterContract,
) -> str:
    value = (
        _node_string(
            node,
            source_path=source_path,
            pointer=pointer,
            diagnostics=diagnostics,
            maximum=contract.max_url_length,
        )
        if node is not None
        else ""
    )
    return (
        _asset_path(
            value,
            source_path=source_path,
            pointer=pointer,
            diagnostics=diagnostics,
            family=family,
        )
        if value
        else ""
    )


def _parse_talk(
    node: Node,
    *,
    variant: str,
    source_path: str,
    pointer: str,
    contract: LegacyMainAdapterContract,
    diagnostics: _Diagnostics,
) -> TalkSourceRecord:
    if variant == "track_date_talk_time":
        allowed = frozenset({"name", "time", "speaker", "youtube", "abstract"})
    else:
        allowed = frozenset({"speaker", "name", "date", "eventbrite", "youtube", "abstract"})
    mapping = _fields(
        node,
        allowed=allowed,
        source_path=source_path,
        pointer=pointer,
        diagnostics=diagnostics,
    )
    name = _required_string(
        mapping,
        "name",
        source_path=source_path,
        pointer=pointer,
        diagnostics=diagnostics,
        maximum=contract.max_name_length,
    )
    abstract = _abstract(
        mapping,
        "abstract",
        source_path=source_path,
        pointer=pointer,
        diagnostics=diagnostics,
        contract=contract,
    )
    speaker_node = mapping.get("speaker")
    speaker_company: str | None = None
    if variant == "track_date_talk_time":
        speaker_key = _person(
            speaker_node,
            source_path=source_path,
            pointer=_pointer_join(pointer, "speaker"),
            diagnostics=diagnostics,
            contract=contract,
        )
        time = _time(
            mapping,
            "time",
            source_path=source_path,
            pointer=pointer,
            diagnostics=diagnostics,
        )
        date = None
        eventbrite = None
    else:
        speaker_mapping = _fields(
            speaker_node,
            allowed=frozenset({"id", "company"}),
            source_path=source_path,
            pointer=_pointer_join(pointer, "speaker"),
            diagnostics=diagnostics,
        )
        speaker_key = _person(
            speaker_mapping.get("id"),
            source_path=source_path,
            pointer=_pointer_join(_pointer_join(pointer, "speaker"), "id"),
            diagnostics=diagnostics,
            contract=contract,
        )
        if "company" in speaker_mapping:
            speaker_company = _node_string(
                speaker_mapping["company"],
                source_path=source_path,
                pointer=_pointer_join(_pointer_join(pointer, "speaker"), "company"),
                diagnostics=diagnostics,
                maximum=contract.max_company_length,
            )
        date = _date(
            mapping,
            "date",
            source_path=source_path,
            pointer=pointer,
            diagnostics=diagnostics,
        )
        eventbrite = _eventbrite(
            mapping,
            "eventbrite",
            source_path=source_path,
            pointer=pointer,
            diagnostics=diagnostics,
        )
        time = None
    youtube = _node_url(
        mapping.get("youtube"),
        source_path=source_path,
        pointer=_pointer_join(pointer, "youtube"),
        diagnostics=diagnostics,
        contract=contract,
    )
    return TalkSourceRecord(
        name=name,
        abstract_fragment=abstract,
        speaker_key=speaker_key,
        speaker_company=speaker_company,
        schedule_variant=variant,
        time=time,
        date=date,
        eventbrite=eventbrite,
        youtube=youtube,
    )


def _parse_track(
    node: Node,
    *,
    variant: str,
    source_path: str,
    pointer: str,
    contract: LegacyMainAdapterContract,
    diagnostics: _Diagnostics,
) -> TrackSourceRecord:
    if variant == "track_date_talk_time":
        allowed = frozenset({"name", "date", "eventbrite", "youtube", "talks"})
    else:
        allowed = frozenset({"name", "eventbrite", "start", "end", "talks"})
    mapping = _fields(
        node,
        allowed=allowed,
        source_path=source_path,
        pointer=pointer,
        diagnostics=diagnostics,
    )
    name = _required_string(
        mapping,
        "name",
        source_path=source_path,
        pointer=pointer,
        diagnostics=diagnostics,
        maximum=contract.max_name_length,
    )
    eventbrite = _eventbrite(
        mapping,
        "eventbrite",
        source_path=source_path,
        pointer=pointer,
        diagnostics=diagnostics,
    )
    if variant == "track_date_talk_time":
        date = _date(
            mapping,
            "date",
            source_path=source_path,
            pointer=pointer,
            diagnostics=diagnostics,
        )
        youtube = _node_url(
            mapping.get("youtube"),
            source_path=source_path,
            pointer=_pointer_join(pointer, "youtube"),
            diagnostics=diagnostics,
            contract=contract,
        )
        start = end = None
    else:
        start = _date(
            mapping,
            "start",
            source_path=source_path,
            pointer=pointer,
            diagnostics=diagnostics,
        )
        end = _date(
            mapping,
            "end",
            source_path=source_path,
            pointer=pointer,
            diagnostics=diagnostics,
        )
        date = None
        youtube = None
    talk_nodes = _sequence(
        mapping,
        "talks",
        source_path=source_path,
        pointer=pointer,
        diagnostics=diagnostics,
        maximum=contract.max_talks,
    )
    talks = tuple(
        _parse_talk(
            talk_node,
            variant=variant,
            source_path=source_path,
            pointer=f"{_pointer_join(pointer, 'talks')}/{index}",
            contract=contract,
            diagnostics=diagnostics,
        )
        for index, talk_node in enumerate(talk_nodes)
    )
    return TrackSourceRecord(
        name=name,
        schedule_variant=variant,
        eventbrite=eventbrite,
        talks=talks,
        date=date,
        youtube=youtube,
        start=start,
        end=end,
    )


def _parse_partner(
    node: Node,
    *,
    source_path: str,
    pointer: str,
    contract: LegacyMainAdapterContract,
    diagnostics: _Diagnostics,
) -> PartnerSourceRecord:
    mapping = _fields(
        node,
        allowed=frozenset({"name", "link", "image"}),
        source_path=source_path,
        pointer=pointer,
        diagnostics=diagnostics,
    )
    name = _required_string(
        mapping,
        "name",
        source_path=source_path,
        pointer=pointer,
        diagnostics=diagnostics,
        maximum=contract.max_name_length,
    )
    link = _url(
        mapping,
        "link",
        source_path=source_path,
        pointer=pointer,
        diagnostics=diagnostics,
    )
    image = _node_asset_path(
        mapping.get("image"),
        source_path=source_path,
        pointer=_pointer_join(pointer, "image"),
        family="partners",
        diagnostics=diagnostics,
        contract=contract,
    )
    return PartnerSourceRecord(name=name, link=link, image_asset_path=image)


def _parse_conference(
    selected: LegacySelectedFile,
    raw: bytes,
    *,
    contract: LegacyMainAdapterContract,
    diagnostics: _Diagnostics,
) -> tuple[ConferenceSourceRecord | None, tuple[AssetReference, ...], tuple[str, ...]]:
    framed = _frontmatter_and_body(
        raw, selected=selected, contract=contract, diagnostics=diagnostics
    )
    if framed is None:
        return None, (), ()
    text, body = framed
    key = PurePosixPath(selected.path).stem
    if _STABLE_KEY.fullmatch(key) is None:
        diagnostics.add("stable_key_invalid", selected.path, "/")
    body_evidence = _body_ok(
        key,
        body,
        selected=selected,
        contract=contract,
        diagnostics=diagnostics,
    )
    node = _yaml_preflight(
        text, source_path=selected.path, diagnostics=diagnostics, contract=contract
    )
    mapping = _fields(
        node,
        allowed=frozenset({"title", "description", "image", "layout", "tracks", "partners"}),
        source_path=selected.path,
        pointer="/",
        diagnostics=diagnostics,
    )
    title = _required_string(
        mapping,
        "title",
        source_path=selected.path,
        pointer="/",
        diagnostics=diagnostics,
        maximum=contract.max_title_length,
    )
    description = _required_string(
        mapping,
        "description",
        source_path=selected.path,
        pointer="/",
        diagnostics=diagnostics,
        maximum=contract.max_description_length,
    )
    cover = _node_asset_path(
        mapping.get("image"),
        source_path=selected.path,
        pointer="/image",
        family="other",
        diagnostics=diagnostics,
        contract=contract,
    )
    layout = _required_string(
        mapping,
        "layout",
        source_path=selected.path,
        pointer="/",
        diagnostics=diagnostics,
        maximum=32,
    )
    if layout and layout != "page":
        diagnostics.add("schema_value_type_invalid", selected.path, "/layout")
    if key == "2021-feb":
        variant = "track_date_talk_time"
    elif key == "2021-summer-marathon":
        variant = "track_window_talk_datetime"
    else:
        diagnostics.add("stable_key_invalid", selected.path, "/")
        variant = "track_date_talk_time"
    track_nodes = _sequence(
        mapping,
        "tracks",
        source_path=selected.path,
        pointer="/",
        diagnostics=diagnostics,
        maximum=contract.max_tracks,
    )
    tracks = tuple(
        _parse_track(
            track_node,
            variant=variant,
            source_path=selected.path,
            pointer=f"/tracks/{index}",
            contract=contract,
            diagnostics=diagnostics,
        )
        for index, track_node in enumerate(track_nodes)
    )
    partner_nodes = _sequence(
        mapping,
        "partners",
        source_path=selected.path,
        pointer="/",
        diagnostics=diagnostics,
        maximum=contract.max_partners,
    )
    partners = tuple(
        _parse_partner(
            partner_node,
            source_path=selected.path,
            pointer=f"/partners/{index}",
            contract=contract,
            diagnostics=diagnostics,
        )
        for index, partner_node in enumerate(partner_nodes)
    )
    references: list[AssetReference] = []
    person_keys: list[str] = []
    if cover:
        references.append(AssetReference("conference", key, "/image"))
    for index, partner in enumerate(partners):
        if partner.image_asset_path:
            references.append(AssetReference("conference", key, f"/partners/{index}/image"))
    for track in tracks:
        person_keys.extend(talk.speaker_key for talk in track.talks if talk.speaker_key)
    if body_evidence is None:
        body_size, body_sha = 0, ""
    else:
        body_size, body_sha = body_evidence
    record = ConferenceSourceRecord(
        key=key,
        source_path=selected.path,
        git_blob_sha=selected.git_blob_sha,
        byte_size=selected.byte_size,
        sha256=selected.sha256,
        title=title,
        description=description,
        cover_asset_path=cover,
        layout=layout,
        tracks=tracks,
        partners=partners,
        legacy_body_size=body_size,
        legacy_body_sha256=body_sha,
    )
    return record, tuple(references), tuple(person_keys)


def _asset_mime(raw: bytes, path: str) -> str | None:
    if path.lower().endswith((".jpg", ".jpeg")) and raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if path.lower().endswith(".png") and raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return None


def _parse_assets(
    root: Path,
    *,
    selected_assets: Sequence[LegacySelectedFile],
    references: Mapping[str, Sequence[AssetReference]],
    contract: LegacyMainAdapterContract,
    diagnostics: _Diagnostics,
) -> tuple[AssetSourceRecord, ...]:
    records: list[AssetSourceRecord] = []
    selected_paths = {item.path for item in selected_assets}
    referenced_paths = set(references)
    for selected in sorted(selected_assets, key=lambda item: item.path):
        raw = _read_selected(root, selected, contract=contract, diagnostics=diagnostics)
        if raw is None:
            continue
        mime = _asset_mime(raw, selected.path)
        if mime is None:
            diagnostics.add("asset_type_invalid", selected.path, "/")
            continue
        asset_references = tuple(references.get(selected.path, ()))
        if not asset_references:
            diagnostics.add("asset_missing", selected.path, "/references")
        records.append(
            AssetSourceRecord(
                source_path=selected.path,
                git_blob_sha=selected.git_blob_sha,
                byte_size=selected.byte_size,
                sha256=selected.sha256,
                mime_family=mime,
                references=tuple(
                    sorted(
                        asset_references,
                        key=lambda reference: (
                            reference.record_kind,
                            reference.record_key,
                            reference.pointer,
                        ),
                    )
                ),
            )
        )
    for _path in sorted(referenced_paths - selected_paths):
        # The parser never reads a referenced non-contract path. Its static normalized path is
        # sufficient to identify the failure without exposing arbitrary source text.
        diagnostics.add("asset_missing", "<record>", "/asset")
    return tuple(records)


def _normalize_origin(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    if value in {
        "git@github.com:DataTalksClub/datatalksclub.github.io.git",
        "https://github.com/DataTalksClub/datatalksclub.github.io",
        "https://github.com/DataTalksClub/datatalksclub.github.io.git",
    }:
        return LEGACY_MAIN_REPOSITORY
    return None


def _validate_metadata(
    *,
    origin: str,
    commit: str,
    tree: str,
    clean: bool,
    contract: LegacyMainAdapterContract,
    diagnostics: _Diagnostics,
) -> None:
    if _normalize_origin(origin) != contract.repository:
        diagnostics.add("source_origin_mismatch", ".", "/")
    if (
        not isinstance(commit, str)
        or commit != contract.commit
        or _HEX_40.fullmatch(commit) is None
    ):
        diagnostics.add("source_revision_mismatch", ".", "/")
    if not isinstance(tree, str) or tree != contract.tree or _HEX_40.fullmatch(tree) is None:
        diagnostics.add("source_tree_mismatch", ".", "/")
    if type(clean) is not bool or not clean:
        diagnostics.add("source_checkout_dirty", ".", "/")


def _validate_collection_directories(
    root: Path,
    *,
    contract: LegacyMainAdapterContract,
    diagnostics: _Diagnostics,
) -> None:
    tool_names = frozenset(
        PurePosixPath(item.path).name for item in contract.record_files if item.kind == "tool"
    )
    conference_names = frozenset(
        PurePosixPath(item.path).name for item in contract.record_files if item.kind == "conference"
    )
    _check_collection_paths(
        root,
        relative_directory="_tools",
        allowed=tool_names,
        diagnostics=diagnostics,
    )
    _check_collection_paths(
        root,
        relative_directory="_conferences",
        allowed=conference_names,
        diagnostics=diagnostics,
    )
    _check_directory(root, "images", diagnostics)
    _check_directory(root, "images/other", diagnostics)
    _check_directory(root, "images/partners", diagnostics)


def _counts(
    *,
    tools: Sequence[ToolSourceRecord],
    conferences: Sequence[ConferenceSourceRecord],
    assets: Sequence[AssetSourceRecord],
    person_keys: Sequence[str],
    references: Mapping[str, Sequence[AssetReference]],
) -> dict[str, int]:
    return {
        "tools": len(tools),
        "conferences": len(conferences),
        "tracks": sum(len(conference.tracks) for conference in conferences),
        "talks": sum(len(track.talks) for conference in conferences for track in conference.tracks),
        "partners": sum(len(conference.partners) for conference in conferences),
        "asset_references": sum(len(items) for items in references.values()),
        "assets": len(assets),
        "person_references": len(person_keys),
        "person_keys": len(set(person_keys)),
    }


def _parse(
    checkout: Path,
    *,
    origin: str,
    commit: str,
    tree: str,
    clean: bool,
    contract: LegacyMainAdapterContract,
) -> LegacyMainBundle:
    diagnostics = _Diagnostics(contract.max_diagnostics)
    _validate_metadata(
        origin=origin,
        commit=commit,
        tree=tree,
        clean=clean,
        contract=contract,
        diagnostics=diagnostics,
    )
    try:
        mode = checkout.lstat().st_mode
    except OSError:
        diagnostics.add("source_path_missing", ".", "/")
        raise LegacyMainValidationError(diagnostics.finish()) from None
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        diagnostics.add("source_path_type_invalid", ".", "/")
        raise LegacyMainValidationError(diagnostics.finish())

    _validate_collection_directories(checkout, contract=contract, diagnostics=diagnostics)
    source_bytes = 0
    files: dict[str, bytes] = {}
    for selected in contract.record_files:
        raw = _read_selected(checkout, selected, contract=contract, diagnostics=diagnostics)
        if raw is not None:
            source_bytes += len(raw)
            files[selected.path] = raw
    if source_bytes > contract.max_source_bytes or source_bytes != sum(
        item.byte_size for item in contract.record_files
    ):
        diagnostics.add("source_corpus_size_invalid", ".", "/")
    if contract.selected_bytes > contract.max_source_bytes:
        diagnostics.add("source_corpus_size_invalid", ".", "/")

    tools: list[ToolSourceRecord] = []
    conferences: list[ConferenceSourceRecord] = []
    references_by_path: dict[str, list[AssetReference]] = {}
    person_keys: list[str] = []
    # Record-kind ordering is part of the evidence contract: tools precede conferences, while
    # each kind retains source-path order. Nested tracks/talks/partners remain source ordered.
    for selected in sorted(
        contract.record_files,
        key=lambda item: (0 if item.kind == "tool" else 1, item.path),
    ):
        raw = files.get(selected.path)
        if raw is None:
            continue
        if selected.kind == "tool":
            tool_record = _parse_tool(selected, raw, contract=contract, diagnostics=diagnostics)
            if tool_record is not None:
                tools.append(tool_record)
                person_keys.extend(tool_record.maintainer_keys)
        else:
            conference_record, references, conference_people = _parse_conference(
                selected,
                raw,
                contract=contract,
                diagnostics=diagnostics,
            )
            if conference_record is not None:
                conferences.append(conference_record)
                person_keys.extend(conference_people)
                for reference in references:
                    # Recover the normalized source path from the static source data. The
                    # reference pointer is safe and the conference object carries no raw body.
                    if reference.pointer == "/image":
                        asset_path = conference_record.cover_asset_path
                    else:
                        index = int(reference.pointer.split("/")[2])
                        asset_path = conference_record.partners[index].image_asset_path
                    if asset_path:
                        references_by_path.setdefault(asset_path.removeprefix("/"), []).append(
                            reference
                        )

    assets = _parse_assets(
        checkout,
        selected_assets=contract.asset_files,
        references=references_by_path,
        contract=contract,
        diagnostics=diagnostics,
    )
    observed_counts = _counts(
        tools=tools,
        conferences=conferences,
        assets=assets,
        person_keys=person_keys,
        references=references_by_path,
    )
    expected_counts = dict(contract.expected_counts)
    if observed_counts != expected_counts:
        diagnostics.add("source_corpus_size_invalid", ".", "/counts")
    if tuple(person_keys) != contract.expected_person_keys:
        diagnostics.add("person_key_invalid", ".", "/person_keys")
    diagnostics.raise_if_any()
    provenance = LegacyMainProvenance(
        repository=contract.repository,
        commit=contract.commit,
        tree=contract.tree,
        parser_version=contract.parser_version,
        schema_version=contract.schema_version,
        selected_manifest_sha256=contract.selected_manifest_sha256,
        counts=MappingProxyType(expected_counts),
    )
    return LegacyMainBundle(
        provenance=provenance,
        tools=tuple(tools),
        conferences=tuple(conferences),
        assets=assets,
    )


def parse_legacy_main_checkout(
    checkout: str | Path,
    *,
    origin: str = LEGACY_MAIN_REPOSITORY,
    commit: str = LEGACY_MAIN_COMMIT,
    tree: str = LEGACY_MAIN_TREE,
    clean: bool = True,
    contract: LegacyMainAdapterContract = LEGACY_MAIN_CONTRACT,
    source_origin: str | None = None,
    source_commit: str | None = None,
    source_tree: str | None = None,
    checkout_clean: bool | None = None,
) -> LegacyMainBundle:
    """Parse an already verified checkout without any external side effect."""

    try:
        # ``contract`` is intentionally a capability token, not a customization hook.  Checking
        # identity before invoking any method prevents a duck-typed or malicious replacement from
        # selecting another source corpus or relaxing a parser limit.
        if contract is not LEGACY_MAIN_CONTRACT:
            raise LegacyMainValidationError(
                (LegacyMainDiagnostic("legacy_main_parser_failed", ".", "/"),)
            )
        contract.validate()
        if source_origin is not None:
            origin = source_origin
        if source_commit is not None:
            commit = source_commit
        if source_tree is not None:
            tree = source_tree
        if checkout_clean is not None:
            clean = checkout_clean
        return _parse(
            Path(checkout),
            origin=origin,
            commit=commit,
            tree=tree,
            clean=clean,
            contract=contract,
        )
    except LegacyMainValidationError:
        raise
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        # Unexpected parser failures have one fixed content-free diagnostic. In particular, do
        # not propagate YAML, filesystem, or Python exception text across the adapter boundary.
        raise LegacyMainValidationError(
            (LegacyMainDiagnostic("legacy_main_parser_failed", ".", "/"),)
        ) from None


parse_legacy_main = parse_legacy_main_checkout
adapt_legacy_main_checkout = parse_legacy_main_checkout
