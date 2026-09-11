from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.template.loader import render_to_string
from django.test import TestCase

from content import catalogue
from content.podcast_routes import podcast_public_id
from content.public_graph import validate_wiki_graph
from content.wiki_content import episode_graph

PODCAST_GRAPH_PATH_PATTERN = re.compile(r"^/podcast/s[0-9]+e[0-9]+/[a-z0-9_][a-z0-9_.-]*$")


def _episode(slug: str) -> dict[str, Any]:
    """The published episode a test names, which the catalogue must hold."""

    record = catalogue.podcast(slug)
    assert record is not None, slug
    return record


def _graph_node(
    node_id: str,
    title: str,
    node_type: str,
    url: str = "",
) -> dict[str, str]:
    return {
        "id": node_id,
        "label": title,
        "title": title,
        "type": node_type,
        "url": url,
    }


def _synthetic_episode(episode_path: str = "/podcast/s01e01/episode") -> dict[str, Any]:
    return {"title": "Synthetic episode", "public_path": episode_path}


class EpisodeGraphContractTests(TestCase):
    def publish_graph(
        self,
        *,
        nodes: list[dict[str, str]],
        links: list[dict[str, Any]],
        podcasts: tuple[dict[str, Any], ...] = (),
    ) -> None:
        """Publish a synthetic graph and episode list for the rest of this check.

        The resolver reads what the catalogue publishes, so a shape it has to
        refuse or resolve exactly is published here rather than handed in.  The
        node and link lists stay live, so a check may extend them and read the
        graph again.
        """

        for target, value in (
            ("content.catalogue.wiki_graph", {"nodes": nodes, "links": links}),
            ("content.catalogue.podcasts", podcasts),
        ):
            published = patch(target, return_value=value)
            published.start()
            self.addCleanup(published.stop)

    # A prior version of this class pinned one real episode's exact real graph
    # (27 raw links, 23 unique neighbours, "Slawomir Tulski" at weight 9, and
    # so on).  Those numbers are incidental shape of the reviewed corpus: the
    # mechanism they exercised -- weight aggregation across duplicate links,
    # relation-kind accumulation, and deterministic neighbour ordering -- is
    # already covered precisely, with synthetic data, by
    # `test_resolution_is_deterministic_and_does_not_use_episode_title_as_identity`
    # below. Pinning a second, larger copy of the same behaviour against a
    # real slug added no functional coverage, so it was dropped rather than
    # rewritten.

    def test_visual_nodes_are_native_links_to_their_resolved_targets(self) -> None:
        nodes = [
            _graph_node(
                "podcast:episode", "Synthetic episode", "podcast", "/podcast/s01e01/episode"
            ),
            *(
                _graph_node(f"wiki:{index}", f"Wiki {index}", "wiki", f"/wiki/wiki-{index}")
                for index in range(1, 9)
            ),
        ]
        links = [
            {"kind": "related", "source": "podcast:episode", "target": node["id"], "weight": 1}
            for node in nodes[1:]
        ]
        episode = _synthetic_episode()
        self.publish_graph(nodes=nodes, links=links)

        resolved = episode_graph(episode)
        body = render_to_string(
            "public/_podcast_episode_knowledge_graph.html", {"episode_graph": resolved}
        )

        for layout in resolved.layouts:
            with self.subTest(layout=layout.kind):
                match = re.search(
                    rf'<svg\s+class="graph-svg graph-svg-{layout.kind}".*?</svg>',
                    body,
                    re.DOTALL,
                )
                self.assertIsNotNone(match)
                assert match is not None
                svg = match.group(0)
                self.assertIn('role="group"', svg)
                self.assertNotIn('aria-hidden="true"', svg.split("\n", 8)[0])
                self.assertEqual(
                    len(re.findall(r'<a\s+class="graph-svg-node', svg)),
                    resolved.visual_count + 1,
                )
                self.assertIn(
                    f'href="{resolved.url}"',
                    svg,
                )
                self.assertIn(
                    f'aria-label="Open this podcast episode: {resolved.title}"',
                    svg,
                )
                for neighbour in resolved.visual_neighbors:
                    with self.subTest(node=neighbour.label):
                        self.assertIn(f'href="{neighbour.url}"', svg)
                        self.assertIn(f'aria-label="Open {neighbour.label}"', svg)

        self.assertNotIn('href=""', body)
        self.assertNotRegex(body, r'href="/podcast/[^" ]+\.html"')

    def test_visual_nodes_without_destinations_remain_named_non_links(self) -> None:
        nodes = [
            _graph_node(
                "podcast:episode", "Synthetic episode", "podcast", "/podcast/s01e01/episode"
            ),
            _graph_node("wiki:safe", "Safe", "wiki", "/wiki/safe"),
            _graph_node("wiki:missing", "Missing", "wiki"),
            _graph_node("wiki:external", "External", "wiki", "https://example.com/out"),
            _graph_node("wiki:protocol", "Protocol", "wiki", "//example.com/out"),
            _graph_node("wiki:traversal", "Traversal", "wiki", "/wiki/%2e%2e/private"),
            _graph_node("wiki:four", "Four", "wiki", "/wiki/four"),
            _graph_node("wiki:five", "Five", "wiki", "/wiki/five"),
            _graph_node("podcast:missing", "Missing podcast", "podcast"),
        ]
        links = [
            {
                "kind": "related",
                "source": "podcast:episode",
                "target": node["id"],
                "weight": 1,
            }
            for node in nodes[1:]
        ]
        episode = _synthetic_episode()
        self.publish_graph(nodes=nodes, links=links)

        resolved = episode_graph(episode)
        body = render_to_string(
            "public/_podcast_episode_knowledge_graph.html", {"episode_graph": resolved}
        )

        self.assertEqual(resolved.visual_count, 8)
        self.assertIn('href="/wiki/safe"', body)
        self.assertIn('class="graph-svg-node graph-svg-node-unavailable"', body)
        self.assertIn('aria-label="Missing (destination unavailable)"', body)
        self.assertIn('aria-label="Missing podcast (destination unavailable)"', body)
        self.assertIn("destination unavailable", body)
        self.assertNotIn("example.com/out", body)
        self.assertNotIn("%2e%2e/private", body)
        self.assertNotIn('href=""', body)

    def test_podcast_targets_use_their_hierarchical_catalogue_paths(self) -> None:
        target = {
            "slug": "target-episode.md",
            "season": 2,
            "episode": 3,
            "public_path": "/podcast/s02e03/target-episode.md",
        }
        nodes = [
            _graph_node(
                "podcast:episode", "Synthetic episode", "podcast", "/podcast/s99e99/stale-episode"
            ),
            _graph_node(
                "podcast:target-episode",
                "Target episode",
                "podcast",
                "/podcast/s02e03/target-episode.md",
            ),
            *(
                _graph_node(f"wiki:{index}", f"Wiki {index}", "wiki", f"/wiki/{index}")
                for index in range(1, 8)
            ),
        ]
        links = [
            {
                "kind": "related",
                "source": "podcast:episode",
                "target": node["id"],
                "weight": 1,
            }
            for node in nodes[1:]
        ]
        episode = _synthetic_episode()
        episode.update({"slug": "episode", "season": 1, "episode": 1})
        self.publish_graph(nodes=nodes, links=links, podcasts=(episode, target))

        resolved = episode_graph(episode)
        target_neighbour = next(
            neighbour
            for neighbour in resolved.neighbors
            if neighbour.id == "podcast:target-episode"
        )
        expected_target = f"/podcast/{podcast_public_id(season=2, episode=3)}/target-episode.md"
        self.assertEqual(resolved.url, "/podcast/s01e01/episode")
        self.assertEqual(target_neighbour.url, expected_target)
        self.assertNotIn(".html", resolved.url)
        self.assertNotIn(".html", target_neighbour.url)
        self.assertRegex(resolved.url, PODCAST_GRAPH_PATH_PATTERN)
        self.assertRegex(target_neighbour.url, PODCAST_GRAPH_PATH_PATTERN)
        body = render_to_string(
            "public/_podcast_episode_knowledge_graph.html", {"episode_graph": resolved}
        )
        self.assertIn(f'href="{expected_target}"', body)
        self.assertNotRegex(body, r'href="/podcast/[^" ]+\.html"')

    def test_narrow_visual_hub_stays_clear_of_all_eight_spokes(self) -> None:
        nodes = [
            _graph_node(
                "podcast:episode", "Synthetic episode", "podcast", "/podcast/s01e01/episode"
            ),
            *(
                _graph_node(f"wiki:{index}", f"Wiki {index}", "wiki", f"/wiki/wiki-{index}")
                for index in range(1, 9)
            ),
        ]
        links = [
            {"kind": "related", "source": "podcast:episode", "target": node["id"], "weight": 1}
            for node in nodes[1:]
        ]
        episode = _synthetic_episode()
        episode.update({"slug": "episode", "season": 1, "episode": 1})
        self.publish_graph(nodes=nodes, links=links)

        narrow = next(
            layout for layout in episode_graph(episode).layouts if layout.kind == "narrow"
        )

        self.assertEqual(narrow.hub.title, "S1E01")
        for node in narrow.nodes:
            with self.subTest(node=node.title):
                self.assertFalse(
                    node.left < narrow.hub.left + narrow.hub.width
                    and narrow.hub.left < node.left + node.width
                    and node.top < narrow.hub.top + narrow.hub.height
                    and narrow.hub.top < node.top + node.height
                )

    def test_resolution_is_deterministic_and_does_not_use_episode_title_as_identity(self) -> None:
        graph_nodes = [
            _graph_node("podcast:other", "Synthetic episode", "podcast", "/podcast/s02e01/other"),
            _graph_node("wiki:z", "Zed", "wiki", "/wiki/zed"),
            _graph_node("wiki:a", "Alpha", "wiki", "/wiki/alpha"),
            _graph_node("wiki:b", "Beta", "wiki", "/wiki/beta"),
        ]
        graph_links: list[dict[str, Any]] = [
            {"kind": "z", "source": "podcast:other", "target": "wiki:z", "weight": 1},
        ]
        episode = _synthetic_episode()
        self.publish_graph(nodes=graph_nodes, links=graph_links)
        # The graph has no node whose type and canonical path identify this episode.
        self.assertEqual(episode_graph(episode).state, "no_data")

        graph_nodes.append(
            _graph_node(
                "podcast:episode", "Synthetic episode", "podcast", "/podcast/s01e01/episode"
            )
        )
        graph_links.extend(
            [
                {"kind": "first", "source": "podcast:episode", "target": "wiki:a", "weight": 2},
                {"kind": "second", "source": "podcast:episode", "target": "wiki:a", "weight": 3},
                {"kind": "beta", "source": "podcast:episode", "target": "wiki:b", "weight": 6},
                {"kind": "z2", "source": "podcast:episode", "target": "wiki:z", "weight": 1},
            ]
        )
        first = episode_graph(episode)
        second = episode_graph(episode)

        self.assertEqual(first.neighbors, second.neighbors)
        self.assertEqual(
            [neighbour.label for neighbour in first.neighbors],
            ["Beta", "Alpha", "Zed"],
        )
        alpha = next(neighbour for neighbour in first.neighbors if neighbour.label == "Alpha")
        self.assertEqual(alpha.weight, 5)
        self.assertEqual(alpha.raw_links, 2)
        self.assertEqual(alpha.relation_kinds, ("first", "second"))

    def test_unsafe_and_missing_destinations_are_never_links(self) -> None:
        nodes = [
            _graph_node(
                "podcast:episode", "Synthetic episode", "podcast", "/podcast/s01e01/episode"
            ),
            _graph_node("wiki:safe", "Safe", "wiki", "/wiki/safe"),
            _graph_node("wiki:none", "No destination", "wiki"),
            _graph_node("wiki:external", "External", "wiki", "https://example.com/out"),
            _graph_node("wiki:protocol", "Protocol relative", "wiki", "//example.com/out"),
            _graph_node("wiki:traversal", "Traversal", "wiki", "/wiki/%2e%2e/private"),
        ]
        links = [
            {
                "kind": "related",
                "source": "podcast:episode",
                "target": node["id"],
                "weight": 1,
            }
            for node in nodes[1:]
        ]
        episode = _synthetic_episode()
        self.publish_graph(nodes=nodes, links=links)

        resolved = episode_graph(episode)
        destinations = {neighbour.label: neighbour.url for neighbour in resolved.neighbors}
        self.assertEqual(destinations["Safe"], "/wiki/safe")
        for label in ("No destination", "External", "Protocol relative", "Traversal"):
            self.assertEqual(destinations[label], "")
        body = render_to_string(
            "public/_podcast_episode_knowledge_graph.html", {"episode_graph": resolved}
        )
        self.assertIn('href="/wiki/safe"', body)
        for unsafe in ("example.com/out", "%2e%2e/private"):
            self.assertNotIn(unsafe, body)
        self.assertNotIn('href=""', body)

    def test_dangling_incident_reference_fails_closed(self) -> None:
        episode = _synthetic_episode()
        self.publish_graph(
            nodes=[
                _graph_node(
                    "podcast:episode",
                    "Synthetic episode",
                    "podcast",
                    "/podcast/s01e01/episode",
                )
            ],
            links=[
                {
                    "kind": "related",
                    "source": "podcast:episode",
                    "target": "wiki:missing",
                    "weight": 1,
                }
            ],
        )

        with self.assertRaises(ImproperlyConfigured):
            episode_graph(episode)

    def test_projection_validator_rejects_unsafe_urls_and_dangling_links(self) -> None:
        unsafe = {
            "nodes": [_graph_node("wiki:bad", "Bad", "wiki", "//example.com")],
            "links": [],
        }
        with self.assertRaises(ImproperlyConfigured):
            validate_wiki_graph(unsafe)

        dangling = {
            "nodes": [_graph_node("wiki:one", "One", "wiki", "/wiki/one")],
            "links": [{"kind": "related", "source": "wiki:one", "target": "wiki:two", "weight": 1}],
        }
        with self.assertRaises(ImproperlyConfigured):
            validate_wiki_graph(dangling)


