"""Presentation helpers for the source-backed public documentation.

The docs projection owns content, URLs, and hierarchy.  This module only derives
bounded navigation and visual groups from that source data so the templates do
not have to render the complete 105-page tree on every route.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .docs_projection import (
    DocsNavigationItem,
    DocsNavigationTree,
    docs_pages,
    render_docs_markdown,
)

_PRIMARY_HEADING = re.compile(
    r'^\s*<h1 id="(?P<id>[^"]+)">(?P<label>.*?)</h1>\s*',
    re.DOTALL,
)
_MODULES_HEADING = re.compile(
    r'<h2 id="modules">(?P<label>.*?)</h2>\s*',
    re.DOTALL,
)
_NEXT_SECONDARY_HEADING = re.compile(r"<h2\b", re.DOTALL)
_CURRICULUM_ITEM = re.compile(
    r"\s*<p>(?P<title>.*?)</p>\s*(?P<details><ul>.*?</ul>)\s*",
    re.DOTALL,
)
_CURRICULUM_LINK = re.compile(
    r'^\s*<a\s+href="(?P<href>[^"]+)"(?:\s+[^>]*)?>(?P<label>.*?)</a>\s*$',
    re.DOTALL,
)
_TAGS = re.compile(r"<[^>]+>")
_MODULE_NUMBER = re.compile(r"\bModule\s+(?P<number>[0-9]+)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class DocsCurriculumItem:
    """One source-authored module, workshop, or project in curriculum order."""

    marker: str
    title_text: str
    title_html: str
    details_html: str
    destination: str | None


@dataclass(frozen=True, slots=True)
class DocsCurriculum:
    """The source curriculum split around its ordered module sequence."""

    intro_html: str
    modules_heading_html: str
    items: tuple[DocsCurriculumItem, ...]
    remainder_html: str


def docs_body_without_primary_heading(rendered: str) -> tuple[str, str]:
    """Move the source H1 into the shared cream page header.

    The visible title and its source heading anchor are preserved.  Returning
    the rendered body separately lets the ordinary content-page shell own the
    page hierarchy without duplicating the title.
    """

    match = _PRIMARY_HEADING.match(rendered)
    if match is None:
        return "", rendered
    return match.group("id"), rendered[match.end() :]


def docs_curriculum(rendered_body: str) -> DocsCurriculum | None:
    """Derive an ordered learning flow from the source's Modules section.

    Curriculum documents use a stable Markdown shape: a Modules H2 followed by
    title paragraphs and their bullet lists.  If a future source page differs,
    this helper returns ``None`` and the template renders the complete prose
    unchanged rather than dropping or guessing content.
    """

    heading = _MODULES_HEADING.search(rendered_body)
    if heading is None:
        return None
    next_heading = _NEXT_SECONDARY_HEADING.search(rendered_body, heading.end())
    items_end = next_heading.start() if next_heading is not None else len(rendered_body)
    items_html = rendered_body[heading.end() : items_end]
    matches = tuple(_CURRICULUM_ITEM.finditer(items_html))
    if not matches:
        return None

    cursor = 0
    items: list[DocsCurriculumItem] = []
    for position, match in enumerate(matches, start=1):
        if items_html[cursor : match.start()].strip():
            return None
        title_html = match.group("title")
        destination = None
        link = _CURRICULUM_LINK.fullmatch(title_html)
        if link is not None:
            destination = html.unescape(link.group("href"))
            title_html = link.group("label")
        title_text = html.unescape(_TAGS.sub("", title_html)).strip()
        number = _MODULE_NUMBER.search(title_text)
        if number is not None:
            marker = number.group("number").zfill(2)
        elif "workshop" in title_text.casefold():
            marker = "Workshop"
        elif "project" in title_text.casefold():
            marker = "Project"
        else:
            marker = str(position).zfill(2)
        items.append(
            DocsCurriculumItem(
                marker=marker,
                title_text=title_text,
                title_html=title_html,
                details_html=match.group("details"),
                destination=destination,
            )
        )
        cursor = match.end()
    if items_html[cursor:].strip():
        return None

    return DocsCurriculum(
        intro_html=rendered_body[: heading.start()],
        modules_heading_html=heading.group("label"),
        items=tuple(items),
        remainder_html=rendered_body[items_end:],
    )


def docs_context_root(
    tree: DocsNavigationTree,
    public_path: str,
) -> DocsNavigationItem:
    """Return the smallest useful source-backed guide around one document.

    A document that already holds children is itself a guide hub, so its local
    nav is its own children.  A leaf's guide is its actual parent directory,
    so "In this guide" always lists the pages the reader is really among.
    Earlier this stopped at a hardcoded depth under ``/docs/courses/`` and
    ``/docs/general/``, which is right for the common two-level course case but
    wrong once a source folder nests deeper -- a Zoomcamp Logistics leaf such
    as Slack landed on Zoomcamp Logistics' section indexes (Communication,
    Course Work, ...) instead of Communication's own pages (Telegram, Email,
    ...).  Walking the real parent link instead of a fixed depth fixes every
    nesting depth, not only the two the old heuristic knew about.
    """

    current = tree.by_path[public_path]
    if current.children:
        return current
    parent_path = current.page.get("parent_path")
    if not parent_path or parent_path == tree.root.public_path:
        return tree.root
    return tree.by_path[str(parent_path)]


def docs_context_items(
    tree: DocsNavigationTree,
    public_path: str,
) -> tuple[DocsNavigationItem, ...]:
    """Return overview plus immediate pages for the local reader navigation."""

    root = docs_context_root(tree, public_path)
    return (root, *root.children)


def docs_local_sequence(
    tree: DocsNavigationTree,
    public_path: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return previous and next within the document's immediate source group."""

    current = tree.by_path[public_path]
    parent_path = current.page.get("parent_path")
    siblings = tree.root.children if not parent_path else tree.by_path[str(parent_path)].children
    index = siblings.index(current)
    previous = dict(siblings[index - 1].page) if index else None
    following = dict(siblings[index + 1].page) if index + 1 < len(siblings) else None
    return previous, following


