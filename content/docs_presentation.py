"""Presentation helpers for the source-backed public documentation.

The docs projection owns content, URLs, and hierarchy.  This module only derives
bounded navigation and visual groups from that source data so the templates do
not have to render the complete 105-page tree on every route.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .docs_projection import (
    DocsNavigationItem,
    DocsNavigationTree,
    docs_breadcrumbs,
    docs_pages,
    docs_sync_stamp,
    render_docs_markdown,
)

_PRIMARY_HEADING = re.compile(
    r'^\s*<h1 id="(?P<id>[^"]+)">(?P<label>.*?)</h1>\s*',
    re.DOTALL,
)
_FIRST_PARAGRAPH = re.compile(r"<p>(?P<text>.*?)</p>", re.DOTALL)
_WHITESPACE = re.compile(r"\s+")
_DEFAULT_META_DESCRIPTION = "DataTalks.Club documentation."
_META_DESCRIPTION_MAX_LENGTH = 160
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
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_TITLE_WORD = re.compile(r"[a-z0-9]+")


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


def docs_meta_description(document: Mapping[str, Any], rendered_body: str) -> str:
    """Return a page's meta description, derived from its own real content.

    A source ``description`` is used verbatim when the page has one. Most pages
    do not (88 of 106 at the time this was written), and publishing the same
    fallback sentence on all of them is a duplicate-content signal, not a
    per-page description. This derives one from the page's own first rendered
    paragraph -- presentation of the synced body, not hand-authored copy -- so
    every docs page keeps a real, page-specific summary.
    """

    existing = str(document.get("description") or "").strip()
    if existing:
        return existing
    match = _FIRST_PARAGRAPH.search(rendered_body)
    if match is None:
        return _DEFAULT_META_DESCRIPTION
    text = _WHITESPACE.sub(" ", html.unescape(_TAGS.sub("", match.group("text")))).strip()
    if not text:
        return _DEFAULT_META_DESCRIPTION
    if len(text) <= _META_DESCRIPTION_MAX_LENGTH:
        return text
    truncated = text[:_META_DESCRIPTION_MAX_LENGTH].rsplit(" ", 1)[0].rstrip(" ,;:—-")
    return f"{truncated}…" if truncated else text[:_META_DESCRIPTION_MAX_LENGTH]


def docs_meta_title(document: Mapping[str, Any]) -> str:
    """Build a docs page's ``<title>`` from its place in the tree, not its name alone.

    Six pages are titled "Prerequisites", six "Getting Started", six
    "Curriculum" and so on -- one per course family -- so the page's own title
    is not enough to make the ``<title>`` unique. Folding in the immediate
    parent from ``docs_breadcrumbs`` (e.g. "Prerequisites · Data Engineering
    Zoomcamp") disambiguates every one of them. Top-level pages (Documentation,
    Courses, General, Activities, each course's own index) have no ambiguity to
    resolve, so they keep the plain form.
    """

    title = str(document["title"])
    breadcrumbs = docs_breadcrumbs(document)
    if len(breadcrumbs) <= 1:
        return f"{title} — DataTalks.Club Documentation"
    parent_title = str(breadcrumbs[-1]["title"])
    return f"{title} · {parent_title} — DataTalks.Club Docs"


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
class DocsGuideEntry:
    """One guide as the hub draws it: its own page, what it holds, its drawing."""

    item: DocsNavigationItem
    page_count: int
    family_slug: str
    sections: tuple[DocsGuideEntry, ...]

    @property
    def title(self) -> str:
        return self.item.title

    @property
    def description(self) -> str:
        return self.item.description

    @property
    def public_path(self) -> str:
        return self.item.public_path

    @property
    def pages(self) -> tuple[DocsNavigationItem, ...]:
        return self.item.children


@dataclass(frozen=True, slots=True)
class DocsHub:
    """The hub's chapters, each derived from the shape of the source tree.

    The split between ``sectioned`` and ``platform`` is structural rather than a
    named path: a support guide whose children have children of their own is deep
    enough that listing only its sections would bury its pages, so it opens a
    chapter of its own; a flat one is a row with its pages beside it.  Zoomcamp
    Logistics is the only two-level guide today, and its leaves -- Certification,
    Joining a Cohort, Final Project -- are the most linked-to pages in the corpus,
    which is exactly why they no longer sit three clicks down.
    """

    courses: DocsNavigationItem | None
    families: tuple[DocsGuideEntry, ...]
    sectioned: tuple[DocsGuideEntry, ...]
    platform: tuple[DocsGuideEntry, ...]
    community: tuple[DocsGuideEntry, ...]


def docs_subtree_count(item: DocsNavigationItem) -> int:
    """Count the documents under one navigation item, at any depth."""

    return sum(1 + docs_subtree_count(child) for child in item.children)


def _title_key(title: str) -> str:
    """A lenient join key between two corpora that name the same course.

    The docs call it "Stock Market Analytics Zoomcamp" and the course catalogue
    "Stock Markets Analytics Zoomcamp".  Comparing the titles word by word, with a
    plural word folded onto its singular, joins the two without either side
    hardcoding the other's spelling -- and without a slug map that would have to be
    edited every time a course is added.
    """

    words = _TITLE_WORD.findall(title.casefold())
    return " ".join(word[:-1] if len(word) > 3 and word.endswith("s") else word for word in words)


def _illustration_slugs() -> dict[str, str]:
    """Map a course title to the family slug its drawing is filed under."""

    from core.home_content import course_catalog

    return {_title_key(course.title): course.family for course in course_catalog()}


def _guide_entry(item: DocsNavigationItem, slugs: Mapping[str, str]) -> DocsGuideEntry:
    return DocsGuideEntry(
        item=item,
        page_count=docs_subtree_count(item),
        family_slug=slugs.get(_title_key(item.title), ""),
        sections=tuple(
            DocsGuideEntry(
                item=child,
                page_count=docs_subtree_count(child),
                family_slug="",
                sections=(),
            )
            for child in item.children
        ),
    )


def _index_rows(area: DocsNavigationItem) -> tuple[DocsNavigationItem, ...]:
    """Draw an area as one row per child when its children hold pages of their own.

    Activities is six flat pages, so it is one row with those six beside it.
    General holds Community Guidelines (five pages) and Jobs (three), so drawing it
    as a single row would hide eight pages behind two titles; it becomes four rows
    instead, each with its own pages.
    """

    if any(child.children for child in area.children):
        return area.children
    return (area,)


def docs_hub(tree: DocsNavigationTree) -> DocsHub:
    """Group every documentation page into the chapters the hub draws."""

    families, support = docs_home_course_groups(tree)
    slugs = _illustration_slugs()
    return DocsHub(
        courses=tree.by_path.get("/docs/courses/"),
        families=tuple(_guide_entry(item, slugs) for item in families),
        sectioned=tuple(
            _guide_entry(item, slugs)
            for item in support
            if any(child.children for child in item.children)
        ),
        platform=tuple(
            _guide_entry(item, slugs)
            for item in support
            if not any(child.children for child in item.children)
        ),
        community=tuple(
            _guide_entry(row, slugs)
            for area in docs_home_areas(tree)
            for row in _index_rows(area)
        ),
    )


@dataclass(frozen=True, slots=True)
class DocsSearchResult:
    """One documentation page matching a reader's search terms.

    Six pages are called "Project" and six "Curriculum", and 88 of the 106 pages
    carry no description at all, so a result row that is only a title tells the
    reader nothing about which of the six they are looking at.  The trail names
    the guide the page sits in, and the snippet is the page's own description or
    the first sentence that actually contains the term, with the term marked.
    """

    title: str
    description: str
    public_path: str
    trail: str
    title_html: str
    snippet_html: str


@dataclass(frozen=True, slots=True)
class _DocsSearchDocument:
    """A search corpus entry: the displayed page plus the fields matches rank by."""

    title: str
    description: str
    public_path: str
    trail: str
    body_text: str
    title_key: str
    description_key: str
    haystack: str


@lru_cache(maxsize=4)
def _docs_search_corpus(stamp: tuple[int, str]) -> tuple[_DocsSearchDocument, ...]:
    """Build the title/description/body search corpus of exactly one synced state.

    Wiki search reads a checked ``wiki_search.json`` built ahead of time from the wiki
    projection.  Docs has no such build step yet, so this derives the same shape
    directly from the rendered bodies the detail pages already produce.  The cache key
    is the synced state the corpus was built from: a sync changes the stamp, and the
    next search rebuilds the corpus instead of serving stale matches for the life of
    the process.
    """

    corpus: list[_DocsSearchDocument] = []
    for page in docs_pages():
        title = str(page["title"])
        description = str(page.get("description") or "")
        rendered, _headings = render_docs_markdown(page)
        body_text = _collapse(html.unescape(_TAGS.sub(" ", rendered)))
        trail = " / ".join(str(level["title"]) for level in docs_breadcrumbs(page)[1:])
        corpus.append(
            _DocsSearchDocument(
                title=title,
                description=description,
                public_path=str(page["public_path"]),
                trail=trail,
                body_text=body_text,
                title_key=title.casefold(),
                description_key=description.casefold(),
                haystack=" ".join((title, description, body_text)).casefold(),
            )
        )
    return tuple(corpus)


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _mark_terms(text: str, terms: tuple[str, ...]) -> str:
    """Escape one line of source text and wrap each matched term in ``<mark>``.

    The marking happens on the plain text and the escaping on each fragment, so a
    term that looks like the middle of an entity ("amp", "lt") can never mark the
    escaping this function itself introduced.
    """

    if not text:
        return ""
    pattern = re.compile("|".join(re.escape(term) for term in terms), re.IGNORECASE)
    parts: list[str] = []
    cursor = 0
    for match in pattern.finditer(text):
        parts.append(html.escape(text[cursor : match.start()]))
        parts.append(f"<mark>{html.escape(match.group(0))}</mark>")
        cursor = match.end()
    parts.append(html.escape(text[cursor:]))
    return "".join(parts)


def _snippet(document: _DocsSearchDocument, terms: tuple[str, ...]) -> str:
    """The page's own description, or the first body sentence holding a term."""

    if document.description:
        return document.description
    for sentence in _SENTENCE.split(document.body_text):
        lowered = sentence.casefold()
        if any(term in lowered for term in terms):
            return _collapse(sentence)
    return ""


def docs_search_results(query: str) -> tuple[DocsSearchResult, ...]:
    """Return documentation pages matching every term, title matches first.

    Terms are ANDed and matched case-insensitively, as the wiki's own search does.
    Results used to come back in tree order and stopped at the first 100 found,
    which put the page literally titled *Certification* nineteenth for the query
    "certificate".  Ranking is where the match was found -- title, then
    description, then body -- and tree order breaks ties, so the whole corpus is
    ranked before the cap applies rather than the cap deciding what is ranked.
    """

    terms = tuple(query.casefold().split())
    if not terms:
        return ()
    stamp = docs_sync_stamp()
    if not stamp[0]:
        return ()
    ranked: list[tuple[int, int, DocsSearchResult]] = []
    for position, document in enumerate(_docs_search_corpus(stamp)):
        if not all(term in document.haystack for term in terms):
            continue
        if all(term in document.title_key for term in terms):
            rank = 0
        elif all(term in f"{document.title_key} {document.description_key}" for term in terms):
            rank = 1
        else:
            rank = 2
        ranked.append(
            (
                rank,
                position,
                DocsSearchResult(
                    title=document.title,
                    description=document.description,
                    public_path=document.public_path,
                    trail=document.trail,
                    title_html=_mark_terms(document.title, terms),
                    snippet_html=_mark_terms(_snippet(document, terms), terms),
                ),
            )
        )
    ranked.sort(key=lambda entry: (entry[0], entry[1]))
    return tuple(result for _rank, _position, result in ranked[:100])
