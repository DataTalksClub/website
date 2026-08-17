"""The recovered article FAQ sections, read from one checked capture.

Ten blog articles closed with a frequently-asked-questions section on the legacy
site.  The pairs were never part of the article Markdown — the body carried only
``{% include faq-accordion.html faqs=site.data.faqs.<key> %}`` and the questions
lived in ``_data/faqs/<key>.yml`` in the legacy site repository — so the projected
article records inherited the heading and nothing beneath it.

``content/article_faq.json`` is that missing half, copied verbatim out of a
pinned public revision by ``scripts/build_article_faq.py``.  This module reads it
the way the rest of the public site reads a checked record: every fact comes from
the file, and a file that cannot supply one fails loudly here rather than letting
the page render a guess.  An article the capture does not name has no FAQ, and
the page shows no FAQ section at all — never an empty heading.

Answers are stored as the source Markdown, so the capture stays diffable against
the legacy commit.  They are rendered here through the same Markdown-then-shared-
sanitizer path the course FAQ answers use, with one carried-over rewrite: the
legacy ``datatalks.club/slack.html`` address becomes this site's ``/slack``, so a
recovered answer does not send a reader back to a legacy alias.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import mistune
from django.core.exceptions import ImproperlyConfigured

from .services import sanitize_rendered_html

ARTICLE_FAQ_PATH = Path(__file__).with_name("article_faq.json")
ARTICLE_FAQ_PUBLIC_PATH = "content/article_faq.json"
PROJECTION_ARTICLES_PATH = Path(__file__).with_name("public_projection") / "articles.json"

SCHEMA_VERSION = 1
LEGACY_FAQ_REPOSITORY = "DataTalksClub/datatalksclub.github.io"
LEGACY_FAQ_REVISION = "ee43d3fa0929faf691178d79f19528e6f15a83e5"
LEGACY_FAQ_DIRECTORY = "_data/faqs"
CONTENT_REPOSITORY = "DataTalksClub/content"

# The recovery is complete and closed: ten articles carried a FAQ accordion on the
# legacy site and they carry the same 159 pairs here.  A capture that grows or
# shrinks is a review event, not something the page absorbs quietly.
EXPECTED_ARTICLE_COUNT = 10
EXPECTED_QUESTION_COUNT = 159

MAX_CAPTURE_BYTES = 1_000_000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ANCHOR = re.compile(r"^faq-[a-z0-9][a-z0-9-]*$")
_ANCHOR_MAX_CHARACTERS = 80
_ARTICLE_KEYS = frozenset(
    {
        "slug",
        "public_path",
        "heading_id",
        "block_index",
        "blocks_sha256",
        "article_source_path",
        "article_source_sha256",
        "source_path",
        "source_sha256",
        "questions",
    }
)
_CAPTURE_KEYS = frozenset(
    {
        "schema_version",
        "source",
        "article_source",
        "projection",
        "counts",
        "articles",
        "content_sha256",
    }
)

# The legacy alias the recovered answers link to twenty-three times.  Rewriting it
# is the same decision `content.faq_data.render_faq_answer` already made for the
# course FAQ answers, and for the same reason.
_LEGACY_SLACK_LINK = re.compile(
    r"(?:https?://datatalks\.club)?/slack\.html(?P<fragment>#[^\s)\"]*)?"
)

_MARKDOWN = mistune.create_markdown(escape=False, plugins=("strikethrough", "table"))


class ArticleFaqError(ImproperlyConfigured):
    """The checked article FAQ capture is missing, invalid, or out of date."""


@dataclass(frozen=True, slots=True)
class FaqQuestion:
    """One recovered question, prepared for the page that reads it."""

    id: str
    question: str
    answer_html: str
    answer_text: str


@dataclass(frozen=True, slots=True)
class ArticleFaq:
    """One article's recovered FAQ section and where its body wants it."""

    slug: str
    heading_id: str
    block_index: int
    questions: tuple[FaqQuestion, ...]


