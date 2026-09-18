"""Shared parsing and derivation helpers for the public editorial content.

This module used to build and write the checked public projection tree under
``~/prod/dtc-data/content-staging/public_projection/`` -- its CLI, orchestration
and JSON-writing code are gone now that nothing reads that tree (the three
``scripts/prod/`` importers that read it were removed once ``content/catalogue.py``,
``content/docs_projection.py`` and ``content/faq_data.py`` moved to reading
``content.models.SyncedDocument`` exclusively -- see
``_docs/architecture/database-only-content.md``).

What is left is a genuinely reusable library: the record-shape validation,
Markdown/HTML body parsing, and URL/link normalization that
``content/sync_parsers/*.py`` -- the live ``community_base.content_sync`` parsers
-- import directly (``from scripts import build_public_projection as builder``),
plus a handful of helpers ``content/public_records.py`` and
``scripts/repin_projection_digests.py`` still use.  The import path is
unchanged on purpose: it is what those modules already spell.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit, urlunsplit

import yaml

from content.podcast_resources import (
    PodcastResourceError,
    normalize_podcast_resource,
)
from content.public_text import strip_target_attributes_from_links
from courses.services.course_family_identity import (
    family_and_year_from_edition_slug,
)

# The one reviewed correction this pinned catalogue needs: it exports the AI Dev
# Tools edition as "ai-dev-tools-2025", but the real course-repository family is
# "ai-dev-tools-zoomcamp" (its course.yaml declares that slug directly, matching
# its own repository name, same as every other course family). Every other
# pinned edition slug's family is already exactly its own de-suffixed form.
# Mirrors the same correction in scripts/prod/import_cmp_content.py and
# courses/services/local_course_seed.py.
_FAMILY_SLUG_OVERRIDES = {"ai-dev-tools": "ai-dev-tools-zoomcamp"}


def cohort_family_identity(edition_slug: str) -> tuple[str, int]:
    family_slug, year = family_and_year_from_edition_slug(edition_slug)
    return _FAMILY_SLUG_OVERRIDES.get(family_slug, family_slug), year


SPOTIFY_FOR_CREATORS_URL = "https://creators.spotify.com/pod/profile/datatalksclub/"
PODCAST_PLATFORM_KEY_ALIASES = {"anchor": "spotify_for_creators"}

#: The one remaining external repository reference: ``content/sync_parsers/podwiki.py``
#: reads this directly for its own record provenance.
WIKI_REPOSITORY = "https://github.com/DataTalksClub/podwiki"

# Media objects are published to an object store, so the complete-tree digest covers the
# JSON artifacts and wiki assets only.  The manifest declares the scope in
# machine-readable form; the runtime rejects a manifest that does not declare it.
MEDIA_TREE_PREFIX = "media/"
TREE_DIGEST_SCOPE = "projection artifacts and wiki assets; excludes manifest.json and media/"
MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024
MAX_GRAPH_FILE_BYTES = 16 * 1024 * 1024
SAFE_KEY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,199}$")
PERSON_KEY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._()-]{0,199}$")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
HTML_TAG = re.compile(r"<[^>]{1,2000}>")
LIQUID = re.compile(r"{%.*?%}|{{.*?}}", re.DOTALL)
MARKDOWN_IMAGE = re.compile(r"!\[([^]]*)\]\([^)]*\)")
MARKDOWN_LINK = re.compile(r"\[([^]]+)\]\([^)]*\)")
WIKI_TOKEN = re.compile(r"\[\[([^]]+)\]\]")
YOUTUBE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")

WIKI_PUBLIC_ASSETS = ("assets/og-default.png",)
RECORDING_LINK_LABELS = frozenset({"Watch recording", "Listen to recording"})


class ProjectionBuildError(RuntimeError):
    """A bounded, content-free projection build failure."""


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
                    "text": _localize_editorial_links(
                        _string(
                            reply.get("text"),
                            field="book archive reply text",
                            maximum=20_000,
                            optional=True,
                        )
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
                "text": _localize_editorial_links(
                    _string(
                        thread.get("text"),
                        field="book archive question",
                        maximum=20_000,
                        optional=True,
                    )
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


def _canonical_podcast_platform_key(value: Any) -> str:
    key = _string(value, field="podcast platform provider", maximum=100)
    return PODCAST_PLATFORM_KEY_ALIASES.get(key, key)


def _canonical_podcast_platform_url(provider: str, value: str) -> str:
    """Keep episode destinations on Spotify for Creators after the Anchor move."""

    if provider != "spotify_for_creators":
        return value
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").casefold().removeprefix("www.")
    if hostname not in {"anchor.fm", "podcasters.spotify.com", "creators.spotify.com"}:
        return value
    marker = "/episodes/"
    if marker in parsed.path:
        suffix = parsed.path.split(marker, 1)[1]
        # A small number of legacy exports duplicated the full destination after
        # the episode slug. Keep the first valid episode path while canonicalizing
        # the provider host and path.
        suffix = re.split(r"https?://", suffix, maxsplit=1)[0].rstrip("/")
        if not suffix:
            return SPOTIFY_FOR_CREATORS_URL
        return urlunsplit(
            (
                "https",
                "creators.spotify.com",
                f"/pod/profile/datatalksclub/episodes/{suffix}",
                parsed.query,
                parsed.fragment,
            )
        )
    return SPOTIFY_FOR_CREATORS_URL


def _podcast_resources(
    value: Any,
    *,
    source_name: str,
    podcast_records: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Validate and normalize the source-ordered resource shape for episode pages."""

    if value is None:
        return []
    if not isinstance(value, list):
        raise ProjectionBuildError(f"podcast resources rejected: {source_name[:120]}")
    resources: list[dict[str, Any]] = []
    for resource in value:
        if not isinstance(resource, dict) or set(resource) != {"title", "url"}:
            raise ProjectionBuildError(f"podcast resource rejected: {source_name[:120]}")
        raw_url = _string(resource.get("url"), field="podcast resource", maximum=2_048)
        if raw_url.startswith("/") and not raw_url.startswith("//"):
            # Root-relative episode resources are valid source data and are resolved
            # against the complete catalogue below.  They must never pass through
            # as a legacy flat path.
            url = raw_url
        else:
            url = _safe_url(resource.get("url"), field="podcast resource", optional=False)
            url = _localize_internal_url(url)
            if not url.startswith("https://") and not url.startswith("/podcast/"):
                # A small number of historical source records still carry HTTP-only
                # external links. Keep the source order for resources that meet the
                # public contract and omit the unsafe destination rather than
                # manufacturing an HTTPS upgrade in the projection. Known internal
                # DataTalks links are localized above before this check.
                continue
        try:
            normalized = normalize_podcast_resource(
                {
                    "title": _string(
                        resource.get("title"),
                        field="podcast resource title",
                        maximum=500,
                    ),
                    "url": url,
                },
                records=podcast_records,
            )
        except PodcastResourceError as error:
            raise ProjectionBuildError(f"podcast resource rejected: {source_name[:120]}") from error
        resources.append(normalized.as_dict())
    return resources


