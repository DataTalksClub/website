from __future__ import annotations

from django.contrib.staticfiles import finders
from django.test import TestCase

from content import catalogue
from content.public_graph import safe_public_graph_url
from scripts import build_public_projection as builder


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

        for page in catalogue.wiki_pages():
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

    def test_sponsor_article_renders_four_local_accessible_charts(self) -> None:
        response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        expected = {
            "sponsor-roles.svg": "Pie chart of DataTalks.Club community roles",
            "sponsor-seniority.svg": "Pie chart of DataTalks.Club community seniority",
            "sponsor-geography.svg": "Pie chart of the DataTalks.Club community by region",
            "sponsor-industries.svg": (
                "Pie chart of industries represented in the DataTalks.Club community"
            ),
        }
        for filename, alt in expected.items():
            with self.subTest(chart=filename):
                asset = f"content/article-charts/{filename}"
                self.assertIsNotNone(finders.find(asset))
                self.assertIn(f'src="/static/{asset}"', body)
                self.assertIn(f'alt="{alt}"', body)

        self.assertNotIn("Chart unavailable.", body)
        self.assertIn(
            'href="/blog/datatalks-club-community-demographics.html"',
            body,
        )
