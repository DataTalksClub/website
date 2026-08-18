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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

import mistune
from django.core.exceptions import ImproperlyConfigured

from .services import sanitize_rendered_html

DOCS_PROJECTION_PATH = Path(__file__).with_name("docs_projection.json")
DOCS_ASSET_ROOT = Path(__file__).with_name("docs_assets")
DOCS_SOURCE_REVISION = "3f23e006ffdaa498bbc69697408853b6f5eb37dc"
DOCS_ROOT_PATH = "/docs/"
DOCS_SEARCH_URL = "https://github.com/DataTalksClub/docs/search"
_DOCS_ASSET_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/svg+xml"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DOCS_PREFIXES = ("/courses/", "/general/", "/activities/", "/assets/")
_LIQUID_RELATIVE_URL = re.compile(
    r"{{\s*(['\"])(?P<path>.*?)\1\s*\|\s*relative_url\s*}}",
    re.DOTALL,
)
_KRAMDOWN_ATTRIBUTE_LINE = re.compile(r"(?m)^\s*\{:\s*[^}\n]+\}\s*$")
_KRAMDOWN_INLINE_ATTRIBUTE = re.compile(r"\]\((?P<url>[^)\n]+)\)\{:\s*[^}\n]+\}")
_HEADING = re.compile(
    r"(?P<open><h(?P<level>[1-6])>)(?P<body>.*?)(?P<close></h(?P=level)>)",
    re.DOTALL,
)
_MARKDOWN = mistune.create_markdown(escape=False, plugins=("strikethrough", "table"))


@dataclass(frozen=True, slots=True)
class DocsNavigationItem:
    """One immutable page position in the source-backed Docs hierarchy."""

    page: Mapping[str, Any]
    children: tuple[DocsNavigationItem, ...]

    @property
    def public_path(self) -> str:
        return str(self.page["public_path"])

    @property
    def title(self) -> str:
        return str(self.page["title"])

    @property
    def description(self) -> str:
        return str(self.page.get("description") or "")


@dataclass(frozen=True, slots=True)
class DocsNavigationTree:
    """A validated hierarchy and its deterministic depth-first reading order."""

    root: DocsNavigationItem
    preorder: tuple[DocsNavigationItem, ...]
    documents: tuple[DocsNavigationItem, ...]
    by_path: Mapping[str, DocsNavigationItem]


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


_INTERNAL_HUB_PATHS = {
    "/events.html": "/events",
    "/podcast.html": "/podcast",
    "/books.html": "/books",
    "/slack.html": "/slack",
    "/slack/guidelines.html": "/slack",
    "/slack": "/slack",
}
_NEWSLETTER_PATH = "/newsletter.html"
_LUMA_EVENTS_URL = "https://luma.com/dtc-events"
_COMMUNITY_WORKSPACE_HOST = "datatalks-club.slack.com"
_SLACK_CLIENT_HOST = "app.slack.com"
_COMMUNITY_WORKSPACE_ID = "T01ATQK62F8"


def _rewritten_internal_destination(value: str) -> str | None:
    """Return the allowlisted destination replacement, preserving query and fragment."""

    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != "https" or parsed.netloc != "datatalks.club":
            return None
    replacement = _INTERNAL_HUB_PATHS.get(parsed.path)
    if replacement is None:
        return None
    query_source = value.split("#", 1)[0]
    query = f"?{parsed.query}" if "?" in query_source else ""
    fragment = f"#{parsed.fragment}" if "#" in value else ""
    return f"{replacement}{query}{fragment}"


def _community_workspace_destination(value: str) -> str | None:
    """Return ``/slack`` for a DataTalks.Club community-workspace link destination.

    The workspace forms in the source are ``datatalks-club.slack.com/<anything>`` and
    ``app.slack.com/client/T01ATQK62F8/<channel>``, where ``T01ATQK62F8`` is this workspace's
    ID.  Both address Slack's own UI rather than anchors on ``/slack``, so unlike the hub
    aliases the rewrite drops the query and fragment.  Slack's product documentation
    (``slack.com/help/...``) and every other external host stay untouched.
    """

    parsed = urlsplit(value)
    if parsed.netloc == _COMMUNITY_WORKSPACE_HOST:
        return "/slack"
    segments = [segment for segment in parsed.path.split("/") if segment]
    if parsed.netloc == _SLACK_CLIENT_HOST and (
        segments[:1] == [_COMMUNITY_WORKSPACE_ID]
        or segments[:2] == ["client", _COMMUNITY_WORKSPACE_ID]
    ):
        return "/slack"
    return None


