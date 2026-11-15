from community_base.content_sync.media import media_store
from community_base.content_sync.parsers import get_parser

from content.models import SyncedDocument
from content.tests.test_sync_parsers import (
    _CheckoutCase,
    _episode_yaml,
    _source,
    _transcript_yaml,
)


class PodcastSlugImportTests(_CheckoutCase):
    def test_markdown_suffix_is_removed_without_renaming_source_identity(self):
        source = _source("dtc-content")
        SyncedDocument.objects.filter(source=source, content_kind="podcast").delete()
        parser = get_parser("podcast")
        tree = {
            "podcasts/s03/e11.yaml": _episode_yaml().replace(b"test-episode", b"test-episode.md"),
            "podcasts/s03/e11-transcript.yaml": _transcript_yaml().replace(
                b"test-episode", b"test-episode.md"
            ),
            "images/podcast/test-episode.md.jpg": b"jpeg-bytes",
        }
        with self.checkout(tree) as checkout:
            (item,) = parser.discover(checkout, source)
            self.assertEqual(item.key, "test-episode.md")
            record = item.data["record"]
            self.assertEqual(record["slug"], "test-episode")
            self.assertEqual(record["public_path"], "/podcast/s03e11/test-episode")
            self.assertEqual(record["provenance"]["source_key"], "test-episode.md")
            parser.upsert(item, source, media_store())
        row = SyncedDocument.objects.get(source=source, stable_key="test-episode.md")
        self.assertEqual(row.slug, "test-episode")
        self.assertEqual(row.record["transcript"][0]["line"], "Hello and welcome.")
        self.assertEqual(parser.soft_delete_missing({item.key}, source), 0)
