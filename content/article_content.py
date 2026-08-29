"""Editorial composition for the public blog article page.

The article page renders in the design 5a system (issue #179).  Every fact it
shows — title, subtitle, byline, author portraits, publication date, reading
time and the body itself — is read from the checked article record and from the
people records that article names.  A record that cannot supply a fact fails
loudly here instead of letting the page render a guess.

The body arrives as a flat list of projected blocks.  :func:`prose_sections`
turns that flat list into the shape long-form markup needs: a heading keeps the
fragment identifier the body already assigned it (those identifiers are linkable
anchors and must not move), a run of list items of the same kind becomes one real
ordered or unordered list, each richer kind is drawn from its own fields, and any
block kind this module does not know keeps its text as a paragraph — so a kind
the projection grows later is rendered rather than silently dropped.

Ten articles closed with a frequently-asked-questions section whose pairs were
never part of the article Markdown, so the projected body carries the heading and
nothing beneath it.  :mod:`content.article_faq` holds that recovered half, and
this module splits the body at the position the capture records so the section
lands where the article put it.  An article the capture does not name renders no
FAQ at all: no heading of its own, no empty region.

A body block carries the plain text it always carried and, where the source held
more than that plain text, the bounded source segment it came from.  This module
renders that segment the way :mod:`content.article_faq` renders a recovered
answer — Markdown, then the shared sanitizer — so an article's links keep their
addresses and its emphasis, inline code and literal markup survive without any
page ever writing unsanitized external HTML.  Illustrations, comparison tables,
code samples, quotations and rules arrive as their own typed blocks and are
drawn from their fields rather than from a rendered fragment, which is what lets
the page reserve an image's aspect box and give a wide table a reachable scroll
frame.

Deliberate omissions, because the record has no such field: an updated date, a
category or tag, and a series.  A heading is still projected as plain text, so
markup written inside a heading is shown as words; the identifier the heading
carries is a linkable anchor and stays exactly as projected.  Survey charts are
drawn by a script this site does not ship. Four charts used by the sponsorship
article have reviewed local SVG bridges; any chart without one keeps its title
and an explicit unavailable state rather than leaving a hole in the argument.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import mistune
from django.core.exceptions import ImproperlyConfigured

from core.home_content import published_display, reading_minutes

from .article_faq import ArticleFaq, FaqQuestion
from .services import sanitize_rendered_html

# The heading levels long-form markup can carry.  The body builder already
# clamps a source heading to level 2 or deeper; this is the other end of that
# range, so an `h7` can never reach the document.
MIN_HEADING_LEVEL = 2
MAX_HEADING_LEVEL = 6


@dataclass(frozen=True, slots=True)
class Author:
    """One named author, with the profile and portrait the people records hold."""

    name: str
    public_path: str
    image_path: str
    media_available: bool


_MARKDOWN = mistune.create_markdown(escape=False, plugins=("strikethrough", "table"))

# The elements that cannot legally sit inside a paragraph.  A fragment carrying
# any of them is drawn as a block instead of being unwrapped into one.
_BLOCK_ELEMENT = re.compile(
    r"<(?:p|div|figure|figcaption|table|thead|tbody|tr|th|td|ul|ol|li|pre|blockquote"
    r"|hr|h[1-6]|dl|dt|dd|details|summary)\b",
    re.IGNORECASE,
)


@lru_cache(maxsize=8192)
def render_body_markdown(markdown: str) -> tuple[str, bool]:
    """Return one source segment as sanitized markup, and whether it is a block.

    The segment is external content, so it goes through the same
    Markdown-then-shared-sanitizer path the recovered FAQ answers use; nothing
    here trusts the source.  Markdown wraps a run of prose in ``<p>``, which the
    page supplies itself, so a fragment that is exactly one paragraph is unwrapped
    and reported as inline.  A fragment that is anything else — a source that
    wrote its own block markup — is reported as a block so the page never puts a
    ``<div>`` inside a ``<p>``.
    """

    rendered = sanitize_rendered_html("article", str(_MARKDOWN(markdown))).strip()
    inner = rendered[3:-4]
    if (
        rendered.startswith("<p>")
        and rendered.endswith("</p>")
        and not _BLOCK_ELEMENT.search(inner)
    ):
        return inner.strip(), False
    return rendered, True


def _rendered(block: dict[str, Any]) -> tuple[str, bool]:
    markdown = block.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        return "", False
    return render_body_markdown(markdown)


@dataclass(frozen=True, slots=True)
class ProseSection:
    """One rendered piece of an article body.

    ``kind`` is what the template switches on: ``heading``, ``list``,
    ``paragraph``, ``quote``, ``code``, ``image``, ``table``, ``chart`` or
    ``separator``.  Only the fields that kind uses are populated.

    ``html`` is sanitized markup when the source carried more than plain text;
    where it is empty the page draws ``text`` as text.  ``block_level`` says the
    markup is already a block element and must not be wrapped in a paragraph.
    """

    kind: str
    text: str = ""
    html: str = ""
    block_level: bool = False
    level: int = 0
    id: str = ""
    ordered: bool = False
    items: tuple[ProseSection, ...] = ()
    # Illustrations.  `width`/`height` are the source file's own pixel size, so
    # the page can reserve the space before the picture arrives; a zero means the
    # build could not read a size and the page says so rather than guessing.
    src: str = ""
    alt: str = ""
    caption: str = ""
    title: str = ""
    width: int = 0
    height: int = 0
    language: str = ""
    label: str = ""
    head: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class ArticleView:
    """One article, prepared for the page that reads it."""

    title: str
    subtitle: str
    public_path: str
    published: str
    published_display: str
    reading_minutes: int
    authors: tuple[Author, ...]
    image_path: str
    media_available: bool
    # The body, split where the recovered FAQ belongs.  Without a FAQ the whole
    # body is `sections` and the other two are empty, so the page draws exactly
    # what it draws today.
    sections: tuple[ProseSection, ...] = field(default_factory=tuple)
    faq: tuple[FaqQuestion, ...] = field(default_factory=tuple)
    sections_after_faq: tuple[ProseSection, ...] = field(default_factory=tuple)

    @property
    def reading_time(self) -> str:
        """The reading estimate, written the way the homepage already writes it."""

        return f"{self.reading_minutes} min read"


def article_public_path(record: dict[str, Any]) -> str:
    """Return the checked canonical article path, ``.html`` suffix included.

    The suffix is part of the published address of every article and was
    restored once already; a record whose path stops matching its slug is a
    failure, never something a page quietly routes around.
    """

    slug = record.get("slug")
    public_path = record.get("public_path")
    if (
        not isinstance(slug, str)
        or not slug
        or not isinstance(public_path, str)
        or public_path != f"/blog/{slug}.html"
    ):
        raise ImproperlyConfigured("Public article canonical path is invalid.")
    return public_path


def _required_text(record: dict[str, Any], name: str) -> str:
    value = record.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ImproperlyConfigured(f"Public article {name} must be a non-empty string.")
    return value


def _authors(record: dict[str, Any], people: dict[str, dict[str, Any]]) -> tuple[Author, ...]:
    """Return the article's byline, joined to the people records it names.

    The article record carries a name and a profile path per author; the
    portrait lives on the person.  An author the people records do not hold
    still gets their name and their link — the page falls back to the system's
    stand-in disc rather than dropping the credit.
    """

    authors: list[Author] = []
    for profile in record.get("author_profiles", ()):
        name = profile.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ImproperlyConfigured("Public article author must have a name.")
        public_path = str(profile.get("public_path") or "")
        if public_path and not public_path.startswith("/"):
            raise ImproperlyConfigured("Public article author link must be a site path.")
        person = people.get(str(profile.get("key") or "")) or {}
        image_path = str(person.get("image_path") or "")
        authors.append(
            Author(
                name=name,
                public_path=public_path,
                image_path=image_path,
                media_available=bool(person.get("media_available")) and bool(image_path),
            )
        )
    return tuple(authors)


def _image_source(block: dict[str, Any]) -> str:
    """Return one illustration's address, or refuse it.

    The build only ever writes a site-relative path here and the shared sanitizer
    rejects anything else, but the page checks again rather than trusting a record
    to have been built by the code that is supposed to have built it.
    """

    source = str(block.get("src") or "")
    if (
        not source.startswith("/")
        or source.startswith("//")
        or any(character.isspace() or ord(character) < 0x20 for character in source)
    ):
        raise ImproperlyConfigured("Public article illustration address is invalid.")
    return source


def _dimension(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _cells(values: Any) -> tuple[str, ...]:
    return tuple(render_body_markdown(str(cell))[0] for cell in values or ())


def prose_sections(blocks: Any) -> tuple[ProseSection, ...]:
    """Return projected body blocks as the sections a long-form page renders.

    Headings keep their level and fragment identifier, a run of list items of the
    same kind becomes one list, an illustration/table/code sample/quotation keeps
    the fields it needs to be drawn properly, and every other block — including a
    kind this projection does not produce today — keeps its text as a paragraph.
    Nothing is dropped for being unrecognised.
    """

    sections: list[ProseSection] = []
    items: list[ProseSection] = []
    ordered = False

    def flush() -> None:
        nonlocal ordered
        if items:
            sections.append(ProseSection(kind="list", ordered=ordered, items=tuple(items)))
            items.clear()
        ordered = False

    for block in blocks or ():
        kind = str(block.get("kind") or "")
        text = str(block.get("text") or "").strip()
        html, block_level = _rendered(block)
        if kind == "list_item":
            item_ordered = bool(block.get("ordered"))
            if items and item_ordered != ordered:
                flush()
            if text or html:
                ordered = item_ordered
                items.append(
                    ProseSection(
                        kind="item", text=text, html=html, block_level=block_level, ordered=ordered
                    )
                )
            continue
        flush()
        if kind == "heading":
            identifier = block.get("id")
            if not isinstance(identifier, str) or not identifier:
                raise ImproperlyConfigured("Public article heading must have a fragment id.")
            level = block.get("level")
            if not isinstance(level, int) or isinstance(level, bool):
                raise ImproperlyConfigured("Public article heading must have a level.")
            if not text:
                continue
            sections.append(
                ProseSection(
                    kind="heading",
                    text=text,
                    level=min(MAX_HEADING_LEVEL, max(MIN_HEADING_LEVEL, level)),
                    id=identifier,
                )
            )
            continue
        if kind == "image":
            sections.append(
                ProseSection(
                    kind="image",
                    src=_image_source(block),
                    alt=str(block.get("alt") or ""),
                    caption=str(block.get("caption") or ""),
                    title=str(block.get("title") or ""),
                    width=_dimension(block.get("width")),
                    height=_dimension(block.get("height")),
                )
            )
            continue
        if kind == "table":
            head = _cells(block.get("head"))
            rows = tuple(_cells(row) for row in block.get("rows") or ())
            if not head and not rows:
                continue
            sections.append(
                ProseSection(
                    kind="table",
                    label=str(block.get("label") or "Table"),
                    head=head,
                    rows=rows,
                )
            )
            continue
        if kind == "code":
            if not text:
                continue
            sections.append(
                ProseSection(kind="code", text=text, language=str(block.get("language") or ""))
            )
            continue
        if kind == "separator":
            sections.append(ProseSection(kind="separator"))
            continue
        if kind == "chart":
            sections.append(
                ProseSection(
                    kind="chart",
                    text=text,
                    src=_image_source(block) if block.get("src") else "",
                    alt=str(block.get("alt") or ""),
                    caption=str(block.get("caption") or ""),
                    title=str(block.get("title") or ""),
                    width=_dimension(block.get("width")),
                    height=_dimension(block.get("height")),
                )
            )
            continue
        if not text and not html:
            continue
        sections.append(
            ProseSection(
                kind="quote" if kind == "quote" else "paragraph",
                text=text,
                html=html,
                block_level=block_level,
            )
        )
    flush()
    return tuple(sections)


def _split_body(
    blocks: Any, faq: ArticleFaq | None
) -> tuple[tuple[ProseSection, ...], tuple[ProseSection, ...]]:
    """Return the body above the recovered FAQ and the body below it.

    The capture records a block index, not a heading, because five of the ten
    articles put a sentence between their FAQ heading and the accordion, and six
    put a call to action or a closing note after it.  Splitting on the recorded
    index keeps every one of them in the order the article was written in.
    """

    body = list(blocks or ())
    if faq is None:
        return prose_sections(body), ()
    if not 0 < faq.block_index <= len(body):
        raise ImproperlyConfigured("Public article FAQ position is outside the body.")
    return prose_sections(body[: faq.block_index]), prose_sections(body[faq.block_index :])


def article_view(
    record: dict[str, Any],
    people: dict[str, dict[str, Any]],
    faq: ArticleFaq | None = None,
) -> ArticleView:
    """Return one checked article record as the value the article page renders."""

    # Articles are dated to the day, and the page shows a day.  The machine value
    # is therefore that same day: a `<time datetime>` carrying a clock time the
    # page does not display would claim a precision (and a timezone) it has not
    # shown the reader.
    published = _required_text(record, "published")[:10]
    try:
        published_text = published_display(published)
    except ValueError as error:
        raise ImproperlyConfigured("Public article publication date is invalid.") from error
    image_path = str(record.get("image_path") or "")
    if faq is not None and faq.slug != record.get("slug"):
        raise ImproperlyConfigured("Public article FAQ belongs to a different article.")
    sections, sections_after_faq = _split_body(record.get("blocks"), faq)
    return ArticleView(
        title=_required_text(record, "title"),
        subtitle=str(record.get("subtitle") or ""),
        public_path=article_public_path(record),
        published=published,
        published_display=published_text,
        reading_minutes=reading_minutes(record),
        authors=_authors(record, people),
        image_path=image_path,
        media_available=bool(record.get("media_available")) and bool(image_path),
        sections=sections,
        faq=faq.questions if faq is not None else (),
        sections_after_faq=sections_after_faq,
    )
