"""One article's frequently-asked-questions section, read from its own row.

Ten blog articles ended with a FAQ accordion on the legacy site, and the pairs
lived outside the article Markdown, so they were recovered once into a checked
capture file.  The pairs are now part of the article's own upstream frontmatter:
the content adapter validates them and carries them in the article document's
``adapter_metadata``, and this module reads them from there.

Three contracts are checked here.

The read contract: an article row that names a FAQ produces one, a row that does
not produces ``None``, and a row whose pairs cannot be rendered is refused
rather than half-rendered.

The composition contract: an article with a FAQ renders it after its body, and
an article without one renders no FAQ at all -- no heading of its own, no empty
region.

The page contract: every question and answer reaches the document with a unique,
linkable anchor, and the section publishes the FAQPage data the legacy accordion
published.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from django.test import SimpleTestCase, TestCase

from content.article_content import article_view, prose_sections
from content.article_faq import (
    FAQ_SECTION_ANCHOR,
    ArticleFaqError,
    article_faq,
    article_faq_answer_text,
    render_article_faq_answer,
)
from content.article_faq_format import faq_anchor_id
from content.public_views import _article_faq_structured_data
from test_support.published_content import PublishedPage, publish_documents

# A slug the reviewed catalogue does not publish, so this module's rows are the
# only ones its assertions can be reading.
ARTICLE_SLUG = "a-synthetic-article-with-a-faq"
ARTICLE_PATH = f"/blog/{ARTICLE_SLUG}.html"
PAIRS = (
    {
        "question": "Is the course free?",
        "answer": "Yes. Ask in [Slack](https://datatalks.club/slack.html#llm) if unsure.",
    },
    {
        "question": "When does the next cohort start?",
        "answer": "Cohorts start in **June**.",
    },
)
BODY_BLOCKS = (
    {"kind": "heading", "id": "about", "level": 2, "text": "About the course"},
    {"kind": "text", "text": "A short course body."},
)


def _publish_article(
    *,
    faq: Sequence[Mapping[str, str]] | None = PAIRS,
    slug: str = ARTICLE_SLUG,
) -> None:
    metadata: dict[str, Any] = {} if faq is None else {"faq": [dict(pair) for pair in faq]}
    publish_documents(
        [
            PublishedPage(
                exact_public_path=f"/blog/{slug}.html",
                title="LLM Zoomcamp",
                content_kind="article",
                slug=slug,
                rendered_html="<p>A short course body.</p>",
                adapter_metadata=metadata,
            )
        ]
    )


def _record(slug: str = ARTICLE_SLUG) -> dict[str, object]:
    """The projected article record the composition still reads its body from."""

    return {
        "slug": slug,
        "title": "LLM Zoomcamp",
        "public_path": f"/blog/{slug}.html",
        "published": "2026-01-15",
        "authors": [],
        "blocks": list(BODY_BLOCKS),
    }


class ArticleFaqReadTests(TestCase):
    def test_a_row_that_names_a_faq_renders_its_questions_in_order(self) -> None:
        _publish_article()

        faq = article_faq(ARTICLE_SLUG)

        assert faq is not None
        self.assertEqual(faq.slug, ARTICLE_SLUG)
        self.assertEqual(faq.heading_id, FAQ_SECTION_ANCHOR)
        self.assertEqual(
            [question.question for question in faq.questions],
            [pair["question"] for pair in PAIRS],
        )

    def test_a_row_without_a_faq_has_none(self) -> None:
        _publish_article(faq=None)

        self.assertIsNone(article_faq(ARTICLE_SLUG))

    def test_an_article_no_row_publishes_has_none(self) -> None:
        self.assertIsNone(article_faq("an-article-nothing-published"))

    def test_an_unpublished_row_is_not_read(self) -> None:
        publish_documents(
            [
                PublishedPage(
                    exact_public_path=ARTICLE_PATH,
                    title="LLM Zoomcamp",
                    content_kind="article",
                    slug=ARTICLE_SLUG,
                    is_published=False,
                    adapter_metadata={"faq": list(PAIRS)},
                )
            ]
        )

        self.assertIsNone(article_faq(ARTICLE_SLUG))

    def test_anchors_are_derived_from_the_question_and_are_unique(self) -> None:
        _publish_article()

        faq = article_faq(ARTICLE_SLUG)

        assert faq is not None
        anchors = [question.id for question in faq.questions]
        self.assertEqual(anchors, [faq_anchor_id(pair["question"]) for pair in PAIRS])
        self.assertEqual(len(set(anchors)), len(anchors))
        for anchor in anchors:
            self.assertTrue(anchor.startswith("faq-"))

    def test_two_questions_deriving_one_anchor_are_refused(self) -> None:
        """A colliding anchor would make one of the two deep links unreachable."""

        _publish_article(
            faq=(
                {"question": "Is it free?", "answer": "Yes."},
                {"question": "Is it free???", "answer": "Still yes."},
            )
        )

        with self.assertRaises(ArticleFaqError):
            article_faq(ARTICLE_SLUG)

    def test_a_pair_missing_its_question_or_answer_is_refused(self) -> None:
        for pairs in (
            ({"question": "  ", "answer": "Yes."},),
            ({"question": "Is it free?", "answer": ""},),
            ({"question": "Is it free?"},),
            (),
        ):
            with self.subTest(pairs=pairs):
                _publish_article(faq=pairs)
                with self.assertRaises(ArticleFaqError):
                    article_faq(ARTICLE_SLUG)


class ArticleFaqAnswerTests(SimpleTestCase):
    def test_an_answer_renders_from_markdown_and_keeps_its_emphasis(self) -> None:
        html = render_article_faq_answer("Cohorts start in **June**.")

        self.assertIn("<strong>June</strong>", html)

    def test_a_legacy_slack_address_becomes_this_site_s_slack_page(self) -> None:
        html = render_article_faq_answer("Ask in [Slack](https://datatalks.club/slack.html#llm).")

        self.assertIn('href="/slack#llm"', html)
        self.assertNotIn("slack.html", html)

    def test_answer_text_is_the_rendered_answer_without_its_markup(self) -> None:
        html = render_article_faq_answer("Cohorts start in **June**.")

        self.assertEqual(article_faq_answer_text(html), "Cohorts start in June.")


class ArticleFaqCompositionTests(TestCase):
    def test_an_article_with_a_faq_renders_it_after_the_whole_body(self) -> None:
        _publish_article()
        faq = article_faq(ARTICLE_SLUG)
        assert faq is not None

        view = article_view(_record(), {}, faq)

        self.assertEqual(view.sections, prose_sections(list(BODY_BLOCKS)))
        self.assertEqual(len(view.faq), len(PAIRS))

    def test_an_article_without_a_faq_renders_no_faq_region(self) -> None:
        view = article_view(_record(), {})

        self.assertEqual(view.faq, ())
        self.assertEqual(view.sections, prose_sections(list(BODY_BLOCKS)))


class ArticleFaqStructuredDataTests(TestCase):
    def test_the_section_publishes_one_faqpage_of_its_own_questions(self) -> None:
        _publish_article()
        faq = article_faq(ARTICLE_SLUG)
        assert faq is not None

        nodes = _article_faq_structured_data(_record(), faq)

        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        canonical = f"https://datatalks.club{ARTICLE_PATH}"
        self.assertEqual(node["@type"], "FAQPage")
        self.assertEqual(node["@id"], f"{canonical}#{FAQ_SECTION_ANCHOR}")
        self.assertEqual(
            [entry["name"] for entry in node["mainEntity"]],
            [pair["question"] for pair in PAIRS],
        )
        for entry, question in zip(node["mainEntity"], faq.questions, strict=True):
            self.assertEqual(entry["url"], f"{canonical}#{question.id}")
            self.assertEqual(entry["acceptedAnswer"]["text"], question.answer_text)

    def test_an_article_without_a_faq_publishes_no_faqpage(self) -> None:
        self.assertEqual(_article_faq_structured_data(_record(), None), ())
