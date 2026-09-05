"""The shared collection hub behind /blog and /books, rebuilt on the design system (issue #179).

One template serves two navigation entries, so every check here runs against both
collections.  The page carries its own inline stylesheet, includes the shared shell
partials instead of copying them, and states only what the catalogue records hold.
"""

from __future__ import annotations

import re
from unittest import mock
from xml.etree import ElementTree

from django.test import TestCase
from django.utils.html import escape

from content.catalogue import PUBLIC_CONTENT_STABLE_ID
from content.models import ContentSource
from content.pagination import PUBLIC_PAGE_SIZE
from content.public_data import public_projection
from core.templatetags.accessibility import human_day, iso_day

from .pagination_support import catalogue_page_bodies

SITEMAP_NAMESPACE = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}

RETIRED_ASSETS = (
    "/static/courses.css",
    "/static/core/site_shell.css",
    "/static/core/accessibility.css",
    "tailwindcss",
    "fontawesome",
)
TEMPLATE_SYNTAX = ("{#", "#}", "{%", "%}", "{{", "}}")


class PublicationDayFilterTests(TestCase):
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
        """Design system pages ship one <style> and link no stylesheet at all."""

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
        """The index pages (issue #174's primitive); the whole blog is still on it."""

        articles = public_projection()["articles"]
        body = "".join(catalogue_page_bodies(self.client, "/blog"))

        self.assertIn(f"blog · {len(articles)} articles", body)
        self.assertEqual(body.count('class="list-row archive-row record-row"'), len(articles))
        for article in articles:
            title = escape(article["title"])
            self.assertIn(f'<a href="{article["public_path"]}">{title}</a>', body)
            for author in article["author_profiles"]:
                name = escape(author["name"])
                # A byline is the shared person chip: a portrait and the name,
                # linked to the profile.
                self.assertIn(
                    f'<a class="band-link person-chip-name" '
                    f'href="{author["public_path"]}">{name}</a>',
                    body,
                )

    def test_books_hub_lists_every_book_and_counts_only_recorded_questions(self) -> None:
        """The archive pages (issue #174); the whole archive is still on it."""

        books = public_projection()["books"]
        with_archive = [book for book in books if book["archive"]]
        pages = catalogue_page_bodies(self.client, "/books")
        body = "".join(pages)

        # The kicker counts the collection, not the page: it says the same number
        # on every page of the archive.
        for page_body in pages:
            self.assertIn(f"books · {len(books)} in the archive", page_body)
        self.assertEqual(body.count('class="list-row archive-row record-row"'), len(books))
        # Every page but the last is exactly one full page of records.
        for page_body in pages[:-1]:
            self.assertEqual(
                page_body.count('class="list-row archive-row record-row"'),
                PUBLIC_PAGE_SIZE,
            )
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
                body = "".join(catalogue_page_bodies(self.client, path))
                self.assertNotIn("00:00 UTC", body)
                for record in public_projection()[collection]:
                    day = str(record["published"])[:10]
                    self.assertIn(f'<time datetime="{day}">', body)
        # The shared archive rail sets the day above the year, so that a column of
        # rows is one column of dates whatever the month is called.
        blog = "".join(catalogue_page_bodies(self.client, "/blog"))
        books = "".join(catalogue_page_bodies(self.client, "/books"))
        self.assertIn("<span>July 28</span>", blog)
        self.assertIn("<span>2026</span>", blog)
        self.assertIn("<span>October 6</span>", books)
        self.assertIn("<span>2025</span>", books)

    def test_no_row_declares_a_machine_time_its_text_does_not_name(self) -> None:
        for path in ("/blog", "/books"):
            with self.subTest(path=path):
                body = "".join(catalogue_page_bodies(self.client, path))
                self.assertEqual(re.findall(r'<time datetime="[^"]*T[^"]*"', body), [])

    def test_descriptions_are_shown_and_no_record_is_summarised_by_the_page(self) -> None:
        for path, collection in (("/blog", "articles"), ("/books", "books")):
            with self.subTest(path=path):
                body = "".join(catalogue_page_bodies(self.client, path))
                for record in public_projection()[collection]:
                    self.assertTrue(record["description"])
                    self.assertIn(
                        f'<p class="archive-summary">{escape(record["description"])}</p>',
                        body,
                    )

    def test_an_empty_articles_collection_says_so_instead_of_drawing_an_empty_list(self) -> None:
        projection = dict(public_projection())
        projection["articles"] = ()
        with mock.patch("content.public_views.public_projection", return_value=projection):
            self.assertContains(self.client.get("/blog"), "No articles yet.")

    def test_an_empty_book_archive_says_so_instead_of_drawing_an_empty_list(self) -> None:
        # The books hub reads the database, so it is emptied the way an
        # un-ingested database is empty rather than by patching a value in.
        ContentSource.objects.filter(stable_id=PUBLIC_CONTENT_STABLE_ID).update(enabled=False)

        empty_books = self.client.get("/books")
        self.assertContains(empty_books, "No books are available yet.")
        # An empty archive is one valid page, so it offers no page controls at
        # all, and the page beyond it is a real miss rather than a nearest page.
        self.assertNotContains(empty_books, 'aria-label="Book archive pages"')
        self.assertEqual(self.client.get("/books?page=2").status_code, 404)


