#!/usr/bin/env python3
"""Validate the checked-in GitHub editorial source/projection evidence slice.

The inventory is intentionally Markdown for human review.  This validator keeps its
machine-readable contract fail-closed without importing Django, parsing YAML with a
third-party package, contacting GitHub, opening a database, or writing any repository
artifact.  Every input is a known, checked-in path below the current repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

REPOSITORY = "DataTalksClub/website"
AUDIT_PATH = Path("_docs/audits/2026-08-14-github-editorial-source-projection-inventory.md")
EXPECTED_REF = "refs/heads/main"
EXPECTED_SNAPSHOT_SHA = "539bd8c6ff73661e174af7183f6f49d181efa1fa"
SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
UTC_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
LINK_PATTERN = re.compile(r"\[[^\]]+\]\((?P<target>[^)]+)\)")
ISSUE_LINK_PATTERN = re.compile(
    rf"^https://github\.com/{re.escape(REPOSITORY)}/issues/(?P<number>\d+)$"
)
SOURCE_ID_PATTERN = re.compile(r"^\s{2}- id:\s*(?P<id>[^\s#]+)\s*$")
SOURCE_FIELD_PATTERN = re.compile(r"^\s{4}(?P<key>[a-z_]+):\s*(?P<value>.*)\s*$")

CONTENT_REVISION = "e29f56ce70bd997171a78a9f0facc9354797f421"
CONTENT_TREE = "c82b0c6ff462dcdd7140f03f2e7d884ed10ff8fa"
CONTENT_COUNTS = (
    "articles=55; podcasts=205; separate_transcripts=203; books=98; source_owned_media=815"
)
EDITORIAL_OVERLAY_SHA = "63969508134e8b2ef3c8471e9c8dbccc96842fcfc25225fe02e1ed5a4f5926f6"
REPAIR_MANIFEST_SHA = "80d3014c47bf57de792473fc1da8f7569daeb55107688c3485153f773948d3aa"
CONTENT_PATTERNS = (
    "articles/*.md",
    "podcasts/*.yaml",
    "podcasts/transcripts/*.yaml",
    "books/*.yaml",
    "images/posts/**",
    "images/podcast/**",
    "images/books/**",
    "migration.yaml",
    "repairs/2026-08-09-missing-media.yaml",
    "editorial-overlays/2026-08-10-podcast-descriptions.yaml",
)

SOURCE_PINS: dict[str, dict[str, str]] = {
    "dtc-main-site": {
        "repository": "DataTalksClub/datatalksclub.github.io",
        "revision": "ee43d3fa0929faf691178d79f19528e6f15a83e5",
        "authority": "primary",
        "page": "wiki/sources/dtc-main-site.md",
    },
    "dtc-docs": {
        "repository": "DataTalksClub/docs",
        "revision": "3f23e006ffdaa498bbc69697408853b6f5eb37dc",
        "authority": "primary",
        "page": "wiki/sources/dtc-docs.md",
    },
    "dtc-faq": {
        "repository": "DataTalksClub/faq",
        "revision": "c8da1deea9e24945922702994de101dd90a5380a",
        "authority": "primary",
        "page": "wiki/sources/dtc-faq.md",
    },
    "dtc-podwiki": {
        "repository": "DataTalksClub/podwiki",
        "revision": "988b79d0d655bf4755945c3118544cb9e0dbead6",
        "authority": "primary",
        "page": "wiki/sources/dtc-podwiki.md",
    },
}

MANIFEST_ARTIFACTS = {
    "articles.json": "119972c04b7b6df972f35950bea20e00ff7f90f7820c07105126fa0b616211db",
    "books.json": "64f14434dee15dd12e12ae510554a80dfe5d635022f431ee552a05c0e0511c5f",
    "courses.json": "318d7cb156cdcf74f346695d3db2b526e81c3321426c33365e91d3f471211c4d",
    "editorial_route_migration.json": "fd68c7e1dd474303e3839c71e0dab0143a0d8ff7c16ce6df71be340cddd64078",
    "events.json": "260eeeb2974a436b80621d87df30bfea743273b3d38a6dfe9532dfb7d99f00ec",
    "media.json": "6b6670d01407c72649f89a7671e240d7c75d9653b9bb25b30f362e14b0325aea",
    "people.json": "f1bc223aee48ff614bcc24351f3253897459b1b7e75ea70ecd5dec98ff1b0a44",
    "podcasts.json": "33409b09c184a02ff6b685d805d9ad05d74bb15d5b34ea86f34c5a10b4cb0c8d",
    "wiki.json": "5f64f4d4a7a7436830d5a5c039e081d82fbf47ac01b7fb891fa6c5816650ce68",
    "wiki_graph.json": "07f433eab8c818abf4a2d270c1f9a582bc450c16b7ffbbee344b8998eeb4ebb8",
    "wiki_search.json": "e8f82b7471ce9152f994f2dfc3ef370b8d2a98384834051985dd45c5269f7307",
}
MANIFEST_COUNTS = {
    "articles": 55,
    "books": 98,
    "courses": 12,
    "events": 421,
    "media": 1253,
    "people": 438,
    "podcasts": 205,
    "transcripts": 203,
    "wiki": 282,
}
MANIFEST_MEMBER_TEXT = ", ".join(sorted(MANIFEST_ARTIFACTS))
WIKI_GRAPH_COUNTS = {
    "articles": 80,
    "books": 98,
    "comparisons": 24,
    "guides": 25,
    "how_tos": 4,
    "links": 13006,
    "nodes": 1072,
    "persons": 439,
    "podcasts": 205,
    "roadmaps": 12,
    "topics": 48,
    "transitions": 15,
    "wikis": 202,
}


@dataclass(frozen=True)
class ArtifactExpectation:
    key: str
    path: str
    source: str
    counts: str
    sha256: str


ARTIFACTS = (
    ArtifactExpectation(
        "articles",
        "content/public_projection/articles.json",
        f"{CONTENT_REVISION} / DataTalksClub/content",
        "items=55",
        "119972c04b7b6df972f35950bea20e00ff7f90f7820c07105126fa0b616211db",
    ),
    ArtifactExpectation(
        "podcasts/transcripts",
        "content/public_projection/podcasts.json",
        f"{CONTENT_REVISION} / DataTalksClub/content",
        "episodes=205; separate_transcripts=203",
        "33409b09c184a02ff6b685d805d9ad05d74bb15d5b34ea86f34c5a10b4cb0c8d",
    ),
    ArtifactExpectation(
        "books",
        "content/public_projection/books.json",
        f"{CONTENT_REVISION} / DataTalksClub/content",
        "items=98",
        "64f14434dee15dd12e12ae510554a80dfe5d635022f431ee552a05c0e0511c5f",
    ),
    ArtifactExpectation(
        "people",
        "content/public_projection/people.json",
        f"{SOURCE_PINS['dtc-main-site']['revision']} / DataTalksClub/datatalksclub.github.io",
        "person_details=438; public_catalogue=absent",
        "f1bc223aee48ff614bcc24351f3253897459b1b7e75ea70ecd5dec98ff1b0a44",
    ),
    ArtifactExpectation(
        "events",
        "content/public_projection/events.json",
        f"{SOURCE_PINS['dtc-main-site']['revision']} / DataTalksClub/datatalksclub.github.io",
        "items=421",
        "260eeeb2974a436b80621d87df30bfea743273b3d38a6dfe9532dfb7d99f00ec",
    ),
    ArtifactExpectation(
        "courses",
        "content/public_projection/courses.json",
        "98a235283904b4ef9ad29e196298540756cf1bcc / DataTalksClub/course-management-platform",
        "items=12",
        "318d7cb156cdcf74f346695d3db2b526e81c3321426c33365e91d3f471211c4d",
    ),
    ArtifactExpectation(
        "media",
        "content/public_projection/media.json",
        f"{CONTENT_REVISION} + {SOURCE_PINS['dtc-main-site']['revision']} / DataTalksClub/content + DataTalksClub/datatalksclub.github.io",
        "total=1253; content=815; legacy_main_portraits=438",
        "6b6670d01407c72649f89a7671e240d7c75d9653b9bb25b30f362e14b0325aea",
    ),
    ArtifactExpectation(
        "FAQ",
        "content/faq_projection.json",
        f"{SOURCE_PINS['dtc-faq']['revision']} / DataTalksClub/faq",
        "courses=6; sections=70; questions=1401; assets=99",
        "7b6e5723b2ab0cf453254c10fb06a08175ca2bee5b9c65d98cc0534acfe8f209",
    ),
    ArtifactExpectation(
        "docs",
        "content/docs_projection.json",
        f"{SOURCE_PINS['dtc-docs']['revision']} / DataTalksClub/docs",
        "pages=106; assets=39",
        "1abd84ab397e5ce70dd570e1f7c4d2d9a753b0fcaf82d9a74a6cfa571b153dd2",
    ),
    ArtifactExpectation(
        "wiki",
        "content/public_projection/wiki.json",
        f"{SOURCE_PINS['dtc-podwiki']['revision']} / DataTalksClub/podwiki",
        "pages=282",
        "5f64f4d4a7a7436830d5a5c039e081d82fbf47ac01b7fb891fa6c5816650ce68",
    ),
    ArtifactExpectation(
        "wiki graph",
        "content/public_projection/wiki_graph.json",
        f"{SOURCE_PINS['dtc-podwiki']['revision']} / DataTalksClub/podwiki",
        'nodes=1072; links=13006; counts_map={"articles":80,"books":98,"comparisons":24,"guides":25,"how_tos":4,"links":13006,"nodes":1072,"persons":439,"podcasts":205,"roadmaps":12,"topics":48,"transitions":15,"wikis":202}',
        "07f433eab8c818abf4a2d270c1f9a582bc450c16b7ffbbee344b8998eeb4ebb8",
    ),
    ArtifactExpectation(
        "wiki search",
        "content/public_projection/wiki_search.json",
        f"{SOURCE_PINS['dtc-podwiki']['revision']} / DataTalksClub/podwiki",
        "documents=2998",
        "e8f82b7471ce9152f994f2dfc3ef370b8d2a98384834051985dd45c5269f7307",
    ),
    ArtifactExpectation(
        "editorial route migration",
        "content/public_projection/editorial_route_migration.json",
        f"{CONTENT_REVISION} + {SOURCE_PINS['dtc-main-site']['revision']} / DataTalksClub/content + DataTalksClub/datatalksclub.github.io",
        "finals=796; aliases=1592",
        "fd68c7e1dd474303e3839c71e0dab0143a0d8ff7c16ce6df71be340cddd64078",
    ),
    ArtifactExpectation(
        "manifest",
        "content/public_projection/manifest.json",
        "preferred checked-in projection evidence / compatibility-evidence",
        "members=11; aggregate_counts=9; selection_mode=preferred",
        "b9ad483c9f3fb16de526d34f4e5ad7d776c4084099e659725af500620923b9cc",
    ),
)
ARTIFACT_BY_KEY = {artifact.key: artifact for artifact in ARTIFACTS}
OWNERSHIP_VALUES = frozenset(
    {"github-editorial-read", "database-operational", "studio-admin-api", "compatibility-evidence"}
)
BOUNDARIES = {
    "articles": "github-editorial-read",
    "podcasts/transcripts": "github-editorial-read",
    "books": "github-editorial-read",
    "source-owned content media": "github-editorial-read",
    "people": "github-editorial-read",
    "docs": "github-editorial-read",
    "FAQ": "github-editorial-read",
    "wiki": "github-editorial-read",
    "events projection": "compatibility-evidence",
    "courses projection": "compatibility-evidence",
    "database operational courses/cohorts/events/registrations": "database-operational",
    "Studio": "studio-admin-api",
    "/api/v1/admin/": "studio-admin-api",
    "legacy manifest and route migration": "compatibility-evidence",
}
RELATED_ISSUES = (34, 39, 41, 42, 43, 103, 105, 119, 132, 150, 152)
HANDOFFS = {
    12: "GitHub-backed editorial workflow and activation ownership remain an owner decision.",
    16: "Authenticated course API-consumer inventory and legacy-host redirect inventory remain an owner decision.",
    23: "Privacy ownership, retention, minors, and public-profile policy remain an owner decision.",
    24: "PostgreSQL search and public-search contract remain an owner decision.",
    27: "Analytics and tracking preservation remain an owner decision.",
}


class ValidationError(ValueError):
    """Raised when the checked-in inventory violates its frozen contract."""


def _strip_code(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
        return value[1:-1]
    return value


def _split_table_row(line: str) -> list[str]:
    value = line.strip()
    if not value.startswith("|"):
        raise ValidationError(f"table row must start with '|': {line!r}")
    value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [cell.strip() for cell in value.split("|")]


def _section(lines: list[str], heading: str) -> list[str]:
    try:
        start = lines.index(heading)
    except ValueError as exc:
        raise ValidationError(f"missing section: {heading}") from exc
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    return lines[start + 1 : end]


def _table(section_lines: list[str], heading: str, expected_headers: tuple[str, ...]) -> list[tuple[str, ...]]:
    header_index = next(
        (index for index, line in enumerate(section_lines) if line.startswith("|")), None
    )
    if header_index is None:
        raise ValidationError(f"{heading} has no Markdown table")
    headers = tuple(_split_table_row(section_lines[header_index]))
    if headers != expected_headers:
        raise ValidationError(f"{heading} table columns differ: {headers!r}")
    if header_index + 1 >= len(section_lines) or not section_lines[header_index + 1].startswith("|"):
        raise ValidationError(f"{heading} table is missing its separator row")
    rows: list[tuple[str, ...]] = []
    for line in section_lines[header_index + 2 :]:
        if not line.startswith("|"):
            break
        cells = tuple(_split_table_row(line))
        if len(cells) != len(expected_headers):
            raise ValidationError(
                f"{heading} row has {len(cells)} cells; expected {len(expected_headers)}"
            )
        rows.append(cells)
    if not rows:
        raise ValidationError(f"{heading} table has no rows")
    return rows


def _metadata_value(lines: list[str], label: str) -> str:
    prefix = f"- {label}: `"
    for line in lines:
        if line.startswith(prefix) and line.endswith("`"):
            return line[len(prefix) : -1]
    raise ValidationError(f"missing metadata field: {label}")


def _repository_root() -> Path:
    return Path.cwd().resolve()


def _read(root: Path, relative: str) -> bytes:
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValidationError(f"input path escapes repository: {relative!r}") from exc
    if not path.is_file():
        raise ValidationError(f"checked-in evidence path does not exist: {relative}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ValidationError(f"cannot read checked-in evidence path: {relative}") from exc


def _validate_local_links(
    cell: str,
    audit_path: Path,
    *,
    require_issue: bool = False,
    require_local: bool = True,
) -> set[int]:
    targets = [match.group("target") for match in LINK_PATTERN.finditer(cell)]
    if not targets:
        raise ValidationError("evidence cell must contain at least one Markdown link")
    root = _repository_root()
    issue_numbers: set[int] = set()
    local_count = 0
    for target in targets:
        issue_match = ISSUE_LINK_PATTERN.fullmatch(target)
        if issue_match:
            issue_numbers.add(int(issue_match.group("number")))
            continue
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            raise ValidationError(f"evidence link is not a local path or repository issue: {target!r}")
        relative = unquote(parsed.path)
        if not relative:
            raise ValidationError(f"evidence link has no path: {target!r}")
        path = (audit_path.parent / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValidationError(f"evidence link escapes repository: {target!r}") from exc
        if not path.is_file():
            raise ValidationError(f"evidence link does not exist: {target!r}")
        local_count += 1
    if require_local and local_count == 0:
        raise ValidationError("evidence cell needs a checked-in local path link")
    if require_issue and not issue_numbers:
        raise ValidationError("evidence cell needs an owning repository issue link")
    return issue_numbers


def _link_label(cell: str) -> tuple[str, str]:
    matches = list(LINK_PATTERN.finditer(cell))
    if len(matches) != 1:
        raise ValidationError(f"expected exactly one source-page link: {cell!r}")
    match = matches[0]
    full = match.group(0)
    return full[1 : full.index("](")], match.group("target")


def _parse_utc(value: str, label: str) -> None:
    if not UTC_PATTERN.fullmatch(value):
        raise ValidationError(f"{label} must be an explicit UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{label} is not a valid timestamp") from exc


def _validate_metadata(lines: list[str]) -> None:
    if _metadata_value(lines, "Snapshot repository") != REPOSITORY:
        raise ValidationError("snapshot repository differs from DataTalksClub/website")
    if _metadata_value(lines, "Snapshot ref") != EXPECTED_REF:
        raise ValidationError("snapshot ref must be refs/heads/main")
    snapshot_sha = _metadata_value(lines, "Snapshot SHA")
    if not SHA1_PATTERN.fullmatch(snapshot_sha) or snapshot_sha != EXPECTED_SNAPSHOT_SHA:
        raise ValidationError("snapshot SHA differs from the accepted current-main snapshot")
    _parse_utc(_metadata_value(lines, "Captured at (UTC)"), "capture time")


def _parse_source_index(root: Path) -> dict[str, list[dict[str, str]]]:
    entries: dict[str, list[dict[str, str]]] = {}
    current: dict[str, str] | None = None
    current_id: str | None = None
    for raw_line in _read(root, "_docs/planning/sources/index.yaml").decode("utf-8").splitlines():
        id_match = SOURCE_ID_PATTERN.match(raw_line)
        if id_match:
            current_id = id_match.group("id")
            current = {"id": current_id}
            entries.setdefault(current_id, []).append(current)
            continue
        if current is None:
            continue
        field_match = SOURCE_FIELD_PATTERN.match(raw_line)
        if field_match:
            value = field_match.group("value").strip()
            if value.startswith("#"):
                value = ""
            current[field_match.group("key")] = value
    return entries


def _validate_source_pins(root: Path, lines: list[str], audit_path: Path) -> None:
    rows = _table(
        _section(lines, "## Selected source pins"),
        "selected source pins",
        ("Source key", "Repository", "Full immutable revision", "Authority", "Source page"),
    )
    if len(rows) != len(SOURCE_PINS):
        raise ValidationError(f"selected source table must contain exactly {len(SOURCE_PINS)} rows")
    seen: set[str] = set()
    for cells in rows:
        key = _strip_code(cells[0])
        if key in seen:
            raise ValidationError(f"duplicate selected source row: {key}")
        seen.add(key)
        expected = SOURCE_PINS.get(key)
        if expected is None:
            raise ValidationError(f"unknown selected source row: {key}")
        if _strip_code(cells[1]) != expected["repository"]:
            raise ValidationError(f"source repository drift for {key}")
        revision = _strip_code(cells[2])
        if not SHA1_PATTERN.fullmatch(revision) or revision != expected["revision"]:
            raise ValidationError(f"source revision drift for {key}")
        if _strip_code(cells[3]) != expected["authority"]:
            raise ValidationError(f"source authority drift for {key}")
        label, target = _link_label(cells[4])
        label = _strip_code(label)
        if label != expected["page"] or target != f"../planning/{expected['page']}":
            raise ValidationError(f"source page drift for {key}")
        _validate_local_links(cells[4], audit_path)
    if seen != set(SOURCE_PINS):
        raise ValidationError("selected source coverage differs from the exact four-source set")

    index_entries = _parse_source_index(root)
    for key, expected in SOURCE_PINS.items():
        entries = index_entries.get(key, [])
        if len(entries) != 1:
            raise ValidationError(f"source index must contain exactly one {key} entry")
        entry = entries[0]
        for field in ("authority", "page", "status"):
            if entry.get(field) != (expected[field] if field in expected else "selected"):
                raise ValidationError(f"source index {key} {field} drift")
        if entry.get("locator") != f"https://github.com/{expected['repository']}/tree/{expected['revision']}":
            raise ValidationError(f"source index {key} locator must remain pinned")
        if not SHA1_PATTERN.fullmatch(expected["revision"]):
            raise ValidationError(f"source index {key} expected revision is not a full SHA")


def _validate_content_contract(root: Path, lines: list[str], audit_path: Path) -> None:
    section = _section(lines, "## DataTalksClub/content contract")
    if _metadata_value(section, "Content source revision") != CONTENT_REVISION:
        raise ValidationError("content source revision drift")
    if _metadata_value(section, "Content source tree") != CONTENT_TREE:
        raise ValidationError("content source tree drift")
    if _metadata_value(section, "Content source counts") != CONTENT_COUNTS:
        raise ValidationError("content source counts drift")
    if _metadata_value(section, "Editorial overlay SHA-256") != EDITORIAL_OVERLAY_SHA:
        raise ValidationError("editorial overlay checksum drift")
    if _metadata_value(section, "Repair manifest SHA-256") != REPAIR_MANIFEST_SHA:
        raise ValidationError("repair manifest checksum drift")
    if "separate transcript identity" not in _metadata_value(section, "Transcript provenance boundary"):
        raise ValidationError("separate transcript provenance boundary is missing")
    if "one authoritative file" not in _metadata_value(section, "Source-owned edit/provenance boundary"):
        raise ValidationError("source-owned edit/provenance boundary is missing")

    content_authoring = _read(root, "_docs/content-authoring.md").decode("utf-8")
    if f"`{CONTENT_REVISION}`, tree\n`{CONTENT_TREE}`" not in content_authoring:
        raise ValidationError("content-authoring source revision/tree drift")
    if "with 55 articles, 205 podcasts, 203 separate\ntranscripts, 98 books, and 815 content media" not in content_authoring:
        raise ValidationError("content-authoring source counts drift")
    if f"`{EDITORIAL_OVERLAY_SHA}`" not in content_authoring:
        raise ValidationError("content-authoring editorial overlay evidence drift")
    match = re.search(
        r"versioned adapter reads only:\n\n```text\n(?P<body>.*?)\n```",
        content_authoring,
        re.DOTALL,
    )
    if match is None:
        raise ValidationError("content-authoring allowed-pattern block is missing")
    patterns = tuple(line.strip() for line in match.group("body").splitlines() if line.strip())
    if patterns != CONTENT_PATTERNS:
        raise ValidationError("content-authoring allowed patterns drift")
    pattern_rows = _table(
        section,
        "content contract",
        ("Pattern", "Contract role", "Evidence"),
    )
    if len(pattern_rows) != len(CONTENT_PATTERNS):
        raise ValidationError("content pattern inventory coverage differs")
    seen: set[str] = set()
    for cells in pattern_rows:
        pattern = _strip_code(cells[0])
        if pattern in seen or pattern not in CONTENT_PATTERNS:
            raise ValidationError(f"unknown or duplicate content pattern: {pattern}")
        seen.add(pattern)
        _validate_local_links(cells[2], audit_path)
    if seen != set(CONTENT_PATTERNS):
        raise ValidationError("content pattern inventory coverage differs")

    manifest = _load_json(root, "content/public_projection/manifest.json")
    preferred = manifest.get("sources", {}).get("preferred_content", {})
    if preferred.get("revision") != CONTENT_REVISION or preferred.get("tree") != CONTENT_TREE:
        raise ValidationError("manifest preferred content source drift")
    if preferred.get("editorial_overlay_sha256") != EDITORIAL_OVERLAY_SHA:
        raise ValidationError("manifest editorial overlay checksum drift")
    if preferred.get("repair_manifest_sha256") != REPAIR_MANIFEST_SHA:
        raise ValidationError("manifest repair checksum drift")


def _load_json(root: Path, relative: str) -> Any:
    try:
        return json.loads(_read(root, relative).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid JSON evidence: {relative}") from exc


def _file_sha256(root: Path, relative: str) -> str:
    return hashlib.sha256(_read(root, relative)).hexdigest()


def _provenance_set(records: list[Any], path: str) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("provenance"), dict):
            raise ValidationError(f"{path} record lacks provenance")
        provenance = record["provenance"]
        repository = provenance.get("repository")
        revision = provenance.get("revision")
        if not isinstance(repository, str) or not isinstance(revision, str):
            raise ValidationError(f"{path} record provenance is malformed")
        result.add((repository, revision))
    return result


def _validate_projection_json(root: Path, artifact: ArtifactExpectation) -> None:
    if artifact.key == "manifest":
        manifest = _load_json(root, artifact.path)
        if manifest.get("schema_version") != 1 or manifest.get("selection_mode") != "preferred":
            raise ValidationError("projection manifest schema/selection drift")
        if manifest.get("artifacts") != MANIFEST_ARTIFACTS:
            raise ValidationError("projection manifest member/hash map drift")
        if manifest.get("counts") != MANIFEST_COUNTS:
            raise ValidationError("projection manifest counts drift")
        return

    data = _load_json(root, artifact.path)
    if artifact.key in {"FAQ", "docs", "wiki graph", "wiki search", "editorial route migration"}:
        if not isinstance(data, dict):
            raise ValidationError(f"{artifact.key} projection must be a JSON object")
    elif not isinstance(data, list):
        raise ValidationError(f"{artifact.key} projection must be a JSON array")

    if artifact.key == "articles":
        if len(data) != 55 or _provenance_set(data, artifact.path) != {("DataTalksClub/content", CONTENT_REVISION)}:
            raise ValidationError("articles count/provenance drift")
    elif artifact.key == "podcasts/transcripts":
        if len(data) != 205 or sum(bool(item.get("transcript")) for item in data) != 203:
            raise ValidationError("podcast episode/transcript count drift")
        if _provenance_set(data, artifact.path) != {("DataTalksClub/content", CONTENT_REVISION)}:
            raise ValidationError("podcast provenance drift")
    elif artifact.key == "books":
        if len(data) != 98 or _provenance_set(data, artifact.path) != {("DataTalksClub/content", CONTENT_REVISION)}:
            raise ValidationError("books count/provenance drift")
    elif artifact.key == "people":
        if len(data) != 438 or _provenance_set(data, artifact.path) != {
            ("DataTalksClub/datatalksclub.github.io", SOURCE_PINS["dtc-main-site"]["revision"])
        }:
            raise ValidationError("people count/provenance drift")
    elif artifact.key == "events":
        if len(data) != 421 or _provenance_set(data, artifact.path) != {
            ("DataTalksClub/datatalksclub.github.io", SOURCE_PINS["dtc-main-site"]["revision"])
        }:
            raise ValidationError("events count/provenance drift")
        if any(item.get("record_schema_version") != 2 for item in data):
            raise ValidationError("events record schema drift")
    elif artifact.key == "courses":
        if len(data) != 12 or _provenance_set(data, artifact.path) != {
            ("DataTalksClub/course-management-platform", "98a235283904b4ef9ad29e196298540756cf1bcc")
        }:
            raise ValidationError("courses count/provenance drift")
    elif artifact.key == "media":
        if len(data) != 1253:
            raise ValidationError("media total count drift")
        repositories = {item.get("provenance", {}).get("repository") for item in data}
        if repositories != {"DataTalksClub/content", "DataTalksClub/datatalksclub.github.io"}:
            raise ValidationError("media provenance repositories drift")
        counts = {
            repository: sum(item.get("provenance", {}).get("repository") == repository for item in data)
            for repository in repositories
        }
        if counts != {"DataTalksClub/content": 815, "DataTalksClub/datatalksclub.github.io": 438}:
            raise ValidationError("media source-owned counts drift")
    elif artifact.key == "FAQ":
        if data.get("schema_version") != 1 or data.get("counts") != {
            "courses": 6,
            "sections": 70,
            "questions": 1401,
            "assets": 99,
        }:
            raise ValidationError("FAQ schema/count drift")
        if data.get("source", {}).get("repository") != "DataTalksClub/faq" or data.get("source", {}).get("revision") != SOURCE_PINS["dtc-faq"]["revision"]:
            raise ValidationError("FAQ source drift")
    elif artifact.key == "docs":
        if data.get("schema_version") != 1 or len(data.get("pages", [])) != 106 or len(data.get("assets", [])) != 39:
            raise ValidationError("docs schema/count drift")
        source_repository = data.get("source", {}).get("repository", "")
        if source_repository.rstrip("/") != "https://github.com/DataTalksClub/docs" or data.get("source", {}).get("revision") != SOURCE_PINS["dtc-docs"]["revision"]:
            raise ValidationError("docs source drift")
    elif artifact.key == "wiki":
        if len(data) != 282 or _provenance_set(data, artifact.path) != {
            ("DataTalksClub/podwiki", SOURCE_PINS["dtc-podwiki"]["revision"])
        }:
            raise ValidationError("wiki count/provenance drift")
    elif artifact.key == "wiki graph":
        if data.get("counts") != WIKI_GRAPH_COUNTS or len(data.get("nodes", [])) != 1072 or len(data.get("links", [])) != 13006:
            raise ValidationError("wiki graph complete counts drift")
    elif artifact.key == "wiki search":
        if len(data.get("docs", [])) != 2998:
            raise ValidationError("wiki search document count drift")
    elif artifact.key == "editorial route migration":
        if data.get("schema_version") != 1 or data.get("counts") != {"aliases": 1592, "finals": 796}:
            raise ValidationError("editorial route migration schema/count drift")
        if len(data.get("aliases", [])) != 1592 or len(data.get("finals", [])) != 796:
            raise ValidationError("editorial route migration member count drift")
    else:  # pragma: no cover - the frozen artifact tuple is exhaustive
        raise ValidationError(f"unknown projection artifact: {artifact.key}")


def _validate_artifacts(root: Path, lines: list[str], audit_path: Path) -> None:
    section = _section(lines, "## Projection artifact inventory")
    rows = _table(
        section,
        "projection artifact inventory",
        (
            "Artifact key",
            "Checked-in path",
            "Source revision / owner",
            "Observed count fields",
            "SHA-256",
            "Schema / evidence",
            "Unresolved hand-off",
        ),
    )
    if len(rows) != len(ARTIFACTS):
        raise ValidationError(f"projection inventory must contain exactly {len(ARTIFACTS)} rows")
    seen: set[str] = set()
    for cells in rows:
        key = _strip_code(cells[0])
        if key in seen:
            raise ValidationError(f"duplicate projection artifact row: {key}")
        seen.add(key)
        expected = ARTIFACT_BY_KEY.get(key)
        if expected is None:
            raise ValidationError(f"unknown projection artifact row: {key}")
        if _strip_code(cells[1]) != expected.path:
            raise ValidationError(f"projection path drift for {key}")
        if _strip_code(cells[2]) != expected.source:
            raise ValidationError(f"projection source/owner drift for {key}")
        if _strip_code(cells[3]) != expected.counts:
            raise ValidationError(f"projection observed count drift for {key}")
        digest = _strip_code(cells[4])
        if not SHA256_PATTERN.fullmatch(digest) or digest != expected.sha256:
            raise ValidationError(f"projection SHA-256 drift for {key}")
        issue_numbers = _validate_local_links(cells[5], audit_path, require_issue=True)
        if key != "manifest" and not issue_numbers:
            raise ValidationError(f"projection {key} lacks owning evidence issue")
        handoff = _strip_code(cells[6])
        if not handoff.strip():
            raise ValidationError(f"projection {key} lacks an unresolved-hand-off field")
        handoff_has_issue = any(
            ISSUE_LINK_PATTERN.fullmatch(match.group("target"))
            for match in LINK_PATTERN.finditer(handoff)
        )
        if not handoff.strip().startswith("None recorded") and not handoff_has_issue:
            raise ValidationError(f"projection {key} hand-off must be None recorded or an issue link")
        _validate_projection_json(root, expected)
        if _file_sha256(root, expected.path) != expected.sha256:
            raise ValidationError(f"checked-in projection bytes drift for {key}")
    if seen != set(ARTIFACT_BY_KEY):
        raise ValidationError("projection artifact coverage differs from the exact frozen set")

    manifest_members = _metadata_value(section, "Manifest members (exact)")
    if manifest_members != MANIFEST_MEMBER_TEXT:
        raise ValidationError("projection manifest member inventory drift")
    if set(manifest_members.split(", ")) != set(MANIFEST_ARTIFACTS):
        raise ValidationError("projection manifest member inventory is malformed")


def _validate_ownership(lines: list[str], audit_path: Path) -> None:
    rows = _table(
        _section(lines, "## Ownership and provenance boundaries"),
        "ownership and provenance boundaries",
        ("Collection or domain", "Ownership", "Explicit boundary", "Owner / evidence"),
    )
    if len(rows) != len(BOUNDARIES):
        raise ValidationError(f"ownership inventory must contain exactly {len(BOUNDARIES)} rows")
    seen: set[str] = set()
    for cells in rows:
        boundary = _strip_code(cells[0])
        owner = _strip_code(cells[1])
        if boundary in seen:
            raise ValidationError(f"duplicate ownership boundary: {boundary}")
        seen.add(boundary)
        expected_owner = BOUNDARIES.get(boundary)
        if expected_owner is None:
            raise ValidationError(f"unknown ownership boundary: {boundary}")
        if owner not in OWNERSHIP_VALUES or owner != expected_owner:
            raise ValidationError(f"invalid ownership vocabulary for {boundary}: {owner!r}")
        if not cells[2].strip():
            raise ValidationError(f"ownership boundary text missing for {boundary}")
        _validate_local_links(cells[3], audit_path, require_issue=True)
    if seen != set(BOUNDARIES):
        raise ValidationError("ownership boundary coverage differs")


def _validate_related_issues(lines: list[str], audit_path: Path) -> None:
    rows = _table(
        _section(lines, "## Related evidence and issue ownership"),
        "related evidence and issue ownership",
        ("Issue", "Owning scope", "Existing evidence"),
    )
    if len(rows) != len(RELATED_ISSUES):
        raise ValidationError("related evidence must cover exactly the required issue set")
    seen: set[int] = set()
    for cells in rows:
        issue_numbers = _validate_local_links(cells[0], audit_path, require_local=False)
        if len(issue_numbers) != 1:
            raise ValidationError("related evidence row must contain exactly one issue link")
        number = next(iter(issue_numbers))
        if number in seen or number not in RELATED_ISSUES:
            raise ValidationError(f"unknown or duplicate related evidence issue: #{number}")
        seen.add(number)
        if not cells[1].strip():
            raise ValidationError(f"related issue #{number} lacks an owning scope")
        _validate_local_links(cells[2], audit_path)
    if seen != set(RELATED_ISSUES):
        raise ValidationError("related evidence issue coverage differs")


def _validate_handoffs(lines: list[str], audit_path: Path) -> None:
    section = _section(lines, "## Unresolved decision hand-offs")
    rows = _table(section, "unresolved decision hand-offs", ("Issue", "Status", "Open contract text", "Decision evidence"))
    if len(rows) != len(HANDOFFS):
        raise ValidationError("unresolved hand-off table must contain exactly five rows")
    seen: set[int] = set()
    for cells in rows:
        issue_numbers = _validate_local_links(cells[0], audit_path, require_local=False)
        if len(issue_numbers) != 1:
            raise ValidationError("hand-off row must contain exactly one issue link")
        number = next(iter(issue_numbers))
        if number in seen or number not in HANDOFFS:
            raise ValidationError(f"unknown or duplicate unresolved hand-off: #{number}")
        seen.add(number)
        if _strip_code(cells[1]) != "OPEN":
            raise ValidationError(f"unresolved hand-off #{number} must remain OPEN")
        if cells[2].strip() != HANDOFFS[number]:
            raise ValidationError(f"unresolved hand-off #{number} contract text drift")
        _validate_local_links(cells[3], audit_path)
    if seen != set(HANDOFFS):
        raise ValidationError("unresolved hand-off coverage differs")
    if "No approval" not in "\n".join(section):
        raise ValidationError("unresolved hand-off section must state that no approval is inferred")


def validate_text(text: str, path: Path = AUDIT_PATH) -> None:
    """Validate Markdown *text* against the checked-in snapshot contract."""

    lines = text.splitlines()
    _validate_metadata(lines)
    audit_path = path.resolve()
    _validate_source_pins(_repository_root(), lines, audit_path)
    _validate_content_contract(_repository_root(), lines, audit_path)
    _validate_artifacts(_repository_root(), lines, audit_path)
    _validate_ownership(lines, audit_path)
    _validate_related_issues(lines, audit_path)
    _validate_handoffs(lines, audit_path)


def validate(path: Path = AUDIT_PATH) -> None:
    """Validate the checked-in inventory at *path*."""

    if not path.is_file():
        raise ValidationError(f"audit file does not exist: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"cannot read audit file: {path}") from exc
    validate_text(text, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=AUDIT_PATH)
    args = parser.parse_args(argv)
    try:
        validate(args.path)
    except ValidationError as exc:
        print(f"GitHub editorial source/projection inventory validation failed: {exc}", file=sys.stderr)
        return 1
    print(f"validated {args.path}: {len(ARTIFACTS) - 1} projections plus manifest; {len(SOURCE_PINS)} source pins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