def docs_home_course_groups(
    tree: DocsNavigationTree,
) -> tuple[tuple[DocsNavigationItem, ...], tuple[DocsNavigationItem, ...]]:
    """Split Zoomcamp families from shared course support guides."""

    courses = tree.by_path.get("/docs/courses/")
    if courses is None:
        return (), ()
    families = tuple(
        item
        for item in courses.children
        if item.title.casefold().endswith("zoomcamp") and item.title != "Zoomcamp Logistics"
    )
    support = tuple(item for item in courses.children if item not in families)
    return families, support


def docs_home_areas(tree: DocsNavigationTree) -> tuple[DocsNavigationItem, ...]:
    """Return the source General and Activities groups in navigation order."""

    wanted = {"/docs/general/", "/docs/activities/"}
    return tuple(item for item in tree.root.children if item.public_path in wanted)


@dataclass(frozen=True, slots=True)
class DocsSearchResult:
    """One documentation page matching a reader's search terms."""

    title: str
    description: str
    public_path: str


@dataclass(frozen=True, slots=True)
class _DocsSearchDocument:
    """A search corpus entry: the displayed result plus its match haystack."""

    result: DocsSearchResult
    haystack: str


@lru_cache(maxsize=1)
def _docs_search_corpus() -> tuple[_DocsSearchDocument, ...]:
    """Build an in-process, title/description/body search corpus once per process.

    Wiki search reads a checked ``wiki_search.json`` built ahead of time from the wiki
    projection.  Docs has no such build step yet (Pass 0 is presentation-only, no source
    or projection change), so this derives the same shape directly from the rendered
    bodies the detail pages already produce, and caches it for the life of the process
    rather than re-rendering 105 pages on every search request.
    """

    corpus: list[_DocsSearchDocument] = []
    for page in docs_pages():
        title = str(page["title"])
        description = str(page.get("description") or "")
        rendered, _headings = render_docs_markdown(page)
        body_text = html.unescape(_TAGS.sub(" ", rendered))
        haystack = " ".join((title, description, body_text)).casefold()
        corpus.append(
            _DocsSearchDocument(
                result=DocsSearchResult(
                    title=title,
                    description=description,
                    public_path=str(page["public_path"]),
                ),
                haystack=haystack,
            )
        )
    return tuple(corpus)


def docs_search_results(query: str) -> tuple[DocsSearchResult, ...]:
    """Return documentation pages whose title, description, or body match every term.

    Modeled on the wiki's ``_wiki_search_results``: terms are ANDed and matched
    case-insensitively, and results are capped so a broad term cannot return the
    whole corpus at once.
    """

    terms = query.casefold().split()
    if not terms:
        return ()
    results: list[DocsSearchResult] = []
    for document in _docs_search_corpus():
        if all(term in document.haystack for term in terms):
            results.append(document.result)
            if len(results) == 100:
                break
    return tuple(results)
