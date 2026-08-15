#!/usr/bin/env python3
"""Build the checked, request-network-free public projection.

The deterministic default is the exact accepted preferred content revision and its pinned green
CI evidence. ``--mode fallback`` exists only for rebuilding the reviewed legacy selection if that
preferred acceptance is withdrawn; the fallback selection is always recorded as unaccepted and
never silently promoted.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit
from zoneinfo import ZoneInfo

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from content.event_description_bridge import (  # noqa: E402
    EVENT_RECORD_SCHEMA_VERSION,
    EventDescriptionBridgeError,
    apply_bridge_to_events,
    bridge_manifest_binding,
)
from content.public_text import strip_target_attributes_from_links  # noqa: E402
from events.slugs import event_title_slug  # noqa: E402

DEFAULT_OUTPUT = REPOSITORY_ROOT / "content" / "public_projection"
EDITORIAL_ROUTE_MIGRATION_FILENAME = "editorial_route_migration.json"
EDITORIAL_ROUTE_MIGRATION_SCHEMA = (
    REPOSITORY_ROOT / "_docs" / "compatibility" / "editorial-route-migration.schema.json"
)
EVENT_IDENTITY_MANIFEST = REPOSITORY_ROOT / "events" / "event_identity_manifest.json"

PREFERRED_CONTENT_REVISION = "e29f56ce70bd997171a78a9f0facc9354797f421"
PREFERRED_CONTENT_TREE = "c82b0c6ff462dcdd7140f03f2e7d884ed10ff8fa"
PREFERRED_REPAIR_MANIFEST_SHA256 = (
    "80d3014c47bf57de792473fc1da8f7569daeb55107688c3485153f773948d3aa"
)
PREFERRED_EDITORIAL_OVERLAY_SHA256 = (
    "63969508134e8b2ef3c8471e9c8dbccc96842fcfc25225fe02e1ed5a4f5926f6"
)
PREFERRED_CI_RUN = "https://github.com/DataTalksClub/content/actions/runs/31365358459"
FALLBACK_SELECTION_REVISION = "373bef2912342ece1d2a2d2a9395aa3417243283"
LEGACY_MAIN_REVISION = "ee43d3fa0929faf691178d79f19528e6f15a83e5"
WIKI_REVISION = "988b79d0d655bf4755945c3118544cb9e0dbead6"
COURSES_REVISION = "98a235283904b4ef9ad29e196298540756cf1bcc"

CONTENT_REPOSITORY = "https://github.com/DataTalksClub/content"
LEGACY_MAIN_REPOSITORY = "https://github.com/DataTalksClub/datatalksclub.github.io"
WIKI_REPOSITORY = "https://github.com/DataTalksClub/podwiki"
COURSES_REPOSITORY = "https://github.com/DataTalksClub/course-management-platform"

EXPECTED_COUNTS = {
    "articles": 55,
    "podcasts": 205,
    "transcripts": 203,
    "books": 98,
    "people": 438,
    "events": 421,
    "wiki": 282,
    "courses": 12,
}
EXPECTED_PREFERRED_CONTENT_MEDIA_COUNT = 815
EXPECTED_FALLBACK_CONTENT_MEDIA_COUNT = 807
EXPECTED_PEOPLE_MEDIA_COUNT = 438
EXPECTED_PREFERRED_MEDIA_COUNT = 1_253
EXPECTED_FALLBACK_MEDIA_COUNT = 1_245
COURSE_SPECS_SHA256 = "34077cd485265ffcae96e9acdb06351ee5b6b3b6a5b370639525cc111f1019a7"
EVENT_SOURCE_TIMEZONE = ZoneInfo("Europe/Berlin")
MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024
MAX_GRAPH_FILE_BYTES = 16 * 1024 * 1024
SAFE_KEY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,199}$")
PERSON_KEY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._()-]{0,199}$")
DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}-(.+)$")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
HTML_TAG = re.compile(r"<[^>]{1,2000}>")
LIQUID = re.compile(r"{%.*?%}|{{.*?}}", re.DOTALL)
MARKDOWN_IMAGE = re.compile(r"!\[([^]]*)\]\([^)]*\)")
MARKDOWN_LINK = re.compile(r"\[([^]]+)\]\([^)]*\)")
WIKI_TOKEN = re.compile(r"\[\[([^]]+)\]\]")
YOUTUBE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")

WIKI_PUBLIC_ASSETS = ("assets/og-default.png",)
MEDIA_EXTENSIONS = frozenset({".gif", ".jpeg", ".jpg", ".png", ".svg"})
EDITORIAL_ROUTE_COLLECTIONS = {
    "articles": "/blog",
    "podcasts": "/podcast",
    "books": "/books",
    "people": "/people",
}
EXPECTED_EDITORIAL_FINALS = sum(EXPECTED_COUNTS[name] for name in EDITORIAL_ROUTE_COLLECTIONS)
EXPECTED_EDITORIAL_ALIASES = 2 * EXPECTED_EDITORIAL_FINALS
RECORDING_LINK_LABELS = frozenset({"Watch recording", "Listen to recording"})


class ProjectionBuildError(RuntimeError):
    """A bounded, content-free projection build failure."""


def _run(arguments: list[str], *, cwd: Path) -> str:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env={
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
        },
    )
    if result.returncode:
        raise ProjectionBuildError(f"source checkout command failed: {arguments[0]}")
    return result.stdout.strip()


def _normalize_repository(value: str) -> str:
    value = value.removesuffix(".git").rstrip("/")
    if value.startswith("git@github.com:"):
        value = "https://github.com/" + value.removeprefix("git@github.com:")
    return value


def _verify_checkout(root: Path, repository: str, revision: str, label: str) -> None:
    if not (root / ".git").exists():
        raise ProjectionBuildError(f"{label}: pinned checkout is missing")
    if _run(["git", "rev-parse", "HEAD"], cwd=root) != revision:
        raise ProjectionBuildError(f"{label}: source revision mismatch")
    origin = _normalize_repository(_run(["git", "remote", "get-url", "origin"], cwd=root))
    if origin != _normalize_repository(repository):
        raise ProjectionBuildError(f"{label}: source origin mismatch")
    if _run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root):
        raise ProjectionBuildError(f"{label}: source checkout is dirty")


def _read_bytes(path: Path, *, maximum: int = MAX_SOURCE_FILE_BYTES) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ProjectionBuildError(f"source file missing: {path.name[:120]}") from exc
    if not path.is_file() or path.is_symlink() or not 1 <= size <= maximum:
        raise ProjectionBuildError(f"source file rejected: {path.name[:120]}")
    return path.read_bytes()


def _read_text(path: Path, *, maximum: int = MAX_SOURCE_FILE_BYTES) -> str:
    try:
        return _read_bytes(path, maximum=maximum).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProjectionBuildError(f"source text is not UTF-8: {path.name[:120]}") from exc


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(_read_text(path))
    except yaml.YAMLError as exc:
        raise ProjectionBuildError(f"invalid YAML source: {path.name[:120]}") from exc


def _frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    text = _read_text(path)
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ProjectionBuildError(f"missing front matter: {path.name[:120]}")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ProjectionBuildError(f"unterminated front matter: {path.name[:120]}") from exc
    try:
        metadata = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        raise ProjectionBuildError(f"invalid front matter: {path.name[:120]}") from exc
    if not isinstance(metadata, dict):
        raise ProjectionBuildError(f"front matter is not a mapping: {path.name[:120]}")
    return metadata, "\n".join(lines[end + 1 :]).strip()


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ProjectionBuildError("unsupported public source value")


def _aware_source_datetime(value: Any, *, field: str, optional: bool = False) -> str:
    if value is None and optional:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ProjectionBuildError(f"invalid public date: {field}") from exc
    if not isinstance(value, datetime):
        raise ProjectionBuildError(f"invalid public date: {field}")
    return value.replace(tzinfo=value.tzinfo or EVENT_SOURCE_TIMEZONE).isoformat()


def _string(value: Any, *, field: str, maximum: int = 20_000, optional: bool = False) -> str:
    if value is None and optional:
        return ""
    if isinstance(value, (datetime, date)):
        value = value.isoformat()
    if (
        isinstance(value, dict)
        and len(value) == 1
        and all(isinstance(item, str) for pair in value.items() for item in pair)
    ):
        key, item = next(iter(value.items()))
        value = f"{key}: {item}"
    if not isinstance(value, str):
        raise ProjectionBuildError(f"invalid public field: {field}")
    value = value.strip()
    if (not value and not optional) or len(value) > maximum or "\x00" in value:
        raise ProjectionBuildError(f"invalid public field: {field}")
    return value


def _positive_integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProjectionBuildError(f"invalid positive integer: {field}")
    return value


def _safe_key(value: Any, *, field: str) -> str:
    value = _string(value, field=field, maximum=200)
    if SAFE_KEY.fullmatch(value) is None or ".." in value:
        raise ProjectionBuildError(f"invalid stable key: {field}")
    return value


def _person_key(value: Any) -> str:
    value = _string(value, field="person short", maximum=200)
    if PERSON_KEY.fullmatch(value) is None or ".." in value:
        raise ProjectionBuildError("invalid stable key: person short")
    return value


def _safe_key_list(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProjectionBuildError(f"invalid public list: {field}")
    return [_safe_key(item, field=field) for item in value]


def _string_list(value: Any, *, field: str, maximum: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProjectionBuildError(f"invalid public list: {field}")
    return [_string(item, field=field, maximum=maximum) for item in value]


def _book_archive(value: Any, *, source_name: str) -> list[dict[str, Any]]:
    """Normalize the legacy book discussion threads without executing their Markdown.

    Book records keep the original participant names and text as bounded data.  Rendering is
    deliberately owned by the public template, where autoescaping prevents source content from
    becoming executable HTML.  A handful of historical exports contain empty reply placeholders;
    those are retained so the source ordering and thread shape remain lossless.
    """

    if value is None:
        return []
    if not isinstance(value, list):
        raise ProjectionBuildError(f"book archive rejected: {source_name[:120]}")

    archive: list[dict[str, Any]] = []
    for thread in value:
        if not isinstance(thread, dict):
            raise ProjectionBuildError(f"book archive thread rejected: {source_name[:120]}")
        replies_value = thread.get("replies", [])
        if replies_value is None:
            replies_value = []
        if not isinstance(replies_value, list):
            raise ProjectionBuildError(f"book archive replies rejected: {source_name[:120]}")
        replies: list[dict[str, str]] = []
        for reply in replies_value:
            if not isinstance(reply, dict):
                raise ProjectionBuildError(f"book archive reply rejected: {source_name[:120]}")
            replies.append(
                {
                    "name": _string(
                        reply.get("name"),
                        field="book archive reply name",
                        maximum=500,
                    ),
                    "text": _string(
                        reply.get("text"),
                        field="book archive reply text",
                        maximum=20_000,
                        optional=True,
                    ),
                }
            )
        archive.append(
            {
                "name": _string(
                    thread.get("name"),
                    field="book archive participant",
                    maximum=500,
                ),
                "text": _string(
                    thread.get("text"),
                    field="book archive question",
                    maximum=20_000,
                    optional=True,
                ),
                "replies": replies,
            }
        )
    return archive


def _safe_url(value: Any, *, field: str, optional: bool = True) -> str:
    value = _string(value, field=field, maximum=2_048, optional=optional)
    if not value:
        return ""
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "\x00" in value
        or "\r" in value
        or "\n" in value
    ):
        raise ProjectionBuildError(f"unsafe public URL: {field}")
    return value


def _source_url(repository: str, revision: str, source_path: str) -> str:
    encoded_path = "/".join(quote(part, safe="") for part in source_path.split("/"))
    return f"{repository}/blob/{revision}/{encoded_path}"


def _provenance(
    *, repository: str, revision: str, source_path: str, source_key: str, checksum: str
) -> dict[str, str]:
    return {
        "repository": repository.removeprefix("https://github.com/"),
        "revision": revision,
        "source_path": source_path,
        "source_key": source_key,
        "checksum": checksum,
        "source_url": _source_url(repository, revision, source_path),
    }


def _slugify(value: str) -> str:
    value = html.unescape(value).casefold()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "section"


def _plain_inline(value: str) -> str:
    value = LIQUID.sub("", value)
    value = strip_target_attributes_from_links(value)
    value = MARKDOWN_IMAGE.sub(lambda match: match.group(1), value)
    value = MARKDOWN_LINK.sub(lambda match: match.group(1), value)

    def wiki_label(match: re.Match[str]) -> str:
        token = match.group(1)
        return token.split("=>", 1)[-1].split(":", 1)[-1].split("@", 1)[0]

    value = WIKI_TOKEN.sub(wiki_label, value)
    value = HTML_TAG.sub(" ", value)
    value = html.unescape(value)
    value = re.sub(r"[`*_~]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _body_blocks(body: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    paragraph: list[str] = []
    used_ids: dict[str, int] = {}

    def flush() -> None:
        text = _plain_inline(" ".join(paragraph))
        paragraph.clear()
        if text:
            blocks.append({"kind": "paragraph", "text": text})

    for raw_line in body.splitlines():
        line = raw_line.strip()
        heading = HEADING.match(line)
        if heading:
            flush()
            title = _plain_inline(heading.group(2))
            base_id = _slugify(title)
            used_ids[base_id] = used_ids.get(base_id, 0) + 1
            fragment_id = base_id if used_ids[base_id] == 1 else f"{base_id}-{used_ids[base_id]}"
            blocks.append(
                {
                    "kind": "heading",
                    "level": max(2, len(heading.group(1))),
                    "id": fragment_id,
                    "text": title,
                }
            )
            continue
        if not line:
            flush()
            continue
        if line.startswith(("```", "~~~")):
            flush()
            continue
        if line.startswith(("- ", "* ", "+ ")):
            flush()
            text = _plain_inline(line[2:])
            if text:
                blocks.append({"kind": "list_item", "text": text})
            continue
        paragraph.append(line)
    flush()
    return blocks


def _title_from_record(record: dict[str, Any], key: str) -> str:
    return _string(record.get("title") or record.get("short") or key, field="title", maximum=500)


def _main_records(
    content_root: Path,
    legacy_main_root: Path,
    *,
    mode: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    articles: list[dict[str, Any]] = []
    podcasts: list[dict[str, Any]] = []
    books: list[dict[str, Any]] = []
    selected_revision = PREFERRED_CONTENT_REVISION if mode == "preferred" else LEGACY_MAIN_REVISION
    selected_repository = CONTENT_REPOSITORY if mode == "preferred" else LEGACY_MAIN_REPOSITORY

    for path in sorted((content_root / "articles").glob("*.md")):
        metadata, body = _frontmatter(path)
        match = DATE_PREFIX.fullmatch(path.stem)
        if match is None:
            raise ProjectionBuildError(f"article selection key rejected: {path.name[:120]}")
        slug = _safe_key(match.group(1), field="article slug")
        source_path = f"articles/{path.name}" if mode == "preferred" else f"_posts/{path.name}"
        source_file = path if mode == "preferred" else legacy_main_root / source_path
        checksum = _sha256_bytes(_read_bytes(source_file))
        title = _title_from_record(metadata, slug)
        published = _string(
            metadata.get("date") or metadata.get("datepublished") or path.name[:10],
            field="article date",
            maximum=50,
        )
        articles.append(
            {
                "slug": slug,
                "public_path": f"/blog/{slug}.html",
                "title": title,
                "subtitle": _string(
                    metadata.get("subtitle"), field="article subtitle", maximum=2_000, optional=True
                ),
                "description": _string(
                    metadata.get("description"),
                    field="article description",
                    maximum=4_000,
                    optional=True,
                ),
                "published": published,
                "authors": _safe_key_list(metadata.get("authors"), field="article author"),
                "blocks": _body_blocks(body),
                "image_source": _string(
                    metadata.get("image"), field="article image", maximum=500, optional=True
                ),
                "provenance": _provenance(
                    repository=selected_repository,
                    revision=selected_revision,
                    source_path=source_path,
                    source_key=slug,
                    checksum=checksum,
                ),
            }
        )

    transcript_root = content_root / "podcasts" / "transcripts"
    for path in sorted((content_root / "podcasts").glob("*.yaml")):
        raw = _load_yaml(path)
        if not isinstance(raw, dict):
            raise ProjectionBuildError(f"podcast record rejected: {path.name[:120]}")
        slug = _safe_key(raw.get("slug"), field="podcast slug")
        legacy_path = _string(raw.get("legacy_path"), field="podcast path", maximum=500)
        if legacy_path != f"/podcast/{slug}.html":
            raise ProjectionBuildError(f"podcast route mismatch: {path.name[:120]}")
        public_path = f"/podcast/{slug}.html"
        transcript_path = raw.get("transcript")
        transcript: list[dict[str, Any]] = []
        transcript_provenance: dict[str, str] | None = None
        if transcript_path:
            expected = f"transcripts/{slug}.yaml"
            if transcript_path != expected:
                raise ProjectionBuildError(f"podcast transcript mismatch: {path.name[:120]}")
            selected_transcript = transcript_root / f"{slug}.yaml"
            transcript_record = _load_yaml(selected_transcript)
            if not isinstance(transcript_record, dict) or transcript_record.get("podcast") != slug:
                raise ProjectionBuildError(f"podcast transcript rejected: {path.name[:120]}")
            segments = transcript_record.get("segments")
            if not isinstance(segments, list):
                raise ProjectionBuildError(f"podcast transcript rejected: {path.name[:120]}")
            for segment in segments:
                if (
                    not isinstance(segment, dict)
                    or not set(segment).issubset({"header", "line", "sec", "time", "who"})
                    or bool(segment.get("header")) == bool(segment.get("line"))
                ):
                    raise ProjectionBuildError(
                        f"podcast transcript segment rejected: {path.name[:120]}"
                    )
                if segment.get("header"):
                    transcript.append(
                        {
                            "header": _string(
                                segment["header"],
                                field="podcast transcript header",
                                maximum=2_000,
                            )
                        }
                    )
                    continue
                sec = segment.get("sec")
                if sec is not None and (isinstance(sec, bool) or not isinstance(sec, (int, float))):
                    raise ProjectionBuildError(
                        f"podcast transcript timestamp rejected: {path.name[:120]}"
                    )
                transcript.append(
                    {
                        "line": _string(
                            segment["line"],
                            field="podcast transcript line",
                            maximum=20_000,
                        ),
                        **({"sec": sec} if sec is not None else {}),
                        **(
                            {
                                "time": _string(
                                    segment["time"],
                                    field="podcast transcript time",
                                    maximum=50,
                                )
                            }
                            if segment.get("time")
                            else {}
                        ),
                        **(
                            {
                                "who": _string(
                                    segment["who"],
                                    field="podcast transcript speaker",
                                    maximum=1_000,
                                )
                            }
                            if segment.get("who")
                            else {}
                        ),
                    }
                )
            if mode == "preferred":
                transcript_source_path = f"podcasts/transcripts/{slug}.yaml"
                transcript_source_file = selected_transcript
                transcript_revision = PREFERRED_CONTENT_REVISION
                transcript_repository = CONTENT_REPOSITORY
            else:
                transcript_source_path = f"_podcast/{path.stem}.md"
                transcript_source_file = legacy_main_root / transcript_source_path
                transcript_revision = LEGACY_MAIN_REVISION
                transcript_repository = LEGACY_MAIN_REPOSITORY
            transcript_provenance = _provenance(
                repository=transcript_repository,
                revision=transcript_revision,
                source_path=transcript_source_path,
                source_key=slug,
                checksum=_sha256_bytes(_read_bytes(transcript_source_file)),
            )
        source_path = f"podcasts/{path.name}" if mode == "preferred" else f"_podcast/{path.stem}.md"
        source_file = path if mode == "preferred" else legacy_main_root / source_path
        podcast_links: dict[str, str] = {}
        raw_links = raw.get("links")
        if raw_links is not None and not isinstance(raw_links, dict):
            raise ProjectionBuildError(f"podcast links rejected: {path.name[:120]}")
        if raw_links:
            for label, value in sorted(raw_links.items()):
                if value == "TODO":
                    continue
                safe = _safe_url(value, field=f"podcast link {label}")
                if safe:
                    podcast_links[str(label)] = safe
        podcasts.append(
            {
                "slug": slug,
                "public_path": public_path,
                "title": _title_from_record(raw, slug),
                "short": _string(
                    raw.get("short"), field="podcast short", maximum=500, optional=True
                ),
                "description": _string(
                    raw.get("description") or raw.get("intro"),
                    field="podcast description",
                    maximum=20_000,
                    optional=True,
                ),
                "season": _positive_integer(raw.get("season"), field="podcast season"),
                "episode": _positive_integer(raw.get("episode"), field="podcast episode"),
                "published": _string(
                    raw.get("dateadded"), field="podcast date", maximum=50, optional=True
                ),
                "guests": _safe_key_list(raw.get("guests"), field="podcast guest"),
                "links": podcast_links,
                "transcript": transcript,
                "transcript_provenance": transcript_provenance,
                "image_source": _string(
                    raw.get("image"), field="podcast image", maximum=500, optional=True
                ),
                "provenance": _provenance(
                    repository=selected_repository,
                    revision=selected_revision,
                    source_path=source_path,
                    source_key=slug,
                    checksum=_sha256_bytes(_read_bytes(source_file)),
                ),
            }
        )

    for path in sorted((content_root / "books").glob("*.yaml")):
        raw = _load_yaml(path)
        if not isinstance(raw, dict):
            raise ProjectionBuildError(f"book record rejected: {path.name[:120]}")
        slug = _safe_key(raw.get("slug"), field="book slug")
        legacy_path = _string(raw.get("legacy_path"), field="book path", maximum=500)
        if legacy_path != f"/books/{slug}.html":
            raise ProjectionBuildError(f"book route mismatch: {path.name[:120]}")
        public_path = f"/books/{slug}.html"
        source_path = f"books/{path.name}" if mode == "preferred" else f"_books/{path.stem}.md"
        source_file = path if mode == "preferred" else legacy_main_root / source_path
        book_links: list[dict[str, str]] = []
        raw_book_links = raw.get("links")
        if raw_book_links is not None and not isinstance(raw_book_links, list):
            raise ProjectionBuildError(f"book links rejected: {path.name[:120]}")
        for item in raw_book_links or []:
            if not isinstance(item, dict):
                raise ProjectionBuildError(f"book link rejected: {path.name[:120]}")
            book_links.append(
                {
                    "label": _string(item.get("text") or "Book link", field="book link label"),
                    "url": _safe_url(
                        item.get("link") or item.get("list"),
                        field="book link",
                        optional=False,
                    ),
                }
            )
        books.append(
            {
                "slug": slug,
                "public_path": public_path,
                "title": _title_from_record(raw, slug),
                "description": _string(
                    raw.get("description"), field="book description", maximum=4_000, optional=True
                ),
                "summary": _string(
                    raw.get("summary"), field="book summary", maximum=100_000, optional=True
                ),
                "authors": _string_list(raw.get("authors"), field="book author", maximum=300),
                "published": _string(
                    raw.get("start"), field="book date", maximum=50, optional=True
                ),
                "links": book_links,
                "archive": _book_archive(raw.get("archive"), source_name=path.name),
                "image_source": _string(
                    raw.get("image") or raw.get("cover"),
                    field="book image",
                    maximum=500,
                    optional=True,
                ),
                "provenance": _provenance(
                    repository=selected_repository,
                    revision=selected_revision,
                    source_path=source_path,
                    source_key=slug,
                    checksum=_sha256_bytes(_read_bytes(source_file)),
                ),
            }
        )

    articles.sort(key=lambda item: (item["published"], item["slug"]), reverse=True)
    podcasts.sort(key=lambda item: (item["published"], item["slug"]), reverse=True)
    books.sort(key=lambda item: (item["published"], item["slug"]), reverse=True)
    return articles, podcasts, books


def _profile_url(value: Any, *, network: str) -> str:
    raw = _string(value, field=f"person {network}", maximum=2_000, optional=True)
    if not raw:
        return ""
    if raw.startswith(("http://", "https://")):
        return _safe_url(raw, field=f"person {network}", optional=False)
    if network == "website":
        return _safe_url(f"https://{raw}", field="person website", optional=False)
    bases = {
        "github": "https://github.com/",
        "linkedin": "https://www.linkedin.com/in/",
        "x": "https://twitter.com/",
    }
    if network not in bases or "\\" in raw or ".." in raw:
        raise ProjectionBuildError(f"unsafe public person link: {network}")
    return _safe_url(
        f"{bases[network]}{raw.strip('/')}",
        field=f"person {network}",
        optional=False,
    )


def _people(legacy_main_root: Path) -> list[dict[str, Any]]:
    root = legacy_main_root / "_people"
    source_files = sorted(root.glob("*.md"))
    private_files = [path.name for path in source_files if path.name.startswith("_")]
    if len(source_files) != 439 or private_files != ["_template.md"]:
        raise ProjectionBuildError("people: public/private source inventory mismatch")
    allowed_metadata = {
        "bio_short",
        "github",
        "layout",
        "linkedin",
        "picture",
        "short",
        "title",
        "twitter",
        "web",
    }
    people: list[dict[str, Any]] = []
    for path in source_files:
        if path.name.startswith("_"):
            continue
        metadata, body = _frontmatter(path)
        if not set(metadata).issubset(allowed_metadata):
            raise ProjectionBuildError(
                f"person fields are not public-allowlisted: {path.name[:120]}"
            )
        key = _person_key(metadata.get("short"))
        if key != path.stem:
            raise ProjectionBuildError(f"person key does not match source path: {path.name[:120]}")
        picture = _string(metadata.get("picture"), field="person picture", maximum=500)
        if (
            re.fullmatch(r"images/authors/[A-Za-z0-9._()-]+\.(?:gif|jpe?g|png)", picture) is None
            and picture != "images/authors/ aashishnair.jpg"
        ):
            raise ProjectionBuildError(
                f"person picture is outside the allowlist: {path.name[:120]}"
            )
        links = []
        for label, network, field in (
            ("Website", "website", "web"),
            ("LinkedIn", "linkedin", "linkedin"),
            ("GitHub", "github", "github"),
            ("X", "x", "twitter"),
        ):
            url = _profile_url(metadata.get(field), network=network)
            if url:
                links.append({"label": label, "url": url})
        blocks = _body_blocks(body)
        people.append(
            {
                "slug": key,
                "public_path": f"/people/{key}.html",
                "title": _title_from_record(metadata, key),
                "summary": _string(
                    metadata.get("bio_short"),
                    field="person short biography",
                    maximum=4_000,
                    optional=True,
                ),
                "blocks": blocks,
                "links": links,
                "image_source": picture,
                "provenance": _provenance(
                    repository=LEGACY_MAIN_REPOSITORY,
                    revision=LEGACY_MAIN_REVISION,
                    source_path=f"_people/{path.name}",
                    source_key=key,
                    checksum=_sha256_bytes(_read_bytes(path)),
                ),
            }
        )
    people.sort(key=lambda item: (item["title"].casefold(), item["slug"]))
    return people


def _events(
    legacy_main_root: Path,
    people_by_slug: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    path = legacy_main_root / "_data" / "events.yaml"
    raw = _load_yaml(path)
    if not isinstance(raw, list):
        raise ProjectionBuildError("event catalog is not a list")
    if (
        sum(isinstance(item, dict) and bool(item.get("youtube")) for item in raw) != 396
        or sum(isinstance(item, dict) and bool(item.get("Youtube")) for item in raw) != 1
    ):
        raise ProjectionBuildError("event YouTube-field checked count mismatch")
    checksum = _sha256_bytes(_read_bytes(path))
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    conference_links_omitted = 0
    for item in raw:
        if not isinstance(item, dict):
            raise ProjectionBuildError("event catalog row is not a mapping")
        title = _string(item.get("title"), field="event title", maximum=1_000)
        raw_time = item.get("time")
        starts_at_text = _aware_source_datetime(raw_time, field="event start")
        starts_at = datetime.fromisoformat(starts_at_text)
        ends_at_text = _aware_source_datetime(item.get("end"), field="event end", optional=True)
        if ends_at_text and datetime.fromisoformat(ends_at_text) < starts_at:
            raise ProjectionBuildError("event end precedes event start")
        provided_slug = item.get("slug")
        stable_key = (
            _safe_key(provided_slug, field="event slug")
            if provided_slug
            else f"{starts_at:%Y-%m-%d}-{_slugify(title)}"
        )
        if stable_key in seen:
            raise ProjectionBuildError("duplicate event stable key")
        seen.add(stable_key)
        speakers = _string_list(
            item.get("speakers"),
            field="event speaker",
            maximum=200,
        )
        if any(
            speaker not in people_by_slug or "/" in speaker or "\\" in speaker or ".." in speaker
            for speaker in speakers
        ):
            raise ProjectionBuildError("event speaker does not resolve to a public profile")
        links = []
        for field in ("link", "youtube", "Youtube", "anchor"):
            value = item.get(field)
            if value:
                if isinstance(value, str) and value.startswith("/conferences/"):
                    # Conference pages are explicitly outside this bounded slice. Keeping the
                    # source label without a broken/guessed destination is the honest fallback.
                    conference_links_omitted += 1
                    continue
                url = _safe_url(value, field=f"event {field}", optional=False)
                hostname = (urlsplit(url).hostname or "").casefold()
                if field.casefold() == "youtube":
                    label = "Watch recording"
                elif field == "anchor":
                    label = "Listen to recording"
                elif hostname in {"luma.com", "lu.ma"}:
                    label = "Register on Luma"
                elif hostname == "eventbrite.com" or hostname.endswith(".eventbrite.com"):
                    label = "View event on Eventbrite"
                else:
                    label = "Open external event page"
                links.append({"label": label, "url": url})
        events.append(
            {
                "slug": stable_key,
                "public_path": f"/events/{stable_key}",
                "title": title,
                "starts_at": starts_at_text,
                "ends_at": ends_at_text,
                "type": _string(item.get("type") or "event", field="event type", maximum=100),
                "speakers": [
                    {
                        "key": key,
                        "name": people_by_slug[key]["title"],
                        "public_path": people_by_slug[key]["public_path"],
                    }
                    for key in speakers
                ],
                "links": links,
                "season": item.get("season"),
                "episode": item.get("episode"),
                "provenance": _provenance(
                    repository=LEGACY_MAIN_REPOSITORY,
                    revision=LEGACY_MAIN_REVISION,
                    source_path="_data/events.yaml",
                    source_key=stable_key,
                    checksum=checksum,
                ),
            }
        )
    if conference_links_omitted != 6:
        raise ProjectionBuildError("event conference-link omission count mismatch")
    events.sort(key=lambda item: (item["starts_at"], item["slug"]), reverse=True)
    try:
        apply_bridge_to_events(events)
    except EventDescriptionBridgeError as exc:
        raise ProjectionBuildError("event description bridge validation failed") from exc
    _apply_event_identity_manifest(events)
    return events


def _apply_event_identity_manifest(events: list[dict[str, Any]]) -> None:
    """Apply reviewed UUIDs without deriving identity from mutable source fields."""

    try:
        payload = json.loads(EVENT_IDENTITY_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectionBuildError("event identity manifest cannot be read") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ProjectionBuildError("event identity manifest schema mismatch")
    entries = payload.get("events")
    if not isinstance(entries, list) or len(entries) != len(events):
        raise ProjectionBuildError("event identity manifest event count mismatch")
    by_source: dict[tuple[str, str, str], dict[str, Any]] = {}
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ProjectionBuildError("event identity manifest entry shape mismatch")
        source = entry.get("source")
        if not isinstance(source, dict):
            raise ProjectionBuildError("event identity manifest source shape mismatch")
        key = tuple(source.get(name, "") for name in ("repository", "revision", "source_key"))
        if not all(isinstance(value, str) and value for value in key):
            raise ProjectionBuildError("event identity manifest source identity mismatch")
        if key in by_source:
            raise ProjectionBuildError("event identity manifest duplicate source identity")
        try:
            parsed_uuid = uuid.UUID(entry.get("id", ""))
        except (ValueError, AttributeError) as exc:
            raise ProjectionBuildError("event identity manifest UUID mismatch") from exc
        if str(parsed_uuid) != entry.get("id") or parsed_uuid.variant != uuid.RFC_4122:
            raise ProjectionBuildError("event identity manifest UUID mismatch")
        if entry["id"] in seen_ids:
            raise ProjectionBuildError("event identity manifest duplicate UUID")
        seen_ids.add(entry["id"])
        by_source[key] = entry
    seen: set[tuple[str, str, str]] = set()
    for event in events:
        provenance = event["provenance"]
        key = (provenance["repository"], provenance["revision"], provenance["source_key"])
        entry = by_source.get(key)
        if entry is None:
            raise ProjectionBuildError("event identity manifest is missing a source identity")
        slug = entry.get("slug")
        if (
            entry.get("title") != event["title"]
            or not isinstance(slug, str)
            or not slug
            or slug != event_title_slug(event["title"])
        ):
            raise ProjectionBuildError("event identity manifest title snapshot mismatch")
        expected = f"/events/{entry['id']}/{slug}"
        if entry.get("path") != expected:
            raise ProjectionBuildError("event identity manifest canonical path mismatch")
        event["identity_id"] = entry["id"]
        event["slug"] = slug
        event["public_path"] = expected
        seen.add(key)
    if len(seen) != len(events):
        raise ProjectionBuildError("event identity manifest contains an unknown source identity")


def _event_identity_manifest_binding() -> dict[str, Any]:
    try:
        payload = json.loads(EVENT_IDENTITY_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectionBuildError("event identity manifest cannot be read") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ProjectionBuildError("event identity manifest schema mismatch")
    counts = payload.get("counts")
    entries = payload.get("events")
    if not isinstance(counts, dict) or not isinstance(entries, list):
        raise ProjectionBuildError("event identity manifest counts are invalid")
    return {
        "path": "events/event_identity_manifest.json",
        "sha256": _sha256_file(EVENT_IDENTITY_MANIFEST),
        "schema_version": payload["schema_version"],
        "counts": dict(counts),
    }


def _recording_identities(url: str) -> frozenset[tuple[str, str]]:
    identities = {("url", url)}
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold().removeprefix("www.")
    candidate = ""
    if hostname == "youtu.be":
        candidate = parsed.path.strip("/").split("/", 1)[0]
    elif hostname in {"youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            video_ids = parse_qs(parsed.query, keep_blank_values=True).get("v", [])
            candidate = video_ids[0] if len(video_ids) == 1 else ""
        else:
            prefix, separator, suffix = parsed.path.strip("/").partition("/")
            if separator and prefix in {"embed", "live", "shorts"}:
                candidate = suffix.split("/", 1)[0]
    if YOUTUBE_VIDEO_ID.fullmatch(candidate):
        identities.add(("youtube", candidate))
    return frozenset(identities)


def _podcast_event_lineage(
    podcasts: list[dict[str, Any]], events: list[dict[str, Any]]
) -> dict[str, str]:
    podcasts_by_identity: dict[tuple[str, str], set[str]] = {}
    for podcast in podcasts:
        for url in podcast["links"].values():
            for identity in _recording_identities(url):
                podcasts_by_identity.setdefault(identity, set()).add(podcast["slug"])

    lineage: dict[str, str] = {}
    for event in events:
        if event["type"].casefold() != "podcast":
            continue
        matches = {
            podcast_slug
            for link in event["links"]
            if link["label"] in RECORDING_LINK_LABELS
            for identity in _recording_identities(link["url"])
            for podcast_slug in podcasts_by_identity.get(identity, ())
        }
        if len(matches) > 1:
            raise ProjectionBuildError("ambiguous podcast event recording lineage")
        if matches:
            podcast_slug = matches.pop()
            # Source-key lookup is used by the identity-aware build path.  Keep the
            # cosmetic slug key as a compatibility seam for callers/tests that provide
            # pre-identity records.
            source_key = event.get("provenance", {}).get("source_key")
            if isinstance(source_key, str) and source_key:
                lineage[source_key] = podcast_slug
            lineage[event["slug"]] = podcast_slug
    return lineage


def _wiki_relations(
    body: str,
    title_to_slug: dict[str, str],
    podcast_paths: dict[str, str],
    book_paths: dict[str, str],
    people_paths: dict[str, str],
) -> list[dict[str, str]]:
    relations: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for match in WIKI_TOKEN.finditer(body):
        token = match.group(1).strip()
        target, separator, label = token.partition("=>")
        label = (label if separator else target).strip()
        relation_type = "wiki"
        href = ""
        if target.startswith("cite:"):
            relation_type = "citation"
            episode = target.removeprefix("cite:").split("@", 1)[0]
            if SAFE_KEY.fullmatch(episode):
                href = podcast_paths.get(episode, "")
        elif ":" in target:
            relation_type, key = target.split(":", 1)
            if SAFE_KEY.fullmatch(key):
                if relation_type == "person":
                    href = people_paths.get(key, "")
                elif relation_type == "book":
                    href = book_paths.get(key, "")
                elif relation_type == "podcast":
                    href = podcast_paths.get(key, "")
        else:
            slug = title_to_slug.get(target.casefold()) or _slugify(target)
            if slug in title_to_slug.values():
                href = f"/wiki/{slug}"
        relation = (relation_type, label, href)
        if relation not in seen:
            seen.add(relation)
            relations.append({"type": relation_type, "label": label, "href": href})
    return relations[:200]


def _canonicalize_wiki_document_urls(
    payload: dict[str, Any],
    podcast_paths: dict[str, str],
    book_paths: dict[str, str],
    people_paths: dict[str, str],
) -> dict[str, Any]:
    result = _json_value(payload)
    records = result.get("nodes") or result.get("docs") or []
    for record in records:
        if not isinstance(record, dict):
            continue
        url = record.get("url")
        if isinstance(url, str) and url.startswith("/wiki/"):
            parsed = urlsplit(url)
            record["url"] = (
                f"{parsed.path.rstrip('/')}{'#' + parsed.fragment if parsed.fragment else ''}"
            )
        elif isinstance(url, str) and url.startswith("/search/"):
            parsed = urlsplit(url)
            suffix = parsed.path.removeprefix("/search/").rstrip("/")
            record["url"] = "/wiki/search" + (f"/{suffix}" if suffix else "")
        elif isinstance(url, str) and url.startswith("https://datatalks.club/"):
            parsed = urlsplit(url)
            if parsed.path.startswith("/podcast/"):
                key = parsed.path.removeprefix("/podcast/").removesuffix(".html")
                record["url"] = podcast_paths.get(key, "")
            elif parsed.path.startswith("/books/"):
                key = parsed.path.removeprefix("/books/").removesuffix(".html")
                record["url"] = book_paths.get(key, "")
            elif parsed.path.startswith("/people/"):
                key = parsed.path.removeprefix("/people/").removesuffix(".html")
                record["url"] = people_paths.get(key, "")
                if not record["url"]:
                    record["interaction"] = "unprojected_public_person"
    return result


def _wiki(
    wiki_root: Path,
    podcast_paths: dict[str, str],
    book_paths: dict[str, str],
    people_paths: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    parsed: list[tuple[Path, dict[str, Any], str]] = []
    title_to_slug: dict[str, str] = {}
    for path in sorted((wiki_root / "_wiki").glob("*.md")):
        metadata, body = _frontmatter(path)
        slug = _safe_key(path.stem, field="wiki slug")
        title = _title_from_record(metadata, slug)
        if title.casefold() in title_to_slug:
            raise ProjectionBuildError("duplicate wiki title")
        title_to_slug[title.casefold()] = slug
        parsed.append((path, metadata, body))

    raw_graph = _load_json_bounded(wiki_root / "graph" / "graph.json")
    raw_search = _load_json_bounded(wiki_root / "search" / "search-corpus.json")
    if set(raw_graph) != {"generated_at", "counts", "nodes", "links"}:
        raise ProjectionBuildError("wiki graph fields are not public-allowlisted")
    if set(raw_search) != {"docs"}:
        raise ProjectionBuildError("wiki search fields are not public-allowlisted")
    graph_node_fields = {
        "collection",
        "count",
        "id",
        "keyword",
        "label",
        "search",
        "title",
        "type",
        "url",
    }
    search_document_fields = {
        "document_type",
        "episode_slug",
        "graph_id",
        "id",
        "level",
        "page_title",
        "related_terms",
        "segment_title",
        "text",
        "title",
        "url",
    }
    if any(
        not isinstance(node, dict) or not set(node).issubset(graph_node_fields)
        for node in raw_graph.get("nodes", [])
    ) or any(
        not isinstance(link, dict) or set(link) != {"kind", "source", "target", "weight"}
        for link in raw_graph.get("links", [])
    ):
        raise ProjectionBuildError("wiki graph record fields are not public-allowlisted")
    if any(
        not isinstance(document, dict) or not set(document).issubset(search_document_fields)
        for document in raw_search.get("docs", [])
    ):
        raise ProjectionBuildError("wiki search record fields are not public-allowlisted")

    graph = _canonicalize_wiki_document_urls(
        raw_graph,
        podcast_paths,
        book_paths,
        people_paths,
    )
    search = _canonicalize_wiki_document_urls(
        raw_search,
        podcast_paths,
        book_paths,
        people_paths,
    )
    podcast_public_paths = set(podcast_paths.values())
    book_public_paths = set(book_paths.values())
    people_public_paths = set(people_paths.values())
    allowed_entity_paths = podcast_public_paths | book_public_paths | people_public_paths
    for document in graph.get("nodes", []) + search.get("docs", []):
        url = document.get("url", "")
        if url and not (
            url.startswith("/wiki/") or url == "/wiki/search" or url in allowed_entity_paths
        ):
            raise ProjectionBuildError("wiki document URL is outside the public projection")
    graph_nodes = graph.get("nodes", [])
    graph_links = graph.get("links", [])
    search_documents = search.get("docs", [])
    if len(graph_nodes) != 1_072 or len(graph_links) != 13_006:
        raise ProjectionBuildError("wiki graph checked count mismatch")
    if len(search_documents) != 2_998:
        raise ProjectionBuildError("wiki search checked count mismatch")
    if sum(node.get("url", "").startswith("/wiki/") for node in graph_nodes) != 330:
        raise ProjectionBuildError("wiki graph page URL count mismatch")
    if sum(node.get("url", "") in podcast_public_paths for node in graph_nodes) != 205:
        raise ProjectionBuildError("wiki graph podcast URL count mismatch")
    if sum(node.get("url", "") in book_public_paths for node in graph_nodes) != 98:
        raise ProjectionBuildError("wiki graph book URL count mismatch")
    if sum(node.get("url", "") in people_public_paths for node in graph_nodes) != 438:
        raise ProjectionBuildError("wiki graph person URL count mismatch")
    if sum(node.get("interaction") == "unprojected_public_person" for node in graph_nodes) != 1:
        raise ProjectionBuildError("wiki graph non-link person count mismatch")
    if sum(document.get("url", "").startswith("/wiki/") for document in search_documents) != 2_256:
        raise ProjectionBuildError("wiki search page URL count mismatch")
    expected_fragments: dict[str, dict[str, str]] = {}
    search_section_count = 0
    for document in search.get("docs", []):
        if not isinstance(document, dict) or not isinstance(document.get("url"), str):
            raise ProjectionBuildError("wiki search document is malformed")
        parsed_url = urlsplit(document["url"])
        if not parsed_url.fragment:
            continue
        search_section_count += 1
        route_match = re.fullmatch(r"/wiki/([A-Za-z0-9._-]+)", parsed_url.path)
        if route_match is None:
            raise ProjectionBuildError("wiki search fragment route is outside the projection")
        slug = route_match.group(1)
        segment_title = _string(
            document.get("segment_title"),
            field="wiki search segment title",
            maximum=1_000,
        )
        by_title = expected_fragments.setdefault(slug, {})
        previous = by_title.get(segment_title.casefold())
        if previous is not None and previous != parsed_url.fragment:
            raise ProjectionBuildError("wiki search heading fragment is ambiguous")
        by_title[segment_title.casefold()] = parsed_url.fragment

    pages: list[dict[str, Any]] = []
    for path, metadata, body in parsed:
        slug = path.stem
        title = _title_from_record(metadata, slug)
        tags = _safe_key_list(metadata.get("tags"), field="wiki tag")
        blocks = _body_blocks(body)
        fragments = expected_fragments.get(slug, {})
        rendered_fragments: set[str] = set()
        for block in blocks:
            if block["kind"] != "heading":
                continue
            expected_fragment = fragments.get(block["text"].casefold())
            if expected_fragment:
                block["id"] = expected_fragment
            if block["id"] in fragments.values():
                rendered_fragments.add(block["id"])
        all_fragments = sorted(set(fragments.values()))
        unresolved_fragments = sorted(set(all_fragments) - rendered_fragments)
        heading_ids = [block["id"] for block in blocks if block["kind"] == "heading"]
        if unresolved_fragments or len(heading_ids) != len(set(heading_ids)):
            raise ProjectionBuildError("wiki heading fragment does not resolve uniquely")
        pages.append(
            {
                "slug": slug,
                "public_path": f"/wiki/{slug}",
                "title": title,
                "summary": _string(
                    metadata.get("summary"), field="wiki summary", maximum=4_000, optional=True
                ),
                "tags": tags,
                "blocks": blocks,
                "fragment_ids": all_fragments,
                "unresolved_fragment_ids": unresolved_fragments,
                "relations": _wiki_relations(
                    body,
                    title_to_slug,
                    podcast_paths,
                    book_paths,
                    people_paths,
                ),
                "provenance": _provenance(
                    repository=WIKI_REPOSITORY,
                    revision=WIKI_REVISION,
                    source_path=f"_wiki/{path.name}",
                    source_key=slug,
                    checksum=_sha256_bytes(_read_bytes(path)),
                ),
            }
        )
    pages.sort(key=lambda item: (item["title"].casefold(), item["slug"]))
    person_relations = [
        relation for page in pages for relation in page["relations"] if relation["type"] == "person"
    ]
    if len(person_relations) != 501:
        raise ProjectionBuildError("wiki person relation count mismatch")
    if any(
        relation["href"] and relation["href"] not in people_public_paths
        for relation in person_relations
    ):
        raise ProjectionBuildError("wiki person relation target mismatch")
    known_slugs = {page["slug"] for page in pages}
    if set(expected_fragments) - known_slugs:
        raise ProjectionBuildError("wiki search references an unknown projected page")
    if search_section_count != 1_974:
        raise ProjectionBuildError("wiki search section count mismatch")
    page_fragment_pairs = {
        (page["slug"], fragment) for page in pages for fragment in page["fragment_ids"]
    }
    if len(page_fragment_pairs) != 1_974:
        raise ProjectionBuildError("wiki search page-fragment target count mismatch")
    if len({fragment for _, fragment in page_fragment_pairs}) != 1_894:
        raise ProjectionBuildError("wiki search distinct fragment ID count mismatch")
    return pages, graph, search


def _load_json_bounded(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read_text(path, maximum=MAX_GRAPH_FILE_BYTES))
    except json.JSONDecodeError as exc:
        raise ProjectionBuildError(f"invalid JSON source: {path.name[:120]}") from exc
    if not isinstance(value, dict):
        raise ProjectionBuildError(f"JSON source is not an object: {path.name[:120]}")
    return value


def _courses(course_specs: Path) -> list[dict[str, Any]]:
    if _sha256_file(course_specs) != COURSE_SPECS_SHA256:
        raise ProjectionBuildError("course catalog specification checksum mismatch")
    try:
        raw = json.loads(_read_text(course_specs))
    except json.JSONDecodeError as exc:
        raise ProjectionBuildError("course catalog specification is invalid") from exc
    if not isinstance(raw, list):
        raise ProjectionBuildError("course catalog specification is not a list")
    checksum = _sha256_bytes(_read_bytes(course_specs))
    courses: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {
            "slug",
            "title",
            "finished",
            "homeworks",
            "projects",
        }:
            raise ProjectionBuildError("course catalog fields are not public-allowlisted")
        slug = _safe_key(item.get("slug"), field="course slug")
        homeworks = item.get("homeworks")
        projects = item.get("projects")
        if not isinstance(homeworks, list) or not isinstance(projects, list):
            raise ProjectionBuildError("course catalog assignments are invalid")
        if not isinstance(item.get("finished"), bool) or any(
            not isinstance(assignment, list) or len(assignment) < 2
            for assignment in homeworks + projects
        ):
            raise ProjectionBuildError("course catalog assignment fields are invalid")
        courses.append(
            {
                "slug": slug,
                "public_path": f"/courses/{slug}",
                "title": _string(item.get("title"), field="course title", maximum=500),
                "finished": bool(item.get("finished")),
                "homework_count": len(homeworks),
                "project_count": len(projects),
                "first_deadline": _json_value((homeworks + projects)[0][1])
                if homeworks or projects
                else "",
                "last_deadline": _json_value((homeworks + projects)[-1][1])
                if homeworks or projects
                else "",
                "provenance": _provenance(
                    repository=COURSES_REPOSITORY,
                    revision=COURSES_REVISION,
                    source_path="scripts/production_like_course_specs.json",
                    source_key=slug,
                    checksum=checksum,
                ),
            }
        )
    courses.sort(key=lambda item: (item["finished"], item["title"].casefold(), item["slug"]))
    return courses


def _validate_collection(name: str, records: list[dict[str, Any]]) -> None:
    if len(records) != EXPECTED_COUNTS[name]:
        raise ProjectionBuildError(f"{name}: checked count mismatch")
    keys = [record["slug"] for record in records]
    paths = [record["public_path"] for record in records]
    if (name != "events" and len(keys) != len(set(keys))) or len(paths) != len(set(paths)):
        raise ProjectionBuildError(f"{name}: duplicate stable key or path")
    for record in records:
        provenance = record.get("provenance") or {}
        if set(provenance) != {
            "repository",
            "revision",
            "source_path",
            "source_key",
            "checksum",
            "source_url",
        }:
            raise ProjectionBuildError(f"{name}: incomplete record provenance")
        if not re.fullmatch(r"[0-9a-f]{64}", provenance["checksum"]):
            raise ProjectionBuildError(f"{name}: invalid record checksum")


def _canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return _sha256_bytes(encoded)


def _editorial_route_manifest_digest(manifest: dict[str, Any]) -> str:
    return _canonical_json_sha256(
        {key: value for key, value in manifest.items() if key != "content_sha256"}
    )


def _expected_editorial_routes(
    collections: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    finals: list[dict[str, Any]] = []
    aliases: list[dict[str, Any]] = []
    for collection, prefix in EDITORIAL_ROUTE_COLLECTIONS.items():
        for record in collections[collection]:
            final_path = record["public_path"]
            clean_path = f"{prefix}/{record['slug']}"
            if final_path != f"{clean_path}.html":
                raise ProjectionBuildError("editorial route final does not match its stable key")
            final = {
                "collection": collection,
                "record_key": record["slug"],
                "final_path": final_path,
                "source": dict(record["provenance"]),
            }
            finals.append(final)
            for source_path in (clean_path, f"{clean_path}/"):
                aliases.append(
                    {
                        "collection": collection,
                        "record_key": record["slug"],
                        "source_path": source_path,
                        "final_path": final_path,
                        "status_code": 301,
                        "query_policy": "preserve_raw",
                    }
                )
    finals.sort(key=lambda item: item["final_path"])
    aliases.sort(key=lambda item: item["source_path"])
    return finals, aliases


def _validate_editorial_route_manifest(
    manifest: dict[str, Any],
    collections: dict[str, list[dict[str, Any]]],
    *,
    selection_mode: str,
    source_artifact_digests: dict[str, str],
) -> None:
    if set(manifest) != {
        "schema_version",
        "schema",
        "provenance",
        "counts",
        "finals",
        "aliases",
        "content_sha256",
    }:
        raise ProjectionBuildError("editorial route manifest shape mismatch")
    if manifest["schema_version"] != 1:
        raise ProjectionBuildError("editorial route manifest schema mismatch")
    expected_schema = {
        "path": "_docs/compatibility/editorial-route-migration.schema.json",
        "sha256": _sha256_file(EDITORIAL_ROUTE_MIGRATION_SCHEMA),
    }
    if manifest["schema"] != expected_schema:
        raise ProjectionBuildError("editorial route manifest schema binding mismatch")

    expected_source_artifacts = {
        f"{name}.json": source_artifact_digests[f"{name}.json"]
        for name in EDITORIAL_ROUTE_COLLECTIONS
    }
    source_revisions = sorted(
        {
            (record["provenance"]["repository"], record["provenance"]["revision"])
            for name in EDITORIAL_ROUTE_COLLECTIONS
            for record in collections[name]
        }
    )
    expected_provenance = {
        "builder": "scripts/build_public_projection.py",
        "projection_schema_version": 1,
        "projection_selection_mode": selection_mode,
        "source_artifacts": expected_source_artifacts,
        "source_revisions": [
            {"repository": repository, "revision": revision}
            for repository, revision in source_revisions
        ],
    }
    if manifest["provenance"] != expected_provenance:
        raise ProjectionBuildError("editorial route manifest provenance binding mismatch")
    if manifest["counts"] != {
        "finals": EXPECTED_EDITORIAL_FINALS,
        "aliases": EXPECTED_EDITORIAL_ALIASES,
    }:
        raise ProjectionBuildError("editorial route manifest count canary mismatch")

    finals = manifest["finals"]
    aliases = manifest["aliases"]
    if not isinstance(finals, list) or len(finals) != EXPECTED_EDITORIAL_FINALS:
        raise ProjectionBuildError("editorial route manifest final count mismatch")
    if not isinstance(aliases, list) or len(aliases) != EXPECTED_EDITORIAL_ALIASES:
        raise ProjectionBuildError("editorial route manifest alias count mismatch")
    try:
        final_paths = [item["final_path"] for item in finals]
        alias_paths = [item["source_path"] for item in aliases]
        alias_graph = {item["source_path"]: item["final_path"] for item in aliases}
    except (KeyError, TypeError) as exc:
        raise ProjectionBuildError("editorial route manifest route shape mismatch") from exc
    if len(final_paths) != len(set(final_paths)):
        raise ProjectionBuildError("editorial route manifest duplicate final")
    if len(alias_paths) != len(set(alias_paths)):
        raise ProjectionBuildError("editorial route manifest duplicate alias")
    if set(final_paths) & set(alias_paths):
        raise ProjectionBuildError("editorial route manifest route collision")
    for source_path, target_path in alias_graph.items():
        visited = {source_path}
        cursor = target_path
        followed_alias = False
        while cursor in alias_graph:
            followed_alias = True
            if cursor in visited:
                raise ProjectionBuildError("editorial route manifest redirect loop")
            visited.add(cursor)
            cursor = alias_graph[cursor]
        if followed_alias:
            raise ProjectionBuildError("editorial route manifest redirect chain")
    if any(target not in set(final_paths) for target in alias_graph.values()):
        raise ProjectionBuildError("editorial route manifest target is not final")

    expected_finals, expected_aliases = _expected_editorial_routes(collections)
    if finals != expected_finals or aliases != expected_aliases:
        raise ProjectionBuildError("editorial route manifest is not exhaustive")
    if manifest["content_sha256"] != _editorial_route_manifest_digest(manifest):
        raise ProjectionBuildError("editorial route manifest content digest mismatch")


def _build_editorial_route_manifest(
    collections: dict[str, list[dict[str, Any]]],
    *,
    selection_mode: str,
    source_artifact_digests: dict[str, str],
) -> dict[str, Any]:
    finals, aliases = _expected_editorial_routes(collections)
    source_revisions = sorted(
        {
            (record["provenance"]["repository"], record["provenance"]["revision"])
            for name in EDITORIAL_ROUTE_COLLECTIONS
            for record in collections[name]
        }
    )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "schema": {
            "path": "_docs/compatibility/editorial-route-migration.schema.json",
            "sha256": _sha256_file(EDITORIAL_ROUTE_MIGRATION_SCHEMA),
        },
        "provenance": {
            "builder": "scripts/build_public_projection.py",
            "projection_schema_version": 1,
            "projection_selection_mode": selection_mode,
            "source_artifacts": {
                f"{name}.json": source_artifact_digests[f"{name}.json"]
                for name in EDITORIAL_ROUTE_COLLECTIONS
            },
            "source_revisions": [
                {"repository": repository, "revision": revision}
                for repository, revision in source_revisions
            ],
        },
        "counts": {
            "finals": EXPECTED_EDITORIAL_FINALS,
            "aliases": EXPECTED_EDITORIAL_ALIASES,
        },
        "finals": finals,
        "aliases": aliases,
    }
    manifest["content_sha256"] = _editorial_route_manifest_digest(manifest)
    _validate_editorial_route_manifest(
        manifest,
        collections,
        selection_mode=selection_mode,
        source_artifact_digests=source_artifact_digests,
    )
    return manifest


def _write_json(path: Path, payload: Any) -> str:
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded)
    return _sha256_bytes(encoded)


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.is_symlink() or path.name == "manifest.json":
            if path.is_symlink():
                raise ProjectionBuildError("projection tree contains a symlink")
            continue
        relative = path.relative_to(root).as_posix().encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _write_wiki_assets(wiki_root: Path, output: Path) -> dict[str, str]:
    target_root = output / "wiki_assets"
    if target_root.exists():
        shutil.rmtree(target_root)
    digests: dict[str, str] = {}
    for source_path in WIKI_PUBLIC_ASSETS:
        source = wiki_root / source_path
        payload = _read_bytes(source, maximum=8 * 1024 * 1024)
        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ProjectionBuildError("wiki PNG asset signature mismatch")
        relative = source_path.removeprefix("assets/")
        target = target_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        digests[f"/wiki/assets/{relative}"] = _sha256_bytes(payload)
    return digests


def _copy_media(
    content_root: Path,
    output: Path,
    *,
    mode: str,
) -> tuple[list[dict[str, Any]], set[str]]:
    source_root = content_root / "images"
    target_root = output / "media"
    if target_root.exists():
        shutil.rmtree(target_root)
    records: list[dict[str, Any]] = []
    public_paths: set[str] = set()
    if mode == "preferred":
        repository = CONTENT_REPOSITORY
        revision = PREFERRED_CONTENT_REVISION
    else:
        repository = LEGACY_MAIN_REPOSITORY
        revision = LEGACY_MAIN_REVISION
    sources = sorted(
        source
        for directory in ("posts", "podcast", "books")
        for source in (source_root / directory).rglob("*")
    )
    for source in sources:
        if source.is_symlink():
            raise ProjectionBuildError("media source is not a regular file")
        if source.is_dir():
            continue
        if not source.is_file():
            raise ProjectionBuildError("media source is not a regular file")
        relative = source.relative_to(content_root).as_posix()
        suffix = source.suffix.lower()
        if suffix not in MEDIA_EXTENSIONS or not relative.startswith(
            ("images/posts/", "images/podcast/", "images/books/")
        ):
            raise ProjectionBuildError("media source path is outside the allowlist")
        payload = _read_bytes(source, maximum=16 * 1024 * 1024)
        if suffix in {".jpg", ".jpeg"} and not (
            payload.startswith(b"\xff\xd8\xff") and payload.endswith(b"\xff\xd9")
        ):
            raise ProjectionBuildError("JPEG media signature mismatch")
        if suffix == ".png" and not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ProjectionBuildError("PNG media signature mismatch")
        if suffix == ".gif" and not payload.startswith((b"GIF87a", b"GIF89a")):
            raise ProjectionBuildError("GIF media signature mismatch")
        if suffix == ".svg":
            try:
                svg = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ProjectionBuildError("SVG media is not UTF-8") from exc
            lowered = svg.casefold()
            if "<svg" not in lowered[:1000] or re.search(
                r"<script\b|<style\b|<!doctype|<!entity|expression\s*\(|"
                r"url\s*\(\s*['\"]?(?:https?:|//|data:)|"
                r"\bon[a-z]+\s*=|\b(?:href|src)\s*=\s*['\"](?:https?:|//|data:)",
                lowered,
            ):
                raise ProjectionBuildError("unsafe SVG media")
        target = target_root / source.relative_to(source_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        public_path = f"/{relative}"
        public_paths.add(public_path)
        records.append(
            {
                "slug": relative,
                "public_path": public_path,
                "content_type": {
                    ".gif": "image/gif",
                    ".jpeg": "image/jpeg",
                    ".jpg": "image/jpeg",
                    ".png": "image/png",
                    ".svg": "image/svg+xml",
                }[suffix],
                "provenance": _provenance(
                    repository=repository,
                    revision=revision,
                    source_path=relative,
                    source_key=relative,
                    checksum=_sha256_bytes(payload),
                ),
            }
        )
    expected = (
        EXPECTED_PREFERRED_CONTENT_MEDIA_COUNT
        if mode == "preferred"
        else EXPECTED_FALLBACK_CONTENT_MEDIA_COUNT
    )
    if len(records) != expected or len(public_paths) != expected:
        raise ProjectionBuildError("media: checked count mismatch")
    return records, public_paths


def _copy_people_media(
    legacy_main_root: Path,
    output: Path,
    people: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    records: list[dict[str, Any]] = []
    public_paths: set[str] = set()
    for person in people:
        relative = person["image_source"]
        source = legacy_main_root / relative
        payload = _read_bytes(source, maximum=16 * 1024 * 1024)
        suffix = source.suffix.casefold()
        if suffix in {".jpg", ".jpeg"} and not (
            payload.startswith(b"\xff\xd8\xff") and payload.endswith(b"\xff\xd9")
        ):
            raise ProjectionBuildError("person JPEG signature mismatch")
        if suffix == ".png" and not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ProjectionBuildError("person PNG signature mismatch")
        if suffix == ".gif" and not payload.startswith((b"GIF87a", b"GIF89a")):
            raise ProjectionBuildError("person GIF signature mismatch")
        target = output / "media" / Path(relative).relative_to("images")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        public_path = f"/{relative}"
        public_paths.add(public_path)
        records.append(
            {
                "slug": relative,
                "public_path": public_path,
                "content_type": {
                    ".gif": "image/gif",
                    ".jpeg": "image/jpeg",
                    ".jpg": "image/jpeg",
                    ".png": "image/png",
                }[suffix],
                "provenance": _provenance(
                    repository=LEGACY_MAIN_REPOSITORY,
                    revision=LEGACY_MAIN_REVISION,
                    source_path=relative,
                    source_key=relative,
                    checksum=_sha256_bytes(payload),
                ),
            }
        )
    if len(records) != EXPECTED_PEOPLE_MEDIA_COUNT or len(public_paths) != len(records):
        raise ProjectionBuildError("people media: checked count mismatch")
    return records, public_paths


def _attach_primary_media(
    collections: dict[str, list[dict[str, Any]]], public_paths: set[str]
) -> None:
    for name in ("articles", "podcasts", "books", "people"):
        for record in collections[name]:
            source = record.pop("image_source")
            if source:
                candidate = "/" + source.lstrip("/")
                if (
                    ".." in candidate
                    or "\\" in candidate
                    or urlsplit(candidate).query
                    or urlsplit(candidate).fragment
                ):
                    raise ProjectionBuildError(f"{name}: unsafe primary media path")
            else:
                candidate = ""
            record["image_path"] = candidate if candidate in public_paths else ""
            record["media_available"] = bool(record["image_path"])


def build(args: argparse.Namespace) -> None:
    content_root = args.content_root.resolve()
    legacy_main_root = args.legacy_main_root.resolve()
    wiki_root = args.wiki_root.resolve()
    destination = args.output.resolve()
    if destination == REPOSITORY_ROOT or not destination.is_relative_to(REPOSITORY_ROOT):
        raise ProjectionBuildError("projection output must stay inside the repository")
    output = destination.parent / f".{destination.name}.building"
    if output.exists():
        shutil.rmtree(output)
    course_specs = (REPOSITORY_ROOT / "scripts" / "production_like_course_specs.json").resolve()
    if args.mode == "preferred":
        _verify_checkout(
            content_root, CONTENT_REPOSITORY, PREFERRED_CONTENT_REVISION, "preferred content"
        )
        if _run(["git", "rev-parse", "HEAD^{tree}"], cwd=content_root) != PREFERRED_CONTENT_TREE:
            raise ProjectionBuildError("preferred content tree mismatch")
        repair_manifest = content_root / "repairs" / "2026-08-09-missing-media.yaml"
        if _sha256_file(repair_manifest) != PREFERRED_REPAIR_MANIFEST_SHA256:
            raise ProjectionBuildError("preferred content repair manifest mismatch")
        editorial_overlay = (
            content_root / "editorial-overlays" / "2026-08-10-podcast-descriptions.yaml"
        )
        if _sha256_file(editorial_overlay) != PREFERRED_EDITORIAL_OVERLAY_SHA256:
            raise ProjectionBuildError("preferred content editorial overlay mismatch")
    else:
        _verify_checkout(
            content_root,
            CONTENT_REPOSITORY,
            FALLBACK_SELECTION_REVISION,
            "fallback selection",
        )
    _verify_checkout(legacy_main_root, LEGACY_MAIN_REPOSITORY, LEGACY_MAIN_REVISION, "legacy main")
    _verify_checkout(wiki_root, WIKI_REPOSITORY, WIKI_REVISION, "wiki")

    articles, podcasts, books = _main_records(content_root, legacy_main_root, mode=args.mode)
    people = _people(legacy_main_root)
    people_by_slug = {person["slug"]: person for person in people}
    people_paths = {slug: person["public_path"] for slug, person in people_by_slug.items()}
    unresolved_podcast_guests = sorted(
        {
            guest
            for podcast in podcasts
            for guest in podcast["guests"]
            if guest not in people_by_slug
        }
    )
    if unresolved_podcast_guests != ["abouzarabbaspour"]:
        raise ProjectionBuildError("podcast guest/profile exception inventory mismatch")
    for article in articles:
        if any(author not in people_by_slug for author in article["authors"]):
            raise ProjectionBuildError("article author does not resolve to a public profile")
        article["author_profiles"] = [
            {
                "key": author,
                "name": people_by_slug[author]["title"],
                "public_path": people_by_slug[author]["public_path"],
            }
            for author in article["authors"]
        ]
    for podcast in podcasts:
        podcast["guest_profiles"] = [
            {
                "key": guest,
                "name": people_by_slug[guest]["title"] if guest in people_by_slug else guest,
                "public_path": people_by_slug[guest]["public_path"]
                if guest in people_by_slug
                else "",
            }
            for guest in podcast["guests"]
        ]
    events = _events(legacy_main_root, people_by_slug)
    podcast_paths: dict[str, str] = {}
    for podcast in podcasts:
        for alias in {podcast["slug"], podcast["slug"].removesuffix(".md").lstrip("_")}:
            previous = podcast_paths.get(alias)
            if previous is not None and previous != podcast["public_path"]:
                raise ProjectionBuildError("ambiguous podcast relation alias")
            podcast_paths[alias] = podcast["public_path"]
    book_paths = {book["slug"]: book["public_path"] for book in books}
    wiki, graph, search = _wiki(wiki_root, podcast_paths, book_paths, people_paths)
    courses = _courses(course_specs)
    relationships: dict[str, list[dict[str, str]]] = {slug: [] for slug in people_by_slug}
    for article in articles:
        for author in article["authors"]:
            relationships[author].append(
                {"role": "author", "label": article["title"], "public_path": article["public_path"]}
            )
    for book in books:
        for author in book["authors"]:
            if author in relationships:
                relationships[author].append(
                    {
                        "role": "author",
                        "label": book["title"],
                        "public_path": book["public_path"],
                    }
                )
    for podcast in podcasts:
        for guest in podcast["guests"]:
            if guest in relationships:
                relationships[guest].append(
                    {
                        "role": "guest",
                        "label": podcast["title"],
                        "public_path": podcast["public_path"],
                    }
                )
    podcast_by_slug = {podcast["slug"]: podcast for podcast in podcasts}
    podcast_event_lineage = _podcast_event_lineage(podcasts, events)
    for event in events:
        canonical_podcast = podcast_by_slug.get(
            podcast_event_lineage.get(event["provenance"]["source_key"], "")
        )
        for speaker in event["speakers"]:
            if canonical_podcast is not None and speaker["key"] in canonical_podcast["guests"]:
                continue
            relationships[speaker["key"]].append(
                {"role": "speaker", "label": event["title"], "public_path": event["public_path"]}
            )
    for person in people:
        person["relationships"] = relationships[person["slug"]]
        person["roles"] = sorted({item["role"] for item in person["relationships"]})
    collections: dict[str, list[dict[str, Any]]] = {
        "articles": articles,
        "podcasts": podcasts,
        "books": books,
        "people": people,
        "events": events,
        "wiki": wiki,
        "courses": courses,
    }
    for name, records in collections.items():
        _validate_collection(name, records)
    transcript_count = sum(bool(record["transcript"]) for record in podcasts)
    if transcript_count != EXPECTED_COUNTS["transcripts"]:
        raise ProjectionBuildError("transcripts: checked count mismatch")

    output.mkdir(parents=True, exist_ok=True)
    media_root = content_root if args.mode == "preferred" else legacy_main_root
    media, public_media_paths = _copy_media(media_root, output, mode=args.mode)
    people_media, public_people_media_paths = _copy_people_media(
        legacy_main_root,
        output,
        people,
    )
    media.extend(people_media)
    media.sort(key=lambda item: item["slug"])
    public_media_paths.update(public_people_media_paths)
    _attach_primary_media(collections, public_media_paths)
    artifact_digests = {
        f"{name}.json": _write_json(output / f"{name}.json", records)
        for name, records in collections.items()
    }
    artifact_digests["media.json"] = _write_json(output / "media.json", media)
    artifact_digests["wiki_graph.json"] = _write_json(output / "wiki_graph.json", graph)
    artifact_digests["wiki_search.json"] = _write_json(output / "wiki_search.json", search)
    editorial_route_manifest = _build_editorial_route_manifest(
        collections,
        selection_mode=args.mode,
        source_artifact_digests=artifact_digests,
    )
    artifact_digests[EDITORIAL_ROUTE_MIGRATION_FILENAME] = _write_json(
        output / EDITORIAL_ROUTE_MIGRATION_FILENAME,
        editorial_route_manifest,
    )
    asset_digests = _write_wiki_assets(wiki_root, output)
    expected_media_count = (
        EXPECTED_PREFERRED_MEDIA_COUNT
        if args.mode == "preferred"
        else EXPECTED_FALLBACK_MEDIA_COUNT
    )
    manifest = {
        "schema_version": 1,
        "selection_mode": args.mode,
        "counts": {**EXPECTED_COUNTS, "media": expected_media_count},
        "sources": {
            "preferred_content": {
                "repository": "DataTalksClub/content",
                "revision": PREFERRED_CONTENT_REVISION,
                "tree": PREFERRED_CONTENT_TREE,
                "repair_manifest_sha256": PREFERRED_REPAIR_MANIFEST_SHA256,
                "editorial_overlay_sha256": PREFERRED_EDITORIAL_OVERLAY_SHA256,
                "ci_run": PREFERRED_CI_RUN,
                "accepted": True,
            },
            "fallback_selection": {
                "repository": "DataTalksClub/content",
                "revision": FALLBACK_SELECTION_REVISION,
                "accepted": False,
                "purpose": "legacy record selection only",
            },
            "legacy_main": {
                "repository": "DataTalksClub/datatalksclub.github.io",
                "revision": LEGACY_MAIN_REVISION,
            },
            "wiki": {"repository": "DataTalksClub/podwiki", "revision": WIKI_REVISION},
            "courses": {
                "repository": "DataTalksClub/course-management-platform",
                "revision": COURSES_REVISION,
                "source_path": "scripts/production_like_course_specs.json",
                "checksum": _sha256_file(course_specs),
            },
        },
        "artifacts": artifact_digests,
        "tree_sha256": _tree_sha256(output),
        "wiki_assets": asset_digests,
        "selection_rule": {
            "preferred": "exact accepted revision with pinned green CI evidence",
            "fallback": "reviewed legacy selection only when preferred acceptance is withdrawn",
            "fallback_promoted": False,
        },
        "projection_rules": {
            "event_source_timezone": str(EVENT_SOURCE_TIMEZONE),
            "event_record_schema_version": EVENT_RECORD_SCHEMA_VERSION,
            "event_identity_manifest": _event_identity_manifest_binding(),
            "event_description_bridge": bridge_manifest_binding(),
            "conference_links_outside_slice": "omitted",
            "people_source": "438 public _people profiles; underscore-prefixed source excluded",
            "unresolved_podcast_guest_keys": unresolved_podcast_guests,
            "media": "signature-checked local allowlist with per-asset provenance",
        },
        "runtime_contract": {
            "network": "none",
            "database_writes": "none",
            "source_execution": "none",
            "event_description_source": "committed_safe_bridge_only",
            "wiki_mount": "/wiki/",
            "podwiki_mount": "absent",
        },
    }
    _write_json(output / "manifest.json", manifest)
    if destination.exists():
        shutil.rmtree(destination)
    output.replace(destination)
    print(json.dumps({"counts": manifest["counts"], "mode": args.mode}, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--mode",
        choices=("preferred", "fallback"),
        default="preferred",
        help="accepted preferred source (default), or the explicitly reviewed legacy fallback",
    )
    result.add_argument("--content-root", type=Path, required=True)
    result.add_argument("--legacy-main-root", type=Path, required=True)
    result.add_argument("--wiki-root", type=Path, required=True)
    result.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return result


if __name__ == "__main__":
    parsed_args = parser().parse_args()
    try:
        build(parsed_args)
    except Exception:
        destination_path = parsed_args.output.resolve()
        staging_path = destination_path.parent / f".{destination_path.name}.building"
        if staging_path.is_relative_to(REPOSITORY_ROOT) and staging_path.exists():
            shutil.rmtree(staging_path)
        raise
