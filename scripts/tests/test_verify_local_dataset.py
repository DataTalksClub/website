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

import pytest
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
            # The catalogue-wide failure names its recovery, not an old
            # command spelling: "run the editorial import scripts".
            "editorial import scripts",
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
        import json
        import tempfile

        from scripts.prod.import_docs import run as import_docs
        from scripts.prod.import_faq import run as import_faq
        from scripts.prod.import_sponsors import run as import_sponsors
        from scripts.prod.import_testimonials import run as import_testimonials
        from test_support.reference_data import (
            DOCS_PROJECTION,
            FAQ_PROJECTION,
            HOMEPAGE_TESTIMONIALS,
            load_reviewed_public_content,
        )

        # The reviewed snapshot lives outside this repository, so the importers
        # run against the synthetic reference sources the test database itself
        # is seeded from: the real write paths over a smaller checked-in input.
        # The sponsor directory has no checked-in fixture, so the test writes
        # one synthetic entry.
        scratch = Path(tempfile.mkdtemp(prefix="step-four-importers-", dir=Path(".tmp")))
        self.addCleanup(lambda: shutil.rmtree(scratch, ignore_errors=True))
        sponsor_directory = scratch / "sponsor_directory.json"
        sponsor_directory.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "sponsors": [
                        {
                            "key": "synthetic-sponsor",
                            "name": "Synthetic Sponsor",
                            "url": "https://synthetic-sponsor.example/",
                            "lifecycle": "active",
                            "description": ("A synthetic sponsor entry for the ingest gate."),
                            "logo_asset_key": "sponsors/synthetic-sponsor.png",
                            "position": 1,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        for importer in (
            load_reviewed_public_content,
            lambda: import_faq(path=FAQ_PROJECTION),
            lambda: import_docs(path=DOCS_PROJECTION),
            lambda: import_sponsors(path=sponsor_directory),
            lambda: import_testimonials(path=HOMEPAGE_TESTIMONIALS),
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
        # The synthetic reference catalogue stands in for the reviewed
        # projection: the same production write path, a checked-in input the
        # corpus-less CI job can read.
        from test_support.reference_data import load_reviewed_public_content

        load_reviewed_public_content()
        from content import catalogue

        records = catalogue.media()
        self.assertGreater(len(records), 0, "the synthetic catalogue carries media")
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
        import hashlib
        from unittest.mock import patch

        from content.media_store import MemoryMediaStore

        payload = b"synthetic-fixture-artwork"
        synthetic_records = (
            {
                "content_type": "image/png",
                "record_key": "images/synthetic-a.png",
                "public_path": "/images/synthetic-a.png",
                "provenance": {
                    "repository": "DataTalksClub/content",
                    "revision": "a" * 40,
                    "checksum": hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                },
            },
        )
        with (
            patch(
                "content.media_tooling.media_records",
                return_value=synthetic_records,
            ),
            patch(
                "content.media_store.media_records",
                return_value=synthetic_records,
            ),
        ):
            # Injected, not resolved from settings: the synthetic marking
            # follows the store itself, whatever backend the environment
            # selected.
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


def _write_manifest(directory: Path, payload: Any) -> Path:
    import json

    path = directory / "expectations.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _manifest(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "as_of": "2026-09-02",
        "source": {"export": "rds-prod-synthetic"},
        "cohorts": {
            "expected": ["ai-dev-tools-2026", "de-zoomcamp-2026"],
            "module_curricula": {"de-zoomcamp-2026": {"modules": 7, "units": 72}},
        },
    }
    payload.update(overrides)
    return payload


def _base_report() -> dict[str, Any]:
    return {
        "courses": {
            "missing_expected_cohorts": [],
            "modules_format_unexpected": [],
            "curriculum_count_mismatches": {},
            "forbidden_cohorts_present": [],
            "split_course_families": {},
            "empty_course_families": [],
        },
        "content": {
            "practice_assignment_homeworks": 0,
            "generated_project_descriptions": 0,
            "homework_total": 1,
            "question_total": 1,
        },
        "editorial": {
            "empty_collections": [],
            "content_asset_total": 1,
            "faq_section_total": 1,
            "sponsor_total": 1,
            "testimonial_total": 1,
        },
        "media_store": {"synthetic_fixture": True},
    }


def test_the_reviewed_manifest_loads(tmp_path: Path) -> None:
    """The reviewed manifest drives the gate, not code literals (REL-15)."""

    from scripts.verify_local_dataset import _load_expectations

    expectations = _load_expectations(_write_manifest(tmp_path, _manifest()))

    assert expectations["as_of"].isoformat() == "2026-09-02"
    assert expectations["module_curricula"]["de-zoomcamp-2026"] == (7, 72)


def test_an_unknown_schema_is_refused_clearly(tmp_path: Path) -> None:
    from scripts.verify_local_dataset import ExpectationError, _load_expectations

    with pytest.raises(ExpectationError, match="schema_version"):
        _load_expectations(_write_manifest(tmp_path, _manifest(schema_version=2)))


def test_malformed_manifests_are_refused_with_the_exact_problem(tmp_path: Path) -> None:
    from scripts.verify_local_dataset import ExpectationError, _load_expectations

    bad_as_of = _manifest()
    bad_as_of["as_of"] = "September 2, 2026"
    missing_block = _manifest()
    del missing_block["cohorts"]["module_curricula"]
    unlisted = _manifest()
    unlisted["cohorts"]["module_curricula"]["unknown-2027"] = {"modules": 1, "units": 1}
    negative = _manifest()
    negative["cohorts"]["module_curricula"]["de-zoomcamp-2026"] = {"modules": -1, "units": 0}

    for name, payload in (
        ("bad as_of", bad_as_of),
        ("missing block", missing_block),
        ("curriculum for an unlisted cohort", unlisted),
        ("negative total", negative),
    ):
        manifest_path = _write_manifest(tmp_path / name.replace(" ", "-"), payload)
        with pytest.raises(ExpectationError):
            _load_expectations(manifest_path)


def test_a_new_delivery_year_is_accepted_by_a_manifest_edit_alone(tmp_path: Path) -> None:
    from scripts.verify_local_dataset import _failures, _load_expectations

    manifest = _manifest()
    manifest["cohorts"]["expected"] = [
        "ai-dev-tools-2026",
        "de-zoomcamp-2026",
        "de-zoomcamp-2027",
    ]
    manifest["cohorts"]["module_curricula"]["de-zoomcamp-2027"] = {
        "modules": 9,
        "units": 105,
    }
    expectations = _load_expectations(_write_manifest(tmp_path, manifest))
    report = _base_report()
    report["events"] = {"events_on_or_after_as_of": 3}

    assert _failures(report, expectations) == []


def test_the_event_horizon_gate_binds_to_as_of_never_to_the_wall_clock(
    tmp_path: Path,
) -> None:
    from scripts.verify_local_dataset import _failures, _load_expectations

    expectations = _load_expectations(_write_manifest(tmp_path, _manifest()))

    # Everything has already happened relative to today -- and that must not
    # fail: the gate asks about the reviewed snapshot's horizon.
    aged = _base_report()
    aged["events"] = {"future_dated_events": 0, "events_on_or_after_as_of": 3}
    assert _failures(aged, expectations) == []

    # The import that never carried a forward horizon is the failure.
    horizon_less = _base_report()
    horizon_less["events"] = {"future_dated_events": 0, "events_on_or_after_as_of": 0}
    failures = _failures(horizon_less, expectations)
    assert len(failures) == 1
    assert "2026-09-02" in failures[0]
