"""Person biographies render however many legacy tokens the synced rows carry.

The request path once refused the whole people collection unless the corpus
held exactly the accepted bootstrap's ten legacy target-metadata tokens (audit
ARC-03): cleaning one biography, or publishing a valid smaller corpus, turned
every person page into a server error.  The frozen corpus-wide count now lives
in the reviewed-projection build contract; rendering just cleans each block.
"""

from __future__ import annotations

from community_base.content_sync.models import ContentSource as EngineContentSource
from django.test import TestCase

from content import catalogue
from content.models import SyncedDocument

_LEGACY_TOKEN = 'Read the docs {: target="blank"} today.'
_CLEAN_TEXT = "A biography with no legacy tokens."


class PersonMarkerCleanupTests(TestCase):
    def setUp(self) -> None:
        # The synced-row cache is process-wide; start from cold so the rows
        # this test writes are the ones the read below answers with.
        catalogue._synced_records.cache_clear()
        catalogue._people.cache_clear()

    def _sync_person(self, source: EngineContentSource, slug: str, title: str, text: str) -> None:
        SyncedDocument.objects.create(
            source=source,
            content_kind=catalogue.PEOPLE_KIND,
            stable_key=slug,
            slug=slug,
            title=title,
            summary="",
            public_path=f"/people/{slug}.html",
            source_path=f"_people/{slug}.md",
            checksum="0" * 64,
            record={
                "slug": slug,
                "public_path": f"/people/{slug}.html",
                "title": title,
                "summary": "",
                "blocks": [{"kind": "paragraph", "text": text}],
                "links": [],
                "image_source": "",
                "provenance": {
                    "repository": "DataTalksClub/datatalksclub.github.io",
                    "revision": "0" * 40,
                    "source_path": f"_people/{slug}.md",
                    "source_key": slug,
                    "checksum": "0" * 64,
                },
            },
        )

    def test_a_corpus_with_any_number_of_legacy_tokens_renders(self) -> None:
        source = EngineContentSource.objects.get(slug=catalogue.PEOPLE_SOURCE_SLUG)
        self._sync_person(source, "clean-person", "Clean Person", _CLEAN_TEXT)
        self._sync_person(source, "legacy-person", "Legacy Person", _LEGACY_TOKEN)

        by_slug = {record["slug"]: record for record in catalogue.people()}

        self.assertEqual(
            by_slug["clean-person"]["blocks"][0]["text"],
            _CLEAN_TEXT,
        )
        self.assertNotIn(
            'target="blank"',
            by_slug["legacy-person"]["blocks"][0]["text"],
        )
        # The stored row is left untouched: the cleanup is a copy the read
        # model holds, not an edit to what the database published.
        stored = SyncedDocument.objects.get(
            source=source, content_kind=catalogue.PEOPLE_KIND, stable_key="legacy-person"
        )
        self.assertIn('target="blank"', stored.record["blocks"][0]["text"])
