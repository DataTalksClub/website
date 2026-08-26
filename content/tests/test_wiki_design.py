"""The wiki surfaces on design 5a (issue #179).

The five wiki pages left the adopted shell for the shared design system.  A rebuild
is where a page silently loses a link, a state or a piece of copy, so these tests
pin what each surface must still offer as well as the design contract every page in
the system carries: one inline stylesheet, no legacy CSS, no leaked template syntax.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from content.pagination import PUBLIC_PAGE_SIZE
from content.public_data import public_projection
from content.public_views import WIKI_SPECIAL_CATEGORIES, _wiki_search_results
from content.wiki_content import (
    GRAPH_GROUPS,
    GROUP_ENTRY_POINTS,
    busiest_neighbourhood,
    graph_groups,
    graph_nodes,
    graph_totals,
    node_connections,
)

from .pagination_support import catalogue_page_bodies

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RETIRED_ASSETS = (
    "/static/courses.css",
    "/static/core/site_shell.css",
    "/static/core/accessibility.css",
    "tailwindcss",
    "fontawesome",
)
TEMPLATE_SYNTAX = ("{#", "#}", "{%", "%}", "{{", "}}")


def wiki_paths() -> dict[str, str]:
    page = public_projection()["wiki"][0]
    return {
        "hub": reverse("wiki-home"),
        "results": f"{reverse('wiki-home')}?q=machine+learning",
        "no results": f"{reverse('wiki-home')}?q=no-such-public-topic",
        "graph": reverse("wiki-graph"),
        "special pages": reverse("wiki-special"),
        "special category": reverse("wiki-special-category", kwargs={"category": "guides"}),
        "wiki page": str(page["public_path"]),
    }


class WikiDesignSystemTests(TestCase):
    """Every wiki surface is a page of the design 5a system."""

    def bodies(self) -> dict[str, str]:
        bodies = {}
        for name, path in wiki_paths().items():
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, name)
            bodies[name] = response.content.decode()
        return bodies

    def test_every_wiki_page_carries_one_inline_stylesheet_and_no_legacy_css(self) -> None:
        for name, body in self.bodies().items():
            with self.subTest(page=name):
                self.assertEqual(body.count("<style>"), 1)
                # The shared design system partial, not a fork of it.
                self.assertIn("--graph-edge:", body)
                self.assertEqual(re.findall(r'<link[^>]+rel="stylesheet"', body), [])
                for retired in RETIRED_ASSETS:
                    self.assertNotIn(retired, body, retired)

    def test_no_wiki_page_leaks_unrendered_template_syntax(self) -> None:
        for name, body in self.bodies().items():
            with self.subTest(page=name):
                for token in TEMPLATE_SYNTAX:
                    self.assertNotIn(token, body, token)

    def test_every_wiki_page_below_the_hub_offers_the_trail_back_to_it(self) -> None:
        """A wiki page, the graph, search and the special pages all sit under /wiki."""

        bodies = self.bodies()
        for name in ("results", "no results", "graph", "special pages", "wiki page"):
            with self.subTest(page=name):
                body = bodies[name]
                self.assertRegex(
                    body, r'<nav class="shell[^"]*\bbreadcrumbs\b[^"]*" aria-label="Breadcrumb">'
                )
                self.assertIn(f'<a href="{reverse("wiki-home")}">Wiki</a>', body)
                self.assertIn('aria-current="page"', body)

    def test_the_hub_is_an_index_and_carries_no_breadcrumb_trail(self) -> None:
        """An index is marked by the masthead; a lone "Wiki" crumb would repeat it."""

        self.assertNotRegex(self.bodies()["hub"], r'<nav class="shell[^"]*\bbreadcrumbs\b')

    def test_every_wiki_page_keeps_its_social_card_and_canonical(self) -> None:
        og_image = "https://datatalks.club/wiki/assets/og-default.png"
        for name, body in self.bodies().items():
            with self.subTest(page=name):
                self.assertIn(f'<meta property="og:image" content="{og_image}">', body)
                self.assertIn(f'<meta name="twitter:image" content="{og_image}">', body)
                self.assertIn('<meta name="twitter:card" content="summary_large_image">', body)
                self.assertIn('<link rel="canonical"', body)


class WikiHubTests(TestCase):
    def setUp(self) -> None:
        self.body = self.client.get(reverse("wiki-home")).content.decode()

    def test_the_hub_keeps_its_title_search_field_and_catalogue_count(self) -> None:
        records = public_projection()["wiki"]

        self.assertIn("DataTalks.Club Podcast Wiki", self.body)
        self.assertIn('<label class="sr-only" for="wiki-query">Search the Wiki</label>', self.body)
        self.assertIn('name="q"', self.body)
        self.assertIn(f"Browse full catalog ({len(records)} topics A&ndash;Z).", self.body)
        # The catalogue pages (issue #175), so it also says which part of that whole
        # inventory is on screen.
        self.assertIn(f"Showing 1&ndash;{PUBLIC_PAGE_SIZE}.", self.body)

    def test_the_hub_keeps_all_three_ways_into_the_wiki(self) -> None:
        """The exploration links are the graph's and the special pages' only entry."""

        self.assertIn('aria-label="Wiki exploration"', self.body)
        self.assertIn(
            '<form class="search-row wiki-search" action="/wiki" method="get">',
            self.body,
        )
        explore = self.body.split('aria-label="Wiki exploration"', 1)[1].split("</nav>", 1)[0]
        for href, label in (
            (reverse("wiki-graph"), "Graph"),
            (f"{reverse('wiki-home')}?q=", "Search"),
            (reverse("wiki-special"), "Special pages"),
        ):
            with self.subTest(href=href):
                self.assertIn(f'href="{href}"', explore)
                self.assertIn(f"<strong>{label}</strong>", explore)
        # Search mode is the empty existing `?q=` query, never the editorial slug.
        self.assertNotIn('href="/wiki/search"', explore)
        self.assertNotIn("?q=data", explore)

    def test_the_catalogue_lists_every_topic_twice_over_as_title_and_read_action(self) -> None:
        """Across its pages the catalogue still carries every topic, exactly once.

        The catalogue pages (issue #175), so this walks it the way a reader does and
        then checks the whole thing: the pages must partition the projection in its
        existing order, with no topic repeated between two pages and none lost.
        """

        records = public_projection()["wiki"]
        self.assertEqual(len(records), 282)
        self.assertEqual(
            [(record["title"], record["slug"]) for record in records],
            sorted(
                ((record["title"], record["slug"]) for record in records),
                key=lambda item: (item[0].casefold(), item[1]),
            ),
        )
        pages = catalogue_page_bodies(self.client, reverse("wiki-home"))
        body = "".join(pages)
        expected_page_count = (len(records) + PUBLIC_PAGE_SIZE - 1) // PUBLIC_PAGE_SIZE
        self.assertEqual(len(pages), expected_page_count)

        collected: list[dict] = []
        for page_number in range(1, expected_page_count + 1):
            path = (
                reverse("wiki-home")
                if page_number == 1
                else f"{reverse('wiki-home')}?page={page_number}"
            )
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            page_records = list(response.context["records"])
            expected_size = (
                PUBLIC_PAGE_SIZE
                if page_number < expected_page_count
                else len(records) - PUBLIC_PAGE_SIZE * (expected_page_count - 1)
            )
            self.assertEqual(len(page_records), expected_size)
            collected.extend(page_records)

        self.assertEqual(
            [(record["title"], record["summary"], record["public_path"]) for record in collected],
            [(record["title"], record["summary"], record["public_path"]) for record in records],
        )
        for record in records:
            with self.subTest(slug=record["slug"]):
                self.assertEqual(body.count(f'href="{record["public_path"]}"'), 2)
                self.assertIn(escape(str(record["title"])), body)
                self.assertIn(escape(str(record["summary"])), body)
        self.assertEqual(body.count("Read topic"), len(records))
        # The first page holds the first slice of the existing order, and the last
        # page holds the last, so the pages are cut from the catalogue rather than
        # re-sorted per page.
        self.assertIn(f'href="{records[0]["public_path"]}"', pages[0])
        self.assertIn(f'href="{records[-1]["public_path"]}"', pages[-1])
        for page_body in pages[:-1]:
            self.assertEqual(page_body.count("Read topic"), PUBLIC_PAGE_SIZE)

    def test_the_catalogue_rows_are_divided_rows_and_never_a_column_grid(self) -> None:
        self.assertIn('<div class="row-list">', self.body)
        self.assertNotRegex(self.body, r"(?:sm|md|lg):grid-cols-")


class WikiCataloguePaginationTests(TestCase):
    """The A–Z catalogue is paged; the other three ways in are not (issue #175)."""

    def test_the_landing_page_offers_all_four_paths_on_every_catalogue_page(self) -> None:
        for path in (reverse("wiki-home"), f"{reverse('wiki-home')}?page=2"):
            with self.subTest(path=path):
                body = self.client.get(path).content.decode()
                explore = body.split('aria-label="Wiki exploration"', 1)[1].split("</nav>", 1)[0]
                self.assertIn('<h1 id="wiki-heading">DataTalks.Club Podcast Wiki</h1>', body)
                self.assertIn('name="q"', body)
                self.assertIn('aria-label="Wiki exploration"', body)
                self.assertIn(f'href="{reverse("wiki-graph")}"', explore)
                self.assertIn(f'href="{reverse("wiki-home")}?q="', explore)
                self.assertIn(f'href="{reverse("wiki-special")}"', explore)
                self.assertNotIn('href="/wiki/search"', explore)
                self.assertIn('<h2 id="catalog-heading">Wiki pages</h2>', body)
                self.assertIn('aria-label="Wiki catalogue pages"', body)

    def test_page_one_is_the_clean_path_and_later_pages_name_themselves(self) -> None:
        for spelling in (reverse("wiki-home"), f"{reverse('wiki-home')}?page=1"):
            with self.subTest(spelling=spelling):
                body = self.client.get(spelling).content.decode()
                self.assertIn("<title>Podcast Wiki — DataTalks.Club</title>", body)
                self.assertIn('<link rel="canonical" href="https://datatalks.club/wiki">', body)
                self.assertIn(
                    '<meta property="og:url" content="https://datatalks.club/wiki">', body
                )
                self.assertNotIn('?page=1"', body)

        body = self.client.get(f"{reverse('wiki-home')}?page=3").content.decode()
        self.assertIn("<title>Podcast Wiki — Page 3 — DataTalks.Club</title>", body)
        self.assertIn('<link rel="canonical" href="https://datatalks.club/wiki?page=3">', body)
        self.assertIn('<meta property="og:url" content="https://datatalks.club/wiki?page=3">', body)
        self.assertIn('<link rel="prev" href="https://datatalks.club/wiki?page=2">', body)
        self.assertIn('<link rel="next" href="https://datatalks.club/wiki?page=4">', body)
        # A catalogue page is still a wiki surface, so it keeps the wiki social card.
        self.assertIn(
            '<meta property="og:image" content="https://datatalks.club/wiki/assets/og-default.png">',
            body,
        )

    def test_a_search_never_reaches_the_paginator_and_is_never_paged(self) -> None:
        for query in ("q=machine+learning", "q=", "q=data&page=2"):
            with self.subTest(query=query):
                response = self.client.get(f"{reverse('wiki-home')}?{query}")
                self.assertEqual(response.status_code, 200)
                body = response.content.decode()
                # `q` is the mode switch, so what every search response shares is
                # the search surface itself.  An empty `q=` is still a search: it
                # renders the form and no results band, because "0 results for
                # nothing" is a sentence about a search the visitor did not make.
                self.assertIn('id="search-heading"', body)
                if query != "q=":
                    self.assertIn("results for", body)
                self.assertNotIn('<nav class="pagination"', body)
                self.assertIn('<link rel="canonical" href="https://datatalks.club/wiki">', body)

    def test_the_catalogue_selector_accepts_one_spelling_and_fails_closed(self) -> None:
        for bad_query in (
            "page=",
            "page=0",
            "page=+2",
            "page=-2",
            "page=01",
            "page=%32",
            "page=١",
            "page=1000",
            "page=2&page=3",
        ):
            with self.subTest(bad_query=bad_query):
                response = self.client.get(f"{reverse('wiki-home')}?{bad_query}")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.headers["Cache-Control"], "no-store, max-age=0")
                self.assertNotContains(response, bad_query, status_code=400)

        # A parameter the catalogue does not select on is ignored rather than
        # refused, so a campaign-tagged link still reaches the page it names.
        tagged = self.client.get(f"{reverse('wiki-home')}?page=2&sort=title")
        self.assertEqual(tagged.status_code, 200)
        self.assertNotContains(tagged, "sort=title")

        beyond = self.client.get(f"{reverse('wiki-home')}?page=999")
        self.assertEqual(beyond.status_code, 404)
        self.assertEqual(beyond.headers["Cache-Control"], "no-store, max-age=0")
        self.assertNotIn("Location", beyond.headers)

        rejected = self.client.post(reverse("wiki-home"))
        self.assertEqual(rejected.status_code, 405)
        self.assertEqual(rejected.headers["Allow"], "GET, HEAD")
        self.assertEqual(rejected.headers["Cache-Control"], "no-store, max-age=0")

    def test_head_matches_get_status_metadata_and_empty_bodies(self) -> None:
        for path in (
            reverse("wiki-home"),
            f"{reverse('wiki-home')}?page=1",
            f"{reverse('wiki-home')}?page=2",
            f"{reverse('wiki-home')}?page=0",
            f"{reverse('wiki-home')}?page=999",
        ):
            with self.subTest(path=path):
                get_response = self.client.get(path)
                head_response = self.client.head(path)
                self.assertEqual(head_response.status_code, get_response.status_code)
                self.assertEqual(
                    head_response.headers.get("Cache-Control"),
                    get_response.headers.get("Cache-Control"),
                )
                self.assertEqual(
                    head_response.headers.get("Allow"),
                    get_response.headers.get("Allow"),
                )
                self.assertEqual(head_response.content, b"")

    def test_the_slash_alias_still_forwards_its_raw_query_in_one_hop(self) -> None:
        response = self.client.get("/wiki/?page=2", follow=False)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "/wiki?page=2")

    def test_an_empty_catalogue_says_so_and_leaves_the_other_paths_useful(self) -> None:
        projection = dict(public_projection())
        projection["wiki"] = ()
        with patch("content.public_views.public_projection", return_value=projection):
            response = self.client.get(reverse("wiki-home"))
            body = response.content.decode()
            beyond = self.client.get(f"{reverse('wiki-home')}?page=2")

        self.assertEqual(response.status_code, 200)
        self.assertIn("No Wiki pages are available yet.", body)
        self.assertNotIn('<nav class="pagination"', body)
        self.assertIn('aria-label="Wiki exploration"', body)
        self.assertEqual(beyond.status_code, 404)

    def test_the_wiki_sitemap_lists_the_clean_hub_and_no_page_query(self) -> None:
        sitemap = self.client.get("/wiki/sitemap.xml").content.decode()

        self.assertIn("<loc>https://datatalks.club/wiki</loc>", sitemap)
        self.assertNotIn("?page=", sitemap)

    def test_the_hub_uses_the_shared_paginator_include_and_no_wiki_only_control(self) -> None:
        source = (REPOSITORY_ROOT / "templates/public/wiki_hub.html").read_text(encoding="utf-8")
        markup = re.sub(
            r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}",
            "",
            source,
            flags=re.DOTALL,
        )

        self.assertIn('include "public/_pagination.html"', markup)
        self.assertNotIn("?page=", markup)
        self.assertFalse((REPOSITORY_ROOT / "templates/public/_wiki_pagination.html").exists())


