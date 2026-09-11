"""ml-zoomcamp-2021's scoring and certificate identities must merge (REL-*).

2021 predates zoomcamp-scoring's plaintext ``graduates.csv`` convention: its
real roster lives only at
``courses/mlzoomcamp-2021/mlzoomcamp-2021-names.csv`` (see
``editions._build_ml_zoomcamp_2021``), separate from the raw weekly form
exports under ``old/ml-zoomcamp/`` that ``email_recovery.py`` otherwise scans
to recover a scoring learner's real email from their upstream ``sha1(email)``
hash.

A graduate whose real email is recoverable only from the roster -- never from
a matching raw-form row -- used to get two separate identities: a synthetic,
no-real-email account from ``scoring_import.py`` (since ``email_recovery``
never saw the roster) and a second, real-email-backed account from
``certificate_import.py`` (which reads the roster directly). Both
``_build_pipeline_edition`` (2022/2023) already feeds its ``certificate_csvs``
into ``email_source_csvs`` for exactly this reason; ``_build_ml_zoomcamp_2021``
did not. These tests build a minimal fake ``zoomcamp-scoring`` checkout,
via ``build_editions`` (so any future regression in the real discovery
function is caught, not just in a hand-built ``EditionSource``), and prove
the scoring and certificate identities land on one account.
"""

from __future__ import annotations

import csv
import shutil
import tempfile
from pathlib import Path

from django.test import TestCase

import scripts.prod
from courses.models import Enrollment, ProjectSubmission, Submission
from scripts.prod.legacy_zoomcamp.certificate_import import import_edition_certificates
from scripts.prod.legacy_zoomcamp.editions import build_editions
from scripts.prod.legacy_zoomcamp.identity import sha1_hex
from scripts.prod.legacy_zoomcamp.scoring_import import import_edition_scoring

PROD_ROOT = Path(scripts.prod.__file__).resolve().parent

# Deliberately not present in any raw-form export below -- only the roster
# knows this address, matching how a real historical graduate can go
# unmatched by any raw weekly form row.
ROSTER_ONLY_EMAIL = "roster-only-graduate@example.invalid"
ROSTER_ONLY_NAME = "Roster Only Graduate"


class MlZoomcamp2021EditionDiscoveryTests(TestCase):
    """``build_editions`` must feed the real roster into email recovery too."""

    def setUp(self) -> None:
        super().setUp()
        scratch_root = PROD_ROOT.parents[1] / ".tmp"
        scratch_root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="ml-zoomcamp-2021-discovery-test-", dir=scratch_root))
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_the_certificate_roster_is_an_email_recovery_source(self) -> None:
        roster_path = self.root / "courses" / "mlzoomcamp-2021" / "mlzoomcamp-2021-names.csv"
        roster_path.parent.mkdir(parents=True, exist_ok=True)
        with roster_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["email", "name"])
            writer.writerow([ROSTER_ONLY_EMAIL, ROSTER_ONLY_NAME])

        editions = {edition.cohort_slug: edition for edition in build_editions(self.root)}
        edition = editions["ml-zoomcamp-2021"]

        self.assertIn(roster_path, edition.email_source_csvs)
        self.assertEqual(edition.certificate_csvs, (roster_path,))


class MlZoomcamp2021IdentityMergeTests(TestCase):
    """End-to-end: a roster-only real email must resolve to one account."""

    def setUp(self) -> None:
        super().setUp()
        scratch_root = PROD_ROOT.parents[1] / ".tmp"
        scratch_root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="ml-zoomcamp-2021-merge-test-", dir=scratch_root))
        self.addCleanup(shutil.rmtree, self.root, True)

        self.source_key = sha1_hex(ROSTER_ONLY_EMAIL)

        homework_csv = self.root / "old" / "ml-zoomcamp" / "homework-1-results.csv"
        homework_csv.parent.mkdir(parents=True, exist_ok=True)
        with homework_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["email", "question1", "learning_in_public", "total_score"])
            writer.writerow([self.source_key, 1, 1, 2])

        project_csv = self.root / "old" / "ml-zoomcamp" / "midterm-project-results.csv"
        with project_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "email",
                    "project_total",
                    "evaluation_score",
                    "evaluated_3_projects",
                    "total_score",
                    "project_passed",
                ]
            )
            writer.writerow([self.source_key, 20, 10, "True", 30, "True"])

        roster_csv = self.root / "courses" / "mlzoomcamp-2021" / "mlzoomcamp-2021-names.csv"
        roster_csv.parent.mkdir(parents=True, exist_ok=True)
        with roster_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["email", "name"])
            writer.writerow([ROSTER_ONLY_EMAIL, ROSTER_ONLY_NAME])

        self.edition = {edition.cohort_slug: edition for edition in build_editions(self.root)}[
            "ml-zoomcamp-2021"
        ]

    def test_scoring_then_certificates_land_on_one_enrollment(self) -> None:
        scoring_result = import_edition_scoring(self.edition)
        self.assertEqual(scoring_result.homework_submissions, 1)
        self.assertEqual(scoring_result.project_submissions, 1)

        certificate_result = import_edition_certificates(scoring_result.cohort, self.edition)
        self.assertEqual(certificate_result.graduates_seen, 1)
        self.assertEqual(certificate_result.certificate_urls_matched, 1)

        # One account, one enrollment -- not a scoring-only identity plus a
        # second, certificate-only one for the same real graduate.
        self.assertEqual(Enrollment.objects.count(), 1)
        enrollment = Enrollment.objects.get()

        self.assertTrue(Submission.objects.filter(enrollment=enrollment).exists())
        self.assertTrue(ProjectSubmission.objects.filter(enrollment=enrollment).exists())
        self.assertTrue(enrollment.certificate_name)
        self.assertTrue(enrollment.certificate_url)

        # The merged account carries the real recovered email, not a
        # synthetic local placeholder.
        self.assertFalse(enrollment.student.username.startswith("zc-hist-"))
        self.assertEqual(enrollment.student.email, ROSTER_ONLY_EMAIL)

    def test_replay_stays_merged_and_duplicate_free(self) -> None:
        import_edition_scoring(self.edition)
        cohort = import_edition_scoring(self.edition).cohort
        import_edition_certificates(cohort, self.edition)
        import_edition_certificates(cohort, self.edition)

        self.assertEqual(Enrollment.objects.count(), 1)
        self.assertEqual(Submission.objects.count(), 1)
        self.assertEqual(ProjectSubmission.objects.count(), 1)