def _podcast_video(
    raw: dict[str, Any], links: dict[str, str], *, source_name: str
) -> dict[str, str] | None:
    """Project only a YouTube id that exactly agrees with the source watch link."""

    identities = raw.get("ids")
    if identities is None:
        return None
    if not isinstance(identities, dict):
        raise ProjectionBuildError(f"podcast ids rejected: {source_name[:120]}")
    video_id = identities.get("youtube")
    if video_id is None:
        return None
    if not isinstance(video_id, str) or YOUTUBE_VIDEO_ID.fullmatch(video_id) is None:
        # The optional source identity is unavailable until content corrects it; the validated
        # watch link still remains available as the page's fallback destination.
        return None
    youtube_url = links.get("youtube", "")
    if ("youtube", video_id) not in _recording_identities(youtube_url):
        raise ProjectionBuildError(f"podcast YouTube identity mismatch: {source_name[:120]}")
    return {"provider": "youtube", "id": video_id}


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


def _body_blocks(body: str, *, preserve_links: bool = False) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    paragraph: list[str] = []
    used_ids: dict[str, int] = {}

    def flush() -> None:
        source = " ".join(paragraph)
        paragraph.clear()
        segment = _localize_editorial_links(source) if preserve_links else source
        text = _plain_inline(segment)
        if text:
            block = {"kind": "paragraph", "text": text}
            if preserve_links and MARKDOWN_LINK.search(segment):
                block["markdown"] = segment
            blocks.append(block)

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
            segment = _localize_editorial_links(line[2:]) if preserve_links else line[2:]
            text = _plain_inline(segment)
            if text:
                block = {"kind": "list_item", "text": text}
                if preserve_links and MARKDOWN_LINK.search(segment):
                    block["markdown"] = segment
                blocks.append(block)
            continue
        paragraph.append(line)
    flush()
    return blocks