def _is_newsletter_destination(value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != "https" or parsed.netloc != "datatalks.club":
            return False
    return parsed.path == _NEWSLETTER_PATH


def _find_unescaped(value: str, start: int, target: str) -> int | None:
    """Find one unescaped character in Markdown text."""

    index = start
    while index < len(value):
        if value[index] == "\\":
            index += 2
            continue
        if value[index] == target:
            return index
        index += 1
    return None


def _find_link_label_end(value: str, start: int) -> int | None:
    """Find a closing Markdown link label bracket, allowing nested brackets."""

    depth = 0
    index = start
    while index < len(value):
        character = value[index]
        if character == "\\":
            index += 2
            continue
        if character == "[":
            depth += 1
        elif character == "]":
            if depth == 0:
                return index
            depth -= 1
        index += 1
    return None


def _find_link_destination_end(value: str, start: int) -> int | None:
    """Find the closing parenthesis for one Markdown inline link."""

    depth = 0
    index = start
    while index < len(value):
        character = value[index]
        if character == "\\":
            index += 2
            continue
        if character == "(":
            depth += 1
        elif character == ")":
            if depth == 0:
                return index
            depth -= 1
        index += 1
    return None


def _link_destination_token(inner: str) -> tuple[int, int, str] | None:
    """Return the destination token offsets and value from Markdown link contents."""

    start = 0
    while start < len(inner) and inner[start].isspace():
        start += 1
    if start == len(inner):
        return None
    if inner[start] == "<":
        end = _find_unescaped(inner, start + 1, ">")
        if end is None:
            return None
        return start + 1, end, inner[start + 1 : end]
    end = start
    while end < len(inner) and not inner[end].isspace():
        end += 1
    return start, end, inner[start:end]


def _rewrite_markdown_links(value: str) -> str:
    """Apply the narrow docs-link compatibility policy to inline Markdown links.

    Parsing links here (instead of replacing URL-looking text globally) keeps code spans, prose,
    unrelated hosts, and source text outside an intended Markdown destination byte-for-byte intact.
    """

    output: list[str] = []
    cursor = 0
    index = 0
    while index < len(value):
        if value[index] == "`":
            run_end = index
            while run_end < len(value) and value[run_end] == "`":
                run_end += 1
            closing = value.find(value[index:run_end], run_end)
            if closing < 0:
                index = len(value)
            else:
                index = closing + run_end - index
            continue
        if value[index] != "[" or (index > 0 and value[index - 1] in {"\\", "!"}):
            index += 1
            continue
        label_end = _find_link_label_end(value, index + 1)
        if label_end is None or label_end + 1 >= len(value) or value[label_end + 1] != "(":
            index += 1
            continue
        destination_end = _find_link_destination_end(value, label_end + 2)
        if destination_end is None:
            index += 1
            continue
        inner = value[label_end + 2 : destination_end]
        token = _link_destination_token(inner)
        if token is None:
            index = destination_end + 1
            continue
        token_start, token_end, destination = token
        replacement_url = _rewritten_internal_destination(destination)
        if replacement_url is None:
            replacement_url = _community_workspace_destination(destination)
        remove_wrapper = _is_newsletter_destination(destination)
        replace_luma_label = destination == _LUMA_EVENTS_URL and value[index + 1 : label_end] == (
            "Luma"
        )
        if replace_luma_label:
            replacement_url = "/events"
        if replacement_url is None and not remove_wrapper and not replace_luma_label:
            index = destination_end + 1
            continue

        output.append(value[cursor:index])
        label = value[index + 1 : label_end]
        if remove_wrapper:
            output.append(label)
        else:
            rewritten_inner = inner
            if replacement_url is not None:
                is_angle_destination = inner[token_start - 1 : token_start] == "<"
                rewritten_destination = (
                    f"<{replacement_url}>" if is_angle_destination else replacement_url
                )
                if is_angle_destination:
                    rewritten_inner = (
                        inner[: token_start - 1] + rewritten_destination + inner[token_end + 1 :]
                    )
                else:
                    rewritten_inner = (
                        inner[:token_start] + rewritten_destination + inner[token_end:]
                    )
            if replace_luma_label:
                label = "our events page"
            output.append(f"[{label}]({rewritten_inner})")
        cursor = destination_end + 1
        index = cursor
    output.append(value[cursor:])
    return "".join(output)


def _prepare_markdown(raw: str) -> str:
    # Just the Docs uses these attribute-only lines for typography and button classes.  They are
    # presentation metadata rather than content, and allowing them through mistune would expose
    # literal ``{: ... }`` text.  Inline attributes are treated the same way.
    prepared = _KRAMDOWN_ATTRIBUTE_LINE.sub("", raw)
    prepared = _KRAMDOWN_INLINE_ATTRIBUTE.sub(lambda match: f"]({match.group('url')})", prepared)
    prepared = _LIQUID_RELATIVE_URL.sub(lambda match: _docs_url(match.group("path")), prepared)
    return _rewrite_markdown_links(prepared)


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


def _bounded_source_path(page: Mapping[str, Any]) -> str:
    """Return a content-free source identifier suitable for a bounded diagnostic."""

    source_path = page.get("source_path")
    if not isinstance(source_path, str) or not source_path:
        return "<unknown>"
    normalized = re.sub(r"[\x00-\x1f\x7f]", "?", source_path.replace("\\", "/"))
    return normalized[:160]


def _navigation_error(code: str, page: Mapping[str, Any]) -> ImproperlyConfigured:
    return ImproperlyConfigured(f"Docs navigation {code}: {_bounded_source_path(page)}")


def build_docs_navigation(pages: Iterable[Mapping[str, Any]]) -> DocsNavigationTree:
    """Validate and build the complete Docs tree from projected parent relationships.

    ``parent_path`` is the only ancestry input. A missing parent means a direct child of the
    documentation root; URL segments and titles are deliberately never used to guess a parent.
    Siblings use the source contract's existing ordering key, and ``documents`` is the resulting
    depth-first pre-order beneath the root for Previous/Next reading context.
    """

    records: list[dict[str, Any]] = []
    by_path: dict[str, dict[str, Any]] = {}
    source_paths: set[str] = set()
    for raw_page in pages:
        if not isinstance(raw_page, Mapping):
            raise ImproperlyConfigured("Docs navigation page must be an object: <unknown>")
        page = dict(raw_page)
        public_path = page.get("public_path")
        source_path = page.get("source_path")
        if (
            not isinstance(public_path, str)
            or not public_path.startswith(DOCS_ROOT_PATH)
            or not public_path.endswith("/")
            or "?" in public_path
            or "#" in public_path
        ):
            raise _navigation_error("noncanonical_public_path", page)
        if public_path in by_path:
            raise _navigation_error("duplicate_public_path", page)
        if not isinstance(source_path, str) or not source_path:
            raise _navigation_error("invalid_source_path", page)
        if source_path in source_paths:
            raise _navigation_error("duplicate_source_path", page)
        title = page.get("title")
        if not isinstance(title, str) or not title.strip():
            raise _navigation_error("missing_title", page)
        parent_path = page.get("parent_path")
        if parent_path is not None and not isinstance(parent_path, str):
            raise _navigation_error("invalid_parent", page)
        records.append(page)
        by_path[public_path] = page
        source_paths.add(source_path)

    root_page = by_path.get(DOCS_ROOT_PATH)
    if root_page is None:
        raise ImproperlyConfigured("Docs navigation root_missing: <unknown>")
    if root_page.get("parent_path") is not None:
        raise _navigation_error("root_parent", root_page)

    for page in records:
        if page is root_page:
            continue
        public_path = str(page["public_path"])
        parent_path = page.get("parent_path")
        if parent_path == public_path:
            raise _navigation_error("self_parent", page)
        if parent_path is not None and parent_path not in by_path:
            raise _navigation_error("orphan", page)

    # Validate every parent chain, including components that are not reachable from a top-level
    # page. This produces a stable source-path diagnostic before a partial tree can be returned.
    resolved: set[str] = {DOCS_ROOT_PATH}
    for page in records:
        public_path = str(page["public_path"])
        if public_path in resolved:
            continue
        chain: list[str] = []
        chain_positions: dict[str, int] = {}
        current = page
        while True:
            current_path = str(current["public_path"])
            if current_path in resolved:
                break
            if current_path in chain_positions:
                raise _navigation_error("parent_cycle", current)
            chain_positions[current_path] = len(chain)
            chain.append(current_path)
            parent_path = current.get("parent_path")
            if parent_path is None or parent_path == DOCS_ROOT_PATH:
                break
            parent = by_path.get(str(parent_path))
            if parent is None:
                raise _navigation_error("orphan", current)
            current = parent
        resolved.update(chain)

    children_by_parent: dict[str, list[dict[str, Any]]] = {path: [] for path in by_path}
    for page in records:
        if page is root_page:
            continue
        parent_path = page.get("parent_path")
        effective_parent = DOCS_ROOT_PATH if parent_path is None else str(parent_path)
        children_by_parent[effective_parent].append(page)
    for children in children_by_parent.values():
        children.sort(key=_nav_key)

    built_by_path: dict[str, DocsNavigationItem] = {}

    def build_item(page: Mapping[str, Any]) -> DocsNavigationItem:
        path = str(page["public_path"])
        item = DocsNavigationItem(
            page=MappingProxyType(dict(page)),
            children=tuple(build_item(child) for child in children_by_parent[path]),
        )
        built_by_path[path] = item
        return item

    root = build_item(root_page)
    preorder: list[DocsNavigationItem] = []

    def visit(item: DocsNavigationItem) -> None:
        preorder.append(item)
        for child in item.children:
            visit(child)

    visit(root)
    if len(preorder) != len(records):
        missing_path = next(path for path in by_path if path not in built_by_path)
        raise _navigation_error("unreachable", by_path[missing_path])
    ordered = tuple(preorder)
    return DocsNavigationTree(
        root=root,
        preorder=ordered,
        documents=ordered[1:],
        by_path=MappingProxyType(dict(built_by_path)),
    )


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
    # Navigation validation runs before the older metadata checks so hierarchy failures always
    # use the bounded, content-free source-path diagnostic and cannot fall through to partial data.
    build_docs_navigation(pages)
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
        value = page.get("grand_parent_path")
        if value is not None and value not in public_paths:
            raise ImproperlyConfigured(
                f"Docs projection grand_parent_path does not resolve: {value}"
            )


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


@lru_cache(maxsize=1)
def docs_navigation_tree() -> DocsNavigationTree:
    """Return the one validated tree for the active checked projection."""

    return build_docs_navigation(tuple(docs_projection()["pages"]))


def docs_children(parent_path: str | None) -> tuple[dict[str, Any], ...]:
    tree = docs_navigation_tree()
    effective_parent = DOCS_ROOT_PATH if parent_path is None else parent_path
    parent = tree.by_path.get(effective_parent)
    if parent is None:
        return ()
    return tuple(dict(child.page) for child in parent.children)


def docs_breadcrumbs(page: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    result: list[dict[str, str]] = [{"title": "Documentation", "public_path": DOCS_ROOT_PATH}]
    chain: list[dict[str, Any]] = []
    current = page
    by_path = docs_navigation_tree().by_path
    while current.get("parent_path"):
        parent_path = str(current["parent_path"])
        if parent_path == DOCS_ROOT_PATH:
            break
        parent_item = by_path.get(parent_path)
        if parent_item is None:
            break
        parent = dict(parent_item.page)
        chain.append(parent)
        current = parent
    for parent in reversed(chain):
        result.append({"title": str(parent["title"]), "public_path": str(parent["public_path"])})
    return tuple(result)


def docs_parent(page: Mapping[str, Any]) -> dict[str, Any]:
    """Return the explicit parent, using the Docs landing for top-level pages."""

    parent_path = page.get("parent_path")
    if parent_path is None or parent_path == DOCS_ROOT_PATH:
        return {"title": "Documentation", "public_path": DOCS_ROOT_PATH}
    parent = docs_navigation_tree().by_path.get(str(parent_path))
    if parent is None:  # The validated tree makes this defensive branch unreachable.
        raise _navigation_error("orphan", page)
    return dict(parent.page)


def docs_sequential_navigation(
    page: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return adjacent detail documents in deterministic depth-first pre-order."""

    documents = docs_navigation_tree().documents
    for index, item in enumerate(documents):
        if item.public_path == page.get("public_path"):
            previous = dict(documents[index - 1].page) if index else None
            following = dict(documents[index + 1].page) if index + 1 < len(documents) else None
            return previous, following
    return None, None


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
    "DOCS_SEARCH_URL",
    "DOCS_SOURCE_REVISION",
    "DocsNavigationItem",
    "DocsNavigationTree",
    "build_docs_navigation",
    "docs_breadcrumbs",
    "docs_children",
    "docs_asset_path",
    "docs_navigation",
    "docs_navigation_tree",
    "docs_page",
    "docs_parent",
    "docs_pages",
    "docs_projection",
    "docs_sequential_navigation",
    "docs_sibling_navigation",
    "render_docs_markdown",
]
