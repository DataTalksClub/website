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