# ---------------------------------------------------------------------------
# Article bodies
#
# `_body_blocks` above is the plain-text projection the wiki pages and the person
# bios still use: it keeps headings, paragraphs and list items and flattens
# everything else away.  An article body is not that shape.  The accepted content
# revision writes articles as Markdown with a large amount of literal HTML in it —
# 379 `<figure>` illustrations, 23 `<table>` comparisons, 90 fenced code samples,
# 111 ordered-list runs and 676 links — and flattening all of that to prose is
# what turned tutorials into unreadable paragraphs (owner report, this issue).
#
# `_article_blocks` therefore keeps the same block-list contract (a flat, ordered,
# JSON-safe list, so the recovered-FAQ split index still means what it meant) and
# widens the vocabulary: `image`, `table`, `code`, `quote`, `separator`, `chart`
# and `embed` join `heading`, `paragraph` and `list_item`.  Two properties are
# deliberate:
#
#   * `text` keeps the exact plain-text projection it had before, so reading time,
#     the leaked-metadata canary and any other plain-text consumer are unchanged
#     for a block whose structure did not change; and
#   * a block whose source carried more than its plain text also carries
#     `markdown`, the bounded source segment.  Rendering that segment is the
#     page's job, through the shared sanitizer, exactly as the recovered FAQ
#     answers are rendered.  Nothing in this file emits HTML.
#
# Liquid remains stripped on purpose: `{% include ... %}` addressed a legacy Jekyll
# site whose partials do not exist here, and the FAQ accordions it pulled in were
# recovered separately (content/article_faq.py).
# ---------------------------------------------------------------------------

ARTICLE_FENCE = re.compile(r"^(`{3,}|~{3,})[ \t]*([A-Za-z0-9_+#.-]{0,32})[ \t]*$")
ARTICLE_ORDERED_ITEM = re.compile(r"^\d{1,3}[.)][ \t]+(.+)$")
ARTICLE_UNORDERED_ITEM = re.compile(r"^[-*+][ \t]+(.+)$")
ARTICLE_QUOTE_LINE = re.compile(r"^>[ \t]?(.*)$")
ARTICLE_RULE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})$")
ARTICLE_TABLE_DIVIDER = re.compile(r"^\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)*\|?$")
ARTICLE_TABLE_PIPE = re.compile(r"(?<!\\)\|")
ARTICLE_TARGET_ATTRIBUTE = re.compile(r"\{:[ \t]*target[ \t]*=[ \t]*\"_?blank\"[ \t]*\}")
ARTICLE_UNSAFE_MARKUP = re.compile(
    r"</?(?:script|style|iframe|object|embed|form|input|button|link|meta|base|noscript)\b"
    r"|\son[a-z]{2,20}[ \t]*="
    r"|(?:href|src|action|formaction)[ \t]*=[ \t]*[\"']?[ \t]*(?:javascript|vbscript|data):",
    re.IGNORECASE,
)
ARTICLE_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
ARTICLE_HTML_ATTRIBUTE = re.compile(
    r"""([A-Za-z_:][-A-Za-z0-9_:.]*)[ \t\r\n]*=[ \t\r\n]*(?:"([^"]*)"|'([^']*)')"""
)
ARTICLE_FIGURE = re.compile(r"<figure\b[^>]*>(.*?)</figure\s*>", re.DOTALL | re.IGNORECASE)
ARTICLE_FIGCAPTION = re.compile(
    r"<figcaption\b[^>]*>(.*?)</figcaption\s*>", re.DOTALL | re.IGNORECASE
)
ARTICLE_IMG = re.compile(r"<img\b([^>]*?)/?>", re.IGNORECASE)
ARTICLE_TABLE_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr\s*>", re.DOTALL | re.IGNORECASE)
ARTICLE_TABLE_CELL = re.compile(r"<(th|td)\b[^>]*>(.*?)</\1\s*>", re.DOTALL | re.IGNORECASE)
ARTICLE_HTML_HEADING = re.compile(r"<h([1-6])\b([^>]*)>(.*?)</h\1\s*>", re.DOTALL | re.IGNORECASE)
ARTICLE_CANVAS = re.compile(r"<canvas\b([^>]*?)(?:/>|>.*?</canvas\s*>)", re.DOTALL | re.IGNORECASE)
ARTICLE_HTML_BLOCK_START = re.compile(
    r"^<(?:figure|table|img|canvas|hr|div|h[1-6])\b", re.IGNORECASE
)
ARTICLE_IMAGE_EXTENSIONS = frozenset({".gif", ".jpeg", ".jpg", ".png", ".svg"})
MAX_ARTICLE_SEGMENT_CHARACTERS = 20_000
MAX_ARTICLE_TABLE_CELLS = 400

# Canaries over the accepted article corpus.  Each one names a decision a reader
# can check against the source, and a build whose numbers move stops rather than
# quietly publishing a different body.
# The five remote illustrations the accepted pin hosts outside this projection.
# They have no checked media record, the shared sanitizer rejects an off-site
# `img src`, and the parity contract already declares them omitted.
# Chart.js canvases.  The data lives in `data-*` attributes and needs a script to
# draw; this projection carries no script, so the block keeps the chart's own
# title and the page says the chart is unavailable rather than dropping it.

