from __future__ import annotations

from django.test import TestCase

from content.event_description_link_policy import projection_routes_and_fragments
from content.public_data import public_paths
from content.review_projection import review_projection


class CanonicalRouteTests(TestCase):
    def test_slack_route_and_alias_have_exact_method_and_query_contracts(self) -> None:
        query = "x=%2F&x=&q=A+B&q=A%20B"
        canonical = self.client.get("/slack")
        self.assertEqual(canonical.status_code, 200)
        self.assertContains(canonical, '<link rel="canonical" href="https://datatalks.club/slack">')
        self.assertContains(
            canonical, '<meta property="og:url" content="https://datatalks.club/slack">'
        )
        self.assertNotContains(canonical, "datatalks.club/slack.html")
        self.assertEqual(self.client.head("/slack").status_code, 200)

        legacy = self.client.get(f"/slack.html?{query}", follow=False)
        self.assertEqual(legacy.status_code, 301)
        self.assertEqual(legacy["Location"], f"/slack?{query}")
        self.assertNotContains(legacy, "Join our Slack", status_code=301)
        head = self.client.head(f"/slack.html?{query}", follow=False)
        self.assertEqual(head.status_code, 301)
        self.assertEqual(head["Location"], f"/slack?{query}")
        self.assertEqual(head.content, b"")
        final = self.client.get(f"/slack.html?{query}", follow=True)
        self.assertEqual(final.status_code, 200)
        self.assertEqual(final.redirect_chain, [(f"/slack?{query}", 301)])
        self.assertContains(final, '<link rel="canonical" href="https://datatalks.club/slack">')

        for path in ("/slack", "/slack.html"):
            with self.subTest(path=path):
                response = self.client.post(path)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response["Allow"], "GET, HEAD")
                self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
                self.assertEqual(response.content, b"")

    def test_slack_guidelines_alias_matches_the_slack_html_alias_contract(self) -> None:
        query = "x=%2F&x=&q=A+B&q=A%20B"
        legacy = self.client.get(f"/slack/guidelines.html?{query}", follow=False)
        self.assertEqual(legacy.status_code, 301)
        self.assertEqual(legacy["Location"], f"/slack?{query}")
        self.assertEqual(legacy["Cache-Control"], "public, max-age=300")
        head = self.client.head(f"/slack/guidelines.html?{query}", follow=False)
        self.assertEqual(head.status_code, 301)
        self.assertEqual(head["Location"], f"/slack?{query}")
        self.assertEqual(head.content, b"")
        final = self.client.get(f"/slack/guidelines.html?{query}", follow=True)
        self.assertEqual(final.status_code, 200)
        self.assertEqual(final.redirect_chain, [(f"/slack?{query}", 301)])
        self.assertContains(final, '<link rel="canonical" href="https://datatalks.club/slack">')

        response = self.client.post("/slack/guidelines.html")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "GET, HEAD")
        self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
        self.assertEqual(response.content, b"")

        # The alias is exact, not a catch-all: unlisted Slack paths stay real 404s.
        for path in ("/slack/guidelines", "/slack/other.html", "/slack/guidelines.html/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_slack_projection_and_link_policy_use_only_the_canonical_path(self) -> None:
        projection = review_projection()
        self.assertEqual(projection["slack"]["public_path"], "/slack")
        self.assertEqual(
            sum(
                isinstance(record, dict) and record.get("public_path") == "/slack"
                for record in projection.values()
            ),
            1,
        )
        self.assertNotIn("/slack.html", public_paths())
        policy_paths, _ = projection_routes_and_fragments()
        self.assertIn("/slack", policy_paths)
        self.assertNotIn("/slack.html", policy_paths)

    def test_docs_and_faq_replace_legacy_slack_links_when_rendered(self) -> None:
        docs = self.client.get("/docs/general/slack/")
        self.assertEqual(docs.status_code, 200)
        self.assertNotContains(docs, "datatalks.club/slack.html")
        self.assertContains(docs, 'href="/slack"')

        faq = self.client.get("/faq/machine-learning-zoomcamp.html")
        self.assertEqual(faq.status_code, 200)
        self.assertNotContains(faq, "datatalks.club/slack.html")

    def test_docs_and_faq_send_workspace_slack_links_to_the_slack_hub(self) -> None:
        docs = self.client.get("/docs/general/slack/")
        self.assertEqual(docs.status_code, 200)
        self.assertNotContains(docs, "app.slack.com")
        self.assertNotContains(docs, "datatalks-club.slack.com")
        self.assertNotContains(docs, 'href="https://datatalks.club/slack"')
        self.assertContains(docs, 'href="/slack"')
        self.assertContains(docs, 'href="https://slack.com/help/articles/205239967-Join-a-channel"')

        getting_started = self.client.get("/docs/courses/ml-zoomcamp/getting-started/")
        self.assertEqual(getting_started.status_code, 200)
        self.assertNotContains(getting_started, "app.slack.com")
        self.assertContains(getting_started, 'href="/slack"')

        for faq_path in ("/faq/machine-learning-zoomcamp.html", "/faq/llm-zoomcamp.html"):
            with self.subTest(faq_path=faq_path):
                faq = self.client.get(faq_path)
                self.assertEqual(faq.status_code, 200)
                self.assertNotContains(faq, "app.slack.com")
                self.assertNotContains(faq, "datatalks-club.slack.com")
                self.assertContains(faq, 'href="/slack"')

        untouched = self.client.get("/faq/data-engineering-zoomcamp.html")
        self.assertEqual(untouched.status_code, 200)
        self.assertContains(untouched, 'href="https://datatalks.club/docs/general/slack/"')
