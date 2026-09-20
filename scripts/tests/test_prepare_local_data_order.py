"""The orchestrator runs the documented bootstrap order, not a subset of it.

Two things are asserted here, both from `_docs/runbooks/data-ingest.md` §11.

CMP reconciles: it matches its rows against what the course repositories wrote.
Running it first is not merely out of order, it is a different result -- the
CMP-first arrangement worked only while no cohort was described by both sources.

Step 4 -- sponsors and testimonials, from their own reviewed one-time inputs
under `~/prod/dtc-data/content-staging/` (outside this repository) -- has to run
at all, and after the catalogue. Only testimonials used to, so a rehearsal
database came out with no sponsors and nothing in the run said so. The
editorial catalogue (articles, podcasts, books, people, wiki, docs, FAQ) is no
longer part of this step: it reads `content.models.SyncedDocument` rows now,
written by the live `community_base.content_sync` engine, which this offline
rehearsal has no checkout to run against -- see the open decision point in the
runbook's step 4.
"""

from __future__ import annotations

from pathlib import Path

from django.test import TestCase

import scripts.prod

PROD_ROOT = Path(scripts.prod.__file__).resolve().parent
ORCHESTRATOR = PROD_ROOT.parents[1] / "scripts" / "prepare_local_data.py"

# The two modules `_docs/runbooks/data-ingest.md` §11 step 4 names now. Both
# declare BOOTSTRAPS_EMPTY_DATABASE, so the set is checked against that
# declaration rather than repeated as a literal a reader has to trust.
EDITORIAL_ENTRY_POINTS = (
    "import_sponsors",
    "import_testimonials",
)


class LocalPreparationOrderTests(TestCase):
    """The orchestrator must run the upstream before the thing that reconciles."""

    def setUp(self) -> None:
        self.source = ORCHESTRATOR.read_text(encoding="utf-8")

    def test_the_course_repositories_are_pulled_before_cmp_is_reconciled(self) -> None:
        pull = self.source.index("pull_course_repositories(")
        cmp_import = self.source.index("import_cmp_course_content(")
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
        cmp_import = self.source.index("import_cmp_course_content(")
        editorial = self.source.index(
            "editorial_content = _import_sponsor_and_testimonial_content()"
        )
        self.assertLess(
            cmp_import,
            editorial,
            "the reviewed editorial inputs are step 4, after the catalogue in step 3",
        )

    def test_the_gallery_enrichment_import_is_composed(self) -> None:
        """The reviewed one-time gallery enrichment (issue #416) actually runs,
        through the production entry point, in the step 4 block it belongs to.

        It bootstraps its own table and touches no row another step writes, so
        its position inside the block is free; that it runs at all, through the
        same ``run()`` the standalone script calls, is what is pinned here.
        """

        self.assertIn("import_project_repo_enrichment", scripts.prod.BOOTSTRAPPING_ENTRY_POINTS)
        composed = self.source.index("run as import_project_repo_enrichment")
        editorial = self.source.index(
            "editorial_content = _import_sponsor_and_testimonial_content()"
        )
        event_stage = self.source.index("event_pipeline = run_event_pipeline(")
        self.assertGreater(composed, editorial, "step 4 runs after the editorial block")
        self.assertLess(composed, event_stage, "the event stage is step 5 and runs last")

    def test_the_event_stage_is_one_composed_pipeline_after_editorial(self) -> None:
        """§11 step 5 runs whole, last, through the production entry point.

        Event identities and content land before attendee rows are imported.
        """

        editorial = self.source.index(
            "editorial_content = _import_sponsor_and_testimonial_content()"
        )
        event_stage = self.source.index("event_pipeline = run_event_pipeline(")
        self.assertLess(
            editorial,
            event_stage,
            "the event pipeline is §11 step 5 and runs after the editorial block",
        )
        self.assertEqual(
            self.source.count("run_event_pipeline("),
            1,
            "the event stage is one composed pipeline, not a leg list",
        )

    def test_the_orchestrator_composes_no_private_event_leg(self) -> None:
        """Reaching into one leg reorders the pipeline around this caller."""

        for leg in (
            "import_identities(",
            "import_content(",
            "import_new_content(",
            "discover_new_luma_event_identities(",
            "derive_registration_sources(",
            "stage_registration_aggregates(",
            "activate_unambiguous_mappings(",
            "activation_coverage(",
            "load_current_registration_input(",
        ):
            with self.subTest(leg=leg):
                self.assertNotIn(
                    leg,
                    self.source,
                    "the orchestrator must run the whole pipeline, not pick legs",
                )