class EpisodeGraphPageTests(TestCase):
    def test_page_exposes_complete_fallback_without_changing_episode_metadata(self) -> None:
        """The episode detail page renders the graph the catalogue publishes.

        Fixed here is only the render contract (heading, canonical link, cache
        header, JSON-LD, the neighbour-card count matching the resolved graph)
        -- not any particular real episode's real neighbour count, which is
        incidental to the reviewed corpus and never meant anything functionally.
        """

        episode = _episode("synthetic-episode-one")
        neighbours = (
            ("wiki:mlops", "MLOps", "wiki", "/wiki/mlops"),
            ("person:synthetic-one", "Synthetic Author One", "person", "/people/synthetic-one.html"),
            (
                "book:synthetic-book-one",
                "Synthetic Book One",
                "book",
                "/books/synthetic-book-one.html",
            ),
            (
                "article:synthetic-guide-one",
                "Synthetic Guide One",
                "article",
                "/wiki/synthetic-guide-one",
            ),
        )
        episode_node_id = f"podcast:{episode['slug']}"
        nodes = [
            _graph_node(episode_node_id, episode["title"], "podcast", episode["public_path"]),
            *(
                _graph_node(node_id, label, node_type, url)
                for node_id, label, node_type, url in neighbours
            ),
        ]
        links = [
            {"kind": "related", "source": episode_node_id, "target": node_id, "weight": 1}
            for node_id, _, _, _ in neighbours
        ]

        with patch(
            "content.catalogue.wiki_graph", return_value={"nodes": nodes, "links": links}
        ):
            response = self.client.get(episode["public_path"])
            resolved = episode_graph(episode)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(resolved.state, "available")
        self.assertContains(response, '<h2 id="episode-graph-heading">Related knowledge graph</h2>')
        for _, label, _, _ in neighbours:
            self.assertContains(response, label)
        self.assertEqual(response.context["episode_graph"].raw_links, len(links))
        self.assertEqual(
            len(re.findall(r'<li class="card">', response.content.decode())),
            resolved.unique_neighbors,
        )
        self.assertEqual(response.headers["Cache-Control"], "max-age=0, must-revalidate")
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club' + episode["public_path"] + '">',
        )
        body = response.content.decode()
        match = re.search(
            r'<script type="application/ld\+json">\s*(.*?)\s*</script>',
            body,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        assert match is not None
        structured_data = json.loads(match.group(1))
        self.assertTrue(
            any(item.get("@type") == "PodcastEpisode" for item in structured_data["@graph"])
        )
        self.assertNotIn('"@type": "KnowledgeGraph"', body)

    def test_no_data_and_known_graph_failure_keep_the_episode_page_successful(self) -> None:
        episode = _episode("synthetic-episode-one")
        with patch("content.catalogue.wiki_graph", return_value={"nodes": [], "links": []}):
            no_data_response = self.client.get(episode["public_path"])
        self.assertEqual(no_data_response.status_code, 200)
        self.assertContains(
            no_data_response,
            "No related knowledge-graph connections are available for this episode yet.",
        )

        with patch(
            "content.public_views.wiki_content.episode_graph",
            side_effect=ImproperlyConfigured("secret graph contract details"),
        ):
            unavailable_response = self.client.get(episode["public_path"])
        self.assertEqual(unavailable_response.status_code, 200)
        self.assertContains(
            unavailable_response,
            "Related knowledge graph is temporarily unavailable.",
        )
        self.assertNotContains(unavailable_response, "secret graph contract details")
