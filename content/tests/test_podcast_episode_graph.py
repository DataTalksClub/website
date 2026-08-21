from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase

from content.public_data import _validate_wiki_graph, public_projection
from content.wiki_content import episode_graph

REPRESENTATIVE = (
    "s23e06-data-engineer-career-in-2026-roles-specializations-and-what-companies-look-for"
)
REPRESENTATIVE_PATH = (
    "/podcast/s23e06-data-engineer-career-in-2026-roles-specializations-and-what-"
    "companies-look-for.html"
)


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


def _synthetic_projection(
    *,
    links: list[dict[str, Any]],
    nodes: list[dict[str, str]],
    episode_path: str = "/podcast/episode.html",
) -> tuple[dict[str, Any], dict[str, str]]:
    episode = {"title": "Synthetic episode", "public_path": episode_path}
    return {"wiki_graph": {"nodes": nodes, "links": links}}, episode


class EpisodeGraphContractTests(SimpleTestCase):
    def test_s23e06_uses_exact_typed_path_and_aggregates_the_checked_oracle(self) -> None:
        projection = public_projection()
        episode = projection["podcasts_by_slug"][REPRESENTATIVE]

        resolved = episode_graph(episode, projection=projection)

        self.assertEqual(resolved.state, "available")
        self.assertEqual(resolved.hub_id, "podcast:" + REPRESENTATIVE)
        self.assertEqual(resolved.url, REPRESENTATIVE_PATH)
        self.assertEqual(resolved.raw_links, 27)
        self.assertEqual(resolved.unique_neighbors, 23)
        self.assertEqual(resolved.visual_count, 8)
        self.assertEqual(len(resolved.layouts), 2)
        self.assertEqual(resolved.neighbors[0].label, "Slawomir Tulski")
        self.assertEqual(resolved.neighbors[0].type, "person")
        self.assertEqual(resolved.neighbors[0].weight, 9)
        self.assertEqual(
            resolved.neighbors[0].relation_kinds,
            ("person-podcast", "podcast-link", "podcast-person"),
        )
        for label in ("Data Engineering", "Portfolio Projects"):
            match = next(neighbour for neighbour in resolved.neighbors if neighbour.label == label)
            self.assertEqual(match.weight, 3)
            self.assertEqual(match.type, "wiki")
        self.assertEqual(
            [neighbour.label for neighbour in resolved.neighbors[3:]],
            sorted(neighbour.label for neighbour in resolved.neighbors[3:]),
        )

    def test_narrow_visual_hub_stays_clear_of_all_eight_spokes(self) -> None:
        projection = public_projection()
        episode = projection["podcasts_by_slug"][REPRESENTATIVE]
        narrow = next(
            layout
            for layout in episode_graph(episode, projection=projection).layouts
            if layout.kind == "narrow"
        )

        self.assertEqual(narrow.hub.title, "S23E06")
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
            _graph_node("podcast:other", "Synthetic episode", "podcast", "/podcast/other.html"),
            _graph_node("wiki:z", "Zed", "wiki", "/wiki/zed"),
            _graph_node("wiki:a", "Alpha", "wiki", "/wiki/alpha"),
            _graph_node("wiki:b", "Beta", "wiki", "/wiki/beta"),
        ]
        graph_links: list[dict[str, Any]] = [
            {"kind": "z", "source": "podcast:other", "target": "wiki:z", "weight": 1},
        ]
        projection, episode = _synthetic_projection(
            nodes=graph_nodes, links=graph_links, episode_path="/podcast/episode.html"
        )
        # The graph has no node whose type and canonical path identify this episode.
        self.assertEqual(episode_graph(episode, projection=projection).state, "no_data")

        graph_nodes.append(
            _graph_node("podcast:episode", "Synthetic episode", "podcast", "/podcast/episode.html")
        )
        graph_links.extend(
            [
                {"kind": "first", "source": "podcast:episode", "target": "wiki:a", "weight": 2},
                {"kind": "second", "source": "podcast:episode", "target": "wiki:a", "weight": 3},
                {"kind": "beta", "source": "podcast:episode", "target": "wiki:b", "weight": 6},
                {"kind": "z2", "source": "podcast:episode", "target": "wiki:z", "weight": 1},
            ]
        )
        first = episode_graph(episode, projection=projection)
        second = episode_graph(episode, projection=projection)

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
            _graph_node("podcast:episode", "Synthetic episode", "podcast", "/podcast/episode.html"),
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
        projection, episode = _synthetic_projection(nodes=nodes, links=links)

        resolved = episode_graph(episode, projection=projection)
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
        projection, episode = _synthetic_projection(
            nodes=[
                _graph_node(
                    "podcast:episode",
                    "Synthetic episode",
                    "podcast",
                    "/podcast/episode.html",
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
            episode_graph(episode, projection=projection)

    def test_projection_validator_rejects_unsafe_urls_and_dangling_links(self) -> None:
        unsafe = {
            "nodes": [_graph_node("wiki:bad", "Bad", "wiki", "//example.com")],
            "links": [],
        }
        with self.assertRaises(ImproperlyConfigured):
            _validate_wiki_graph(unsafe)

        dangling = {
            "nodes": [_graph_node("wiki:one", "One", "wiki", "/wiki/one")],
            "links": [{"kind": "related", "source": "wiki:one", "target": "wiki:two", "weight": 1}],
        }
        with self.assertRaises(ImproperlyConfigured):
            _validate_wiki_graph(dangling)


class EpisodeGraphPageTests(TestCase):
    def test_s23e06_page_exposes_complete_fallback_without_changing_episode_metadata(self) -> None:
        projection = public_projection()
        episode = projection["podcasts_by_slug"][REPRESENTATIVE]

        response = self.client.get(episode["public_path"])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h2 id="episode-graph-heading">Related knowledge graph</h2>')
        self.assertContains(response, "Slawomir Tulski")
        self.assertContains(response, "Data Engineering")
        self.assertContains(response, "Portfolio Projects")
        self.assertEqual(response.context["episode_graph"].raw_links, 27)
        self.assertEqual(
            len(re.findall(r'<li class="card">', response.content.decode())),
            23,
        )
        self.assertEqual(response.headers["Cache-Control"], "max-age=0, must-revalidate")
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club' + REPRESENTATIVE_PATH + '">',
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
        projection = public_projection()
        episode = projection["podcasts_by_slug"][REPRESENTATIVE]
        no_data_projection = {**projection, "wiki_graph": {"nodes": [], "links": []}}
        with patch("content.public_views.public_projection", return_value=no_data_projection):
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