class WikiSearchTests(TestCase):
    def test_a_query_reports_its_own_count_and_lists_each_hit_with_its_kind(self) -> None:
        response = self.client.get(reverse("wiki-home"), {"q": "machine learning"})
        body = response.content.decode()
        results = response.context["results"]

        self.assertTrue(results)
        self.assertIn(f"{len(results)} results for &ldquo;machine learning&rdquo;", body)
        for result in results[:5]:
            with self.subTest(url=result["url"]):
                self.assertIn(f'<a href="{result["url"]}">', body)
                self.assertIn(
                    f'<span class="status-pill result-kind">{result["document_type"]}</span>',
                    body,
                )
                if result["segment_title"]:
                    self.assertIn(escape(str(result["segment_title"])), body)
        special_segment = next(
            result
            for result in results
            if result["segment_title"] and "&" in str(result["segment_title"])
        )
        self.assertNotEqual(
            escape(str(special_segment["segment_title"])),
            str(special_segment["segment_title"]),
        )
        self.assertIn(escape(str(special_segment["segment_title"])), body)

    def test_a_query_with_no_hits_keeps_its_zero_count_and_its_empty_state(self) -> None:
        body = self.client.get(reverse("wiki-home"), {"q": "no-such-public-topic"}).content.decode()

        self.assertIn("0 results for &ldquo;no-such-public-topic&rdquo;", body)
        self.assertIn("No matching Wiki pages were found. Try a broader term.", body)

    def test_the_search_field_returns_the_term_already_searched_for(self) -> None:
        body = self.client.get(reverse("wiki-home"), {"q": "machine learning"}).content.decode()

        self.assertIn('<label class="sr-only" for="wiki-query">Search term</label>', body)
        self.assertIn('value="machine learning"', body)

    def test_an_unqueried_hub_renders_the_catalogue_and_not_an_empty_result_list(self) -> None:
        body = self.client.get(reverse("wiki-home")).content.decode()

        self.assertNotIn("results for", body)
        self.assertNotIn("No matching Wiki pages were found.", body)

    def test_the_editorial_search_slug_stays_a_wiki_page(self) -> None:
        response = self.client.get("/wiki/search")
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "public/wiki_detail.html")
        self.assertIn("<title>Search — DataTalks.Club Wiki</title>", body)
        self.assertIn(
            "Search as the product system that turns retrieval, ranking, answers",
            body,
        )
        self.assertNotIn("results for", body)
        self.assertNotIn('<nav class="pagination"', body)

    def test_search_still_trims_bounds_and_matches_every_term(self) -> None:
        trimmed = self.client.get(reverse("wiki-home"), {"q": "  volunteer nonprofit  "})
        plain = self.client.get(reverse("wiki-home"), {"q": "volunteer nonprofit"})
        volunteer = self.client.get(reverse("wiki-home"), {"q": "volunteer"})

        self.assertEqual(trimmed.context["query"], "volunteer nonprofit")
        self.assertEqual(
            [result["url"] for result in trimmed.context["results"]],
            [result["url"] for result in plain.context["results"]],
        )
        self.assertTrue(plain.context["results"])
        self.assertLess(len(plain.context["results"]), len(volunteer.context["results"]))
        self.assertTrue(
            {result["url"] for result in plain.context["results"]}.issubset(
                {result["url"] for result in volunteer.context["results"]}
            )
        )
        for result in plain.context["results"]:
            haystack = " ".join(
                str(result.get(field, ""))
                for field in ("title", "page_title", "segment_title", "text", "related_terms")
            ).casefold()
            self.assertIn("volunteer", haystack)
            self.assertIn("nonprofit", haystack)

        long_query = "x" * 250
        bounded = self.client.get(reverse("wiki-home"), {"q": long_query})
        self.assertEqual(bounded.context["query"], "x" * 200)
        self.assertEqual(len(bounded.context["query"]), 200)

    def test_search_still_caps_results_at_one_hundred(self) -> None:
        results = _wiki_search_results("data")
        response = self.client.get(reverse("wiki-home"), {"q": "data"})

        self.assertEqual(len(results), 100)
        self.assertEqual(len(response.context["results"]), 100)
        self.assertIn("100 results for &ldquo;data&rdquo;", response.content.decode())
        self.assertNotIn('<nav class="pagination"', response.content.decode())

    def test_accepted_companion_search_parameters_do_not_page_or_leave_search(self) -> None:
        response = self.client.get("/wiki?q=machine+learning&document_type=page")
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("results for", body)
        self.assertNotIn('<nav class="pagination"', body)
        self.assertIn('<link rel="canonical" href="https://datatalks.club/wiki">', body)


