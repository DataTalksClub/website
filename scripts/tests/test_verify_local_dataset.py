"""The dataset gate has to see an editorial catalogue that never arrived.

Step 4 of the bootstrap order in `_docs/runbooks/data-ingest.md` §11 fills the
blog, the podcast, the book archive, the profiles, the wiki, the documentation,
the FAQ, the sponsor directory and the homepage testimonials. Nothing in this
gate looked at any of it, so a database that skipped every one of those
importers verified clean while serving an empty site.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.test import TestCase

if TYPE_CHECKING:
    from content.media_store import LocalMediaStore

from scripts.verify_local_dataset import (
    EXPECTED_EDITORIAL_COLLECTIONS,
    _editorial_content_report,
    _editorial_failures,
    _media_store_failures,
    _media_store_report,
)

# One plausible ingested database, in the shape `_editorial_content_report`
# returns it. The counts are the ones a real fresh ingest produced; the checks
# read them as "present", never as an expected total.
INGESTED: dict[str, Any] = {
    "published_records": {
        "articles": 55,
        "podcasts": 203,
        "books": 98,
        "people": 438,
        "wiki": 282,
        "media": 997,
        "docs": 106,
        "faq": 6,
    },
    "empty_collections": [],
    "faq_section_total": 70,
    "content_asset_total": 39,
    "sponsor_total": 33,
    "testimonial_total": 6,
}


def _without(**overrides: Any) -> dict[str, Any]:
    report = {**INGESTED, "published_records": dict(INGESTED["published_records"])}
    for key, value in overrides.items():
        if key in report["published_records"]:
            report["published_records"][key] = value
        else:
            report[key] = value
    report["empty_collections"] = [
        name for name in EXPECTED_EDITORIAL_COLLECTIONS if not report["published_records"][name]
    ]
    return report


class EditorialFailureTests(TestCase):
    """What the gate does with a report, independent of any database."""

    def test_a_fully_ingested_catalogue_passes(self) -> None:
        self.assertEqual(_editorial_failures(INGESTED), [])

    def test_an_empty_catalogue_fails_and_names_every_importer(self) -> None:
        empty = _without(
            **{name: 0 for name in EXPECTED_EDITORIAL_COLLECTIONS},
            faq_section_total=0,
            content_asset_total=0,
            sponsor_total=0,
            testimonial_total=0,
        )

        failures = _editorial_failures(empty)

        self.assertTrue(failures)
        joined = " ".join(failures)
        for importer in (
            "import-editorial-content",
            "import_docs.py",
            "import_faq.py",
            "import_sponsors.py",
            "import_testimonials.py",
        ):
            with self.subTest(importer=importer):
                self.assertIn(importer, joined)

    def test_one_missing_collection_is_enough_to_fail(self) -> None:
        """A half-ingested catalogue is a failure, not a rounding error."""

        for collection in EXPECTED_EDITORIAL_COLLECTIONS:
            with self.subTest(collection=collection):
                failures = _editorial_failures(_without(**{collection: 0}))
                self.assertTrue(failures)
                self.assertIn(collection, failures[0])


class EditorialReportTests(TestCase):
    """The report itself, read from a database through the public readers."""

    def test_a_database_that_skipped_step_four_reports_every_collection_empty(self) -> None:
        from content.models import (
            ActiveContentPath,
            ContentAsset,
            ContentDocument,
            ContentRelation,
            ContentSource,
        )
        from core.models import Sponsor
        from courses.models import Testimonial

        # Releases and the registry rows are left alone: a release nothing
        # points at publishes nothing, which is the state a database is in
        # before its first editorial import and after a failed one alike.
        ActiveContentPath.objects.all().delete()
        ContentRelation.objects.all().delete()
        ContentDocument.objects.all().delete()
        ContentAsset.objects.all().delete()
        ContentSource.objects.update(active_release=None)
        Sponsor.objects.all().delete()
        Testimonial.objects.all().delete()

        report = _editorial_content_report()

        self.assertEqual(list(report["empty_collections"]), list(EXPECTED_EDITORIAL_COLLECTIONS))
        self.assertEqual(report["active_catalogue_release"], "")
        self.assertTrue(_editorial_failures(report))

    def test_the_step_four_importers_make_the_same_report_pass(self) -> None:
        from scripts.prod.import_docs import run as import_docs
        from scripts.prod.import_faq import run as import_faq
        from scripts.prod.import_public_content import run as import_public_content
        from scripts.prod.import_sponsors import run as import_sponsors
        from scripts.prod.import_testimonials import run as import_testimonials

        for importer in (
            import_public_content,
            import_faq,
            import_docs,
            import_sponsors,
            import_testimonials,
        ):
            importer()

        report = _editorial_content_report()

        self.assertEqual(report["empty_collections"], [])
        self.assertEqual(_editorial_failures(report), [])
        # The row totals are context, not a gate: a re-import supersedes a
        # release rather than replacing its rows, so the table outgrows what
        # the site publishes and only the published counts can be asserted.
        self.assertGreaterEqual(
            report["content_document_total"], sum(report["published_records"].values())
        )


class MediaStoreFailureDecisionTests(TestCase):
    """The store-side gate decision, independent of any store."""

    def test_a_real_store_with_missing_objects_fails_and_names_the_recovery(self) -> None:

        failures = _media_store_failures(
            {
                "backend": "local",
                "synthetic_fixture": False,
                "missing_count": 3,
                "mismatched_count": 0,
                "unreadable_count": 0,
                "recovery_command": (
                    "uv run --frozen python scripts/prod/sync_public_media_hydrate.py"
                ),
            }
        )

        self.assertEqual(len(failures), 1)
        self.assertIn("3 missing", failures[0])
        self.assertIn("sync_public_media_hydrate.py", failures[0])

    def test_a_synthetic_fixture_never_fails_and_never_claims_verification(self) -> None:

        failures = _media_store_failures(
            {
                "backend": "memory",
                "synthetic_fixture": True,
                "missing_count": 997,
                "mismatched_count": 0,
                "unreadable_count": 0,
            }
        )

        self.assertEqual(failures, [])

    def test_a_clean_real_store_passes(self) -> None:

        self.assertEqual(
            _media_store_failures(
                {
                    "backend": "local",
                    "synthetic_fixture": False,
                    "missing_count": 0,
                    "mismatched_count": 0,
                    "unreadable_count": 0,
                }
            ),
            [],
        )


class MediaStoreReportTests(TestCase):
    """Byte-level verification against a real local store (audit REL-14).

    The store is injected, so these tests drive real filesystem objects
    without touching deployment settings.  The test runtime denies network
    access and the reviewed artwork lives in a hydrated store, so the fixtures
    here are an empty store and a corrupt object -- exactly the two states the
    audit asks the gate to distinguish -- rather than downloaded media.
    """

    def _store(self, root: Path) -> LocalMediaStore:
        from content.media_store import LocalMediaStore

        return LocalMediaStore(root=root, maximum_object_bytes=10_000_000)

    def _imported_media(self) -> tuple[Any, tuple[dict[str, Any], ...]]:
        from scripts.prod.import_faq import run as import_faq
        from scripts.prod.import_public_content import run as import_public_content
        from scripts.prod.import_sponsors import run as import_sponsors
        from scripts.prod.import_testimonials import run as import_testimonials

        for importer in (
            import_public_content,
            import_faq,
            import_sponsors,
            import_testimonials,
        ):
            importer()
        from content import catalogue

        records = catalogue.media()
        self.assertGreater(len(records), 0, "the reviewed projection carries media")
        return None, records

    def test_an_empty_store_fails_with_every_record_missing(self) -> None:
        self._imported_media()
        import tempfile

        root = Path(tempfile.mkdtemp(dir=Path(".tmp")))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))

        report = _media_store_report(store=self._store(root))

        self.assertFalse(report["synthetic_fixture"])
        self.assertEqual(report["missing_count"], report["record_total"])
        self.assertIn("recovery_command", report)
        failures = _media_store_failures(report)
        self.assertTrue(failures)
        self.assertIn("sync_public_media_hydrate.py", failures[0])

    def test_a_corrupt_object_is_checksum_mismatched_not_missing(self) -> None:
        self._imported_media()
        import tempfile

        root = Path(tempfile.mkdtemp(dir=Path(".tmp")))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        store = self._store(root)

        from content import catalogue

        first = catalogue.media()[0]
        corrupt = store.path_for(first)
        corrupt.parent.mkdir(parents=True, exist_ok=True)
        corrupt.write_bytes(b"not the reviewed artwork")

        report = _media_store_report(store=store)

        self.assertEqual(report["mismatched_count"], 1)
        self.assertIn(first["record_key"], report["mismatched"])
        # The corrupt object proves the verifier read bytes: the rest of the
        # empty store is missing, not matched.
        self.assertEqual(report["matched"], 0)

    def test_a_memory_store_is_marked_synthetic(self) -> None:
        from content.media_store import MemoryMediaStore

        # Injected, not resolved from settings: the synthetic marking follows
        # the store itself, whatever backend the environment selected.
        report = _media_store_report(store=MemoryMediaStore())

        self.assertEqual(report["backend"], "memory")
        self.assertTrue(report["synthetic_fixture"])
        # Verification against fixtures gates nothing, either way.
        self.assertEqual(_media_store_failures(report), [])


class CompleteObjectPassesTests(TestCase):
    """A store holding the exact reviewed bytes verifies clean.

    The bytes and their record are built here -- hashing the payload into the
    record's provenance is precisely what the projection build does -- so no
    real media is downloaded and no preimage is needed.
    """

    def test_a_complete_object_matches_and_the_report_is_clean(self) -> None:
        import hashlib
        import tempfile

        from content.media_store import LocalMediaStore
        from content.media_tooling import verify_media

        payload = b"fixture-artwork-bytes"
        record = {
            "record_key": "images/unit/fixture.png",
            "public_path": "/media/unit/fixture.png",
            "provenance": {
                "checksum": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            },
        }
        root = Path(tempfile.mkdtemp(dir=Path(".tmp")))
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        store = LocalMediaStore(root=root, maximum_object_bytes=10_000_000)
        path = store.path_for(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

        report = verify_media(store=store, records=[record])

        self.assertTrue(report.clean)
        self.assertEqual(report.matched, 1)