def canonical_sha256(payload: Any) -> str:
    """Digest a value the way every other checked artifact in this app does."""

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def question_anchor_id(question: str) -> str:
    """Return the stable, linkable anchor one recovered question keeps.

    The legacy accordion gave its questions no identifiers at all, so nothing is
    being moved here.  The anchor is derived from the question the way the
    projected article headings derive theirs, and carries a ``faq-`` prefix so it
    can never collide with a heading in the same document.
    """

    slug = re.sub(r"[^a-z0-9]+", "-", question.casefold()).strip("-")
    if len(slug) > _ANCHOR_MAX_CHARACTERS:
        slug = slug[:_ANCHOR_MAX_CHARACTERS].rsplit("-", 1)[0]
    return f"faq-{slug or 'question'}"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ArticleFaqError(message)


def _digest(value: Any, message: str) -> str:
    _require(isinstance(value, str) and bool(_SHA256.fullmatch(value)), message)
    return str(value)


def _text(value: Any, message: str, *, maximum: int) -> str:
    _require(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum, message)
    return str(value)


def validate_article_faq(capture: Any) -> dict[str, Any]:
    """Check one capture completely, or refuse it.

    Everything the page later relies on is proven here: the schema version, the
    pinned sources, the counts, the per-article binding to the checked projection,
    and the shape of every recovered pair.
    """

    _require(isinstance(capture, dict), "Article FAQ capture must be an object.")
    _require(set(capture) == _CAPTURE_KEYS, "Article FAQ capture keys are unexpected.")
    _require(capture["schema_version"] == SCHEMA_VERSION, "Article FAQ schema version mismatch.")
    _require(
        capture["source"]
        == {
            "repository": LEGACY_FAQ_REPOSITORY,
            "revision": LEGACY_FAQ_REVISION,
            "directory": LEGACY_FAQ_DIRECTORY,
        },
        "Article FAQ legacy source is not the pinned revision.",
    )
    _require(
        isinstance(capture["article_source"], dict)
        and capture["article_source"].get("repository") == CONTENT_REPOSITORY,
        "Article FAQ article source is not the accepted content repository.",
    )
    _require(
        capture["content_sha256"]
        == canonical_sha256(
            {key: value for key, value in capture.items() if key != "content_sha256"}
        ),
        "Article FAQ capture digest mismatch.",
    )

    articles = capture["articles"]
    _require(isinstance(articles, list), "Article FAQ capture must list articles.")
    _require(
        capture["counts"]
        == {
            "articles": EXPECTED_ARTICLE_COUNT,
            "questions": EXPECTED_QUESTION_COUNT,
        },
        "Article FAQ recovery counts do not match the accepted inventory.",
    )
    _require(len(articles) == EXPECTED_ARTICLE_COUNT, "Article FAQ article count mismatch.")

    slugs: set[str] = set()
    questions_seen = 0
    for article in articles:
        _require(isinstance(article, dict), "Article FAQ record must be an object.")
        _require(set(article) == _ARTICLE_KEYS, "Article FAQ record keys are unexpected.")
        slug = article["slug"]
        _require(
            isinstance(slug, str) and bool(_SLUG.fullmatch(slug)) and slug not in slugs,
            "Article FAQ slug is invalid or repeated.",
        )
        slugs.add(slug)
        _require(
            article["public_path"] == f"/blog/{slug}.html",
            "Article FAQ public path does not match its slug.",
        )
        _text(article["heading_id"], "Article FAQ heading id is invalid.", maximum=200)
        index = article["block_index"]
        _require(
            isinstance(index, int) and not isinstance(index, bool) and index > 0,
            "Article FAQ block index is invalid.",
        )
        _digest(article["blocks_sha256"], "Article FAQ block digest is invalid.")
        _digest(article["source_sha256"], "Article FAQ source digest is invalid.")
        _digest(article["article_source_sha256"], "Article FAQ article digest is invalid.")
        _require(
            article["source_path"] == f"{LEGACY_FAQ_DIRECTORY}/{Path(article['source_path']).name}",
            "Article FAQ source path is outside the pinned data directory.",
        )
        _require(
            str(article["article_source_path"]).startswith("articles/"),
            "Article FAQ article source path is outside the pinned articles directory.",
        )

        recovered = article["questions"]
        _require(
            isinstance(recovered, list) and bool(recovered),
            "Article FAQ record must carry at least one question.",
        )
        anchors: set[str] = set()
        for question in recovered:
            _require(
                isinstance(question, dict) and set(question) == {"id", "question", "answer"},
                "Article FAQ question keys are unexpected.",
            )
            anchor = question["id"]
            _require(
                isinstance(anchor, str)
                and bool(_ANCHOR.fullmatch(anchor))
                and len(anchor) <= _ANCHOR_MAX_CHARACTERS + 4
                and anchor not in anchors,
                "Article FAQ anchor is invalid or repeated.",
            )
            anchors.add(anchor)
            text = _text(question["question"], "Article FAQ question text is invalid.", maximum=500)
            _require(
                anchor == question_anchor_id(text),
                "Article FAQ anchor is not derived from its question.",
            )
            _text(question["answer"], "Article FAQ answer text is invalid.", maximum=5_000)
        questions_seen += len(recovered)
    _require(questions_seen == EXPECTED_QUESTION_COUNT, "Article FAQ question count mismatch.")
    return capture


