"""End-to-end cover for the project-gallery repository enrichment import.

The fixtures are synthetic repositories built in the project-local ``.tmp/``:
no real export, no real write-up.  They assert the properties the importer
promises -- it populates an empty database with the enrichment rows and their
attached structured records, a replay writes nothing, and a staged pair that
disagrees is refused before anything is written.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from django.test import TestCase

import scripts.prod
from courses.models import ProjectRepoEnrichment
from scripts.prod.import_project_repo_enrichment import (
    ProjectRepoEnrichmentImportFailure,
    run,
)

PROD_ROOT = Path(scripts.prod.__file__).resolve().parent

ENRICHMENT_ROWS = [
    {
        "repo": "alice/capstone",
        "url": "https://github.com/alice/capstone",
        "effective_url": "https://github.com/alice/capstone-v2",
        "availability": "live",
        "what_it_is": "A end-to-end pipeline with dbt and BigQuery.",
        "interesting": "Compares two orchestration approaches.",
        "why_check": "A clean reference for the terraform-mage-dbt pattern.",
        "improvements": "",
        "topics": "dbt,bigquery,terraform",
        "card_summary": "An ad-performance pipeline on GCP.",
        "confidence": "high",
    },
    {
        "repo": "alice/homework-dump",
        "availability": "live",
        "card_summary": "Homework submissions for the course.",
        "what_it_is": "",
        "interesting": "",
        "why_check": "",
        "improvements": "",
        "topics": "",
        "confidence": "low",
    },
    {
        "repo": "bob/gone-repo",
        "availability": "404",
        "card_summary": "",
        "what_it_is": "",
        "interesting": "",
        "why_check": "",
        "improvements": "",
        "topics": "",
        "confidence": "baseline",
    },
]

STRUCTURED_ROWS = [
    {
        "repo": "alice/capstone",
        "course": "Data Engineering Zoomcamp 2024",
        "schema": "course-structured-v1",
        "confidence": "high",
        "technologies": {
            "value": ["dbt", "bigquery", "terraform"],
            "confidence": "high",
            "evidence": 'README: "dbt models on BigQuery, deployed with Terraform"',
        },
    },
    {
        "repo": "alice/homework-dump",
        "course": "Data Engineering Zoomcamp 2024",
        "schema": "course-structured-v1",
        "confidence": "low",
    },
]


class ProjectRepoEnrichmentImportTests(TestCase):
    """End-to-end against synthetic staging files."""

    def setUp(self) -> None:
        super().setUp()
        # Scratch data belongs in the project-local .tmp/, never a system temp dir.
        scratch_root = PROD_ROOT.parents[1] / ".tmp"
        scratch_root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="repo-enrichment-test-", dir=scratch_root))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.enrichment_source = self.root / "enrichment.jsonl"
        self.structured_source = self.root / "structured.jsonl"
        self._write(self.enrichment_source, ENRICHMENT_ROWS)
        self._write(self.structured_source, STRUCTURED_ROWS)

    @staticmethod
    def _write(path: Path, rows: list[dict]) -> None:
        path.write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n",
            encoding="utf-8",
        )

    def _run(self, *, apply: bool = True) -> dict:
        return run(
            enrichment_source=self.enrichment_source,
            structured_source=self.structured_source,
            apply=apply,
        )

    def test_bootstraps_an_empty_database(self) -> None:
        report = self._run()

        self.assertTrue(report["applied"])
        self.assertEqual(
            report["enrichment"], {"total": 3, "created": 3, "updated": 0, "unchanged": 0}
        )
        self.assertEqual(
            report["structured"], {"total": 2, "created": 2, "updated": 0, "unchanged": 0}
        )
        capstone = ProjectRepoEnrichment.objects.get(repo_lower="alice/capstone")
        self.assertEqual(capstone.effective_url, "https://github.com/alice/capstone-v2")
        self.assertFalse(capstone.is_unavailable)
        self.assertFalse(capstone.is_coursework)
        self.assertEqual(capstone.topics, "dbt,bigquery,terraform")
        self.assertEqual(capstone.structured, STRUCTURED_ROWS[0])
        # Coursework is the name heuristic, not the write-up: the dump is
        # labelled coursework, the capstone never is.
        self.assertTrue(
            ProjectRepoEnrichment.objects.get(repo_lower="alice/homework-dump").is_coursework
        )
        gone = ProjectRepoEnrichment.objects.get(repo_lower="bob/gone-repo")
        self.assertTrue(gone.is_unavailable)
        self.assertIsNone(gone.structured)

    def test_a_replay_writes_nothing_and_reports_unchanged(self) -> None:
        self._run()

        replay = self._run()

        self.assertEqual(
            replay["enrichment"], {"total": 3, "created": 0, "updated": 0, "unchanged": 3}
        )
        self.assertEqual(
            replay["structured"], {"total": 2, "created": 0, "updated": 0, "unchanged": 2}
        )
        self.assertEqual(ProjectRepoEnrichment.objects.count(), 3)

    def test_a_changed_write_up_updates_in_place(self) -> None:
        self._run()
        changed = [dict(ENRICHMENT_ROWS[0], confidence="medium")]
        self._write(self.enrichment_source, [*changed, *ENRICHMENT_ROWS[1:]])

        report = self._run()

        self.assertEqual(report["enrichment"]["updated"], 1)
        self.assertEqual(report["enrichment"]["unchanged"], 2)
        self.assertEqual(
            ProjectRepoEnrichment.objects.get(repo_lower="alice/capstone").confidence, "medium"
        )

    def test_a_dry_run_validates_and_writes_nothing(self) -> None:
        report = self._run(apply=False)

        self.assertFalse(report["applied"])
        self.assertEqual(report["enrichment"]["created"], 3)
        self.assertEqual(ProjectRepoEnrichment.objects.count(), 0)

    def test_a_structured_record_without_an_enrichment_row_is_refused(self) -> None:
        self._write(
            self.structured_source,
            [*STRUCTURED_ROWS, {"repo": "carol/orphan", "schema": "course-structured-v1"}],
        )

        with self.assertRaises(ProjectRepoEnrichmentImportFailure) as caught:
            self._run()

        self.assertIn("structured_without_enrichment_row", str(caught.exception))
        self.assertEqual(ProjectRepoEnrichment.objects.count(), 0)

    def test_an_unexpected_structured_schema_is_refused(self) -> None:
        self._write(
            self.structured_source,
            [{**STRUCTURED_ROWS[0], "schema": "course-structured-v2"}],
        )

        with self.assertRaises(ProjectRepoEnrichmentImportFailure) as caught:
            self._run()

        self.assertIn("unexpected_structured_schema", str(caught.exception))

    def test_a_duplicate_repo_is_refused(self) -> None:
        self._write(self.enrichment_source, [*ENRICHMENT_ROWS, ENRICHMENT_ROWS[0]])

        with self.assertRaises(ProjectRepoEnrichmentImportFailure) as caught:
            self._run()

        self.assertIn("duplicate_enrichment_repo", str(caught.exception))
