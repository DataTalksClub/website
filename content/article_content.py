"""Editorial composition for the public blog article page.

The article page renders in the design 5a system (issue #179).  Every fact it
shows — title, subtitle, byline, author portraits, publication date, reading
time and the body itself — is read from the checked article record and from the
people records that article names.  A record that cannot supply a fact fails
loudly here instead of letting the page render a guess.

The body arrives as a flat list of projected blocks.  :func:`prose_sections`
turns that flat list into the shape long-form markup needs: a heading keeps the
fragment identifier the body already assigned it (those identifiers are linkable
anchors and must not move), consecutive list items become one real list, and any
other block keeps its text as a paragraph — so a block kind that the projection
grows later is rendered rather than silently dropped.

Deliberate omissions, because the record has no such field: an updated date, a
category or tag, a series, and any in-body link.  The body text arrives already
flattened to plain text, so a link written in the source is present as its label
without an address; the page shows the label rather than inventing a target.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from core.home_content import published_display, reading_minutes

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


@dataclass(frozen=True, slots=True)
class ProseSection:
    """One rendered piece of an article body.

    ``kind`` is what the template switches on: ``heading``, ``list`` or
    ``paragraph``.  Only the fields that kind uses are populated.
    """

    kind: str
    text: str = ""
    level: int = 0
    id: str = ""
    items: tuple[str, ...] = ()


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
    sections: tuple[ProseSection, ...] = field(default_factory=tuple)

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


def prose_sections(blocks: Any) -> tuple[ProseSection, ...]:
    """Return projected body blocks as the sections a long-form page renders.

    Headings keep their level and fragment identifier, a run of list items
    becomes one list, and every other block — including a kind this projection
    does not produce today — keeps its text as a paragraph.
    """

    sections: list[ProseSection] = []
    items: list[str] = []

    def flush() -> None:
        if items:
            sections.append(ProseSection(kind="list", items=tuple(items)))
            items.clear()

    for block in blocks or ():
        kind = str(block.get("kind") or "")
        text = str(block.get("text") or "").strip()
        if kind == "list_item":
            if text:
                items.append(text)
            continue
        flush()
        if not text:
            continue
        if kind == "heading":
            identifier = block.get("id")
            if not isinstance(identifier, str) or not identifier:
                raise ImproperlyConfigured("Public article heading must have a fragment id.")
            level = block.get("level")
            if not isinstance(level, int) or isinstance(level, bool):
                raise ImproperlyConfigured("Public article heading must have a level.")
            sections.append(
                ProseSection(
                    kind="heading",
                    text=text,
                    level=min(MAX_HEADING_LEVEL, max(MIN_HEADING_LEVEL, level)),
                    id=identifier,
                )
            )
            continue
        sections.append(ProseSection(kind="paragraph", text=text))
    flush()
    return tuple(sections)


def article_view(record: dict[str, Any], people: dict[str, dict[str, Any]]) -> ArticleView:
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
        sections=prose_sections(record.get("blocks")),
    )
