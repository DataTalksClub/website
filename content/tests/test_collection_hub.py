"""The shared collection hub behind /blog and /books, rebuilt on design 5a (issue #179).

One template serves two navigation entries, so every check here runs against both
collections.  The page carries its own inline stylesheet, includes the shared shell
partials instead of copying them, and states only what the catalogue records hold.
"""

from __future__ import annotations

import re
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils.html import escape

from content.public_data import public_projection
from core.templatetags.accessibility import human_day, iso_day

RETIRED_ASSETS = (
    "/static/courses.css",
    "/static/core/site_shell.css",
    "/static/core/accessibility.css",
    "tailwindcss",
    "fontawesome",
)
TEMPLATE_SYNTAX = ("{#", "#}", "{%", "%}", "{{", "}}")


class PublicationDayFilterTests(SimpleTestCase):
    """``human_day`` publishes the recorded day and never a clock reading."""

    def test_a_bare_day_and_a_padded_midnight_render_the_same_day(self) -> None:
        self.assertEqual(human_day("2026-07-28"), "July 28, 2026")
        self.assertEqual(human_day("2025-10-06T00:00:00"), "October 6, 2025")
        self.assertEqual(human_day("2025-10-06T14:30:00+02:00"), "October 6, 2025")

    def test_the_machine_value_is_the_same_day_the_text_names(self) -> None:
        for stored in ("2026-07-28", "2025-10-06T00:00:00", "2025-10-06T14:30:00+02:00"):
            with self.subTest(stored=stored):
                self.assertEqual(iso_day(stored), stored[:10])
                self.assertNotIn("T", iso_day(stored))

    def test_an_unreadable_value_is_returned_untouched_rather_than_guessed(self) -> None:
        self.assertEqual(human_day(""), "")
        self.assertEqual(human_day("not a date"), "not a date")
        self.assertEqual(iso_day(""), "")
        self.assertEqual(iso_day("not a date"), "not a date")


class CollectionHubDesignTests(TestCase):
    def test_both_hubs_carry_one_inline_stylesheet_and_no_legacy_css(self) -> None:
        """Design 5a pages ship one <style> and link no stylesheet at all."""

        for path in ("/blog", "/books"):
            with self.subTest(path=path):
                body = self.client.get(path).content.decode()
                self.assertEqual(body.count("<style>"), 1)
                self.assertIn("--bubble:", body)
                self.assertEqual(re.findall(r'<link[^>]+rel="stylesheet"', body), [])
                for retired in RETIRED_ASSETS:
                    self.assertNotIn(retired, body)
                for leak in TEMPLATE_SYNTAX:
                    self.assertNotIn(leak, body)

    def test_both_hubs_compose_bands_and_shared_row_primitives(self) -> None:
        for path, heading in (("/blog", "All articles"), ("/books", "Archive")):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertTemplateUsed(response, "core/_site_shell_head.html")
                self.assertTemplateUsed(response, "core/_site_shell_foot.html")
                body = response.content.decode()
                self.assertIn('<section class="band band-cream collection-hero"', body)
                self.assertIn('class="row-list collection-rows" data-collection-list', body)
                self.assertIn(f'<h2 id="collection-list-heading">{heading}</h2>', body)
                # One h1 per page, and it is the name the site has always used.
                self.assertEqual(body.count("<h1"), 1)

    def test_each_hub_keeps_its_own_title_canonical_and_description(self) -> None:
        expected = {
            "/blog": ("Articles — DataTalks.Club", "Explore the latest articles"),
            "/books": ("Book of the Week — DataTalks.Club", "Discover the latest books"),
        }
        for path, (title, description) in expected.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                self.assertIn(f"<title>{title}</title>", body)
                self.assertIn(
                    f'<link rel="canonical" href="https://datatalks.club{path}">',
                    body,
                )
                self.assertIn(f'<meta name="description" content="{description}', body)
                self.assertIn(
                    f'<meta property="og:url" content="https://datatalks.club{path}">',
                    body,
                )
                self.assertIn('<meta name="twitter:card" content="summary">', body)


