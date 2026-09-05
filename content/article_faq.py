"""One article's frequently-asked-questions section, read from its own row.

Ten blog articles closed with a FAQ accordion on the legacy site.  The pairs
lived in ``_data/faqs/<key>.yml`` and the body carried only an include, so they
were recovered once into a checked capture file and rendered from there.

They now live where the article lives.  The pairs are part of an article's
frontmatter ``faq:`` section upstream; the content adapter validates them into
``{id, question, answer}`` rows and carries them in the article document's
``adapter_metadata``, so the FAQ arrives with the article rather than through a
second pipeline that has to be kept in step with it.

Answers are stored as the source Markdown, which keeps them diffable against
upstream.  They are rendered here through the same Markdown-then-shared-
sanitizer path the course FAQ answers use, with one carried-over rewrite: the
legacy ``datatalks.club/slack.html`` address becomes this site's ``/slack``, so
a recovered answer does not send a reader back to a legacy alias.

An article whose row names no FAQ has none, and the page draws no FAQ section at
all -- never an empty heading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

import mistune
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.db.models import F

from .article_faq_format import faq_anchor_id
from .models import ContentDocument, ContentRelease
from .services import sanitize_rendered_html

#: The fragment the article page publishes its FAQ section under. The section is
#: one region of one page, so it needs one stable anchor rather than an
#: identifier recorded per article.
FAQ_SECTION_ANCHOR = "faq"

_ANCHOR = re.compile(r"^faq-[a-z0-9][a-z0-9-]*$")
_LEGACY_SLACK = re.compile(
    r"(?:https?://datatalks\.club)?/slack\.html(?P<fragment>#[^\s)\"]*)?",
)


class ArticleFaqError(ImproperlyConfigured):
    """An article row carries a FAQ that cannot be rendered."""


@dataclass(frozen=True, slots=True)
class FaqQuestion:
    """One question, prepared for the page that reads it."""

    id: str
    question: str
    answer_html: str
    answer_text: str


@dataclass(frozen=True, slots=True)
class ArticleFaq:
    """One article's FAQ section."""

    slug: str
    heading_id: str
    questions: tuple[FaqQuestion, ...]


def _rewrite_legacy_links(markdown: str) -> str:
    return _LEGACY_SLACK.sub(lambda match: "/slack" + (match.group("fragment") or ""), markdown)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def render_article_faq_answer(answer: str) -> str:
    """Render one stored Markdown answer to the sanitized HTML the page shows."""

    rendered = mistune.html(_rewrite_legacy_links(answer))
    return sanitize_rendered_html("article", rendered if isinstance(rendered, str) else "")


def article_faq_answer_text(answer_html: str) -> str:
    """The plain text of a rendered answer, for the page's structured data."""

    extractor = _TextExtractor()
    extractor.feed(answer_html)
    extractor.close()
    return " ".join("".join(extractor.parts).split())


def _questions(pairs: Any, *, slug: str) -> tuple[FaqQuestion, ...]:
    if not isinstance(pairs, list) or not pairs:
        raise ArticleFaqError(f"Article {slug} carries an empty FAQ.")
    prepared: list[FaqQuestion] = []
    seen: set[str] = set()
    for pair in pairs:
        if not isinstance(pair, dict):
            raise ArticleFaqError(f"Article {slug} carries a malformed FAQ pair.")
        question = pair.get("question")
        answer = pair.get("answer")
        if not isinstance(question, str) or not question.strip():
            raise ArticleFaqError(f"Article {slug} carries a FAQ pair without a question.")
        if not isinstance(answer, str) or not answer.strip():
            raise ArticleFaqError(f"Article {slug} carries a FAQ pair without an answer.")
        # The stored anchor is re-derived rather than trusted: it is a public
        # fragment, and the deriving rule is the one the adapter validated
        # against, so a row written by an older adapter still lands correctly.
        anchor = faq_anchor_id(question)
        if _ANCHOR.fullmatch(anchor) is None or anchor in seen:
            raise ArticleFaqError(f"Article {slug} carries an unusable FAQ anchor.")
        seen.add(anchor)
        answer_html = render_article_faq_answer(answer)
        if not answer_html.strip():
            raise ArticleFaqError(f"Article {slug} has a FAQ answer that rendered to nothing.")
        prepared.append(
            FaqQuestion(
                id=anchor,
                question=question,
                answer_html=answer_html,
                answer_text=article_faq_answer_text(answer_html),
            )
        )
    return tuple(prepared)


def article_faq(slug: str, *, using: str = "default") -> ArticleFaq | None:
    """Return one article's FAQ section, or ``None`` when it publishes none.

    Reads the same active-release row the article page itself resolves, so the
    FAQ can never belong to a different release of the article than the body
    above it.
    """

    try:
        metadata = (
            ContentDocument.objects.using(using)
            .filter(
                content_kind="article",
                stable_key=slug,
                is_published=True,
                release__status=ContentRelease.Status.ACTIVE,
                release__source__enabled=True,
                release_id=F("release__source__active_release_id"),
            )
            .values_list("adapter_metadata", flat=True)
            .first()
        )
    except DatabaseError:
        return None
    if not isinstance(metadata, dict):
        return None
    # An imported catalogue record is stored beside its position, so the record
    # itself is one level in; an adapter-written document is the record. Both are
    # article documents and either can carry the FAQ.
    held = metadata.get("record")
    record: dict[str, Any] = held if isinstance(held, dict) else metadata
    pairs = record.get("faq")
    if pairs is None:
        return None
    return ArticleFaq(
        slug=slug,
        heading_id=FAQ_SECTION_ANCHOR,
        questions=_questions(pairs, slug=slug),
    )
