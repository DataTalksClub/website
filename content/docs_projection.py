"""Validated, source-backed projection for the public documentation site.

The documentation repository is intentionally not read during a request.  The checked-in
projection is generated from the pinned source checkout and contains the Markdown body, source
checksums, and the navigation metadata needed by the public renderer.  Keeping the source commit
in this module and validating it at load time makes an accidental partial or stale projection fail
closed instead of silently serving a mixture of document versions.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from collections.abc import Mapping
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import mistune
from django.core.exceptions import ImproperlyConfigured

from .services import sanitize_rendered_html

DOCS_PROJECTION_PATH = Path(__file__).with_name("docs_projection.json")
DOCS_ASSET_ROOT = Path(__file__).with_name("docs_assets")
DOCS_SOURCE_REVISION = "3f23e006ffdaa498bbc69697408853b6f5eb37dc"
DOCS_ROOT_PATH = "/docs/"
_DOCS_ASSET_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/svg+xml"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DOCS_PREFIXES = ("/courses/", "/general/", "/activities/", "/assets/")
_LIQUID_RELATIVE_URL = re.compile(
    r"{{\s*(['\"])(?P<path>.*?)\1\s*\|\s*relative_url\s*}}",
    re.DOTALL,
)
_KRAMDOWN_ATTRIBUTE_LINE = re.compile(r"(?m)^\s*\{:\s*[^}\n]+\}\s*$")
_KRAMDOWN_INLINE_ATTRIBUTE = re.compile(r"\]\((?P<url>[^)\n]+)\)\{:\s*[^}\n]+\}")
_LEGACY_SLACK_URL = re.compile(r"https?://datatalks\.club/slack\.html(?:#[^\s)]+)?")
_HEADING = re.compile(
    r"(?P<open><h(?P<level>[1-6])>)(?P<body>.*?)(?P<close></h(?P=level)>)",
    re.DOTALL,
)
_MARKDOWN = mistune.create_markdown(escape=False, plugins=("strikethrough", "table"))


class _HeadingText(HTMLParser):
    """Collect visible text from one rendered heading without trusting source HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _slugify_heading(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", html.unescape(value))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    return slug or "section"


def _heading_text(value: str) -> str:
    parser = _HeadingText()
    parser.feed(value)
    return " ".join(" ".join(parser.parts).split())


def _heading_ids(rendered: str) -> tuple[str, tuple[dict[str, Any], ...]]:
    seen: dict[str, int] = {}
    headings: list[dict[str, Any]] = []

    def replace(match: re.Match[str]) -> str:
        text = _heading_text(match.group("body"))
        base = _slugify_heading(text)
        count = seen.get(base, 0)
        seen[base] = count + 1
        slug = base if count == 0 else f"{base}-{count}"
        headings.append({"level": int(match.group("level")), "id": slug, "title": text})
        return (
            f'<h{match.group("level")} id="{slug}">{match.group("body")}</h{match.group("level")}>'
        )

    return _HEADING.sub(replace, rendered), tuple(headings)


def _docs_url(value: str) -> str:
    """Rewrite a source-root URL to the Django docs mount when it is a docs URL.

    The source uses Jekyll's ``relative_url`` filter.  Main-site links (for example ``/slack``)
    remain main-site links, while paths owned by the docs repository gain the ``/docs`` mount.
    """

    value = value.strip()
    if not value:
        return value
    if value.startswith("//"):
        return value
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return value
    if value == "/":
        return DOCS_ROOT_PATH
    if value.startswith(_DOCS_PREFIXES):
        return f"/docs{value}"
    return value


def _prepare_markdown(raw: str) -> str:
    # Just the Docs uses these attribute-only lines for typography and button classes.  They are
    # presentation metadata rather than content, and allowing them through mistune would expose
    # literal ``{: ... }`` text.  Inline attributes are treated the same way.
    prepared = _KRAMDOWN_ATTRIBUTE_LINE.sub("", raw)
    prepared = _KRAMDOWN_INLINE_ATTRIBUTE.sub(lambda match: f"]({match.group('url')})", prepared)
    prepared = _LIQUID_RELATIVE_URL.sub(lambda match: _docs_url(match.group("path")), prepared)
    # The source repository still contains the old GitHub Pages URL in a few documents.  Keep
    # those source bodies immutable, but ensure rendered Django pages link to the canonical route.
    return _LEGACY_SLACK_URL.sub(
        lambda match: (
            "/slack" + ("#" + match.group(0).split("#", 1)[1] if "#" in match.group(0) else "")
        ),
        prepared,
    )


def _asset_file(source_path: str) -> Path | None:
    """Resolve a projected asset path without allowing traversal or symlinks."""

    if not source_path.startswith("assets/") or "\\" in source_path:
        return None
    relative = source_path.removeprefix("assets/")
    raw_parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        return None
    parts = PurePosixPath(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    path = DOCS_ASSET_ROOT / Path(*parts)
    if path.is_symlink() or not path.is_file():
        return None
    try:
        path.resolve().relative_to(DOCS_ASSET_ROOT.resolve())
    except ValueError:
        return None
    return path


def docs_asset_path(asset: str) -> tuple[Path, str] | None:
    """Return a checked-in docs image and its declared media type for one URL segment."""

    if not asset or asset.startswith("/") or "\\" in asset:
        return None
    raw_parts = asset.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        return None
    parts = PurePosixPath(asset).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    public_path = f"/docs/assets/{PurePosixPath(asset).as_posix()}"
    for record in docs_projection().get("assets", ()):
        if record["public_path"] != public_path:
            continue
        path = _asset_file(str(record["source_path"]))
        if path is None:
            return None
        return path, str(record["content_type"])
    return None


def render_docs_markdown(page: Mapping[str, Any] | str) -> tuple[str, tuple[dict[str, Any], ...]]:
    """Render one projected Markdown body and return sanitized HTML plus heading metadata."""

    if isinstance(page, str):
        projected = docs_page(page)
        if projected is None:
            raise KeyError(page)
        record: Mapping[str, Any] = projected
    else:
        record = page
    raw = record.get("body")
    if not isinstance(raw, str):
        raise ImproperlyConfigured("Docs projection page body must be text.")
    rendered = str(_MARKDOWN(_prepare_markdown(raw)))
    rendered, headings = _heading_ids(rendered)
    return sanitize_rendered_html("docs", rendered), headings


def _validate_projection(projection: Mapping[str, Any]) -> None:
    if projection.get("schema_version") != 1:
        raise ImproperlyConfigured("Unsupported docs content projection schema.")
    source = projection.get("source")
    if not isinstance(source, Mapping) or source.get("revision") != DOCS_SOURCE_REVISION:
        raise ImproperlyConfigured("Docs content projection revision does not match inventory.")
    if projection.get("root_path") != DOCS_ROOT_PATH:
        raise ImproperlyConfigured("Docs projection root path is invalid.")
    pages = projection.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ImproperlyConfigured("Docs content projection contains no pages.")
    assets = projection.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ImproperlyConfigured("Docs content projection contains no assets.")
    asset_paths: set[str] = set()
    asset_sources: set[str] = set()
    for asset in assets:
        if not isinstance(asset, Mapping):
            raise ImproperlyConfigured("Docs projection asset must be an object.")
        public_path = asset.get("public_path")
        source_path = asset.get("source_path")
        content_type = asset.get("content_type")
        size = asset.get("size")
        checksum = asset.get("sha256")
        if (
            not isinstance(public_path, str)
            or not public_path.startswith("/docs/assets/")
            or "?" in public_path
            or "#" in public_path
            or public_path in asset_paths
            or not isinstance(source_path, str)
            or source_path in asset_sources
            or not source_path.startswith("assets/")
            or "?" in source_path
            or "#" in source_path
            or "\\" in source_path
            or any(part in {"", ".", ".."} for part in source_path.split("/"))
            or not isinstance(content_type, str)
            or content_type not in _DOCS_ASSET_CONTENT_TYPES
            or not isinstance(size, int)
            or size < 1
            or not isinstance(checksum, str)
            or _SHA256.fullmatch(checksum) is None
            or asset.get("source_revision") != DOCS_SOURCE_REVISION
        ):
            raise ImproperlyConfigured("Docs projection asset metadata is invalid.")
        expected_public_path = f"/docs/{source_path}"
        if public_path != expected_public_path:
            raise ImproperlyConfigured("Docs projection asset public path is inconsistent.")
        path = _asset_file(source_path)
        if path is None:
            raise ImproperlyConfigured(f"Docs projection asset is unavailable: {source_path}")
        if path.stat().st_size != size:
            raise ImproperlyConfigured(f"Docs projection asset size mismatch: {source_path}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise ImproperlyConfigured(f"Docs projection asset checksum mismatch: {source_path}")
        asset_paths.add(public_path)
        asset_sources.add(source_path)
    public_paths: set[str] = set()
    source_paths: set[str] = set()
    for page in pages:
        if not isinstance(page, Mapping):
            raise ImproperlyConfigured("Docs projection page must be an object.")
        public_path = page.get("public_path")
        source_path = page.get("source_path")
        title = page.get("title")
        body = page.get("body")
        if (
            not isinstance(public_path, str)
            or not public_path.startswith(DOCS_ROOT_PATH)
            or not public_path.endswith("/")
            or "?" in public_path
            or "#" in public_path
            or public_path in public_paths
        ):
            raise ImproperlyConfigured(
                "Docs projection contains an invalid or duplicate public path."
            )
        if not isinstance(source_path, str) or not source_path or source_path in source_paths:
            raise ImproperlyConfigured(
                "Docs projection contains an invalid or duplicate source path."
            )
        if not isinstance(title, str) or not title.strip() or not isinstance(body, str):
            raise ImproperlyConfigured("Docs projection page metadata is incomplete.")
        body_hash = page.get("body_sha256")
        if body_hash != hashlib.sha256(body.encode("utf-8")).hexdigest():
            raise ImproperlyConfigured(f"Docs projection body checksum mismatch: {source_path}")
        if page.get("source_revision") != DOCS_SOURCE_REVISION:
            raise ImproperlyConfigured(f"Docs projection page revision mismatch: {source_path}")
        public_paths.add(public_path)
        source_paths.add(source_path)
    if DOCS_ROOT_PATH not in public_paths:
        raise ImproperlyConfigured("Docs projection root page is missing.")
    for page in pages:
        for key in ("parent_path", "grand_parent_path"):
            value = page.get(key)
            if value is not None and value not in public_paths:
                raise ImproperlyConfigured(f"Docs projection {key} does not resolve: {value}")


@lru_cache(maxsize=1)
def docs_projection() -> dict[str, Any]:
    try:
        projection = json.loads(DOCS_PROJECTION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImproperlyConfigured("Docs content projection cannot be loaded.") from exc
    if not isinstance(projection, dict):
        raise ImproperlyConfigured("Docs content projection must be an object.")
    _validate_projection(projection)
    return projection


def docs_pages() -> tuple[dict[str, Any], ...]:
    return tuple(dict(page) for page in docs_projection()["pages"])


def docs_page(public_path: str) -> dict[str, Any] | None:
    normalized = public_path or DOCS_ROOT_PATH
    if normalized == "/docs":
        normalized = DOCS_ROOT_PATH
    elif normalized.startswith("/docs/") and not normalized.endswith("/"):
        normalized += "/"
    for page in docs_projection()["pages"]:
        if page["public_path"] == normalized:
            return dict(page)
    return None


def _nav_key(page: Mapping[str, Any]) -> tuple[int, str, str]:
    try:
        order = int(page.get("nav_order") or 0)
    except (TypeError, ValueError):
        order = 0
    return order, str(page.get("title") or "").casefold(), str(page["public_path"])


def docs_children(parent_path: str | None) -> tuple[dict[str, Any], ...]:
    children = [
        dict(page)
        for page in docs_projection()["pages"]
        if page["public_path"] != DOCS_ROOT_PATH and page.get("parent_path") == parent_path
    ]
    children.sort(key=_nav_key)
    return tuple(children)


def docs_breadcrumbs(page: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    result: list[dict[str, str]] = [{"title": "Documentation", "public_path": DOCS_ROOT_PATH}]
    chain: list[dict[str, Any]] = []
    current = page
    by_path = {item["public_path"]: item for item in docs_projection()["pages"]}
    while current.get("parent_path"):
        parent_path = str(current["parent_path"])
        parent = by_path.get(parent_path)
        if parent is None:
            break
        chain.append(parent)
        current = parent
    for parent in reversed(chain):
        result.append({"title": str(parent["title"]), "public_path": str(parent["public_path"])})
    return tuple(result)


def docs_sibling_navigation(
    page: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    siblings = list(docs_children(page.get("parent_path")))
    for index, sibling in enumerate(siblings):
        if sibling["public_path"] == page.get("public_path"):
            previous = siblings[index - 1] if index else None
            following = siblings[index + 1] if index + 1 < len(siblings) else None
            return previous, following
    return None, None


def docs_navigation() -> tuple[dict[str, Any], ...]:
    """Return top-level pages in deterministic source navigation order."""

    return docs_children(None)


__all__ = [
    "DOCS_ASSET_ROOT",
    "DOCS_ROOT_PATH",
    "DOCS_SOURCE_REVISION",
    "docs_breadcrumbs",
    "docs_children",
    "docs_asset_path",
    "docs_navigation",
    "docs_page",
    "docs_pages",
    "docs_projection",
    "docs_sibling_navigation",
    "render_docs_markdown",
]
