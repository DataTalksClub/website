from __future__ import annotations

from django.test import TestCase

from content import catalogue, wiki_reader
from content import projection_helpers as builder
from content.public_graph import safe_public_graph_url


class SponsorArticleChartTests(TestCase):
    path = "/blog/sponsor-datatalks-club.html"

    def test_all_owned_projected_editorial_links_to_this_site_are_root_relative(self) -> None:
        occurrences = [
            (article["slug"], index, field)
            for article in catalogue.articles()
            for index, block in enumerate(article.get("blocks", ()))
            for field in ("markdown", "html")
            if builder._localize_editorial_links(str(block.get(field) or ""))
            != str(block.get(field) or "")
        ]

        for book in catalogue.books():
            for index, thread in enumerate(book.get("archive", ())):
                texts = [thread.get("text", "")]
                texts.extend(reply.get("text", "") for reply in thread.get("replies", ()))
                occurrences.extend(
                    (book["slug"], index, "archive")
                    for text in texts
                    if builder._localize_editorial_links(str(text)) != str(text)
                )

        for page in wiki_reader.wiki_pages():
            occurrences.extend(
                (page["slug"], index, "wiki")
                for index, block in enumerate(page.get("blocks", ()))
                if builder._localize_editorial_links(str(block.get("markdown") or ""))
                != str(block.get("markdown") or "")
            )

        occurrences.extend(
            (podcast["slug"], index, "resource")
            for podcast in catalogue.podcasts()
            for index, resource in enumerate(podcast.get("resources", ()))
            if builder._localize_internal_url(resource["url"]) != resource["url"]
        )

        self.assertEqual(occurrences, [])

    def test_wiki_source_links_render_as_local_navigation(self) -> None:
        response = self.client.get("/wiki/how-to-build-data-pipelines")

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'href="/blog/how-to-setup-lightweight-local-version-for-airflow.html"',
        )
        self.assertNotContains(
            response,
            'href="https://datatalks.club/blog/how-to-setup-lightweight-local-version-for-airflow.html"',
        )

    def test_wiki_graph_keeps_only_bounded_internal_query_and_fragment_urls(self) -> None:
        self.assertEqual(
            safe_public_graph_url("/wiki/search?q=data%20engineering"),
            "/wiki/search?q=data%20engineering",
        )
        self.assertEqual(safe_public_graph_url("/wiki/topic#section-2"), "/wiki/topic#section-2")
        for unsafe in (
            "/wiki/topic?next=https://example.com",
            "/wiki/search?q=one&next=/admin",
            "/wiki/search?q=bad%0Avalue",
            "/wiki/topic#bad fragment",
            "/wiki/topic#bad%0Afragment",
            "/wiki/%0aheader",
        ):
            with self.subTest(url=unsafe):
                self.assertEqual(safe_public_graph_url(unsafe), "")

    def test_sponsor_article_draws_its_charts_as_static_svg_with_their_values(self) -> None:
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        self.assertEqual(body.count('<figure class="prose-chart">'), 4)
        self.assertEqual(body.count('role="img"'), 4)
        for title in ("Roles", "Seniority", "Geography"):
            with self.subTest(chart=title):
                self.assertIn(f'<p class="prose-chart-title">{title}</p>', body)
        self.assertIn(
            'aria-label="Roles. Horizontal bar chart of 6 answers by share of respondents; '
            'highest: Data engineering, 28.5%."',
            body,
        )
        # Every value is both drawn and read out by the hidden table.
        self.assertEqual(body.count(">28.5%</text>"), 1)
        self.assertIn('<th scope="row">Senior individual contributors</th>', body)
        self.assertIn("<td>40.6%</td>", body)
        # A count is not a share.
        self.assertIn(">117</text>", body)
        self.assertNotIn(">117%", body)
        self.assertNotIn("Chart unavailable.", body)
        self.assertNotIn("<canvas", body)
        self.assertIn(
            'href="/blog/datatalks-club-community-demographics.html"',
            body,
        )
