"""The orchestrator runs the documented bootstrap order, not a subset of it.

Two things are asserted here, both from `_docs/runbooks/data-ingest.md` §11.

CMP reconciles: it matches its rows against what the course repositories wrote.
Running it first is not merely out of order, it is a different result -- the
CMP-first arrangement worked only while no cohort was described by both sources.

Step 4 -- the reviewed editorial inputs under `temporary/content/` -- has to run
at all, and after the catalogue. Only testimonials used to, so a rehearsal
database came out with no articles, podcasts, books, people, wiki, docs, FAQ or
sponsors, and nothing in the run said so.
"""

from __future__ import annotations

from pathlib import Path

from django.test import TestCase

import scripts.prod

PROD_ROOT = Path(scripts.prod.__file__).resolve().parent
ORCHESTRATOR = PROD_ROOT.parents[1] / "scripts" / "prepare_local_data.py"

# The five modules `_docs/runbooks/data-ingest.md` §11 step 4 names. Every one
# declares BOOTSTRAPS_EMPTY_DATABASE, so the set is checked against that
# declaration rather than repeated as a literal a reader has to trust.
EDITORIAL_ENTRY_POINTS = (
    "import_public_content",
    "import_faq",
    "import_docs",
    "import_sponsors",
    "import_testimonials",
)


class LocalPreparationOrderTests(TestCase):
    """The orchestrator must run the upstream before the thing that reconciles."""

    def setUp(self) -> None:
        self.source = ORCHESTRATOR.read_text(encoding="utf-8")

    def test_the_course_repositories_are_pulled_before_cmp_is_reconciled(self) -> None:
        pull = self.source.index("pull_course_repositories(")
        cmp_import = self.source.index("import_cmp_course_content(cmp_source_db")
        self.assertLess(
            pull,
            cmp_import,
            "CMP reconciles against what the repositories wrote, so it runs second",
        )

    def test_every_step_four_importer_is_composed(self) -> None:
        for entry_point in EDITORIAL_ENTRY_POINTS:
            with self.subTest(entry_point=entry_point):
                self.assertIn(
                    f"from scripts.prod.{entry_point} import run",
                    self.source,
                    "step 4 of the documented bootstrap order must actually run",
                )

    def test_the_step_four_importers_all_bootstrap_an_empty_database(self) -> None:
        """They are safe to run as one block only because none reconciles."""

        for entry_point in EDITORIAL_ENTRY_POINTS:
            with self.subTest(entry_point=entry_point):
                self.assertIn(entry_point, scripts.prod.BOOTSTRAPPING_ENTRY_POINTS)

    def test_the_editorial_content_is_imported_after_the_course_catalogue(self) -> None:
        cmp_import = self.source.index("import_cmp_course_content(cmp_source_db")
        editorial = self.source.index("editorial_content = _import_editorial_content()")
        self.assertLess(
            cmp_import,
            editorial,
            "the reviewed editorial inputs are step 4, after the catalogue in step 3",
        )