# The legacy sponsor article drew these four survey pies at runtime with
# Chart.js from a public CDN.  Keep the source canvas as the authority, but
# bridge its reviewed title/data/caption tuple to a deterministic local SVG so
# the migrated article does not depend on JavaScript or a third-party asset.
SPONSOR_CHART_ASSET_BRIDGE = {
    (
        "Roles",
        "Data engineering 28.5%, data science and ML 26.8%, analytics 16.8%, "
        "software development 13.1%, management and consulting 7.0%, other 7.8%.",
    ): {
        "src": "/static/content/article-charts/sponsor-roles.svg",
        "alt": "Pie chart of DataTalks.Club community roles",
    },
    (
        "Seniority",
        "Senior individual contributors 40.6%, entry-level 35.6%, team leads 10.1%, "
        "directors and above 3.7%, students 3.0%, other 7.0%.",
    ): {
        "src": "/static/content/article-charts/sponsor-seniority.svg",
        "alt": "Pie chart of DataTalks.Club community seniority",
    },
    (
        "Geography",
        "North America 37.2%, Europe 25.1%, Asia-Pacific 24.5%, Africa 6.8%, "
        "South America 3.8%, Middle East and other 2.6%. Members come from more "
        "than 65 countries.",
    ): {
        "src": "/static/content/article-charts/sponsor-geography.svg",
        "alt": "Pie chart of the DataTalks.Club community by region",
    },
    (
        "Industries",
        "Technology 40.6%, finance 9.4%, education 9.1%, healthcare 8.1%, retail "
        "7.4%, other 25.4%.",
    ): {
        "src": "/static/content/article-charts/sponsor-industries.svg",
        "alt": "Pie chart of industries represented in the DataTalks.Club community",
    },
}

ARTICLE_INTERNAL_MARKDOWN_LINK = re.compile(
    r"(?P<prefix>(?<!\!)\[[^\]\n]*\]\([ \t]*<?)"
    r"(?P<url>https?://(?:www\.)?datatalks\.club(?::(?:80|443))?"
    r"(?P<path>/[^\s<>)]*|[?#][^\s<>)]*|))"
    r"(?P<suffix>>?)",
    re.IGNORECASE,
)
ARTICLE_INTERNAL_HTML_LINK = re.compile(
    r"(?P<prefix><a\b[^>]*?\bhref[ \t]*=[ \t]*[\"'])"
    r"(?P<url>https?://(?:www\.)?datatalks\.club(?::(?:80|443))?"
    r"(?P<path>/[^\"']*|[?#][^\"']*|))",
    re.IGNORECASE,
)


def _localize_editorial_links(value: str) -> str:
    """Keep links to our own pages on the active deployment host.

    Only anchor destinations are rewritten.  Images, CDN hosts, plain text and
    external destinations stay byte-for-byte unchanged; query strings and
    fragments are carried by the parsed path rather than reconstructed.
    """

    def replace(match: re.Match[str]) -> str:
        localized = _localize_internal_url(match.group("url"))
        return f"{match.group('prefix')}{localized}{match.groupdict().get('suffix', '')}"

    value = ARTICLE_INTERNAL_MARKDOWN_LINK.sub(replace, value)
    return ARTICLE_INTERNAL_HTML_LINK.sub(replace, value)


def _localize_internal_url(value: str) -> str:
    """Return one canonical site URL as a root-relative deployment URL."""

    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").casefold().removeprefix("www.")
    try:
        port = parsed.port
    except ValueError:
        return value
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or hostname != "datatalks.club"
        or port not in {None, 80, 443}
    ):
        return value
    return urlunsplit(("", "", parsed.path or "/", parsed.query, parsed.fragment))


def _html_attributes(value: str) -> dict[str, str]:
    """Return one HTML start tag's quoted attributes, lower-cased by name."""

    attributes: dict[str, str] = {}
    for match in ARTICLE_HTML_ATTRIBUTE.finditer(value):
        name = match.group(1).casefold()
        attributes.setdefault(name, html.unescape(match.group(2) or match.group(3) or ""))
    return attributes