class CollectionHubRecordTests(TestCase):
    def test_blog_hub_lists_every_article_with_its_own_title_path_and_authors(self) -> None:
        articles = public_projection()["articles"]
        body = self.client.get("/blog").content.decode()

        self.assertIn(f"blog · {len(articles)} articles", body)
        self.assertEqual(body.count('class="list-row record-row"'), len(articles))
        for article in articles:
            title = escape(article["title"])
            self.assertIn(f'<a href="{article["public_path"]}">{title}</a>', body)
            for author in article["author_profiles"]:
                name = escape(author["name"])
                self.assertIn(f'<a href="{author["public_path"]}">{name}</a>', body)

    def test_books_hub_lists_every_book_and_counts_only_recorded_questions(self) -> None:
        books = public_projection()["books"]
        with_archive = [book for book in books if book["archive"]]
        body = self.client.get("/books").content.decode()

        self.assertIn(f"books · {len(books)} in the archive", body)
        self.assertEqual(body.count('class="list-row record-row"'), len(books))
        self.assertEqual(body.count('<span class="status-pill">'), len(with_archive))
        sample = with_archive[0]
        self.assertIn(f"{len(sample['archive'])} questions", body)

    def test_every_row_shows_the_recorded_day_and_never_an_invented_time(self) -> None:
        """The catalogues store a day; the page must not render a clock reading.

        The books catalogue pads its day with ``T00:00:00``.  A ``<time>`` whose
        machine value carries a time but whose text does not name a timezone is an
        accessibility failure, so the day is what both halves of the element say.
        """

        for path, collection in (("/blog", "articles"), ("/books", "books")):
            with self.subTest(path=path):
                body = self.client.get(path).content.decode()
                self.assertNotIn("00:00 UTC", body)
                for record in public_projection()[collection]:
                    day = str(record["published"])[:10]
                    self.assertIn(f'<time datetime="{day}">', body)
        self.assertIn("July 28, 2026", self.client.get("/blog").content.decode())
        self.assertIn("October 6, 2025", self.client.get("/books").content.decode())

    def test_no_row_declares_a_machine_time_its_text_does_not_name(self) -> None:
        for path in ("/blog", "/books"):
            with self.subTest(path=path):
                body = self.client.get(path).content.decode()
                self.assertEqual(re.findall(r'<time datetime="[^"]*T[^"]*"', body), [])

    def test_descriptions_are_shown_and_no_record_is_summarised_by_the_page(self) -> None:
        for path, collection in (("/blog", "articles"), ("/books", "books")):
            with self.subTest(path=path):
                body = self.client.get(path).content.decode()
                for record in public_projection()[collection]:
                    self.assertTrue(record["description"])
                    self.assertIn(
                        f'<p class="record-summary">{escape(record["description"])}</p>',
                        body,
                    )

    def test_an_empty_collection_says_so_instead_of_drawing_an_empty_list(self) -> None:
        projection = dict(public_projection())
        projection["articles"] = ()
        projection["books"] = ()
        with mock.patch("content.public_views.public_projection", return_value=projection):
            self.assertContains(self.client.get("/blog"), "No articles yet.")
            self.assertContains(self.client.get("/books"), "No books in the archive yet.")


class BooksExplainerTests(TestCase):
    """The books hub is the only public entry point to the book-of-the-week routine."""

    def test_the_explainer_keeps_every_step_and_the_slack_registration_route(self) -> None:
        body = self.client.get("/books").content.decode()

        self.assertIn('<h2 id="how-it-works-heading">How it works</h2>', body)
        self.assertIn('<a class="band-link" href="/slack">Register on DataTalks.Club</a>', body)
        self.assertIn('<code class="mono-code">#book-of-the-week</code>', body)
        for step in (
            "Ask as many questions as you'd like",
            "The book authors answer questions from Monday till Thursday",
            "On Friday, the authors decide who wins free copies of their book",
        ):
            self.assertIn(step, body)
        # The sequence stays an ordered list, and keeps that role after the design
        # system removes the markers.
        self.assertIn('<ol class="row-list how-it-works" role="list">', body)
        self.assertEqual(body.count('<span class="step-number" aria-hidden="true">'), 5)

    def test_the_blog_hub_does_not_carry_the_books_explainer(self) -> None:
        body = self.client.get("/blog").content.decode()

        self.assertNotIn('id="how-it-works-heading"', body)
        self.assertNotIn('class="mono-code"', body)
