from __future__ import annotations

from django.test import TestCase


class CanonicalRouteTests(TestCase):
    def test_slack_html_is_removed_from_the_public_surface(self) -> None:
        canonical = self.client.get("/slack")
        self.assertEqual(canonical.status_code, 200)
        self.assertContains(canonical, '<link rel="canonical" href="https://datatalks.club/slack">')

        legacy = self.client.get("/slack.html")
        self.assertEqual(legacy.status_code, 301)
        self.assertEqual(legacy["Location"], "/slack")

    def test_docs_and_faq_replace_legacy_slack_links_when_rendered(self) -> None:
        docs = self.client.get("/docs/general/slack/")
        self.assertEqual(docs.status_code, 200)
        self.assertNotContains(docs, "datatalks.club/slack.html")
        self.assertContains(docs, 'href="/slack"')

        faq = self.client.get("/faq/machine-learning-zoomcamp.html")
        self.assertEqual(faq.status_code, 200)
        self.assertNotContains(faq, "datatalks.club/slack.html")
