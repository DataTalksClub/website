"""The dataset gate has to see an editorial catalogue that never arrived.

Step 4 of the bootstrap order in `_docs/runbooks/data-ingest.md` §11 fills the
blog, the podcast, the book archive, the profiles, the wiki, the documentation,
the FAQ, the sponsor directory and the homepage testimonials. Nothing in this
gate looked at any of it, so a database that skipped every one of those
importers verified clean while serving an empty site.
"""

from __future__ import annotations

from typing import Any

from django.test import TestCase

from scripts.verify_local_dataset import (
    EXPECTED_EDITORIAL_COLLECTIONS,
    _editorial_content_report,
    _editorial_failures,
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