def _rewrite_legacy_links(markdown: str) -> str:
    return _LEGACY_SLACK_LINK.sub(
        lambda match: "/slack" + (match.group("fragment") or ""), markdown
    )


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def render_article_faq_answer(answer: str) -> str:
    """Return one recovered Markdown answer as sanitized page markup."""

    rendered = str(_MARKDOWN(_rewrite_legacy_links(answer)))
    return sanitize_rendered_html("article", rendered)


def article_faq_answer_text(answer_html: str) -> str:
    """Return the plain text of a rendered answer, for structured data.

    The runs are joined with nothing between them rather than with a space:
    Markdown puts a newline between block elements and that newline is itself a
    run, so paragraphs still separate while a sentence that ends in a link keeps
    its full stop against the last word instead of ``Slack .``.
    """

    extractor = _TextExtractor()
    extractor.feed(answer_html)
    return " ".join("".join(extractor.parts).split())


def _load_capture() -> dict[str, Any]:
    try:
        raw = ARTICLE_FAQ_PATH.read_bytes()
    except OSError as error:
        raise ArticleFaqError("Article FAQ capture is unreadable.") from error
    _require(len(raw) <= MAX_CAPTURE_BYTES, "Article FAQ capture is too large.")
    try:
        capture = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArticleFaqError("Article FAQ capture is not valid JSON.") from error
    return validate_article_faq(capture)


@lru_cache(maxsize=1)
def article_faq_by_slug() -> dict[str, ArticleFaq]:
    """Return every recovered article FAQ, bound to the checked projection.

    The capture records where each section belongs inside a specific projected
    body.  If that body has moved on, the position and the heading it answers to
    are no longer known, so the load fails here instead of rendering a section in
    the wrong place.
    """

    capture = _load_capture()
    try:
        records = json.loads(PROJECTION_ARTICLES_PATH.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArticleFaqError("Article FAQ cannot read the checked projection.") from error
    blocks_by_slug = {str(record["slug"]): record["blocks"] for record in records}
    _require(
        capture["projection"]["article_count"] == len(records),
        "Article FAQ projection article count mismatch.",
    )

    prepared: dict[str, ArticleFaq] = {}
    for article in capture["articles"]:
        slug = article["slug"]
        if slug not in blocks_by_slug:
            raise ArticleFaqError("Article FAQ names an article the projection does not hold.")
        blocks = blocks_by_slug[slug]
        _require(
            canonical_sha256(blocks) == article["blocks_sha256"],
            "Article FAQ is bound to an article body that has changed.",
        )
        index = article["block_index"]
        _require(index <= len(blocks), "Article FAQ block index is past the end of the body.")
        heading_ids = {
            str(block.get("id"))
            for block in blocks[:index]
            if block.get("kind") == "heading" and block.get("id")
        }
        _require(
            article["heading_id"] in heading_ids,
            "Article FAQ heading is not in the body above its position.",
        )
        questions = []
        for question in article["questions"]:
            answer_html = render_article_faq_answer(question["answer"])
            _require(bool(answer_html.strip()), "Article FAQ answer rendered to nothing.")
            questions.append(
                FaqQuestion(
                    id=question["id"],
                    question=question["question"],
                    answer_html=answer_html,
                    answer_text=article_faq_answer_text(answer_html),
                )
            )
        prepared[slug] = ArticleFaq(
            slug=slug,
            heading_id=article["heading_id"],
            block_index=index,
            questions=tuple(questions),
        )
    return prepared


def article_faq(slug: str) -> ArticleFaq | None:
    """Return one article's recovered FAQ, or ``None`` when it never had one."""

    return article_faq_by_slug().get(slug)
