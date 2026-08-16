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

from content.public_data import public_projection
from content.public_views import WIKI_SPECIAL_CATEGORIES
from content.wiki_content import (
    GRAPH_GROUPS,
    busiest_neighbourhood,
    graph_groups,
    graph_nodes,
    graph_totals,
)

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
                    body, r'<nav class="shell breadcrumbs[^"]*" aria-label="Breadcrumb">'
                )
                self.assertIn(f'<a href="{reverse("wiki-home")}">Wiki</a>', body)
                self.assertIn('aria-current="page"', body)

    def test_the_hub_is_an_index_and_carries_no_breadcrumb_trail(self) -> None:
        """An index is marked by the masthead; a lone "Wiki" crumb would repeat it."""

        self.assertNotIn('<nav class="shell breadcrumbs', self.bodies()["hub"])

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

    def test_the_hub_keeps_all_three_ways_into_the_wiki(self) -> None:
        """The exploration links are the graph's and the special pages' only entry."""

        self.assertIn('aria-label="Wiki exploration"', self.body)
        for href, label in (
            (reverse("wiki-graph"), "Graph"),
            (f"{reverse('wiki-home')}?q=data", "Search"),
            (reverse("wiki-special"), "Special pages"),
        ):
            with self.subTest(href=href):
                self.assertIn(f'href="{href}"', self.body)
                self.assertIn(f"<strong>{label}</strong>", self.body)

    def test_the_catalogue_lists_every_topic_twice_over_as_title_and_read_action(self) -> None:
        records = public_projection()["wiki"]

        for record in (records[0], records[-1]):
            with self.subTest(slug=record["slug"]):
                self.assertEqual(self.body.count(f'href="{record["public_path"]}"'), 2)
                self.assertIn(str(record["title"]), self.body)
                self.assertIn(str(record["summary"]), self.body)
        self.assertEqual(self.body.count("Read topic"), len(records))

    def test_the_catalogue_rows_are_divided_rows_and_never_a_column_grid(self) -> None:
        self.assertIn('<div class="row-list">', self.body)
        self.assertNotRegex(self.body, r"(?:sm|md|lg):grid-cols-")


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
                    self.assertIn(str(result["segment_title"]), body)

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
                self.assertIn(f'<h2 id="{group.heading_id}">{group.title}</h2>', self.body)
                self.assertIn(f"{group.count} in the graph", self.body)

    def test_the_plotted_neighbourhood_is_read_from_the_graph_and_never_invented(self) -> None:
        neighbourhood = busiest_neighbourhood()

        self.assertIn(
            f'<a class="graph-node graph-node-hub" href="{neighbourhood.url}">', self.body
        )
        self.assertIn(f"{neighbourhood.connections} connections from", self.body)
        for spoke in neighbourhood.spokes:
            with self.subTest(spoke=spoke.title):
                self.assertIn(f"left: {spoke.left}%; top: {spoke.top}%", self.body)
        self.assertEqual(len(re.findall(r"<line ", self.body)), len(neighbourhood.spokes))

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
        declared = {key for key, _title, _description in GRAPH_GROUPS}

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
                self.assertIn(str(record["title"]), body)
                self.assertIn(str(record["summary"]), body)
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

    def test_the_page_keeps_its_kicker_title_summary_and_every_relation(self) -> None:
        self.assertIn('<p class="mono-label mono-label-indigo">Wiki</p>', self.body)
        self.assertIn(str(self.record["title"]), self.body)
        self.assertIn(str(self.record["summary"]), self.body)
        self.assertIn('aria-label="Related concepts and sources"', self.body)
        for relation in self.record["relations"]:
            with self.subTest(relation=relation["label"]):
                self.assertIn(f'data-relation-type="{relation["type"]}"', self.body)
                self.assertIn(
                    f'<span class="sr-only">{relation["type"]}: </span>{relation["label"]}',
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
                if block["kind"] == "heading":
                    self.assertIn(f'<h2 id="{block["id"]}">{block["text"]}</h2>', self.body)
                elif block["kind"] == "list_item":
                    self.assertIn(f'<p class="prose-item">{block["text"]}</p>', self.body)
        self.assertIn('<div class="prose wiki-prose">', self.body)

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
