"""The two documentation pages a reader actually meets: the hub and a detail page.

``test_docs_projection`` owns the source projection -- what pages exist, where they
live, and what their markdown renders to.  This module owns what the two templates
make of that: how the hub groups 105 pages so they can be found, how search ranks
and explains a result, and how a detail page says where the reader is, what else is
in the guide, and where to go next.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from content.docs_presentation import docs_hub, docs_search_results
from content.docs_projection import DOCS_ROOT_PATH, docs_navigation_tree


def hub_body(client) -> str:
    response = client.get(DOCS_ROOT_PATH)
    assert response.status_code == 200
    return response.content.decode("utf-8").split("</head>", 1)[1]


class DocsHubTests(TestCase):
    def test_the_hub_puts_every_page_but_the_two_indexes_one_click_away(self) -> None:
        body = hub_body(self.client)
        linked = set(re.findall(r'href="(/docs/[^"]*)"', body))
        tree = docs_navigation_tree()

        unreachable = [
            item.public_path for item in tree.documents if item.public_path not in linked
        ]
        # The only pages the hub may leave unlinked are the area indexes whose
        # whole content is the list of children the hub now draws itself, and each
        # of those is still one crumb from every page beneath it.  Before this, 21
        # of 105 pages were one click from the hub and the certification page --
        # the most linked-to page in the corpus -- was three.
        areas = {item.public_path for item in tree.root.children}
        self.assertEqual([path for path in unreachable if path not in areas], [])
        self.assertGreaterEqual(len(tree.documents) - len(unreachable), len(tree.documents) - 2)

    def test_course_families_are_illustrated_cards_with_their_real_pages(self) -> None:
        hub = docs_hub(docs_navigation_tree())
        body = hub_body(self.client)

        self.assertTrue(hub.families)
        self.assertEqual(body.count("docs-course-card"), len(hub.families))
        for guide in hub.families:
            with self.subTest(public_path=guide.public_path):
                self.assertEqual(guide.page_count, len(guide.pages))
                self.assertIn(f'href="{guide.public_path}"', body)
                self.assertIn(f"{guide.page_count} page", body)
                for page in guide.pages:
                    self.assertIn(f'href="{page.public_path}"', body)
        # Every card carries a drawing: the hub used to be the one index page on
        # the site with no illustration at all, while the six course files sat on
        # disk for the homepage, the tour and the catalogue to draw.
        self.assertEqual(body.count("docs-course-figure"), len(hub.families))
        self.assertIn("doodle-dark", body)

    def test_each_family_card_draws_its_own_course_illustration(self) -> None:
        # The join between the two corpora is the course title, so it survives a
        # course being added and needs no slug map -- and it is lenient about a
        # plural, which is the whole difference between the documentation's "Stock
        # Market Analytics Zoomcamp" and the catalogue's "Stock Markets Analytics
        # Zoomcamp".  The local catalogue is empty in this fixture, so the join
        # itself is exercised against a stubbed one.
        tree = docs_navigation_tree()
        titles = [guide.title for guide in docs_hub(tree).families]
        self.assertTrue(titles)
        catalog = tuple(
            SimpleNamespace(title=title.replace("Zoomcamp", "Zoomcamps"), family=f"slug-{index}")
            for index, title in enumerate(titles)
        )
        with patch("core.home_content.course_catalog", return_value=catalog):
            families = docs_hub(tree).families
        self.assertEqual(
            [guide.family_slug for guide in families],
            [f"slug-{index}" for index in range(len(titles))],
        )

    def test_a_guide_with_sections_opens_its_own_chapter(self) -> None:
        tree = docs_navigation_tree()
        hub = docs_hub(tree)
        body = hub_body(self.client)

        for guide in hub.sectioned:
            with self.subTest(public_path=guide.public_path):
                self.assertTrue(any(section.pages for section in guide.sections))
                self.assertIn(f">{guide.title}</h2>", body)
                for section in guide.sections:
                    self.assertIn(f'href="{section.public_path}"', body)
        for guide in hub.platform:
            with self.subTest(public_path=guide.public_path):
                self.assertFalse(any(section.pages for section in guide.sections))

    def test_chapters_use_the_shared_band_head_not_a_local_restatement(self) -> None:
        body = hub_body(self.client)

        self.assertIn('class="docs-chapter docs-chapter-first', body)
        self.assertIn('class="band-head"', body)
        self.assertNotIn("docs-section-head", body)

    def test_an_area_holding_guides_becomes_one_row_for_each_of_them(self) -> None:
        hub = docs_hub(docs_navigation_tree())
        titles = [row.title for row in hub.community]

        # Activities is six flat pages, so it stays one row with its pages beside
        # it; General holds guides of its own, so drawing it as a single row would
        # hide their pages behind two titles.
        self.assertIn("Activities", titles)
        self.assertNotIn("General", titles)
        self.assertIn("Community Guidelines", titles)

    def test_the_hero_draws_the_reading_illustration_beside_the_title(self) -> None:
        body = hub_body(self.client)

        self.assertIn('class="docs-hero-art"', body)
        self.assertIn("course-learning", body)
        self.assertIn("doodle-light", body)


class DocsSearchTests(TestCase):
    def test_title_matches_rank_above_body_matches(self) -> None:
        results = docs_search_results("certificate")

        self.assertTrue(results)
        self.assertEqual(results[0].title, "Certificate")
        titles = [result.title for result in results]
        body_only = [
            index
            for index, result in enumerate(results)
            if "certificate" not in result.title.casefold()
        ]
        title_matches = [
            index
            for index, result in enumerate(results)
            if "certificate" in result.title.casefold()
        ]
        self.assertTrue(title_matches, titles)
        if body_only:
            self.assertLess(max(title_matches), min(body_only), titles)

    def test_every_result_carries_a_trail_and_a_marked_term(self) -> None:
        results = docs_search_results("certificate")

        for result in results:
            with self.subTest(public_path=result.public_path):
                # The root is the page the reader is already on, so the trail names
                # only the guide levels between it and the result.
                self.assertNotIn("Documentation /", result.trail)
                self.assertTrue(result.trail or result.public_path == DOCS_ROOT_PATH)
                marked = f"{result.title_html} {result.snippet_html}"
                self.assertIn("<mark>", marked)

    def test_marking_escapes_the_page_text_it_marks(self) -> None:
        for result in docs_search_results("a"):
            with self.subTest(public_path=result.public_path):
                self.assertNotIn("<script", result.snippet_html.casefold())
                unmarked = result.snippet_html.replace("<mark>", "").replace("</mark>", "")
                self.assertNotIn("<", unmarked)

    def test_results_render_as_one_link_per_row_under_a_band_head(self) -> None:
        response = self.client.get(DOCS_ROOT_PATH, query_params={"q": "certificate"})

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8").split("</head>", 1)[1]
        rows = re.findall(
            r'<article class="list-row docs-row docs-result-row">.*?</article>', body, re.DOTALL
        )
        self.assertEqual(len(rows), len(response.context["docs_results"]))
        for row in rows:
            with self.subTest(row=row[:80]):
                self.assertEqual(row.count("<a "), 1)
        self.assertIn("<mark>", body)
        self.assertNotIn("Read guide", body)
        self.assertIn('class="band-head"', body)

    def test_the_search_field_is_labelled_and_shows_real_examples(self) -> None:
        response = self.client.get(DOCS_ROOT_PATH)

        self.assertContains(response, 'class="field-label" for="docs-query"')
        self.assertContains(
            response, 'placeholder="e.g. certificate, homework deadline, GCP credits"'
        )
        self.assertNotContains(response, '<label class="sr-only" for="docs-query">')
