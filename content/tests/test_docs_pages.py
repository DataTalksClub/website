"""The two documentation pages a reader actually meets: the hub and a detail page.

``test_docs_projection`` owns the source projection -- what pages exist, where they
live, and what their markdown renders to.  This module owns what the two templates
make of that: how the hub groups 105 pages so they can be found, how search ranks
and explains a result, and how a detail page says where the reader is, what else is
in the guide, and where to go next.
"""

from __future__ import annotations

import re

from django.test import TestCase

from content.docs_presentation import docs_search_results
from content.docs_projection import DOCS_ROOT_PATH


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