class WikiGraphTests(TestCase):
    def setUp(self) -> None:
        self.body = self.client.get(reverse("wiki-graph")).content.decode()

    def test_the_graph_page_keeps_every_node_it_used_to_draw(self) -> None:
        nodes = graph_nodes()

        self.assertEqual(sum(group.count for group in graph_groups()), len(nodes))
        for node in nodes:
            identifier = str(node["id"])
            with self.subTest(node=identifier):
                self.assertIn(f'id="{identifier}"', self.body)
        linked = [node for node in nodes if node["url"]]
        self.assertEqual(
            len(re.findall(r'<a class="graph-node" id="', self.body)),
            len(linked),
        )

    def test_a_node_without_a_destination_is_named_but_never_a_link(self) -> None:
        unlinked = [node for node in graph_nodes() if not node["url"]]

        self.assertTrue(unlinked, "the graph no longer carries an unresolved node")
        for node in unlinked:
            with self.subTest(node=node["id"]):
                self.assertIn(f'<span class="graph-node" id="{node["id"]}">', self.body)

    def test_the_graph_page_states_its_own_totals_and_group_counts(self) -> None:
        for total in graph_totals():
            with self.subTest(label=total.label):
                self.assertIn(f"<strong>{total.value}</strong>", self.body)
                self.assertIn(total.label, self.body)
        for group in graph_groups():
            with self.subTest(group=group.key):
                self.assertIn(f'<h2 id="{group.heading_id}">{escape(group.title)}</h2>', self.body)
                self.assertIn(f"{group.count} in the graph", self.body)

    def test_the_drawn_neighbourhood_is_read_from_the_graph_and_never_invented(self) -> None:
        neighbourhood = busiest_neighbourhood()

        self.assertIn(
            f"{neighbourhood.connections} connections from {escape(neighbourhood.title)}",
            self.body,
        )
        for layout in neighbourhood.layouts:
            with self.subTest(layout=layout.kind):
                svg = self.rendered_layout(layout.kind)
                self.assertIn(
                    f'<a class="graph-svg-node graph-svg-hub" href="{neighbourhood.url}">', svg
                )
                self.assertEqual(
                    len(re.findall(r'<line class="graph-svg-edge"', svg)),
                    len(neighbourhood.spokes),
                )
                for spoke in neighbourhood.spokes:
                    with self.subTest(spoke=spoke.title):
                        self.assertIn(f'<a class="graph-svg-node" href="{spoke.url}">', svg)

    def rendered_layout(self, kind: str) -> str:
        found = re.search(
            rf'<svg\s+class="graph-svg graph-svg-{kind}".*?</svg>', self.body, re.DOTALL
        )
        if found is None:
            self.fail(f"the graph page renders no {kind} drawing")
        return found.group(0)

    def test_the_drawing_is_one_scalable_svg_per_width_and_names_itself(self) -> None:
        """The whole drawing shares one viewBox, so no label can escape its node."""

        neighbourhood = busiest_neighbourhood()

        for layout in neighbourhood.layouts:
            with self.subTest(layout=layout.kind):
                svg = self.rendered_layout(layout.kind)
                self.assertIn(f'viewBox="0 0 {layout.width} {layout.height}"', svg)
                self.assertIn('role="group"', svg)
                title = escape(neighbourhood.title)
                self.assertIn(
                    f'aria-label="{title} and {len(neighbourhood.spokes)} of its '
                    f'{neighbourhood.connections} linked wiki topics"',
                    svg,
                )
                for node in layout.nodes:
                    for line in node.lines:
                        self.assertIn(f">{escape(line.text)}</tspan>", svg)
        # The pills the drawing used to be positioned with are gone: they
        # collided with each other and overflowed their labels below full width.
        self.assertNotIn("graph-plot", self.body)
        self.assertNotIn('style="left:', self.body)

    def test_the_page_holds_the_reading_column_and_never_breaks_out_of_it(self) -> None:
        """Content width, one column: the drawing scales, so it needs no breakout."""

        # The class attribute, not the primitive's own rule in the stylesheet.
        self.assertNotIn('shell-breakout"', self.body)
        self.assertNotIn('graph-grid"', self.body)
        self.assertEqual(
            len(re.findall(r'<div class="shell shell-reading">', self.body)),
            1 + len(GRAPH_GROUPS),
        )

    def test_each_group_leads_with_its_busiest_nodes_and_folds_the_rest_away(self) -> None:
        """The band is a way in, not an inventory: the ways in are what it shows."""

        connections = node_connections()
        for group in graph_groups():
            with self.subTest(group=group.key):
                self.assertEqual(len(group.entries), min(GROUP_ENTRY_POINTS, group.count))
                weakest_entry = min(connections.get(str(node["id"]), 0) for node in group.entries)
                for node in group.rest:
                    self.assertLessEqual(connections.get(str(node["id"]), 0), weakest_entry)
                self.assertIn(
                    f"The other {group.rest_count} {escape(group.title.lower())}, A&ndash;Z",
                    self.body,
                )
        # Every node is still on the page, each one exactly once.
        self.assertEqual(len(re.findall(r'class="graph-node" id="', self.body)), len(graph_nodes()))

    def test_a_group_points_at_the_page_that_indexes_its_kind_in_full(self) -> None:
        for group in graph_groups():
            if group.browse is None:
                continue
            with self.subTest(group=group.key):
                self.assertIn(
                    f'<a class="band-link group-browse" href="{reverse(group.browse.url_name)}">',
                    self.body,
                )
                self.assertIn(group.browse.label, self.body)

    def test_an_unknown_node_type_fails_loudly_instead_of_disappearing(self) -> None:
        graph: dict[str, Any] = {
            "nodes": [{"id": "sculpture:1", "type": "sculpture", "url": "", "title": "x"}]
        }
        with patch("content.wiki_content._graph", return_value=graph):
            with self.assertRaises(ImproperlyConfigured):
                graph_groups()

    def test_a_missing_headline_total_fails_loudly_instead_of_reading_zero(self) -> None:
        with patch("content.wiki_content._graph", return_value={"counts": {"nodes": 3}}):
            with self.assertRaises(ImproperlyConfigured):
                graph_totals()

    def test_the_group_table_covers_every_node_type_the_data_carries(self) -> None:
        declared = {key for key, _title, _description, _browse in GRAPH_GROUPS}

        self.assertEqual({str(node["type"]) for node in graph_nodes()}, declared)