class BooksArchiveContractTests(TestCase):
    """The Book of the Week archive as its own contract (issue #174).

    Both hubs page through the one shared paginator, so the slicing, the query
    grammar and the controls are tested once for the pair above.  What is left
    here is everything the issue names for the books archive alone: the method
    and cache boundary of a public catalogue page, the archive's sitemap
    membership, and the introduction that stays above every page of records.
    """

    def test_the_archive_rejects_unsafe_methods_and_supports_head(self) -> None:
        self.assertEqual(self.client.post("/books").status_code, 405)

        get_response = self.client.get("/books?page=2")
        head_response = self.client.head("/books?page=2")
        self.assertEqual(head_response.status_code, get_response.status_code)
        self.assertEqual(
            head_response.headers["Cache-Control"], get_response.headers["Cache-Control"]
        )
        self.assertEqual(head_response.content, b"")
        # A successful archive page is a public hub in the spec 02 cache classes:
        # the browser revalidates, the edge TTL stays with the route class.
        self.assertEqual(get_response.headers["Cache-Control"], "max-age=0, must-revalidate")
        self.assertEqual(
            self.client.get("/books").headers["Cache-Control"], "max-age=0, must-revalidate"
        )

        # A credentialed request never shares an anonymous catalogue object.
        credentialed = self.client.get(
            "/books?page=2", HTTP_AUTHORIZATION="Bearer synthetic-not-a-secret"
        )
        self.assertEqual(credentialed.status_code, 200)
        self.assertIn("private", credentialed.headers["Cache-Control"])
        self.assertIn("no-store", credentialed.headers["Cache-Control"])

    def test_the_sitemap_keeps_the_clean_hub_and_the_html_details_only(self) -> None:
        response = self.client.get("/sitemaps/books.xml")

        self.assertEqual(response.status_code, 200)
        document = ElementTree.fromstring(response.content)
        locations = [node.text or "" for node in document.findall("s:url/s:loc", SITEMAP_NAMESPACE)]
        expected = ["https://datatalks.club/books"] + [
            f"https://datatalks.club{book['public_path']}" for book in public_projection()["books"]
        ]
        # Query pages are discoverable through the controls, never through the
        # sitemap: page one is the only archive location in it.
        self.assertEqual(locations, expected)
        self.assertEqual(len(set(locations)), len(locations))
        self.assertFalse(any("?" in location for location in locations))
        self.assertTrue(all(location.endswith(".html") for location in locations[1:]))
        # The details keep their source publication day as lastmod.
        lastmods = [
            node.text or "" for node in document.findall("s:url/s:lastmod", SITEMAP_NAMESPACE)
        ]
        self.assertEqual(
            lastmods, [book["published"][:10] for book in public_projection()["books"]]
        )

    def test_every_archive_page_keeps_the_introduction_above_the_records(self) -> None:
        books = public_projection()["books"]
        pages = catalogue_page_bodies(self.client, "/books")
        page_count = -(-len(books) // PUBLIC_PAGE_SIZE)
        self.assertEqual(len(pages), page_count)

        for page_number, body in enumerate(pages, start=1):
            with self.subTest(page=page_number):
                self.assertIn('<h1 id="collection-heading">Book of the Week</h1>', body)
                self.assertIn(
                    "Each week we have a book author coming to DataTalks.Club to answer "
                    "your questions",
                    body,
                )
                self.assertIn('<h2 id="how-it-works-heading">How it works</h2>', body)
                self.assertIn(
                    '<a class="band-link" href="/slack">Register on DataTalks.Club</a>', body
                )
                self.assertIn('<code class="mono-code">#book-of-the-week</code>', body)
                self.assertIn('<h2 id="collection-list-heading">Archive</h2>', body)
                self.assertIn(f"books · {len(books)} in the archive", body)
                # The introduction is not merely present: every piece of it is
                # above the records, on every page of the archive.  The Slack
                # search starts at the explainer, because the site navigation
                # above carries its own link to `/slack`.
                intro = body.index("Each week we have a book author")
                how_it_works = body.index('id="how-it-works-heading"')
                slack = body.index('href="/slack"', how_it_works)
                archive = body.index('id="collection-list-heading"')
                first_record = body.index('class="list-row archive-row record-row"')
                self.assertLess(intro, how_it_works)
                self.assertLess(how_it_works, slack)
                self.assertLess(slack, archive)
                self.assertLess(archive, first_record)
                first = (page_number - 1) * PUBLIC_PAGE_SIZE + 1
                last = first - 1 + PUBLIC_PAGE_SIZE if page_number < page_count else len(books)
                self.assertIn(f"Showing {first}&ndash;{last} of {len(books)}.", body)


class CollectionHubPaginationTests(TestCase):
    """Both hubs page, through the one shared control (issues #174, #178).

    55 articles and 98 books were one screen each; the hub now cuts them into pages
    of 20 without changing what a row says, what a page is called, or which URL a
    record lives at.
    """

    def test_the_first_page_is_the_clean_path_and_keeps_its_original_metadata(self) -> None:
        expected = {
            "/blog": "Articles — DataTalks.Club",
            "/books": "Book of the Week — DataTalks.Club",
        }
        for path, title in expected.items():
            with self.subTest(path=path):
                for spelling in (path, f"{path}?page=1"):
                    response = self.client.get(spelling)
                    self.assertEqual(response.status_code, 200)
                    body = response.content.decode()
                    self.assertIn(f"<title>{title}</title>", body)
                    self.assertIn(
                        f'<link rel="canonical" href="https://datatalks.club{path}">', body
                    )
                    self.assertIn(
                        f'<meta property="og:url" content="https://datatalks.club{path}">', body
                    )
                    # No internal link ever spells the first page as a query.
                    self.assertNotIn('?page=1"', body)
                    self.assertNotIn('<link rel="prev"', body)

    def test_a_later_page_names_itself_in_its_title_canonical_and_relations(self) -> None:
        response = self.client.get("/books?page=2")
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("<title>Book of the Week — Page 2 — DataTalks.Club</title>", body)
        self.assertIn('<link rel="canonical" href="https://datatalks.club/books?page=2">', body)
        self.assertIn(
            '<meta property="og:url" content="https://datatalks.club/books?page=2">', body
        )
        self.assertIn('<link rel="prev" href="https://datatalks.club/books">', body)
        self.assertIn('<link rel="next" href="https://datatalks.club/books?page=3">', body)
        # The archive's own introduction stays above every page of records.
        self.assertIn('<h1 id="collection-heading">Book of the Week</h1>', body)
        self.assertIn('<h2 id="how-it-works-heading">How it works</h2>', body)
        self.assertIn('<h2 id="collection-list-heading">Archive</h2>', body)

    def test_the_pages_partition_the_projection_in_its_recorded_order(self) -> None:
        for path, collection in (("/blog", "articles"), ("/books", "books")):
            with self.subTest(path=path):
                records = public_projection()[collection]
                pages = catalogue_page_bodies(self.client, path)
                self.assertEqual(
                    len(pages),
                    -(-len(records) // PUBLIC_PAGE_SIZE),
                )
                for index, page_body in enumerate(pages):
                    expected = records[index * PUBLIC_PAGE_SIZE : (index + 1) * PUBLIC_PAGE_SIZE]
                    found = re.findall(r'<h3 class="archive-title">\s*<a href="([^"]+)"', page_body)
                    self.assertEqual(found, [record["public_path"] for record in expected])

    def test_the_page_selector_accepts_one_spelling_and_fails_closed_otherwise(self) -> None:
        for path in ("/blog", "/books"):
            with self.subTest(path=path):
                for query in ("page=0", "page=01", "page=%32", "page=2&page=3"):
                    bad = self.client.get(f"{path}?{query}")
                    self.assertEqual(bad.status_code, 400)
                    self.assertEqual(bad.headers["Cache-Control"], "no-store, max-age=0")
                    self.assertNotContains(bad, query, status_code=400)

                # A parameter this hub does not select on rides along ignored, so a
                # campaign-tagged link is a link to the hub rather than an error.
                tagged = self.client.get(f"{path}?page=2&x=1")
                self.assertEqual(tagged.status_code, 200)
                self.assertNotContains(tagged, "x=1")

                beyond = self.client.get(f"{path}?page=99")
                self.assertEqual(beyond.status_code, 404)
                self.assertEqual(beyond.headers["Cache-Control"], "no-store, max-age=0")
                self.assertNotIn("Location", beyond.headers)

                rejected = self.client.post(path)
                self.assertEqual(rejected.status_code, 405)
                self.assertEqual(rejected.headers["Allow"], "GET, HEAD")
                self.assertEqual(rejected.headers["Cache-Control"], "no-store, max-age=0")

    def test_the_hub_aliases_still_forward_their_raw_query_in_one_hop(self) -> None:
        for alias in ("/books.html", "/books/"):
            with self.subTest(alias=alias):
                response = self.client.get(f"{alias}?page=2", follow=False)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response.headers["Location"], "/books?page=2")

    def test_controls_are_one_labelled_landmark_with_one_current_page(self) -> None:
        body = self.client.get("/books?page=2").content.decode()
        markup = body.split('<nav class="pagination"', 1)[1].split("</nav>", 1)[0]

        self.assertEqual(body.count('<nav class="pagination"'), 1)
        self.assertIn('aria-label="Book archive pages"', markup)
        self.assertEqual(markup.count('aria-current="page"'), 1)
        self.assertIn('aria-label="Page 2, current page"', markup)
        self.assertIn('aria-label="Previous page — page 1"', markup)
        self.assertIn('aria-label="Next page — page 3"', markup)
        # The current page is a marker, not a link a reader can follow to itself.
        self.assertIn(
            'class="filter-pill pagination-number interactive-lift"\n              aria-current',
            markup,
        )

    def test_the_blog_index_pages_and_keeps_its_own_words(self) -> None:
        articles = public_projection()["articles"]
        second = self.client.get("/blog?page=2")
        body = second.content.decode()

        self.assertEqual(second.status_code, 200)
        self.assertIn("<title>Articles — Page 2 — DataTalks.Club</title>", body)
        self.assertIn(f"blog · {len(articles)} articles", body)
        self.assertIn('<h1 id="collection-heading">Latest Articles</h1>', body)
        self.assertIn('<h2 id="collection-list-heading">All articles</h2>', body)
        self.assertIn('aria-label="Article pages"', body)
        # The books explainer belongs to books, on every page of either hub.
        self.assertNotIn('id="how-it-works-heading"', body)


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
