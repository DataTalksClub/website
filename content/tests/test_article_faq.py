"""The recovered article FAQ sections (issue: legacy article FAQ recovery).

Ten blog articles ended with a frequently-asked-questions section on the legacy
site.  The pairs lived in the legacy site repository's ``_data/faqs`` directory
rather than in the article Markdown, so the projected bodies carry the heading
and nothing beneath it and the rebuilt page rendered an empty region.

Three contracts are checked here.

The recovery contract: the capture is complete, closed, internally consistent,
and bound to the exact projected bodies it records positions inside.  Nothing is
invented — a capture whose digests, counts, or shapes drift is refused rather
than rendered.

The composition contract: an article with a recovered FAQ renders it where its
body put it, and an article without one renders no FAQ at all — no heading of its
own, no empty region.

The page contract: every recovered question and answer reaches the document with
a unique, linkable anchor, and the section publishes the FAQPage data the legacy
accordion published.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase
from django.utils.html import escape

from content.article_content import article_view, prose_sections
from content.article_faq import (
    ARTICLE_FAQ_PATH,
    LEGACY_FAQ_DIRECTORY,
    LEGACY_FAQ_REPOSITORY,
    LEGACY_FAQ_REVISION,
    ArticleFaqError,
    article_faq,
    article_faq_answer_text,
    article_faq_by_slug,
    canonical_sha256,
    question_anchor_id,
    render_article_faq_answer,
    validate_article_faq,
)
from content.public_data import public_projection

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
# The ten articles the legacy site gave a FAQ accordion, and how many pairs each
# one published.  Written out rather than derived, so a capture that quietly
# gains, loses, or re-keys a section fails here by name.
RECOVERED_SECTIONS = {
    "ai-dev-tools-zoomcamp": 24,
    "data-engineering-zoomcamp": 17,
    "free-machine-learning-courses": 8,
    "guide-to-free-online-courses-at-datatalks-club": 9,
    "llm-zoomcamp": 26,
    "machine-learning-zoomcamp": 20,
    "mlops-zoomcamp": 23,
    "open-source-free-ai-agent-evaluation-tools": 8,
    "slack-communities": 15,
    "sponsor-datatalks-club": 9,
}


def _capture() -> dict:
    return json.loads(ARTICLE_FAQ_PATH.read_text(encoding="utf-8"))


def _article_without_a_faq() -> dict:
    return next(
        record
        for record in public_projection()["articles"]
        if record["slug"] not in RECOVERED_SECTIONS
    )


class ArticleFaqRecoveryTests(SimpleTestCase):
    def test_the_capture_names_its_pinned_public_source(self) -> None:
        """Every recovered word can be diffed against a public commit."""

        capture = _capture()

        self.assertEqual(
            capture["source"],
            {
                "repository": LEGACY_FAQ_REPOSITORY,
                "revision": LEGACY_FAQ_REVISION,
                "directory": LEGACY_FAQ_DIRECTORY,
            },
        )
        self.assertEqual(capture["article_source"]["repository"], "DataTalksClub/content")
        for article in capture["articles"]:
            self.assertEqual(
                article["source_path"],
                f"{LEGACY_FAQ_DIRECTORY}/{Path(article['source_path']).name}",
            )
            self.assertRegex(article["source_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(article["article_source_sha256"], r"^[0-9a-f]{64}$")

    def test_a_tampered_capture_is_refused_rather_than_rendered(self) -> None:
        capture = _capture()

        def tamper(**changes: object) -> dict:
            edited = {**capture, **changes}
            edited["content_sha256"] = canonical_sha256(
                {key: value for key, value in edited.items() if key != "content_sha256"}
            )
            return edited

        cases = {
            "digest": {**capture, "content_sha256": "0" * 64},
            "schema": tamper(schema_version=2),
            "source": tamper(source={**capture["source"], "revision": "0" * 40}),
            "counts": tamper(counts={"articles": 9, "questions": 100}),
            "articles": tamper(articles=capture["articles"][:-1]),
        }
        for name, edited in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(ArticleFaqError):
                    validate_article_faq(edited)

        edited_question = json.loads(json.dumps(capture))
        edited_question["articles"][0]["questions"][0]["question"] = "A question nobody asked."
        edited_question["content_sha256"] = canonical_sha256(
            {key: value for key, value in edited_question.items() if key != "content_sha256"}
        )
        # A rewritten question no longer matches the anchor derived from it, so a
        # hand-edited pair cannot pass as recovered content.
        with self.assertRaisesRegex(ImproperlyConfigured, "anchor is not derived"):
            validate_article_faq(edited_question)

    def test_an_anchor_is_derived_from_its_question_and_cannot_collide(self) -> None:
        self.assertEqual(question_anchor_id("Who should sponsor?"), "faq-who-should-sponsor")
        # Always prefixed, so a derived anchor can never take a body heading's id.
        self.assertTrue(question_anchor_id("Frequently asked questions").startswith("faq-"))
        # Bounded, so a long question cannot publish an unusable identifier.
        self.assertLessEqual(len(question_anchor_id("word " * 100)), 84)
        self.assertEqual(question_anchor_id("???"), "faq-question")

    def test_a_recovered_answer_renders_as_reviewed_markup(self) -> None:
        rendered = render_article_faq_answer(
            "Join us on [Slack](https://datatalks.club/slack.html#join).\n\n"
            "<script>alert(1)</script>Second paragraph."
        )

        self.assertIn('href="/slack#join"', rendered)
        self.assertNotIn("slack.html", rendered)
        self.assertNotIn("<script", rendered)
        self.assertIn("<p>", rendered)
        self.assertEqual(
            article_faq_answer_text(rendered),
            "Join us on Slack. alert(1)Second paragraph.",
        )

    def test_every_recovered_answer_survives_rendering(self) -> None:
        for slug, faq in sorted(article_faq_by_slug().items()):
            with self.subTest(slug=slug):
                for question in faq.questions:
                    self.assertTrue(question.answer_html.strip())
                    self.assertTrue(question.answer_text.strip())
                    self.assertNotIn("<script", question.answer_html)
                    # The legacy alias never reaches a reader from here.
                    self.assertNotIn("slack.html", question.answer_html)


class ArticleFaqCompositionTests(SimpleTestCase):
    def test_the_recovery_is_bound_to_the_bodies_it_positions_itself_in(self) -> None:
        projection = public_projection()
        blocks_by_slug = {record["slug"]: record["blocks"] for record in projection["articles"]}

        for slug, faq in sorted(article_faq_by_slug().items()):
            with self.subTest(slug=slug):
                blocks = blocks_by_slug[slug]
                self.assertLessEqual(faq.block_index, len(blocks))
                headings = [
                    block["id"] for block in blocks[: faq.block_index] if block["kind"] == "heading"
                ]
                # The section lands under a heading the body already carries, and
                # under the last one before its position.
                self.assertEqual(faq.heading_id, headings[-1])

    def test_a_body_with_a_faq_is_split_without_losing_a_block(self) -> None:
        projection = public_projection()
        people = projection["people_by_slug"]

        for slug, expected in sorted(RECOVERED_SECTIONS.items()):
            record = projection["articles_by_slug"][slug]
            faq = article_faq(slug)
            # A slug in RECOVERED_SECTIONS without a recovered FAQ is a
            # fixture failure, not a case to skip past.
            assert faq is not None
            with self.subTest(slug=slug):
                view = article_view(record, people, faq)

                self.assertEqual(len(view.faq), expected)
                self.assertEqual(
                    view.sections + view.sections_after_faq,
                    prose_sections(record["blocks"]),
                )
                # The FAQ heading is the last heading above the section, never
                # left stranded below it.
                headings = [s.id for s in view.sections if s.kind == "heading"]
                self.assertEqual(headings[-1], faq.heading_id)
                self.assertNotIn(
                    faq.heading_id, [s.id for s in view.sections_after_faq if s.kind == "heading"]
                )

    def test_an_article_without_a_recovered_faq_composes_exactly_as_before(self) -> None:
        record = _article_without_a_faq()
        people = public_projection()["people_by_slug"]

        view = article_view(record, people)

        self.assertIsNone(article_faq(record["slug"]))
        self.assertEqual(view.faq, ())
        self.assertEqual(view.sections_after_faq, ())
        self.assertEqual(view.sections, prose_sections(record["blocks"]))

    def test_a_faq_that_does_not_belong_to_the_article_fails_loudly(self) -> None:
        projection = public_projection()
        people = projection["people_by_slug"]
        record = projection["articles_by_slug"]["sponsor-datatalks-club"]
        other = article_faq("llm-zoomcamp")

        with self.assertRaisesRegex(ImproperlyConfigured, "belongs to a different article"):
            article_view(record, people, other)
        with self.assertRaisesRegex(ImproperlyConfigured, "outside the body"):
            article_view(
                {**record, "blocks": record["blocks"][:2]}, people, article_faq(record["slug"])
            )


class ArticleFaqPageTests(TestCase):
    def test_every_recovered_question_and_answer_reaches_the_page(self) -> None:
        for slug in sorted(RECOVERED_SECTIONS):
            faq = article_faq(slug)
            assert faq is not None
            with self.subTest(slug=slug):
                body = self.client.get(f"/blog/{slug}.html").content.decode()

                for question in faq.questions:
                    self.assertIn(
                        f'<h3 class="faq-question-title">{escape(question.question)}</h3>', body
                    )
                    self.assertIn(f'id="{question.id}"', body)
                    self.assertIn(f'href="#{question.id}"', body)
                # No source markup escapes into the page as literal text.
                self.assertNotIn("faq-accordion", body)
                self.assertNotIn("{% include", body)

    def test_an_anchor_is_unique_in_the_document_and_answers_its_own_link(self) -> None:
        for slug in sorted(RECOVERED_SECTIONS):
            with self.subTest(slug=slug):
                body = self.client.get(f"/blog/{slug}.html").content.decode()

                identifiers = re.findall(r'\sid="([^"]+)"', body)
                self.assertEqual(len(identifiers), len(set(identifiers)))
                anchors = re.findall(r'<a class="faq-permalink" href="#([^"]+)"', body)
                self.assertEqual(len(anchors), RECOVERED_SECTIONS[slug])
                self.assertTrue(set(anchors).issubset(set(identifiers)))

    def test_the_section_is_drawn_with_the_faq_pages_own_marks(self) -> None:
        """One question-and-answer vocabulary across the site, not three."""

        partial = (REPOSITORY_ROOT / "templates/public/_article_faq.html").read_text(
            encoding="utf-8"
        )
        faq_page = (REPOSITORY_ROOT / "templates/review/faq_detail.html").read_text(
            encoding="utf-8"
        )

        # The shared vocabulary is the folded question the design system owns:
        # both surfaces draw <details class="faq-fold">, both keep the question a
        # real heading so the document outline still holds, and both link to
        # themselves.  The marks changed when the questions learned to fold; what
        # the test pins is that they changed together.
        for mark in (
            'class="faq-fold"',
            'class="faq-fold-summary"',
            'class="faq-question-title"',
            'class="faq-fold-marker"',
            'class="faq-permalink"',
            'class="prose faq-answer"',
        ):
            self.assertIn(mark, partial)
            self.assertIn(mark, faq_page)

    def test_the_section_stays_inside_the_reading_band(self) -> None:
        body = self.client.get("/blog/sponsor-datatalks-club.html").content.decode()
        band = body[body.index('class="band band-lavender article-read"') :]

        self.assertIn('class="article-faq faq-panel', band)
        # The band is the last one on the page, so the section cannot have
        # escaped below it.
        self.assertNotIn("band band-", band[band.index('class="article-faq faq-panel') :])

    def test_prose_that_follows_the_section_keeps_its_place_below_it(self) -> None:
        body = self.client.get("/blog/mlops-zoomcamp.html").content.decode()

        self.assertLess(
            body.index('<h2 id="frequently-asked-questions">'),
            body.index('class="article-faq faq-panel'),
        )
        self.assertLess(
            body.index('class="article-faq faq-panel'),
            body.index('class="prose prose-reading article-body article-body-tail"'),
        )

    def test_an_article_without_a_recovered_faq_draws_no_section(self) -> None:
        record = _article_without_a_faq()

        body = self.client.get(record["public_path"]).content.decode()

        self.assertNotIn('class="row-list article-faq"', body)
        self.assertNotIn('class="faq-question"', body)
        self.assertNotIn("FAQPage", body)

    def test_the_section_publishes_the_faq_data_the_legacy_accordion_published(self) -> None:
        for slug in sorted(RECOVERED_SECTIONS):
            faq = article_faq(slug)
            assert faq is not None
            with self.subTest(slug=slug):
                body = self.client.get(f"/blog/{slug}.html").content.decode()
                structured = re.search(
                    r'<script type="application/ld\+json">\s*(.*?)\s*</script>', body, re.S
                )
                assert structured is not None
                graph = json.loads(structured.group(1).replace("\\u003c", "<"))["@graph"]
                page = next(node for node in graph if node["@type"] == "FAQPage")
                canonical = f"https://datatalks.club/blog/{slug}.html"

                self.assertEqual(page["@id"], f"{canonical}#{faq.heading_id}")
                self.assertEqual(len(page["mainEntity"]), RECOVERED_SECTIONS[slug])
                self.assertEqual(
                    [item["name"] for item in page["mainEntity"]],
                    [question.question for question in faq.questions],
                )
                for item, question in zip(page["mainEntity"], faq.questions, strict=True):
                    self.assertEqual(item["@id"], f"{canonical}#{question.id}")
                    self.assertEqual(item["acceptedAnswer"]["text"], question.answer_text)
                # The article's own node is still first and still a BlogPosting.
                self.assertEqual(graph[0]["@type"], "BlogPosting")