class WikiSpecialPagesTests(TestCase):
    def test_every_category_route_is_offered_and_the_current_one_is_marked(self) -> None:
        body = self.client.get(reverse("wiki-special")).content.decode()

        self.assertIn('aria-label="Special page categories"', body)
        self.assertIn(
            f'<a class="filter-pill" href="{reverse("wiki-special")}" aria-current=', body
        )
        for slug in WIKI_SPECIAL_CATEGORIES:
            with self.subTest(slug=slug):
                path = reverse("wiki-special-category", kwargs={"category": slug})
                self.assertIn(f'href="{path}"', body)

        category = self.client.get(
            reverse("wiki-special-category", kwargs={"category": "guides"})
        ).content.decode()
        self.assertIn(
            '<a class="filter-pill" href="/wiki/special-pages/guides"'
            ' aria-current="page">Guides</a>',
            category,
        )

    def test_a_category_lists_its_pages_with_the_hub_row_shape(self) -> None:
        response = self.client.get(reverse("wiki-special-category", kwargs={"category": "guides"}))
        body = response.content.decode()
        records = response.context["records"]

        self.assertTrue(records)
        self.assertIn(f"{len(records)} pages", body)
        for record in records[:5]:
            with self.subTest(slug=record["slug"]):
                self.assertIn(escape(str(record["title"])), body)
                self.assertIn(escape(str(record["summary"])), body)
                self.assertEqual(body.count(f'href="{record["public_path"]}"'), 2)

    def test_an_empty_category_still_says_so(self) -> None:
        projection = dict(public_projection())
        projection["wiki"] = ()
        with patch("content.public_views.public_projection", return_value=projection):
            body = self.client.get(reverse("wiki-special")).content.decode()

        self.assertIn("No pages found.", body)


