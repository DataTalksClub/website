"""The public blog article page, rebuilt in the design 5a system (issue #179).

Two contracts are checked here.  The composition contract: every fact the page
shows is read from the checked article record and the people it names, and a
record that cannot supply one fails loudly.  The page contract: the rebuild kept
every affordance the previous page had — the trail back to the blog, the byline
and its profile links, the publication date, the artwork and its alternative
text, the heading anchors and every word of the body — while carrying the design
system's own stylesheet and shell.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.utils.html import escape

from content.article_content import (
    MAX_HEADING_LEVEL,
    MIN_HEADING_LEVEL,
    article_public_path,
    article_view,
    prose_sections,
)
from content.public_data import public_projection
from core.home_content import published_display, reading_minutes

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
# The block kinds the projected article bodies actually contain.  The page must
# handle each of them; a kind added later must still render (see the unknown-kind
# test below), never disappear.
PROJECTED_BLOCK_KINDS = {"heading", "list_item", "paragraph"}


def _richest_article() -> dict:
    """The article that exercises the most block kinds, then the most blocks."""

    return max(
        public_projection()["articles"],
        key=lambda record: (
            len({block["kind"] for block in record["blocks"]}),
            len(record["blocks"]),
        ),
    )


class ArticleCompositionTests(SimpleTestCase):
    def test_every_article_composes_without_invention(self) -> None:
        projection = public_projection()
        people = projection["people_by_slug"]
        records = projection["articles"]

        views = tuple(article_view(record, people) for record in records)

        self.assertEqual(len(views), len(records))
        for view, record in zip(views, records, strict=True):
            self.assertEqual(view.title, record["title"])
            self.assertEqual(view.subtitle, record["subtitle"])
            self.assertEqual(view.public_path, record["public_path"])
            self.assertEqual(view.published, record["published"])
            self.assertEqual(view.published_display, published_display(record["published"]))
            # The reading estimate is the homepage's own, not a second one.
            self.assertEqual(view.reading_minutes, reading_minutes(record))
            self.assertEqual(view.reading_time, f"{reading_minutes(record)} min read")
            self.assertEqual(
                [author.name for author in view.authors],
                [profile["name"] for profile in record["author_profiles"]],
            )
            self.assertEqual(
                [author.public_path for author in view.authors],
                [profile["public_path"] for profile in record["author_profiles"]],
            )
            self.assertEqual(view.image_path, record["image_path"])
            self.assertEqual(view.media_available, bool(record["media_available"]))

    def test_the_body_keeps_every_projected_block_in_its_own_order(self) -> None:
        kinds: set[str] = set()
        for record in public_projection()["articles"]:
            with self.subTest(slug=record["slug"]):
                kinds.update(block["kind"] for block in record["blocks"])
                sections = prose_sections(record["blocks"])
                rendered: list[str] = []
                for section in sections:
                    if section.kind == "list":
                        rendered.extend(section.items)
                    else:
                        rendered.append(section.text)
                self.assertEqual(rendered, [block["text"] for block in record["blocks"]])
        # If the projection grows a kind, this test names it and the page must
        # decide what to do with it rather than silently inheriting a paragraph.
        self.assertEqual(kinds, PROJECTED_BLOCK_KINDS)

    def test_headings_keep_their_anchors_and_stay_inside_the_heading_range(self) -> None:
        for record in public_projection()["articles"]:
            headings = [block for block in record["blocks"] if block["kind"] == "heading"]
            composed = [
                section for section in prose_sections(record["blocks"]) if section.kind == "heading"
            ]
            with self.subTest(slug=record["slug"]):
                self.assertEqual(
                    [(section.id, section.text) for section in composed],
                    [(block["id"], block["text"]) for block in headings],
                )
                for section in composed:
                    self.assertGreaterEqual(section.level, MIN_HEADING_LEVEL)
                    self.assertLessEqual(section.level, MAX_HEADING_LEVEL)

    def test_a_run_of_list_items_becomes_one_list_and_a_break_starts_another(self) -> None:
        sections = prose_sections(
            (
                {"kind": "list_item", "text": "first"},
                {"kind": "list_item", "text": "second"},
                {"kind": "paragraph", "text": "between"},
                {"kind": "list_item", "text": "third"},
            )
        )

        self.assertEqual([section.kind for section in sections], ["list", "paragraph", "list"])
        self.assertEqual(sections[0].items, ("first", "second"))
        self.assertEqual(sections[2].items, ("third",))

    def test_a_block_kind_the_projection_grows_later_is_still_rendered(self) -> None:
        sections = prose_sections(
            (
                {"kind": "quote", "text": "A pull quote the projection does not make today."},
                {"kind": "", "text": "An unnamed block."},
                {"kind": "paragraph", "text": ""},
            )
        )

        self.assertEqual([section.kind for section in sections], ["paragraph", "paragraph"])
        self.assertEqual(
            [section.text for section in sections],
            ["A pull quote the projection does not make today.", "An unnamed block."],
        )

    def test_a_record_that_cannot_supply_a_fact_fails_loudly(self) -> None:
        record = dict(public_projection()["articles"][0])
        people = public_projection()["people_by_slug"]

        for field, value in (("title", ""), ("title", "  "), ("published", "")):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ImproperlyConfigured):
                    article_view({**record, field: value}, people)
        with self.assertRaisesRegex(ImproperlyConfigured, "publication date is invalid"):
            article_view({**record, "published": "1 January 2021"}, people)
        with self.assertRaisesRegex(ImproperlyConfigured, "canonical path is invalid"):
            article_view({**record, "public_path": "/blog/renamed-by-hand"}, people)
        with self.assertRaisesRegex(ImproperlyConfigured, "author must have a name"):
            article_view({**record, "author_profiles": ({"name": ""},)}, people)
        with self.assertRaisesRegex(ImproperlyConfigured, "fragment id"):
            prose_sections(({"kind": "heading", "level": 2, "text": "No anchor"},))
        with self.assertRaisesRegex(ImproperlyConfigured, "level"):
            prose_sections(({"kind": "heading", "id": "no-level", "text": "No level"},))

    def test_a_timestamped_record_still_publishes_the_day_it_shows(self) -> None:
        """A `<time>` never carries a clock the page has not shown the reader."""

        record = dict(public_projection()["articles"][0])
        record["published"] = "2026-08-10T12:00:00+00:00"

        view = article_view(record, public_projection()["people_by_slug"])

        self.assertEqual(view.published, "2026-08-10")
        self.assertEqual(view.published_display, "Aug 10, 2026")

    def test_the_canonical_html_suffix_is_part_of_the_identity(self) -> None:
        """The `.html` article addresses were restored once; they stay checked."""

        for record in public_projection()["articles"]:
            with self.subTest(slug=record["slug"]):
                self.assertEqual(article_public_path(record), f"/blog/{record['slug']}.html")

    def test_an_author_without_a_portrait_keeps_the_credit(self) -> None:
        record = dict(public_projection()["articles"][0])

        view = article_view(record, {})

        self.assertTrue(view.authors)
        for author, profile in zip(view.authors, record["author_profiles"], strict=True):
            self.assertEqual(author.name, profile["name"])
            self.assertEqual(author.public_path, profile["public_path"])
            self.assertFalse(author.media_available)
            self.assertEqual(author.image_path, "")

    def test_the_page_reads_its_prose_from_the_shared_primitive(self) -> None:
        """Long-form typography is the system's, not this page's own fork."""

        page = (REPOSITORY_ROOT / "templates/public/article_detail.html").read_text(
            encoding="utf-8"
        )
        partial = (REPOSITORY_ROOT / "templates/core/_design_system.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('class="prose prose-reading article-body"', page)
        self.assertIn(
            '{% include "public/_prose_blocks.html" with sections=article.sections %}',
            page,
        )
        for rule in (
            ".prose > * + * {",
            ".prose h2 {",
            ".prose-reading {",
            ".prose blockquote {",
            ".prose pre {",
            ".prose-lede {",
        ):
            self.assertIn(rule, partial)
        # No prose typography in the page: the primitive owns all of it.
        for forked in (".article-body p", ".article-body h", ".article-body li"):
            self.assertNotIn(forked, page)


class ArticlePageTests(TestCase):
    def test_the_page_carries_its_own_stylesheet_and_no_legacy_css(self) -> None:
        article = _richest_article()

        body = self.client.get(article["public_path"]).content.decode()

        self.assertIn("<style>", body)
        self.assertIn("--measure:", body)
        self.assertEqual(re.findall(r'<link[^>]+rel="stylesheet"', body), [])
        for retired in (
            "/static/courses.css",
            "/static/core/site_shell.css",
            "/static/core/accessibility.css",
            "tailwindcss",
            "fontawesome",
        ):
            self.assertNotIn(retired, body)
        # `{{`/`}}` are deliberately absent from this list: article bodies quote
        # JSON and shell snippets that contain them as their own content.
        for leak in ("{#", "#}", "{%", "%}"):
            self.assertNotIn(leak, body)

    def test_the_trail_back_to_the_blog_survives_the_rebuild(self) -> None:
        article = public_projection()["articles"][0]

        response = self.client.get(article["public_path"])

        # The stylesheet's own comment names the pattern too, so the count is of
        # the trail itself, not of every occurrence of the word.
        self.assertContains(
            response,
            '<nav class="shell article-shell breadcrumbs" aria-label="Breadcrumb">',
            count=1,
        )
        self.assertContains(response, '<a href="/blog">Blog</a>', html=True)
        self.assertContains(response, '<li aria-current="page">')
        # The trail is the only way back; the page does not also grow a back-link.
        self.assertEqual(response.content.decode().count('href="/blog"'), 2)

    def test_the_byline_keeps_every_author_their_link_and_their_portrait(self) -> None:
        projection = public_projection()
        article = next(
            record for record in projection["articles"] if len(record["author_profiles"]) > 1
        )

        response = self.client.get(article["public_path"])

        body = response.content.decode()
        for profile in article["author_profiles"]:
            person = projection["people_by_slug"][profile["key"]]
            self.assertIn(escape(profile["name"]), body)
            self.assertIn(f'href="{profile["public_path"]}"', body)
            self.assertIn(f'src="{person["image_path"]}"', body)
            self.assertIn(f'alt="Portrait of {escape(profile["name"])}"', body)

    def test_the_date_the_reading_time_and_the_artwork_are_all_shown(self) -> None:
        article = public_projection()["articles"][0]

        response = self.client.get(article["public_path"])

        self.assertContains(response, f'datetime="{article["published"]}"')
        self.assertContains(response, published_display(article["published"]))
        self.assertContains(response, f"{reading_minutes(article)} min read")
        self.assertContains(response, f'src="{article["image_path"]}"')
        self.assertContains(response, f'alt="Artwork for {escape(article["title"])}"')
        self.assertContains(response, escape(article["subtitle"]))

    def test_an_article_without_artwork_says_so_where_the_picture_would_be(self) -> None:
        """No catalogue entry lacks artwork today; the state is still drawn."""

        record = dict(public_projection()["articles"][0])
        record["media_available"] = False
        record["image_path"] = ""
        composed = article_view(record, public_projection()["people_by_slug"])

        rendered = render_to_string("public/article_detail.html", {"article": composed})

        self.assertFalse(composed.media_available)
        self.assertIn("Artwork unavailable.", rendered)
        self.assertNotIn('alt="Artwork for', rendered)
        self.assertIn(escape(record["title"]), rendered)

    def test_every_word_of_the_richest_body_reaches_the_page(self) -> None:
        article = _richest_article()

        body = self.client.get(article["public_path"]).content.decode()

        for block in article["blocks"]:
            self.assertIn(escape(block["text"]), body)
            if block["kind"] == "heading":
                self.assertIn(f'id="{block["id"]}"', body)
        self.assertIn("<li>", body)
        self.assertIn("<ul>", body)
        self.assertIn("<h2 ", body)

    def test_the_seo_contract_is_exactly_what_it_was(self) -> None:
        article = public_projection()["articles"][0]

        response = self.client.get(article["public_path"])

        self.assertContains(
            response,
            f'<link rel="canonical" href="https://datatalks.club{article["public_path"]}">',
            count=1,
        )
        self.assertContains(response, f"<title>{escape(article['title'])} — DataTalks.Club</title>")
        self.assertContains(response, '<meta property="og:type" content="article">')
        self.assertContains(
            response,
            f'<meta property="article:published_time" content="{article["published"]}">',
        )
        self.assertContains(
            response,
            f'<meta property="og:image" content="https://datatalks.club{article["image_path"]}">',
        )
        self.assertContains(response, '"@type": "BlogPosting"')
