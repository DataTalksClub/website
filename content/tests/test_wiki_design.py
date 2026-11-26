"""The wiki surfaces on the design system (issue #179).

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

from community_base.content_sync.models import ContentSource as EngineContentSource
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils.html import escape

from content import catalogue, wiki_content, wiki_hub, wiki_reader
from content.models import ContentSource
from content.public_views import WIKI_SPECIAL_CATEGORIES
from content.topic_reading import topic_reading
from content.wiki_content import (
    GRAPH_GROUPS,
    GROUP_ENTRY_POINTS,
    busiest_neighbourhood,
    graph_groups,
    graph_nodes,
    graph_totals,
    node_connections,
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


def _wiki_page(slug: str) -> dict[str, Any]:
    """The published wiki page a test names, which the catalogue must hold."""

    record = wiki_reader.wiki_page(slug)
    assert record is not None, slug
    return record


def wiki_paths() -> dict[str, str]:
    page = wiki_reader.wiki_pages()[0]
    return {
        "hub": reverse("wiki-home"),
        "results": f"{reverse('wiki-search')}?q=data+pipelines",
        "no results": f"{reverse('wiki-search')}?q=no-such-public-topic",
        "graph": reverse("wiki-graph"),
        "special pages": reverse("wiki-special"),
        "special category": reverse("wiki-special-category", kwargs={"category": "guides"}),
        "wiki page": str(page["public_path"]),
    }


class WikiDesignSystemTests(TestCase):
    """Every wiki surface is a page of the design system."""

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
    """The hub is a landing page: search first, then the ways into a topic."""

    def setUp(self) -> None:
        self.response = self.client.get(reverse("wiki-home"))
        self.body = self.response.content.decode()

    def test_the_hub_leads_with_its_title_search_box_and_computed_proof_line(self) -> None:
        stats = wiki_hub.stats()

        self.assertEqual(self.response.status_code, 200)
        self.assertIn('<h1 class="page-title" id="wiki-heading">Wiki</h1>', self.body)
        self.assertIn(
            '<form class="search-row wiki-search-form" action="/wiki/search" method="get" '
            'role="search">',
            self.body,
        )
        self.assertIn('<label class="sr-only" for="wiki-query">Search the wiki</label>', self.body)
        self.assertIn('data-live-url="/wiki/search.json"', self.body)
        self.assertIn('aria-controls="wiki-search-results"', self.body)
        self.assertIn(f"Search {stats.topics} topics", self.body)
        self.assertEqual(stats.topics, len(wiki_reader.wiki_pages()))
        self.assertIn(f"<strong>{stats.topics}</strong> topics", self.body)
        self.assertIn(f"<strong>{stats.episodes}</strong> podcast episodes", self.body)
        self.assertIn('src="/static/core/wiki_search.js"', self.body)

    def test_the_hub_draws_the_homepage_graph_explorer_in_its_compact_form(self) -> None:
        if self.response.context["wiki_graph"] is None:
            self.skipTest("the fixture wiki does not publish the graph hub")
        self.assertIn("home-graph-explorer home-graph-compact", self.body)
        self.assertIn("data-home-graph-random", self.body)
        self.assertIn(f'href="{reverse("wiki-graph")}">full graph →</a>', self.body)
        # The compact card carries no legend, count line or connections row.
        self.assertNotIn('class="graph-legend"', self.body)
        self.assertNotIn("data-home-graph-all", self.body)
        self.assertIn('src="/static/core/home_graph.js"', self.body)

    def test_featured_areas_and_goals_link_only_to_published_pages(self) -> None:
        published = {str(record["public_path"]) for record in wiki_reader.wiki_pages()}
        context = self.response.context
        cards = [*context["featured_roadmaps"], *context["featured_topics"]]
        cards += [goal.topic for goal in context["start_goals"]]
        cards += [topic for area in context["areas"] for topic in area.topics]
        for topic in cards:
            with self.subTest(topic=topic.slug):
                self.assertIn(topic.url, published)
                self.assertIn(f'href="{topic.url}"', self.body)
        self.assertEqual(
            sum(area.count for area in context["areas"]), len(wiki_reader.wiki_pages())
        )

    def test_the_a_to_z_lists_every_topic_once_inside_a_closed_fold(self) -> None:
        records = wiki_reader.wiki_pages()
        self.assertIn('<details class="wiki-az">', self.body)
        fold = self.body.split('<details class="wiki-az">', 1)[1].split("</details>", 1)[0]
        for record in records:
            with self.subTest(slug=record["slug"]):
                self.assertEqual(fold.count(f'href="{record["public_path"]}"'), 1)
                self.assertIn(escape(str(record["title"])), fold)

    def test_the_hub_has_no_query_state(self) -> None:
        search = self.client.get("/wiki?q=machine+learning")
        self.assertEqual(search.status_code, 302)
        self.assertEqual(search.headers["Location"], "/wiki/search?q=machine+learning")
        self.assertEqual(search.headers["Cache-Control"], "private, no-store, max-age=0")
        for query in ("q=", "page=2", "page=1", "sort=title"):
            with self.subTest(query=query):
                response = self.client.get(f"/wiki?{query}")
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response.headers["Location"], "/wiki")

        body = self.body
        self.assertIn("<title>Wiki — DataTalks.Club</title>", body)
        self.assertIn('<link rel="canonical" href="https://datatalks.club/wiki">', body)
        self.assertNotIn('<nav class="pagination"', body)
        self.assertNotIn("results for", body)

        rejected = self.client.post(reverse("wiki-home"))
        self.assertEqual(rejected.status_code, 405)
        self.assertEqual(rejected.headers["Allow"], "GET, HEAD")
        self.assertEqual(rejected.headers["Cache-Control"], "no-store, max-age=0")

    def test_head_matches_get_status_metadata_and_empty_bodies(self) -> None:
        for path in (reverse("wiki-home"), "/wiki?page=2", "/wiki?q=data"):
            with self.subTest(path=path):
                get_response = self.client.get(path)
                head_response = self.client.head(path)
                self.assertEqual(head_response.status_code, get_response.status_code)
                self.assertEqual(
                    head_response.headers.get("Cache-Control"),
                    get_response.headers.get("Cache-Control"),
                )
                self.assertEqual(head_response.content, b"")

    def test_the_slash_alias_still_forwards_its_raw_query_in_one_hop(self) -> None:
        response = self.client.get("/wiki/?page=2", follow=False)
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers["Location"], "/wiki?page=2")

    def test_an_empty_wiki_says_so(self) -> None:
        # The hub reads the database, so it is emptied the way an un-ingested
        # database is empty rather than by patching a value in.
        ContentSource.objects.filter(stable_id=catalogue.PUBLIC_CONTENT_STABLE_ID).update(
            enabled=False
        )
        EngineContentSource.objects.filter(slug=catalogue.WIKI_SOURCE_SLUG).update(is_enabled=False)

        response = self.client.get(reverse("wiki-home"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("No wiki pages are available yet.", response.content.decode())

    def test_the_wiki_sitemap_lists_the_clean_hub_and_no_page_query(self) -> None:
        sitemap = self.client.get("/wiki/sitemap.xml").content.decode()

        self.assertIn("<loc>https://datatalks.club/wiki</loc>", sitemap)
        self.assertNotIn("?page=", sitemap)


class WikiSearchTests(TestCase):
    def test_a_query_lists_its_ranked_topics_as_cards_on_a_private_page(self) -> None:
        response = self.client.get(reverse("wiki-search"), {"q": "data pipelines"})
        body = response.content.decode()
        results = response.context["results"]

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "public/wiki_search.html")
        self.assertEqual(results, wiki_hub.search("data pipelines"))
        self.assertTrue(results)
        noun = "topic" if len(results) == 1 else "topics"
        self.assertIn(f"{len(results)} {noun} for &ldquo;data pipelines&rdquo;", body)
        for topic in results[:5]:
            with self.subTest(url=topic.url):
                self.assertIn(f'<a href="{topic.url}">', body)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store, max-age=0")
        self.assertIn('<meta name="robots" content="noindex">', body)

    def test_a_query_with_no_hits_keeps_its_zero_count_and_its_empty_state(self) -> None:
        body = self.client.get(
            reverse("wiki-search"), {"q": "no-such-public-topic"}
        ).content.decode()

        self.assertIn("0 topics for &ldquo;no-such-public-topic&rdquo;", body)
        self.assertIn("No wiki topic matches that.", body)

    def test_the_search_field_returns_the_term_already_searched_for(self) -> None:
        body = self.client.get(reverse("wiki-search"), {"q": "data pipelines"}).content.decode()

        self.assertIn('value="data pipelines"', body)

    def test_a_blank_term_goes_back_to_the_hub(self) -> None:
        response = self.client.get("/wiki/search?q=++")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/wiki")

    def test_the_editorial_search_slug_stays_a_wiki_page(self) -> None:
        response = self.client.get("/wiki/search")
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "public/wiki_detail.html")
        self.assertIn("<title>Search — DataTalks.Club Wiki</title>", body)
        # The real reviewed body copy is the actual page's own real-content
        # words; test_support/fixtures/reference/'s synthetic wiki page at
        # this same slug carries its own synthetic copy instead.
        self.assertIn(
            "search as the product system readers use to find a topic",
            body,
        )
        self.assertNotIn("topics for", body)

    def test_search_trims_and_bounds_the_term(self) -> None:
        trimmed = self.client.get(reverse("wiki-search"), {"q": "  data   pipelines  "})
        self.assertEqual(trimmed.context["query"], "data pipelines")

        bounded = self.client.get(reverse("wiki-search"), {"q": "x" * 250})
        self.assertEqual(bounded.context["query"], "x" * 200)

    def test_the_live_endpoint_returns_the_top_ranked_topics_as_private_json(self) -> None:
        response = self.client.get(reverse("wiki-search-live"), {"q": "data pipelines"})
        payload = response.json()
        ranked = wiki_hub.search("data pipelines")

        self.assertEqual(response.headers["Cache-Control"], "private, no-store, max-age=0")
        self.assertEqual(payload["total"], len(ranked))
        self.assertEqual(
            [result["url"] for result in payload["results"]],
            [topic.url for topic in ranked[: wiki_hub.LIVE_RESULTS]],
        )
        self.assertEqual(payload["all_url"], "/wiki/search?q=data+pipelines")
        empty = self.client.get(reverse("wiki-search-live"), {"q": " "}).json()
        self.assertEqual((empty["total"], empty["results"]), (0, []))


def _synthetic_index(*pages: tuple[str, str, str, str, int]) -> Any:
    """A hub index over hand-written pages: (slug, title, summary, body, episodes)."""

    records = tuple(
        {
            "slug": slug,
            "title": title,
            "summary": summary,
            "public_path": f"/wiki/{slug}",
            "tags": [],
            "relations": [],
        }
        for slug, title, summary, _body, _episodes in pages
    )
    nodes = [{"id": f"wiki:{slug}", "type": "wiki"} for slug, *_ in pages]
    links = []
    for slug, *_rest, episodes in pages:
        for number in range(episodes):
            nodes.append({"id": f"podcast:{slug}-{number}", "type": "podcast"})
            links.append({"source": f"podcast:{slug}-{number}", "target": f"wiki:{slug}"})
    corpus = {
        "docs": [
            {"graph_id": f"wiki:{slug}", "document_type": "page", "text": body}
            for slug, _title, _summary, body, _episodes in pages
        ]
    }
    return wiki_hub._build(records, {"nodes": nodes, "links": links}, corpus)


class WikiSearchRankingTests(SimpleTestCase):
    def ranked(self, query: str, *pages: tuple[str, str, str, str, int]) -> list[str]:
        with patch("content.wiki_hub._index", return_value=_synthetic_index(*pages)):
            return [topic.slug for topic in wiki_hub.search(query)]

    def test_title_beats_summary_beats_body_and_every_word_must_match(self) -> None:
        pages = (
            ("body", "Pipelines", "Moving data.", "Teams schedule airflow jobs.", 50),
            ("summary", "Orchestration", "Scheduling with Airflow and peers.", "", 40),
            ("partial", "Airflowing Ideas", "", "", 0),
            ("title", "Apache Airflow", "A scheduler.", "", 1),
            ("none", "dbt", "Transformations.", "", 90),
        )
        self.assertEqual(self.ranked("airflow", *pages), ["title", "partial", "summary", "body"])
        self.assertEqual(self.ranked("airflow scheduler", *pages), ["title"])

    def test_a_query_spelling_a_titles_initials_ranks_it_first(self) -> None:
        pages = (
            ("rag-vs-ft", "RAG vs Fine-Tuning", "", "", 30),
            ("rag", "Retrieval-Augmented Generation", "", "uses rag pipelines", 1),
            ("gov", "Responsible AI and Governance", "", "", 50),
        )
        self.assertEqual(self.ranked("rag", *pages), ["rag", "rag-vs-ft"])

    def test_a_partial_match_must_start_a_word_and_ties_go_to_more_episodes(self) -> None:
        pages = (
            ("storage", "Storage", "Where data lives.", "", 99),
            ("few", "Career Growth", "", "", 2),
            ("many", "Career Transitions", "", "", 60),
        )
        self.assertEqual(self.ranked("career", *pages), ["many", "few"])
        self.assertEqual(self.ranked("rag", *pages), [])
        self.assertEqual(self.ranked("care", *pages), ["many", "few"])


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
            f'<a class="filter-pill interactive-lift" href="{reverse("wiki-special")}" '
            "aria-current=",
            body,
        )
        for slug in WIKI_SPECIAL_CATEGORIES:
            with self.subTest(slug=slug):
                path = reverse("wiki-special-category", kwargs={"category": slug})
                self.assertIn(f'href="{path}"', body)

        category = self.client.get(
            reverse("wiki-special-category", kwargs={"category": "guides"})
        ).content.decode()
        self.assertIn(
            '<a class="filter-pill interactive-lift" href="/wiki/special-pages/guides"'
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
        ContentSource.objects.filter(stable_id=catalogue.PUBLIC_CONTENT_STABLE_ID).update(
            enabled=False
        )
        EngineContentSource.objects.filter(slug=catalogue.WIKI_SOURCE_SLUG).update(is_enabled=False)

        body = self.client.get(reverse("wiki-special")).content.decode()

        self.assertIn("No pages found.", body)


class WikiPageTests(TestCase):
    def setUp(self) -> None:
        self.record = _wiki_page("a-a-testing")
        self.body = self.client.get(str(self.record["public_path"])).content.decode()

    def _section(self, marker: str) -> str:
        """The rendered markup from ``marker`` to the end of its section."""

        start = self.body.index(marker)
        return self.body[start : self.body.index("</section>", start)]

    def test_the_page_keeps_one_wiki_context_label_title_and_its_related_topics(self) -> None:
        # The breadcrumb supplies the page's Wiki context; repeating it as an
        # eyebrow immediately above the title adds no information.
        self.assertNotIn('<p class="mono-label mono-label-indigo">Wiki</p>', self.body)
        self.assertIn(f'<a href="{reverse("wiki-home")}">Wiki</a>', self.body)
        heading = f'<h1 id="wiki-page-heading">{escape(str(self.record["title"]))}</h1>'
        self.assertEqual(self.body.count(heading), 1)
        self.assertIn(
            '<section class="band band-cream wiki-hero" aria-labelledby="wiki-page-heading">',
            self.body,
        )
        self.assertIn(escape(str(self.record["summary"])), self.body)
        cards = wiki_content.wiki_related_cards(self.record, pages=wiki_reader.wiki_pages())
        self.assertTrue(cards, "the fixture page names related pages")
        # Chips are the curated related topics only, each under its page's own
        # title, never an episode or a person.
        chips = self._section('aria-label="Related topics"')
        self.assertEqual(chips.count('class="chip chip-plain relation"'), min(len(cards), 8))
        for card in cards[:8]:
            with self.subTest(topic=card.title):
                self.assertIn(f'href="{card.url}">{escape(card.title)}</a>', chips)
        self.assertNotIn('href="/podcast/', chips)
        self.assertNotIn('href="/people/', chips)
        block = self._section('id="related-pages"')
        for card in cards:
            with self.subTest(card=card.title):
                self.assertIn(f'<h3><a href="{card.url}">{escape(card.title)}</a></h3>', block)

    def test_each_linked_relation_lands_in_its_own_group(self) -> None:
        episodes = (
            self.body[self.body.index('id="episodes"') :] if 'id="episodes"' in self.body else ""
        )
        for relation in self.record["relations"]:
            href = relation["href"].split("#", 1)[0]
            with self.subTest(relation=relation["label"]):
                if relation["type"] in {"citation", "podcast"}:
                    self.assertIn(f'href="{href}"', episodes)
                elif relation["type"] == "person":
                    self.assertIn(f'href="{href}"', self._section('aria-label="People"'))
                elif relation["type"] == "book":
                    # A book the catalogue publishes is a /books row in the
                    # page's own Books section; one it does not is dropped.
                    for book in topic_reading([href]).books:
                        self.assertIn(
                            f'href="{book["public_path"]}"',
                            self._section('id="topic-books-heading"'),
                        )
        if episodes:
            self.assertIn('href="#episodes"', self.body)

    def test_the_body_renders_every_block_and_keeps_its_anchor_identifiers(self) -> None:
        for block in self.record["blocks"]:
            with self.subTest(kind=block["kind"]):
                text = escape(block["text"])
                if block["kind"] == "heading":
                    self.assertIn(f'<h2 id="{block["id"]}">{text}</h2>', self.body)
                elif block["kind"] == "list_item":
                    self.assertIn(f"<li>{text}</li>", self.body)
                else:
                    self.assertIn(f'<p class="prose-paragraph">{text}</p>', self.body)
        self.assertTrue(
            any(escape(block["text"]) != block["text"] for block in self.record["blocks"])
        )
        self.assertIn('<div class="prose wiki-prose">', self.body)

    def test_an_ampersand_in_a_wiki_title_is_escaped_in_the_heading(self) -> None:
        record = _wiki_page("data-scientist-cv-and-portfolio")
        self.assertNotEqual(escape(record["title"]), record["title"])
        body = self.client.get(str(record["public_path"])).content.decode()

        self.assertIn(f'<h1 id="wiki-page-heading">{escape(record["title"])}</h1>', body)

    def test_an_ampersand_in_a_related_topic_title_is_escaped(self) -> None:
        pages = wiki_reader.wiki_pages()
        record, card = next(
            (page, card)
            for page in pages
            for card in wiki_content.wiki_related_cards(page, pages=pages)
            if "&" in card.title
        )
        body = self.client.get(str(record["public_path"])).content.decode()

        self.assertIn(f'href="{card.url}">{escape(card.title)}</a>', body)

    def test_an_anchor_the_source_never_defined_still_lands_on_the_page(self) -> None:
        page = next(
            (record for record in wiki_reader.wiki_pages() if record["unresolved_fragment_ids"]),
            None,
        )
        if page is None:
            self.skipTest("no wiki page currently carries an unresolved anchor")
        body = self.client.get(str(page["public_path"])).content.decode()
        for fragment in page["unresolved_fragment_ids"]:
            with self.subTest(fragment=fragment):
                self.assertIn(f'<span id="{fragment}"></span>', body)
