"""Related episodes: the episodes the podcast wiki files under the same pages.

An episode page ends on the episodes that share wiki concepts with it, ranked
by how strongly and how distinctively they share them, each naming the shared
concepts as chips.  An episode the wiki has not filed falls back to its season,
and says so.  These checks publish a synthetic graph over the synthetic
catalogue, so each ranking rule is exercised by a graph built to isolate it.
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase

from content import catalogue
from content.podcast_content import related_episodes
from content.wiki_content import SharedConcept, related_episode_matches


def _episode(slug: str) -> dict[str, Any]:
    record = catalogue.podcast(slug)
    assert record is not None, slug
    return record


def _podcast_node(slug: str) -> dict[str, str]:
    record = _episode(slug)
    return {
        "id": f"podcast:{slug}",
        "label": record["title"],
        "title": record["title"],
        "type": "podcast",
        "url": record["public_path"],
    }


def _node(node_id: str, label: str, node_type: str, url: str) -> dict[str, str]:
    return {"id": node_id, "label": label, "title": label, "type": node_type, "url": url}


def _concept(slug: str, label: str) -> dict[str, str]:
    return _node(f"wiki:{slug}", label, "wiki", f"/wiki/{slug}")


def _link(episode: str, concept: str, weight: int = 1) -> dict[str, Any]:
    return {
        "source": concept,
        "target": f"podcast:{episode}",
        "kind": "wiki-chip",
        "weight": weight,
    }


# The synthetic catalogue's episodes, named for what each check makes of them.
CURRENT = "synthetic-episode-s24e03"  # Season 20, episode 3
NEWER = "synthetic-episode-s24e05"  # Season 20, episode 5
OLDER = "synthetic-episode-s24e01"  # Season 20, episode 1
OTHER_SEASON = "synthetic-episode-two"  # Season 1, episode 2
LATEST = "s24e06-how-to-build-ai-that-actually-ships-in-production"


class RelatedGraphTestCase(TestCase):
    def publish_graph(self, *, nodes: list[dict[str, str]], links: list[dict[str, Any]]) -> None:
        published = patch(
            "content.catalogue.wiki_graph", return_value={"nodes": nodes, "links": links}
        )
        published.start()
        self.addCleanup(published.stop)

    def slugs(self, slug: str = CURRENT) -> list[str]:
        return [match.slug for match in related_episode_matches(_episode(slug))]


class RelatedEpisodeRankingTests(RelatedGraphTestCase):
    def test_episodes_sharing_more_weight_rank_first_and_the_episode_is_never_its_own(
        self,
    ) -> None:
        nodes = [
            *(_podcast_node(slug) for slug in (CURRENT, NEWER, OLDER, OTHER_SEASON)),
            _concept("rag", "RAG"),
            _concept("evals", "Evaluation"),
            _concept("unrelated", "Unrelated"),
        ]
        links = [
            _link(CURRENT, "wiki:rag", 3),
            _link(CURRENT, "wiki:evals", 3),
            # OLDER shares both concepts strongly; NEWER shares one weakly.
            _link(OLDER, "wiki:rag", 3),
            _link(OLDER, "wiki:evals", 3),
            _link(NEWER, "wiki:rag", 1),
            # Shares nothing with the current episode.
            _link(OTHER_SEASON, "wiki:unrelated", 5),
        ]
        self.publish_graph(nodes=nodes, links=links)

        matches = related_episode_matches(_episode(CURRENT))

        self.assertEqual([match.slug for match in matches], [OLDER, NEWER])
        self.assertEqual(
            matches[0].concepts,
            (
                SharedConcept(label="Evaluation", url="/wiki/evals"),
                SharedConcept(label="RAG", url="/wiki/rag"),
            ),
        )
        self.assertEqual(matches[1].concepts, (SharedConcept(label="RAG", url="/wiki/rag"),))

    def test_a_concept_most_episodes_share_counts_for_less_than_a_rare_one(self) -> None:
        everyone = (CURRENT, NEWER, OLDER, OTHER_SEASON, LATEST)
        nodes = [
            *(_podcast_node(slug) for slug in everyone),
            _concept("machine-learning", "Machine Learning"),
            _concept("graph-rag", "Graph RAG"),
        ]
        links = [
            *(_link(slug, "wiki:machine-learning", 2) for slug in everyone),
            _link(CURRENT, "wiki:graph-rag", 2),
            _link(OLDER, "wiki:graph-rag", 2),
        ]
        self.publish_graph(nodes=nodes, links=links)

        matches = related_episode_matches(_episode(CURRENT))

        # The rare shared concept lifts the older episode above the newer ones
        # that share only what every episode shares.
        self.assertEqual(matches[0].slug, OLDER)
        self.assertEqual(matches[0].concepts[0].label, "Graph RAG")
        self.assertGreater(matches[0].score, matches[1].score)

    def test_equal_scores_fall_to_the_newer_episode(self) -> None:
        nodes = [
            *(_podcast_node(slug) for slug in (CURRENT, NEWER, OLDER, LATEST)),
            _concept("rag", "RAG"),
        ]
        links = [_link(slug, "wiki:rag", 2) for slug in (CURRENT, OLDER, NEWER, LATEST)]
        self.publish_graph(nodes=nodes, links=links)

        self.assertEqual(self.slugs(), [LATEST, NEWER, OLDER])

    def test_only_wiki_pages_are_shared_concepts(self) -> None:
        nodes = [
            *(_podcast_node(slug) for slug in (CURRENT, NEWER, OLDER, OTHER_SEASON)),
            _node("person:guest", "A Guest", "person", "/people/guest"),
            _node("topic:aws", "aws", "topic", "/wiki/search?q=aws"),
            _node("book:ml", "A Book", "book", "/books/a-book"),
            _node("wiki:ml-roadmap", "ML Roadmap", "article", "/wiki/ml-roadmap"),
        ]
        links = [
            _link(CURRENT, "person:guest"),
            _link(NEWER, "person:guest"),
            _link(CURRENT, "topic:aws"),
            _link(OLDER, "topic:aws"),
            _link(CURRENT, "book:ml"),
            _link(OTHER_SEASON, "book:ml"),
            # A wiki guide/roadmap page is a wiki page too.
            _link(CURRENT, "wiki:ml-roadmap"),
            _link(OTHER_SEASON, "wiki:ml-roadmap"),
        ]
        self.publish_graph(nodes=nodes, links=links)

        matches = related_episode_matches(_episode(CURRENT))

        self.assertEqual([match.slug for match in matches], [OTHER_SEASON])
        self.assertEqual(matches[0].concepts[0].url, "/wiki/ml-roadmap")

    def test_the_list_is_bounded(self) -> None:
        slugs = [record["slug"] for record in catalogue.podcasts()]
        nodes = [*(_podcast_node(slug) for slug in slugs), _concept("rag", "RAG")]
        self.publish_graph(nodes=nodes, links=[_link(slug, "wiki:rag") for slug in slugs])

        self.assertEqual(len(self.slugs()), 5)
        self.assertEqual(len(related_episode_matches(_episode(CURRENT), limit=2)), 2)

    def test_an_episode_the_graph_does_not_file_has_no_matches(self) -> None:
        nodes = [_podcast_node(NEWER), _podcast_node(OLDER), _concept("rag", "RAG")]
        links = [_link(NEWER, "wiki:rag"), _link(OLDER, "wiki:rag")]
        self.publish_graph(nodes=nodes, links=links)

        self.assertEqual(self.slugs(), [])


class RelatedEpisodeCompositionTests(TestCase):
    def test_graph_matches_become_cards_with_at_most_three_concept_chips(self) -> None:
        class Match:
            slug = OLDER
            concepts = tuple(
                SharedConcept(label=f"Concept {index}", url=f"/wiki/c{index}") for index in range(5)
            )

        band = related_episodes(_episode(CURRENT), catalogue.podcasts(), (Match(),))

        self.assertEqual(band.basis, "graph")
        self.assertEqual([item.episode.title for item in band.items], [_episode(OLDER)["title"]])
        self.assertEqual(
            [concept.label for concept in band.items[0].concepts],
            ["Concept 0", "Concept 1", "Concept 2"],
        )

    def test_no_graph_relations_fall_back_to_the_newest_of_the_season(self) -> None:
        current = _episode(CURRENT)

        band = related_episodes(current, catalogue.podcasts(), ())

        self.assertEqual(band.basis, "season")
        self.assertTrue(band.items)
        self.assertTrue(all(item.episode.season == current["season"] for item in band.items))
        self.assertNotIn(current["title"], [item.episode.title for item in band.items])
        numbers = [item.episode.episode for item in band.items]
        self.assertEqual(numbers, sorted(numbers, reverse=True))
        self.assertTrue(all(item.concepts == () for item in band.items))

    def test_an_episode_alone_in_its_season_with_no_relations_offers_nothing(self) -> None:
        band = related_episodes(_episode(OTHER_SEASON), catalogue.podcasts(), ())

        self.assertEqual(band.basis, "")
        self.assertEqual(band.items, ())


class RelatedEpisodePageTests(RelatedGraphTestCase):
    def band(self, slug: str = CURRENT) -> str:
        response = self.client.get(_episode(slug)["public_path"])
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        start = body.index('<section class="band band-lavender episode-related"')
        return body[start : body.index("</section>", start)]

    def test_the_page_ends_on_related_episode_rows_naming_their_shared_concepts(self) -> None:
        nodes = [
            _podcast_node(CURRENT),
            _podcast_node(OLDER),
            _concept("rag", "RAG"),
        ]
        self.publish_graph(
            nodes=nodes, links=[_link(CURRENT, "wiki:rag"), _link(OLDER, "wiki:rag")]
        )

        band = self.band()
        older = _episode(OLDER)

        self.assertIn('id="related-episodes-heading">Related episodes</h2>', band)
        self.assertIn('data-related-basis="graph"', band)
        self.assertIn("files\n              under the same topics", band)
        # The shared episode row /podcast draws, with the play disc.
        self.assertEqual(band.count("data-related-episode"), 1)
        self.assertIn("episode-row", band)
        self.assertIn('class="play-disc"', band)
        self.assertIn(f'href="{older["public_path"]}"', band)
        self.assertRegex(
            band,
            r'<ul class="archive-tags" aria-label="Shared wiki topics">\s*<li>\s*'
            r'<a class="chip chip-plain chip-link" href="/wiki/rag">RAG</a>',
        )
        self.assertIn('href="/podcast?season=20"', band)

    def test_the_retired_neighbour_and_season_bands_are_gone(self) -> None:
        body = self.client.get(_episode(CURRENT)["public_path"]).content.decode()

        self.assertNotIn("More episodes", body)
        self.assertNotIn("More from this season", body)
        self.assertNotIn("episode-navigation", body)

    def test_an_unfiled_episode_says_it_is_showing_its_season(self) -> None:
        self.publish_graph(nodes=[], links=[])

        band = self.band()

        self.assertIn('data-related-basis="season"', band)
        self.assertIn("has not linked this episode to others yet", band)
        self.assertIn("Season 20", band)
        self.assertNotIn("archive-tags", band)
        self.assertEqual(
            len(re.findall(r"data-related-episode", band)),
            min(5, sum(1 for item in catalogue.podcasts() if item["season"] == 20) - 1),
        )

    def test_a_graph_the_catalogue_cannot_supply_keeps_the_page_up(self) -> None:
        with patch(
            "content.wiki_content.related_episode_matches",
            side_effect=ImproperlyConfigured("broken graph"),
        ):
            band = self.band()

        self.assertIn('data-related-basis="season"', band)


class RelatedEpisodeCommonConceptTests(TestCase):
    def test_concepts_every_card_shares_are_named_once_and_left_off_the_cards(self) -> None:
        common = SharedConcept(label="AI Engineering", url="/wiki/ai-engineering")
        rag = SharedConcept(label="RAG", url="/wiki/rag")

        class Match:
            def __init__(self, slug: str, concepts: tuple[SharedConcept, ...]) -> None:
                self.slug = slug
                self.concepts = concepts

        band = related_episodes(
            _episode(CURRENT),
            catalogue.podcasts(),
            (Match(OLDER, (common, rag)), Match(NEWER, (common,))),
        )

        self.assertEqual(band.common_concepts, (common,))
        self.assertEqual([item.concepts for item in band.items], [(rag,), ()])

    def test_a_single_card_keeps_its_own_chips(self) -> None:
        concept = SharedConcept(label="RAG", url="/wiki/rag")

        class Match:
            slug = OLDER
            concepts = (concept,)

        band = related_episodes(_episode(CURRENT), catalogue.podcasts(), (Match(),))

        self.assertEqual(band.common_concepts, ())
        self.assertEqual(band.items[0].concepts, (concept,))
