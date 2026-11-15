"""Subscription compatibility: ten latest published blog posts, with full bodies."""

import xml.etree.ElementTree as ET

from community_base.content_sync.models import ContentSource
from django.test import TestCase

from content import catalogue
from content.models import SyncedDocument

ATOM = {"a": "http://www.w3.org/2005/Atom"}


class SiteFeedTests(TestCase):
    def setUp(self):
        self.source = ContentSource.objects.get(slug="dtc-content")
        SyncedDocument.objects.filter(source=self.source).delete()
        catalogue._synced_records.cache_clear()

    def article(self, number, *, published=True, kind="article"):
        slug = f"feed-test-{number}"
        return SyncedDocument.objects.create(
            source=self.source,
            content_kind=kind,
            stable_key=slug,
            slug=slug,
            title=f"Post {number} & <news>",
            public_path=f"/blog/{slug}.html",
            source_path=f"articles/{slug}.md",
            checksum="a" * 64,
            is_published=published,
            record={
                "slug": slug,
                "public_path": f"/blog/{slug}.html",
                "title": f"Post {number} & <news>",
                "published": f"2026-01-{number:02d}",
                "blocks": [
                    {
                        "kind": "paragraph",
                        "text": "Full article body & details.",
                        "markdown": "Full **article** body & details.",
                    }
                ],
            },
        )

    def feed(self):
        response = self.client.get("/feed.xml")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/atom+xml; charset=utf-8")
        return ET.fromstring(response.content)

    def test_latest_ten_blog_posts_with_stable_ids_dates_and_full_content(self):
        for number in range(1, 13):
            self.article(number)
        self.article(20, published=False)
        self.article(21, kind="podcast")
        self.article(22, kind="book")
        root = self.feed()
        entries = root.findall("a:entry", ATOM)
        self.assertEqual(len(entries), 10)
        self.assertEqual(
            [e.findtext("a:title", namespaces=ATOM) for e in entries],
            [f"Post {n} & <news>" for n in range(12, 2, -1)],
        )
        for entry, number in zip(entries, range(12, 2, -1), strict=True):
            link = entry.find("a:link", ATOM).get("href")
            self.assertTrue(link.endswith(f"/blog/feed-test-{number}.html"))
            self.assertEqual(entry.findtext("a:id", namespaces=ATOM), link[:-5])
            self.assertEqual(
                entry.findtext("a:published", namespaces=ATOM),
                f"2026-01-{number:02d}T00:00:00+00:00",
            )
            content = entry.find("a:content", ATOM)
            self.assertEqual(content.get("type"), "html")
            self.assertEqual(content.get("{http://www.w3.org/XML/1998/namespace}base"), link)
            self.assertIn("<strong>article</strong>", content.text)
            self.assertIn("body &amp; details.", content.text)

    def test_empty_database_publishes_valid_empty_feed(self):
        root = self.feed()
        self.assertEqual(root.findtext("a:title", namespaces=ATOM), "DataTalks.Club")
        self.assertEqual(root.findall("a:entry", ATOM), [])

    def test_database_updates_and_unpublishing_are_visible(self):
        row = self.article(1)
        self.feed()
        row.record["title"] = "Updated title"
        row.save()
        self.assertEqual(self.feed().findtext("a:entry/a:title", namespaces=ATOM), "Updated title")
        row.is_published = False
        row.save()
        self.assertEqual(self.feed().findall("a:entry", ATOM), [])

    def test_head_and_method_contract(self):
        self.assertEqual(self.client.head("/feed.xml").status_code, 200)
        self.assertEqual(self.client.post("/feed.xml").status_code, 405)
