"""The public blog article page, rebuilt in the design system (issue #179).

Two contracts are checked here.  The composition contract: every fact the page
shows is read from the checked article record and the people it names, and a
record that cannot supply one fails loudly.  The page contract: the rebuild kept
every affordance the previous page had — the trail back to the blog, the byline
and its profile links, the publication date, the heading anchors and every word
of the body — while carrying the design system's own stylesheet and shell.  The
article's artwork is a social card: it is published in the head as `og:image`
and is never drawn in the reading band.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from django.utils.html import escape

from content import catalogue
from content.article_content import (
    MAX_HEADING_LEVEL,
    MIN_HEADING_LEVEL,
    article_public_path,
    article_view,
    prose_sections,
)
from core.home_content import published_display, reading_minutes

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
# The block kinds the projected article bodies actually contain.  The page must
# handle each of them; a kind added later must still render (see the unknown-kind
# test below), never disappear.
PROJECTED_BLOCK_KINDS = {
    "chart",
    "code",
    "heading",
    "image",
    "list_item",
    "paragraph",
    "quote",
    "separator",
    "table",
}


def _article(slug: str) -> dict[str, Any]:
    """The published article a test names, which the catalogue must hold."""

    record = catalogue.article(slug)
    assert record is not None, slug
    return record


def _richest_article() -> dict:
    """The article that exercises the most block kinds, then the most blocks."""

    return max(
        catalogue.articles(),
        key=lambda record: (
            len({block["kind"] for block in record["blocks"]}),
            len(record["blocks"]),
        ),
    )


class ArticleCompositionTests(TestCase):
    def test_every_article_composes_without_invention(self) -> None:
        people = catalogue.people_by_slug()
        records = catalogue.articles()

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
            # The artwork is deliberately absent from the composed view: the page
            # publishes it as a social card from the record, not in the body.
            self.assertFalse(hasattr(view, "image_path"))
            self.assertFalse(hasattr(view, "media_available"))

    def test_the_body_keeps_every_projected_block_in_its_own_order(self) -> None:
        """Every block becomes exactly one drawn thing, in the order it was written."""

        kinds: set[str] = set()
        for record in catalogue.articles():
            with self.subTest(slug=record["slug"]):
                kinds.update(block["kind"] for block in record["blocks"])
                drawn: list[str] = []
                for section in prose_sections(record["blocks"]):
                    if section.kind == "list":
                        drawn.extend("list_item" for _ in section.items)
                    else:
                        drawn.append(section.kind)
                self.assertEqual(drawn, [block["kind"] for block in record["blocks"]])
        # If the projection grows a kind, this test names it and the page must
        # decide what to do with it rather than silently inheriting a paragraph.
        self.assertEqual(kinds, PROJECTED_BLOCK_KINDS)

    def test_the_restored_body_kinds_carry_what_they_need_to_be_drawn(self) -> None:
        """The kinds the flattening used to destroy arrive whole, with their fields."""

        found: dict[str, int] = {}
        for record in catalogue.articles():
            for section in prose_sections(record["blocks"]):
                found[section.kind] = found.get(section.kind, 0) + 1
                with self.subTest(slug=record["slug"], kind=section.kind):
                    if section.kind == "image":
                        # A site-relative address, and a size the page can reserve.
                        self.assertRegex(section.src, r"^/images/[^\s]+$")
                        self.assertGreater(section.width, 0)
                        self.assertGreater(section.height, 0)
                    elif section.kind == "table":
                        self.assertTrue(section.rows or section.head)
                        self.assertTrue(section.label)
                    elif section.kind == "code":
                        self.assertTrue(section.text)
        self.assertEqual(found["image"], 325)
        self.assertEqual(found["table"], 33)
        self.assertEqual(found["code"], 90)
        self.assertEqual(found["chart"], 50)

    def test_a_table_frame_is_named_once_inside_its_own_article(self) -> None:
        """A scroll frame is a named region, and two of them may not share a name."""

        for record in catalogue.articles():
            labels = [
                section.label
                for section in prose_sections(record["blocks"])
                if section.kind == "table"
            ]
            with self.subTest(slug=record["slug"]):
                self.assertEqual(len(labels), len(set(labels)))

    def test_a_link_written_in_the_source_keeps_its_address(self) -> None:
        record = _article("how-to-run-postgresql-and-pgadmin-with-docker")

        markup = " ".join(
            section.html for section in prose_sections(record["blocks"]) if section.html
        )

        self.assertIn('<a href="https://www.postgresql.org/">PostgreSQL</a>', markup)
        # The sanitizer owns what may survive: a legacy renderer's directive and a
        # target attribute are not markup this site publishes.
        self.assertNotIn("target=", markup)
        self.assertNotIn("{:", markup)

    def test_headings_keep_their_anchors_and_stay_inside_the_heading_range(self) -> None:
        for record in catalogue.articles():
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
        self.assertEqual([item.text for item in sections[0].items], ["first", "second"])
        self.assertEqual([item.text for item in sections[2].items], ["third"])
        self.assertFalse(any(section.ordered for section in sections))

    def test_a_numbered_run_becomes_its_own_ordered_list(self) -> None:
        """A source that counted its steps keeps the count, and the counter."""

        sections = prose_sections(
            (
                {"kind": "list_item", "text": "bullet"},
                {"kind": "list_item", "text": "step one", "ordered": True},
                {"kind": "list_item", "text": "step two", "ordered": True},
            )
        )

        self.assertEqual([section.kind for section in sections], ["list", "list"])
        self.assertEqual([section.ordered for section in sections], [False, True])
        self.assertEqual([item.text for item in sections[1].items], ["step one", "step two"])

    def test_a_block_kind_the_projection_grows_later_is_still_rendered(self) -> None:
        sections = prose_sections(
            (
                {"kind": "callout", "text": "A callout the projection does not make today."},
                {"kind": "", "text": "An unnamed block."},
                {"kind": "paragraph", "text": ""},
            )
        )

        self.assertEqual([section.kind for section in sections], ["paragraph", "paragraph"])
        self.assertEqual(
            [section.text for section in sections],
            ["A callout the projection does not make today.", "An unnamed block."],
        )

    def test_an_illustration_the_record_addresses_off_site_is_refused(self) -> None:
        """The page never publishes an address the sanitizer would have rejected."""

        for source in ("https://example.invalid/a.png", "//example.invalid/a.png", "/a b.png"):
            with self.subTest(src=source):
                with self.assertRaisesRegex(ImproperlyConfigured, "illustration address"):
                    prose_sections(({"kind": "image", "src": source},))

    def test_a_record_that_cannot_supply_a_fact_fails_loudly(self) -> None:
        record = dict(catalogue.articles()[0])
        people = catalogue.people_by_slug()

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

        record = dict(catalogue.articles()[0])
        record["published"] = "2026-08-10T12:00:00+00:00"

        view = article_view(record, catalogue.people_by_slug())

        self.assertEqual(view.published, "2026-08-10")
        self.assertEqual(view.published_display, "Aug 10, 2026")

    def test_the_canonical_html_suffix_is_part_of_the_identity(self) -> None:
        """The `.html` article addresses were restored once; they stay checked."""

        for record in catalogue.articles():
            with self.subTest(slug=record["slug"]):
                self.assertEqual(article_public_path(record), f"/blog/{record['slug']}.html")

    def test_an_author_without_a_portrait_keeps_the_credit(self) -> None:
        record = dict(catalogue.articles()[0])

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

    def test_a_code_sample_wraps_instead_of_hiding_behind_a_scroll(self) -> None:
        """The FAQ pages settled this: a scroll with no focus stop is a barrier."""

        partial = (REPOSITORY_ROOT / "templates/core/_design_system.html").read_text(
            encoding="utf-8"
        )
        rule = partial.split(".prose pre {", 1)[1].split("}", 1)[0]

        self.assertIn("white-space: pre-wrap", rule)
        self.assertNotIn("overflow-x: auto", rule)


class ArticlePageTests(TestCase):
    def test_a_body_with_pictures_tables_and_code_is_drawn_properly(self) -> None:
        """The restored kinds reach the page as the marks each one needs."""

        body = self.client.get("/blog/machine-learning-zoomcamp.html").content.decode()

        # A picture reserves its own box, so the reading column does not jump.
        self.assertRegex(
            body, r'<img\s+src="/images/posts/[^"]+"[\s\S]*?width="\d+"[\s\S]*?height="\d+"'
        )
        self.assertIn("<figure>", body)
        self.assertIn("<figcaption>", body)
        # A wide table scrolls inside a named frame a keyboard can reach.
        self.assertIn(
            '<div class="prose-scroll" role="region" tabindex="0" aria-label="Table 1">', body
        )
        self.assertIn('<th scope="col">Topic</th>', body)
        # A link keeps the address the source wrote.
        self.assertIn('<a href="https://', body)

    def test_a_code_sample_reaches_the_page_as_code(self) -> None:
        body = self.client.get(
            "/blog/how-to-run-postgresql-and-pgadmin-with-docker.html"
        ).content.decode()

        self.assertIn('<pre><code class="language-bash">docker volume create', body)
        # Escaped, never executed: a sample's angle brackets and quotes are text
        # inside the element, and the body region carries no markup of its own.
        prose = body.split('class="prose prose-reading article-body"', 1)[1].split("</div>", 1)[0]
        self.assertNotIn("<script", prose)
        self.assertIn("&quot;root&quot;", prose)

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
        article = catalogue.articles()[0]

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
        article = next(
            record for record in catalogue.articles() if len(record["author_profiles"]) > 1
        )

        response = self.client.get(article["public_path"])

        body = response.content.decode()
        for profile in article["author_profiles"]:
            person = catalogue.people_by_slug()[profile["key"]]
            # The byline is the shared person chip: the name is the link, and the
            # portrait beside it is decorative, because a screen reader that heard
            # "Portrait of Alexey Grigorev, link, Alexey Grigorev" would hear the
            # same credit twice.
            self.assertIn(
                f'<a class="band-link person-chip-name" '
                f'href="{profile["public_path"]}">{escape(profile["name"])}</a>',
                body,
            )
            self.assertIn(f'src="{person["image_path"]}"', body)
            self.assertNotIn(f'alt="Portrait of {escape(profile["name"])}"', body)

    def test_the_date_and_the_reading_time_are_shown(self) -> None:
        article = catalogue.articles()[0]

        response = self.client.get(article["public_path"])

        self.assertContains(response, f'datetime="{article["published"]}"')
        self.assertContains(response, published_display(article["published"]))
        self.assertContains(response, f"{reading_minutes(article)} min read")
        self.assertContains(response, escape(article["subtitle"]))

    def test_the_artwork_is_a_social_card_and_is_never_drawn_in_the_body(self) -> None:
        """The image is composed for a link preview, so only the head publishes it.

        A body that embeds the same file as one of its own image blocks is a
        different thing and stays; what must not come back is the cover plate the
        reading band once opened with, or the placeholder that stood in for it.
        """

        for article in catalogue.articles()[:20]:
            with self.subTest(slug=article["slug"]):
                body = self.client.get(article["public_path"]).content.decode()

                self.assertNotIn("article-cover", body)
                self.assertNotIn('alt="Artwork for', body)
                self.assertNotIn("Artwork unavailable.", body)
                if article["image_path"]:
                    canonical = f"{settings.CANONICAL_ORIGIN.rstrip('/')}{article['image_path']}"
                    self.assertIn(f'<meta property="og:image" content="{canonical}">', body)
                    self.assertIn(f'<meta name="twitter:image" content="{canonical}">', body)

    def test_every_word_of_the_richest_body_reaches_the_page(self) -> None:
        article = _richest_article()

        body = self.client.get(article["public_path"]).content.decode()

        for block in article["blocks"]:
            # A block that carried more than its plain text is drawn from that
            # source segment, so the flattened text is not what reaches the page.
            if block.get("markdown") is None and block.get("text"):
                self.assertIn(escape(block["text"]), body)
            if block["kind"] == "heading":
                self.assertIn(f'id="{block["id"]}"', body)
            if block["kind"] == "image":
                self.assertIn(f'src="{block["src"]}"', body)
        self.assertIn("<li>", body)
        self.assertIn("<ul>", body)
        self.assertIn("<h2 ", body)

    def test_the_seo_contract_is_exactly_what_it_was(self) -> None:
        article = catalogue.articles()[0]

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