def _image_dimensions(path: Path) -> tuple[int, int] | None:
    """Return one raster image's intrinsic pixel size, read from its own header.

    The page needs a real aspect box so an article does not reflow while its
    illustrations load.  Only the three header shapes this corpus contains are
    read — PNG, GIF and the JPEG frame markers — and anything else (an SVG, a
    truncated file) returns ``None`` so the page can say it has no dimensions
    instead of guessing one.
    """

    try:
        payload = path.read_bytes()
    except OSError:
        return None
    if payload.startswith(b"\x89PNG\r\n\x1a\n") and len(payload) >= 24:
        width = int.from_bytes(payload[16:20], "big")
        height = int.from_bytes(payload[20:24], "big")
    elif payload.startswith((b"GIF87a", b"GIF89a")) and len(payload) >= 10:
        width = int.from_bytes(payload[6:8], "little")
        height = int.from_bytes(payload[8:10], "little")
    elif payload.startswith(b"\xff\xd8"):
        width = height = 0
        position = 2
        while position + 9 < len(payload):
            if payload[position] != 0xFF:
                return None
            marker = payload[position + 1]
            if marker in {0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
                position += 2
                continue
            length = int.from_bytes(payload[position + 2 : position + 4], "big")
            if length < 2:
                return None
            if 0xC0 <= marker <= 0xCF and marker not in {0xC4, 0xC8, 0xCC}:
                height = int.from_bytes(payload[position + 5 : position + 7], "big")
                width = int.from_bytes(payload[position + 7 : position + 9], "big")
                break
            position += 2 + length
    else:
        return None
    if not 0 < width <= 20_000 or not 0 < height <= 20_000:
        return None
    return width, height


def _article_segment(value: str) -> str:
    """Return one bounded source segment, with the Liquid this site cannot run removed."""

    value = LIQUID.sub("", value)
    value = ARTICLE_HTML_COMMENT.sub("", value)
    value = strip_target_attributes_from_links(value)
    value = _localize_editorial_links(value)
    # The accepted articles attach Kramdown's `{:target="_blank"}` to 270 links.
    # The plain-text projection keeps that token and the runtime strips it under a
    # counted canary (content/catalogue.py); the source segment this field
    # adds is written clean instead, because nothing downstream would ever want a
    # legacy renderer's directive rendered as prose.
    value = ARTICLE_TARGET_ATTRIBUTE.sub("", value)
    value = value.strip()
    if len(value) > MAX_ARTICLE_SEGMENT_CHARACTERS or "\x00" in value:
        raise ProjectionBuildError("article body segment rejected")
    # The page renders this segment through the shared sanitizer, which would
    # remove all of the below.  The build refuses it anyway: a checked artifact
    # should not carry executable or embedding markup as data, and the accepted
    # corpus contains none of it, so a source that grows some is a review event
    # rather than something a sanitizer quietly absorbs.
    if ARTICLE_UNSAFE_MARKUP.search(value) is not None:
        raise ProjectionBuildError("article body segment contains disallowed markup")
    return value


def _article_text_block(kind: str, source: str, **extra: Any) -> dict[str, Any] | None:
    """Return one text-carrying article block, or ``None`` when it says nothing.

    ``text`` is the same plain-text projection this builder always produced.
    ``markdown`` is added only when the source segment carried more than that
    plain text, so a block the flattening never damaged keeps its exact previous
    shape and a block it did damage carries what the page needs to render it.

    A segment with no words at all is not a block.  These bodies contain a
    handful of ``&nbsp;`` spacers and one orphaned closing tag, and every element
    that carries meaning without words — an illustration, a table, a rule — has
    its own kind above, so nothing is lost by refusing them here.
    """

    segment = _article_segment(source)
    text = _plain_inline(segment)
    if not text:
        return None
    block: dict[str, Any] = {"kind": kind, "text": text, **extra}
    if segment != text:
        block["markdown"] = segment
    return block


def _article_table_block(head: list[str], rows: list[list[str]], index: int) -> dict[str, Any]:
    if not head and not rows:
        raise ProjectionBuildError("article table is empty")
    width = max([len(head), *(len(row) for row in rows)] or [0])
    if width * (len(rows) + 1) > MAX_ARTICLE_TABLE_CELLS:
        raise ProjectionBuildError("article table is too large")
    return {
        "kind": "table",
        # The reading column is narrower than a wide comparison table, so the page
        # puts one in a keyboard-reachable scroll frame.  That frame needs a name,
        # and a name that repeats inside one document is its own defect, so the
        # build numbers the tables it found rather than inventing a description.
        "label": f"Table {index}",
        "head": [_article_segment(cell) for cell in head],
        "rows": [[_article_segment(cell) for cell in row] for row in rows],
    }


def _article_html_table(markup: str, index: int) -> dict[str, Any]:
    head: list[str] = []
    rows: list[list[str]] = []
    for row_match in ARTICLE_TABLE_ROW.finditer(markup):
        cells = [
            (cell.group(1).casefold(), cell.group(2))
            for cell in ARTICLE_TABLE_CELL.finditer(row_match.group(1))
        ]
        if not cells:
            continue
        if not head and all(name == "th" for name, _ in cells):
            head = [value for _, value in cells]
            continue
        rows.append([value for _, value in cells])
    return _article_table_block(head, rows, index)


def _article_markdown_table(lines: list[str], index: int) -> dict[str, Any]:
    def cells(line: str) -> list[str]:
        # A cell may contain an escaped pipe, and one of these tables does; the
        # row is therefore split on unescaped delimiters only.
        parts = [cell.strip() for cell in ARTICLE_TABLE_PIPE.split(line.strip())]
        if parts and not parts[0]:
            parts.pop(0)
        if parts and not parts[-1]:
            parts.pop()
        return parts

    head = cells(lines[0])
    rows = [cells(line) for line in lines[2:] if line.strip()]
    return _article_table_block(head, rows, index)


def _article_image_block(
    tag_attributes: dict[str, str],
    caption: str,
    *,
    media_root: Path | None,
    counters: dict[str, int],
) -> dict[str, Any] | None:
    source = tag_attributes.get("src", "").strip()
    if not source:
        return None
    if not source.startswith("/") or source.startswith("//"):
        # An off-site illustration has no checked media record and the shared
        # sanitizer rejects its address; the accepted parity contract already
        # declares these omitted rather than published from a remote host.
        counters["remote_images"] = counters.get("remote_images", 0) + 1
        return None
    relative = source.lstrip("/")
    if ".." in relative or Path(relative).suffix.casefold() not in ARTICLE_IMAGE_EXTENSIONS:
        raise ProjectionBuildError("article image path rejected")
    path = None
    if media_root is not None:
        path = media_root / relative
        if not path.is_file() or path.is_symlink():
            raise ProjectionBuildError("article image is missing from the pinned source")
    counters["images"] = counters.get("images", 0) + 1
    alt = tag_attributes.get("alt", "").strip()
    if not alt:
        # Said rather than invented: this illustration carries no description in
        # the source.  An empty `alt` keeps a screen reader from reading a file
        # name aloud; the caption below it, where the source wrote one, is what
        # actually describes the picture.
        counters["images_without_alt"] = counters.get("images_without_alt", 0) + 1
    block: dict[str, Any] = {"kind": "image", "src": source, "alt": alt}
    title = tag_attributes.get("title", "").strip()
    if title:
        block["title"] = title
    caption_text = _plain_inline(_article_segment(caption))
    if caption_text:
        block["caption"] = caption_text
    dimensions = _image_dimensions(path) if path is not None else None
    if dimensions is not None:
        block["width"], block["height"] = dimensions
    elif path is not None:
        counters["images_without_dimensions"] = counters.get("images_without_dimensions", 0) + 1
    return block


def _article_html_segment(
    segment: str,
    *,
    media_root: Path | None,
    counters: dict[str, int],
    numbering: dict[str, int],
    heading: Any,
    text_blocks: Any,
) -> list[dict[str, Any]]:
    """Return the blocks one literal-HTML source segment carries.

    The accepted articles write their illustrations, comparison tables and a
    handful of section headings as HTML inside the Markdown.  Each recognised
    element becomes its own typed block; whatever is left over is kept whole as
    an ``embed`` so a call-to-action or a layout wrapper still reaches the page
    through the sanitizer instead of disappearing.
    """

    # Checked once for the whole run, because the elements below are read field by
    # field rather than through `_article_segment`: an event handler on an image
    # is discarded by that reading, and it still stops the build.
    if ARTICLE_UNSAFE_MARKUP.search(segment) is not None:
        raise ProjectionBuildError("article body segment contains disallowed markup")
    blocks: list[dict[str, Any]] = []
    position = 0
    for match in re.finditer(
        r"<figure\b[^>]*>.*?</figure\s*>"
        r"|<table\b[^>]*>.*?</table\s*>"
        r"|<h([1-6])\b[^>]*>.*?</h\1\s*>"
        r"|<img\b[^>]*?/?>"
        r"|<canvas\b[^>]*?(?:/>|>.*?</canvas\s*>)"
        r"|<div\b[^>]*class=\"[^\"]*article-divider[^\"]*\"[^>]*>\s*</div\s*>"
        r"|<hr\b[^>]*/?>",
        segment,
        re.DOTALL | re.IGNORECASE,
    ):
        leading = segment[position : match.start()]
        position = match.end()
        if leading.strip():
            blocks.extend(text_blocks(leading))
        found = match.group(0)
        lowered = found[:9].casefold()
        if lowered.startswith("<figure"):
            inner = ARTICLE_FIGURE.match(found)
            body = inner.group(1) if inner else ""
            caption_match = ARTICLE_FIGCAPTION.search(body)
            caption = caption_match.group(1) if caption_match else ""
            image_match = ARTICLE_IMG.search(body)
            if image_match is not None:
                image = _article_image_block(
                    _html_attributes(image_match.group(1)),
                    caption,
                    media_root=media_root,
                    counters=counters,
                )
                if image is not None:
                    blocks.append(image)
                continue
            canvas_match = ARTICLE_CANVAS.search(body)
            if canvas_match is not None:
                blocks.append(
                    _article_chart_block(_html_attributes(canvas_match.group(1)), caption, counters)
                )
                continue
            blocks.extend(text_blocks(body))
        elif lowered.startswith("<table"):
            counters["tables"] = counters.get("tables", 0) + 1
            numbering["tables"] = numbering.get("tables", 0) + 1
            blocks.append(_article_html_table(found, numbering["tables"]))
        elif lowered.startswith("<img"):
            image_tag = ARTICLE_IMG.match(found)
            image = (
                _article_image_block(
                    _html_attributes(image_tag.group(1)),
                    "",
                    media_root=media_root,
                    counters=counters,
                )
                if image_tag is not None
                else None
            )
            if image is not None:
                blocks.append(image)
        elif lowered.startswith("<canvas"):
            canvas_tag = ARTICLE_CANVAS.match(found)
            blocks.append(
                _article_chart_block(
                    _html_attributes(canvas_tag.group(1) if canvas_tag else ""), "", counters
                )
            )
        elif lowered.startswith("<h"):
            parsed = ARTICLE_HTML_HEADING.match(found)
            if parsed is None:
                raise ProjectionBuildError("article heading markup rejected")
            blocks.append(
                heading(
                    int(parsed.group(1)),
                    parsed.group(3),
                    _html_attributes(parsed.group(0).split(">", 1)[0]).get("id", ""),
                )
            )
        else:
            blocks.append({"kind": "separator"})
    trailing = segment[position:]
    if trailing.strip():
        blocks.extend(text_blocks(trailing))
    return blocks


def _article_chart_block(
    attributes: dict[str, str], caption: str, counters: dict[str, int]
) -> dict[str, Any]:
    counters["charts"] = counters.get("charts", 0) + 1
    title = attributes.get("data-title", "").strip()
    caption_text = _plain_inline(_article_segment(caption))
    bridged = SPONSOR_CHART_ASSET_BRIDGE.get((title, caption_text))
    if bridged is not None:
        return {
            "kind": "chart",
            "src": bridged["src"],
            "alt": bridged["alt"],
            "title": title,
            "caption": caption_text,
            "width": 640,
            "height": 400,
        }
    block: dict[str, Any] = {"kind": "chart", "text": title or caption_text}
    if caption_text and caption_text != block["text"]:
        block["caption"] = caption_text
    return block


def _article_blocks(
    body: str, *, media_root: Path | None, counters: dict[str, int]
) -> list[dict[str, Any]]:
    """Return one article body as its ordered blocks, keeping what it carries."""

    blocks: list[dict[str, Any]] = []
    used_ids: dict[str, int] = {}
    pending: list[str] = []
    # Table numbering is per document, so a body reads the same whether it is
    # built whole or as the prefix the recovered-FAQ position is measured from.
    numbering: dict[str, int] = {}

    def heading_block(level: int, title_source: str, source_id: str) -> dict[str, Any]:
        title = _plain_inline(title_source)
        # A source that already names its heading keeps that name: the article's
        # own table of contents links to it, and a derived identifier would break
        # every one of those links.
        base_id = source_id.strip() or _slugify(title)
        used_ids[base_id] = used_ids.get(base_id, 0) + 1
        fragment_id = base_id if used_ids[base_id] == 1 else f"{base_id}-{used_ids[base_id]}"
        return {
            "kind": "heading",
            "level": max(2, min(6, level)),
            "id": fragment_id,
            "text": title,
        }

    def flush() -> None:
        if not pending:
            return
        chunk = list(pending)
        pending.clear()
        blocks.extend(_article_chunk(chunk))

    def _article_chunk(lines: list[str]) -> list[dict[str, Any]]:
        """Return one blank-line-separated source chunk as blocks.

        A chunk is a table, a run of literal HTML, or text — and an article
        regularly puts an illustration straight under a sentence with no blank
        line between them, so the text before the first HTML element is read as
        text and the rest is handed to the HTML reader.
        """

        stripped = [line.strip() for line in lines]
        if (
            len(lines) > 2
            and "|" in stripped[0]
            and ARTICLE_TABLE_DIVIDER.fullmatch(stripped[1]) is not None
        ):
            counters["tables"] = counters.get("tables", 0) + 1
            numbering["tables"] = numbering.get("tables", 0) + 1
            return [_article_markdown_table(lines, numbering["tables"])]
        opening = next(
            (
                position
                for position, bare in enumerate(stripped)
                if ARTICLE_HTML_BLOCK_START.match(bare) is not None
            ),
            None,
        )
        if opening is not None:
            return _text_chunk(lines[:opening]) + _article_html_segment(
                "\n".join(lines[opening:]),
                media_root=media_root,
                counters=counters,
                numbering=numbering,
                heading=heading_block,
                text_blocks=lambda segment: _text_chunk(segment.splitlines()),
            )
        return _text_chunk(lines)

    def _text_chunk(lines: list[str]) -> list[dict[str, Any]]:
        stripped = [line.strip() for line in lines]
        produced: list[dict[str, Any]] = []
        paragraph: list[str] = []
        quote: list[str] = []

        def close_paragraph() -> None:
            if paragraph:
                block = _article_text_block("paragraph", "\n".join(paragraph))
                paragraph.clear()
                if block is not None:
                    produced.append(block)

        def close_quote() -> None:
            if quote:
                block = _article_text_block("quote", "\n".join(quote))
                quote.clear()
                if block is not None:
                    produced.append(block)

        for line, bare in zip(lines, stripped, strict=True):
            quoted = ARTICLE_QUOTE_LINE.match(bare)
            if quoted is not None:
                close_paragraph()
                quote.append(quoted.group(1))
                continue
            close_quote()
            if ARTICLE_RULE.fullmatch(bare) is not None:
                close_paragraph()
                produced.append({"kind": "separator"})
                continue
            unordered = ARTICLE_UNORDERED_ITEM.match(bare)
            ordered = None if unordered else ARTICLE_ORDERED_ITEM.match(bare)
            item_match = unordered or ordered
            if item_match is not None:
                close_paragraph()
                block = _article_text_block(
                    "list_item",
                    item_match.group(1),
                    **({"ordered": True} if ordered is not None else {}),
                )
                if block is not None:
                    produced.append(block)
                continue
            paragraph.append(line)
        close_quote()
        close_paragraph()
        return produced

    lines = body.splitlines()
    index = 0
    while index < len(lines):
        raw_line = lines[index]
        line = raw_line.strip()
        fence = ARTICLE_FENCE.match(line)
        if fence is not None:
            flush()
            closing = fence.group(1)[0] * 3
            collected: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith(closing):
                collected.append(lines[index])
                index += 1
            index += 1
            counters["code_blocks"] = counters.get("code_blocks", 0) + 1
            code = "\n".join(collected).rstrip()
            if len(code) > MAX_ARTICLE_SEGMENT_CHARACTERS or "\x00" in code:
                raise ProjectionBuildError("article code block rejected")
            block: dict[str, Any] = {"kind": "code", "text": code}
            if fence.group(2):
                block["language"] = fence.group(2).casefold()
            blocks.append(block)
            continue
        index += 1
        heading = HEADING.match(line)
        if heading is not None:
            flush()
            blocks.append(heading_block(len(heading.group(1)), heading.group(2), ""))
            continue
        if not line:
            flush()
            continue
        pending.append(raw_line)
    flush()
    return blocks


def _title_from_record(record: dict[str, Any], key: str) -> str:
    return _string(record.get("title") or record.get("short") or key, field="title", maximum=500)


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
        if relation_type in {"podcast", "citation"} and not href:
            continue
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
            record["url"] = urlunsplit(
                ("", "", parsed.path.rstrip("/"), parsed.query, parsed.fragment)
            )
        elif isinstance(url, str) and url.startswith("/search/"):
            parsed = urlsplit(url)
            suffix = parsed.path.removeprefix("/search/").rstrip("/")
            path = "/wiki/search" + (f"/{suffix}" if suffix else "")
            record["url"] = urlunsplit(("", "", path, parsed.query, parsed.fragment))
        elif isinstance(url, str) and _localize_internal_url(url) != url:
            parsed = urlsplit(_localize_internal_url(url))
            canonical = ""
            recognized = False
            if parsed.path.startswith("/podcast/"):
                recognized = True
                key = parsed.path.removeprefix("/podcast/").removesuffix(".html")
                canonical = podcast_paths.get(key, "")
            elif parsed.path.startswith("/books/"):
                recognized = True
                key = parsed.path.removeprefix("/books/").removesuffix(".html")
                canonical = book_paths.get(key, "")
            elif parsed.path.startswith("/people/"):
                recognized = True
                key = parsed.path.removeprefix("/people/").removesuffix(".html")
                canonical = people_paths.get(key, "")
                if not canonical:
                    record["interaction"] = "unprojected_public_person"
            if canonical:
                record["url"] = urlunsplit(("", "", canonical, parsed.query, parsed.fragment))
            elif recognized:
                record["url"] = ""
    return result


def _load_json_bounded(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read_text(path, maximum=MAX_GRAPH_FILE_BYTES))
    except json.JSONDecodeError as exc:
        raise ProjectionBuildError(f"invalid JSON source: {path.name[:120]}") from exc
    if not isinstance(value, dict):
        raise ProjectionBuildError(f"JSON source is not an object: {path.name[:120]}")
    return value


def _tree_sha256(root: Path) -> str:
    """Digest the projection artifacts and wiki assets, excluding the media objects.

    The media objects are served from an object store and verified per record against
    ``provenance.checksum``, while a symlink anywhere below the root — including under
    ``media/`` — is still a hard failure.
    """

    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.is_symlink():
            raise ProjectionBuildError("projection tree contains a symlink")
        relative_path = path.relative_to(root).as_posix()
        if path.name == "manifest.json" or relative_path.startswith(MEDIA_TREE_PREFIX):
            continue
        relative = relative_path.encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()
