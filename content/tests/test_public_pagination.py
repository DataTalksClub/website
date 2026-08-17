"""The one paginator the public catalogues share (issue #178).

Three groups of checks: the model and its parser as pure values, the route contract
through a synthetic catalogue mounted on the reviewed base paths, and the repository
guards that stop a fifth catalogue from growing a paginator of its own.

The consumers' own behaviour lives with them — `test_collection_hub.py` for the blog
and books archives, `test_wiki_design.py` for the Wiki catalogue and
`test_events_timeline.py` for past events.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from content.pagination import (
    PUBLIC_MAX_PAGE,
    PUBLIC_PAGE_SIZE,
    PUBLIC_PAGINATION_PATHS,
    InvalidPaginationQuery,
    PaginationPageNotFound,
    PublicPagination,
    build_public_pagination,
    paginate_public_request,
    pagination_title,
    parse_page_query,
    public_page_url,
)
from content.public_views import section_sitemap
from core.seo import validated_canonical_url

FIXTURE_URLCONF = "content.tests.pagination_fixture_urls"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_COMMENT = re.compile(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}", re.DOTALL)


def pagination_markup(body: str) -> str:
    """The rendered control row, without the inline stylesheet that precedes it."""

    return body.split('<nav class="pagination"', 1)[1].split("</nav>", 1)[0]


class PublicPaginationParserTests(SimpleTestCase):
    def test_empty_and_exact_ascii_positive_queries_are_accepted(self) -> None:
        self.assertEqual(parse_page_query(""), 1)
        self.assertEqual(parse_page_query("page=1"), 1)
        self.assertEqual(parse_page_query("page=2"), 2)
        self.assertEqual(parse_page_query("page=999"), 999)

    def test_every_alternate_or_unbounded_query_shape_is_rejected(self) -> None:
        malformed = (
            None,
            b"page=1",
            "page=",
            "page=0",
            "page=+1",
            "page=-1",
            "page=01",
            "page=١",
            "page=１００",
            "page=%31",
            "page=1000",
            "Page=1",
            "PAGE=1",
            "page=1&",
            "page=1&page=2",
            "page=1&sort=title",
            "sort=title&page=1",
            " page=1",
        )
        for raw_query in malformed:
            with self.subTest(raw_query=raw_query):
                with self.assertRaises(InvalidPaginationQuery):
                    parse_page_query(raw_query)

    def test_url_builder_emits_only_reviewed_clean_or_page_paths(self) -> None:
        for base_path in sorted(PUBLIC_PAGINATION_PATHS):
            with self.subTest(base_path=base_path):
                self.assertEqual(public_page_url(base_path, 1), base_path)
                self.assertEqual(public_page_url(base_path, 2), f"{base_path}?page=2")
                self.assertEqual(
                    public_page_url(base_path, PUBLIC_MAX_PAGE),
                    f"{base_path}?page={PUBLIC_MAX_PAGE}",
                )

        for base_path in (
            "/podcast",
            "/books/",
            "/books?page=2",
            "https://datatalks.club/books",
            "//books",
        ):
            with self.subTest(base_path=base_path):
                with self.assertRaises(ValueError):
                    public_page_url(base_path, 2)

        for page_number in (0, -1, 1000):
            with self.subTest(page_number=page_number):
                with self.assertRaises(ValueError):
                    public_page_url("/books", page_number)
        for invalid_page in (True, 1.0, "2"):
            with self.subTest(page_number=invalid_page):
                with self.assertRaises(TypeError):
                    public_page_url("/books", invalid_page)  # type: ignore[arg-type]

    def test_page_titles_keep_page_one_clean_and_suffix_later_pages(self) -> None:
        self.assertEqual(pagination_title("Books", 1), "Books — DataTalks.Club")
        self.assertEqual(pagination_title("Books", 27), "Books — Page 27 — DataTalks.Club")


class PublicPaginationModelTests(SimpleTestCase):
    def test_boundary_lengths_have_expected_metadata_and_slices(self) -> None:
        scenarios = ((0, 1), (1, 1), (20, 1), (21, 2), (100, 5))
        for length, page_count in scenarios:
            source = tuple(object() for _ in range(length))
            original = tuple(source)
            with self.subTest(length=length):
                first = build_public_pagination(
                    source,
                    page_number=1,
                    clean_base_path="/books",
                    catalogue_label="Book archive pages",
                )
                self.assertEqual(first.total_items, length)
                self.assertEqual(first.page_count, page_count)
                self.assertEqual(first.items, source[:PUBLIC_PAGE_SIZE])
                self.assertEqual(source, original)
                self.assertEqual(first.clean_path, "/books")
                self.assertEqual(first.canonical_path, "/books")
                self.assertIsNone(first.previous_url)
                self.assertEqual(first.has_controls, page_count > 1)
                self.assertEqual(
                    first.next_url,
                    "/books?page=2" if page_count > 1 else None,
                )

    def test_the_stated_slice_positions_match_the_items_on_the_page(self) -> None:
        source = tuple(range(55))
        expected = ((1, 1, 20), (2, 21, 40), (3, 41, 55))
        for page_number, first_item, last_item in expected:
            pagination = build_public_pagination(
                source,
                page_number=page_number,
                clean_base_path="/blog",
                catalogue_label="Article pages",
            )
            with self.subTest(page_number=page_number):
                self.assertEqual(pagination.first_item_number, first_item)
                self.assertEqual(pagination.last_item_number, last_item)
                self.assertEqual(pagination.items, source[first_item - 1 : last_item])

        empty: PublicPagination[int] = build_public_pagination(
            (),
            page_number=1,
            clean_base_path="/blog",
            catalogue_label="Article pages",
        )
        self.assertEqual((empty.first_item_number, empty.last_item_number), (0, 0))

    def test_concatenated_slices_reproduce_input_once_without_reordering_objects(self) -> None:
        for length in (0, 1, 20, 21, 100, 240):
            source = [{"position": position} for position in range(length)]
            identities_before = tuple(map(id, source))
            page_count = max(1, (length + PUBLIC_PAGE_SIZE - 1) // PUBLIC_PAGE_SIZE)
            rebuilt: list[dict[str, int]] = []
            for page_number in range(1, page_count + 1):
                pagination = build_public_pagination(
                    source,
                    page_number=page_number,
                    clean_base_path="/wiki",
                    catalogue_label="Wiki catalogue pages",
                )
                rebuilt.extend(pagination.items)

            with self.subTest(length=length):
                self.assertEqual(rebuilt, source)
                self.assertEqual(tuple(map(id, rebuilt)), identities_before)
                self.assertEqual(tuple(map(id, source)), identities_before)

    def test_numeric_window_is_bounded_sorted_and_has_exact_ellipses(self) -> None:
        scenarios = {
            1: (("page", 1), ("page", 2), ("page", 3), ("ellipsis", None), ("page", 12)),
            6: (
                ("page", 1),
                ("ellipsis", None),
                ("page", 4),
                ("page", 5),
                ("page", 6),
                ("page", 7),
                ("page", 8),
                ("ellipsis", None),
                ("page", 12),
            ),
            12: (
                ("page", 1),
                ("ellipsis", None),
                ("page", 10),
                ("page", 11),
                ("page", 12),
            ),
        }
        source = tuple(range(240))
        for page_number, expected in scenarios.items():
            pagination = build_public_pagination(
                source,
                page_number=page_number,
                clean_base_path="/events/past",
                catalogue_label="Past event pages",
            )
            actual = tuple((control.kind, control.page_number) for control in pagination.controls)
            with self.subTest(page_number=page_number):
                self.assertEqual(actual, expected)
                self.assertLessEqual(len(actual), 9)
                page_controls = [
                    control for control in pagination.controls if control.kind == "page"
                ]
                self.assertEqual(
                    [control.page_number for control in page_controls],
                    sorted({control.page_number for control in page_controls}),
                )
                self.assertEqual(sum(control.is_current for control in page_controls), 1)
                self.assertTrue(
                    all(
                        control.page_number is not None
                        and control.page_number <= pagination.page_count
                        for control in page_controls
                    )
                )

    def test_a_short_catalogue_offers_every_page_and_elides_nothing(self) -> None:
        pagination = build_public_pagination(
            tuple(range(55)),
            page_number=2,
            clean_base_path="/blog",
            catalogue_label="Article pages",
        )
        self.assertEqual(
            tuple((control.kind, control.page_number) for control in pagination.controls),
            (("page", 1), ("page", 2), ("page", 3)),
        )
        self.assertEqual(pagination.controls[0].url, "/blog")

    def test_adjacent_relationships_and_page_one_urls_are_exact(self) -> None:
        middle = build_public_pagination(
            tuple(range(240)),
            page_number=2,
            clean_base_path="/books",
            catalogue_label="Book archive pages",
        )
        self.assertEqual(middle.previous_page_number, 1)
        self.assertEqual(middle.previous_url, "/books")
        self.assertEqual(middle.next_page_number, 3)
        self.assertEqual(middle.next_url, "/books?page=3")

    def test_missing_pages_and_unrepresentable_catalogues_fail_closed(self) -> None:
        with self.assertRaises(PaginationPageNotFound):
            build_public_pagination(
                (),
                page_number=2,
                clean_base_path="/wiki",
                catalogue_label="Wiki catalogue pages",
            )
        with self.assertRaises(ValueError):
            build_public_pagination(
                range(PUBLIC_PAGE_SIZE * PUBLIC_MAX_PAGE + 1),
                page_number=1,
                clean_base_path="/books",
                catalogue_label="Book archive pages",
            )
        with self.assertRaises(ValueError):
            build_public_pagination(
                (),
                page_number=1,
                clean_base_path="/books",
                catalogue_label="Book archive pages",
                page_size=10,
            )
        with self.assertRaises(ValueError):
            build_public_pagination(
                (),
                page_number=1,
                clean_base_path="/books",
                catalogue_label="   ",
            )

    def test_unsafe_request_is_rejected_before_sequence_access(self) -> None:
        class UnreadableSequence:
            def __len__(self) -> int:
                raise AssertionError("sequence was read")

            def __getitem__(self, index):
                raise AssertionError(f"sequence was read at {index}")

        request = RequestFactory().post("/books")
        response: PublicPagination[object] | HttpResponse = paginate_public_request(
            request,
            UnreadableSequence(),  # type: ignore[arg-type]
            clean_base_path="/books",
            catalogue_label="Book archive pages",
        )
        self.assertIsInstance(response, HttpResponse)
        assert isinstance(response, HttpResponse)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["Allow"], "GET, HEAD")
        self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")


@override_settings(ROOT_URLCONF=FIXTURE_URLCONF, APPEND_SLASH=False, NOINDEX=False)
class PublicPaginationRouteContractTests(TestCase):
    """The route contract, through a synthetic catalogue on the reviewed base paths."""

    def test_page_one_spellings_share_clean_canonical_and_never_emit_page_one(self) -> None:
        for path in ("/books", "/books?page=1"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    '<link rel="canonical" href="https://datatalks.club/books">',
                    count=1,
                )
                self.assertContains(
                    response,
                    '<meta property="og:url" content="https://datatalks.club/books">',
                    count=1,
                )
                self.assertNotContains(response, '?page=1"')
                self.assertEqual(response.context["seo_title"], "Books — DataTalks.Club")

    def test_middle_page_has_exact_seo_relations_and_accessible_controls(self) -> None:
        response = self.client.get("/books?page=6")
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club/books?page=6">',
            count=1,
        )
        self.assertContains(
            response,
            '<meta property="og:url" content="https://datatalks.club/books?page=6">',
            count=1,
        )
        self.assertContains(
            response,
            '<link rel="prev" href="https://datatalks.club/books?page=5">',
            count=1,
        )
        self.assertContains(
            response,
            '<link rel="next" href="https://datatalks.club/books?page=7">',
            count=1,
        )
        self.assertEqual(response.context["seo_title"], "Books — Page 6 — DataTalks.Club")
        body = response.content.decode()
        self.assertEqual(body.count('<nav class="pagination"'), 1)
        self.assertEqual(body.count('aria-label="Book archive pages"'), 1)
        markup = pagination_markup(body)
        self.assertEqual(markup.count('aria-current="page"'), 1)
        # Two elided ranges and two arrows, and none of the four is announced.
        self.assertEqual(markup.count('<span class="status-pill pagination-gap"'), 2)
        self.assertEqual(markup.count('aria-hidden="true"'), 4)
        self.assertIn('aria-label="Previous page — page 5"', markup)
        self.assertIn('aria-label="Next page — page 7"', markup)
        self.assertIn('aria-label="Page 6, current page"', markup)
        self.assertIn('aria-label="Go to page 1"', markup)
        # Each direction is the glyph, hidden from assistive technology, with the
        # direction and its destination carried by the control's accessible name.
        self.assertIn('<span aria-hidden="true">&larr;</span>', markup)
        self.assertIn('<span aria-hidden="true">&rarr;</span>', markup)
        # The numbers are the shared drawn mark; the directions keep the stadium.
        # Page 6 of 12 offers 1, 4-8 and 12: seven numbers plus the two directions.
        self.assertEqual(markup.count('class="filter-pill pagination-number"'), 7)
        self.assertEqual(markup.count('class="filter-pill pagination-step"'), 2)

    def test_the_boundaries_omit_the_direction_that_leads_nowhere(self) -> None:
        first = pagination_markup(self.client.get("/books").content.decode())
        last = pagination_markup(self.client.get("/books?page=12").content.decode())
        second = pagination_markup(self.client.get("/books?page=2").content.decode())

        self.assertNotIn('rel="prev"', first)
        self.assertIn('rel="next"', first)
        self.assertIn('rel="prev"', last)
        self.assertNotIn('rel="next"', last)
        self.assertNotIn("Previous page", first)
        self.assertNotIn("Next page", last)
        # No control reaches past the real last page.
        self.assertNotIn("?page=13", last)
        # The walk back to the first page is the clean path, never a page-one query.
        self.assertIn('href="/books"', second)
        self.assertNotIn("?page=1&", second)
        self.assertNotIn('?page=1"', second)

    def test_empty_and_single_page_catalogues_have_no_pagination_landmark(self) -> None:
        for path, label in (
            ("/wiki", "Wiki catalogue pages"),
            ("/events/past", "Past event pages"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, '<nav class="pagination"')
                self.assertNotContains(response, f'aria-label="{label}"')

        self.assertContains(self.client.get("/wiki"), "No fixture items.")
        missing = self.client.get("/wiki?page=2")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.headers["Cache-Control"], "no-store, max-age=0")
        self.assertContains(missing, "Page not found", status_code=404)

    def test_malformed_queries_are_bounded_non_reflective_no_store_errors(self) -> None:
        malformed = (
            "page=",
            "page=0",
            "page=01",
            "page=%31",
            "page=1&page=2",
            "page=1&private=synthetic-canary",
            "page=1000",
            "sort=title",
        )
        for query in malformed:
            with self.subTest(query=query):
                response = self.client.get(f"/books?{query}")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
                self.assertContains(response, "Bad request", status_code=400)
                self.assertLess(len(response.content), 200_000)
                self.assertNotContains(response, query, status_code=400)
                self.assertNotContains(response, "synthetic-canary", status_code=400)
                self.assertNotContains(response, 'rel="canonical"', status_code=400)

    def test_valid_out_of_range_and_unsafe_methods_are_bounded(self) -> None:
        missing = self.client.get("/books?page=13")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.headers["Cache-Control"], "no-store, max-age=0")
        self.assertNotContains(missing, 'rel="canonical"', status_code=404)

        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)("/books?page=6")
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.headers["Allow"], "GET, HEAD")
                self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")

    def test_head_matches_get_status_metadata_and_cache_with_empty_bodies(self) -> None:
        for path in ("/books", "/books?page=6", "/books?page=13", "/books?page=0"):
            with self.subTest(path=path):
                get_response = self.client.get(path)
                head_response = self.client.head(path)
                self.assertEqual(head_response.status_code, get_response.status_code)
                self.assertEqual(
                    head_response.headers.get("Cache-Control"),
                    get_response.headers.get("Cache-Control"),
                )
                self.assertEqual(
                    head_response.headers.get("Allow"), get_response.headers.get("Allow")
                )
                self.assertEqual(head_response.content, b"")

    def test_one_include_serves_distinct_callers_without_a_collection_branch(self) -> None:
        """Two catalogues, two labels, two base paths, one component."""

        books = pagination_markup(self.client.get("/books?page=2").content.decode())
        blog = pagination_markup(self.client.get("/blog?page=2").content.decode())

        self.assertIn('aria-label="Book archive pages"', books)
        self.assertIn('aria-label="Article pages"', blog)
        self.assertIn('href="/books?page=3"', books)
        self.assertIn('href="/blog?page=3"', blog)
        self.assertNotIn("/blog", books)
        self.assertNotIn("/books", blog)
        # The blog fixture is three pages, so its window elides nothing.
        self.assertNotIn("pagination-gap", blog)


class PublicPaginationSeoAndRepositoryTests(SimpleTestCase):
    def test_canonical_registry_allows_only_reviewed_exact_page_selectors(self) -> None:
        for base_path in sorted(PUBLIC_PAGINATION_PATHS):
            for page_number in (1, 2, 999):
                canonical = f"https://datatalks.club{base_path}?page={page_number}"
                with self.subTest(canonical=canonical):
                    self.assertEqual(validated_canonical_url(canonical), canonical)

        rejected = (
            "https://datatalks.club/podcast?page=2",
            "https://datatalks.club/books?page=0",
            "https://datatalks.club/books?page=01",
            "https://datatalks.club/books?page=1000",
            "https://datatalks.club/books?page=2&sort=title",
            "https://datatalks.club/wiki?sort=title&page=2",
            "https://datatalks.club/events?page=2",
        )
        for canonical in rejected:
            with self.subTest(canonical=canonical):
                self.assertEqual(validated_canonical_url(canonical), "")

    def test_the_canonical_registry_and_the_paginator_agree_on_the_catalogues(self) -> None:
        from core import seo

        self.assertEqual(seo._PUBLIC_PAGINATION_PATHS, PUBLIC_PAGINATION_PATHS)

    def test_sitemaps_contain_only_clean_hubs_and_no_page_queries(self) -> None:
        request = RequestFactory().get("/sitemap.xml")
        sections = (
            ("blog", "/blog"),
            ("books", "/books"),
            ("wiki", "/wiki"),
            ("events", "/events"),
        )
        for section, clean_hub in sections:
            with self.subTest(section=section):
                sitemap = section_sitemap(request, section=section).content.decode()
                self.assertNotIn("?page=", sitemap)
                self.assertIn(f"https://datatalks.club{clean_hub}</loc>", sitemap)

    def test_one_collection_neutral_include_owns_public_page_controls(self) -> None:
        pagination_templates = sorted(
            path.name for path in (REPOSITORY_ROOT / "templates/public").glob("_*pagination*.html")
        )
        self.assertEqual(pagination_templates, ["_pagination.html"])
        source = (REPOSITORY_ROOT / "templates/public/_pagination.html").read_text(encoding="utf-8")
        markup = source.split("{% endcomment %}", 1)[1]
        self.assertNotRegex(markup.casefold(), r"\b(?:books?|wiki|events?|blog|articles?)\b")
        self.assertNotIn("?page=", markup)

    def test_the_public_and_adopted_paginators_wear_the_same_design_system_mark(self) -> None:
        """One visual language: both rows are the same `.pagination` component.

        The two templates stay separate because their behaviour differs — the public
        one accepts a single query spelling, carries no unrelated parameter and omits
        a direction that leads nowhere, while the adopted one preserves its callers'
        filter querystring and marks an unavailable direction quietly — but a reader
        must not be able to tell two paginators apart by looking at them.
        """

        public = (REPOSITORY_ROOT / "templates/public/_pagination.html").read_text(encoding="utf-8")
        adopted = (REPOSITORY_ROOT / "course_platform_templates/include/pagination.html").read_text(
            encoding="utf-8"
        )
        design_system = (REPOSITORY_ROOT / "templates/core/_design_system.html").read_text(
            encoding="utf-8"
        )

        for source in (public, adopted):
            self.assertIn("filter-pills pagination-pills", source)
            self.assertIn("filter-pill pagination-number", source)
            self.assertIn("pagination-step", source)
            self.assertIn("pagination-gap", source)
            self.assertIn('aria-current="page"', source)
            # Both directions are the arrow, and neither arrow is announced.
            self.assertIn('<span aria-hidden="true">&larr;</span>', source)
            self.assertIn('<span aria-hidden="true">&rarr;</span>', source)
            self.assertIn('aria-label="Previous page', source)
            self.assertIn('aria-label="Next page', source)
        for rule in (
            ".pagination {",
            ".pagination-pills {",
            ".pagination-number {",
            ".pagination-step {",
            ".pagination-gap {",
        ):
            self.assertIn(rule, design_system)
        # The drawn mark is the system's token, not a fourth wobble of its own, and
        # it rotates so three identical circles never sit side by side.
        self.assertIn("border-radius: var(--drawn-circle);", design_system)
        for variant in ("--drawn-circle-b", "--drawn-circle-c"):
            self.assertIn(f"border-radius: var({variant});", design_system)
        pagination_rules = design_system.split(".pagination {", 1)[1].split(".status-dot {", 1)[0]
        self.assertNotRegex(pagination_rules, r"\d+%\s+\d+%\s+\d+%\s+\d+%")

    def test_public_sources_reject_bespoke_page_urls_templates_and_css(self) -> None:
        """No fifth catalogue may assemble a page URL or style a paginator of its own.

        Prose is exempt — a comment is allowed to name the spelling it is explaining —
        so Python comment lines and template comment blocks are stripped before the
        scan, and only code and markup are read.
        """

        violations: list[str] = []
        for path in sorted((REPOSITORY_ROOT / "content").glob("*.py")):
            if path.name == "pagination.py":
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "?page=" in line and not line.strip().startswith("#"):
                    violations.append(f"{path.relative_to(REPOSITORY_ROOT)}:{line_number}")

        for path in sorted((REPOSITORY_ROOT / "templates/public").glob("*.html")):
            markup = TEMPLATE_COMMENT.sub("", path.read_text(encoding="utf-8"))
            if "?page=" in markup:
                violations.append(str(path.relative_to(REPOSITORY_ROOT)))

        design_system = (REPOSITORY_ROOT / "templates/core/_design_system.html").read_text(
            encoding="utf-8"
        )
        violations.extend(
            re.findall(
                r"\.(?:books?|blog|wiki|events?|article)-pagination[-_a-z0-9]*",
                design_system,
                re.IGNORECASE,
            )
        )
        self.assertEqual(violations, [])