class WikiPageTests(TestCase):
    def setUp(self) -> None:
        self.record = public_projection()["wiki_by_slug"]["a-a-testing"]
        self.body = self.client.get(str(self.record["public_path"])).content.decode()

    def test_the_page_keeps_its_title_summary_and_every_relation(self) -> None:
        # No `WIKI` kicker: the breadcrumb directly above already names the
        # section, so a page-level eyebrow would only repeat it.
        self.assertNotIn('<p class="mono-label mono-label-indigo">Wiki</p>', self.body)
        self.assertIn(escape(str(self.record["title"])), self.body)
        self.assertIn(escape(str(self.record["summary"])), self.body)
        self.assertIn('aria-label="Related concepts and sources"', self.body)
        for relation in self.record["relations"]:
            with self.subTest(relation=relation["label"]):
                self.assertIn(f'data-relation-type="{escape(relation["type"])}"', self.body)
                self.assertIn(
                    f'<span class="sr-only">{escape(relation["type"])}: </span>'
                    f"{escape(relation['label'])}",
                    self.body,
                )
                if relation["href"]:
                    self.assertIn(f'href="{relation["href"]}"', self.body)

    def test_a_relation_the_wiki_cannot_follow_stays_a_named_state(self) -> None:
        """No page carries one today; the state must survive until one does."""

        self.assertFalse(
            [
                record["slug"]
                for record in public_projection()["wiki"]
                if any(not relation["href"] for relation in record["relations"])
            ],
            "a wiki page now carries an unlinked relation: assert its rendering here",
        )
        source = (REPOSITORY_ROOT / "templates/public/wiki_detail.html").read_text(encoding="utf-8")
        self.assertIn('class="chip chip-plain relation-plain"', source)
        self.assertIn("{% if relation.href %}", source)

    def test_the_body_renders_every_block_and_keeps_its_anchor_identifiers(self) -> None:
        for block in self.record["blocks"]:
            with self.subTest(kind=block["kind"]):
                text = escape(block["text"])
                if block["kind"] == "heading":
                    self.assertIn(f'<h2 id="{block["id"]}">{text}</h2>', self.body)
                elif block["kind"] == "list_item":
                    self.assertIn(f'<p class="prose-item">{text}</p>', self.body)
                else:
                    self.assertIn(f'<p class="prose-paragraph">{text}</p>', self.body)
        self.assertTrue(
            any(escape(block["text"]) != block["text"] for block in self.record["blocks"])
        )
        self.assertIn('<div class="prose wiki-prose">', self.body)

    def test_an_ampersand_in_a_wiki_title_is_escaped_in_the_heading(self) -> None:
        record = public_projection()["wiki_by_slug"]["data-scientist-cv-and-portfolio"]
        self.assertNotEqual(escape(record["title"]), record["title"])
        body = self.client.get(str(record["public_path"])).content.decode()

        self.assertIn(f'<h1 id="wiki-page-heading">{escape(record["title"])}</h1>', body)

    def test_an_ampersand_in_a_relation_label_is_escaped(self) -> None:
        record = next(
            page
            for page in public_projection()["wiki"]
            if any("&" in relation["label"] for relation in page["relations"])
        )
        relation = next(item for item in record["relations"] if "&" in item["label"])
        self.assertNotEqual(escape(relation["label"]), relation["label"])
        body = self.client.get(str(record["public_path"])).content.decode()

        self.assertIn(
            f'<span class="sr-only">{escape(relation["type"])}: </span>{escape(relation["label"])}',
            body,
        )

    def test_an_anchor_the_source_never_defined_still_lands_on_the_page(self) -> None:
        page = next(
            (record for record in public_projection()["wiki"] if record["unresolved_fragment_ids"]),
            None,
        )
        if page is None:
            self.skipTest("no wiki page currently carries an unresolved anchor")
        body = self.client.get(str(page["public_path"])).content.decode()
        for fragment in page["unresolved_fragment_ids"]:
            with self.subTest(fragment=fragment):
                self.assertIn(f'<span id="{fragment}"></span>', body)
