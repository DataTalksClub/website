"""End-to-end cover for the frozen pre-2024 Zoomcamp import.

The fixture is a synthetic ``zoomcamp-scoring`` tree built in the project-local
``.tmp/``: no real export, no real learner data.  It asserts the two properties
this importer promises -- it populates an empty database, and a replay writes no
second row.
"""

from __future__ import annotations

import csv
import json
import shutil
import tempfile
from pathlib import Path

from django.test import TestCase

import scripts.prod

PROD_ROOT = Path(scripts.prod.__file__).resolve().parent


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class LegacyZoomcampImportTests(TestCase):
    """End-to-end against a synthetic ``zoomcamp-scoring`` tree."""

    # Two synthetic learners. The upstream key is sha1(email); these are the
    # digests of addresses that exist only inside this test.
    HASHES = (
        "356a192b7913b04c54574d18c28d46e6395428ab",
        "da4b9237bacccdf19c0760cab7aec4a8359010b0",
    )

    def setUp(self) -> None:
        super().setUp()
        # Scratch data belongs in the project-local .tmp/, never a system temp dir.
        scratch_root = PROD_ROOT.parents[1] / ".tmp"
        scratch_root.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="legacy-zoomcamp-test-", dir=scratch_root))
        self.addCleanup(shutil.rmtree, self.root, True)
        self._build_source_tree()

    def _build_source_tree(self) -> None:
        data = self.root / "old" / "mlops-zoomcamp-2022" / "data"
        _write_csv(
            data / "processed" / "hw-1.csv",
            ["email", "question1", "question2", "learning_in_public", "total_score"],
            [
                {
                    "email": self.HASHES[0],
                    "question1": 1,
                    "question2": 1,
                    "learning_in_public": 2,
                    "total_score": 4,
                },
                {
                    "email": self.HASHES[1],
                    "question1": 1,
                    "question2": 0,
                    "learning_in_public": 0,
                    "total_score": 1,
                },
            ],
        )
        (data / "answers").mkdir(parents=True, exist_ok=True)
        (data / "answers" / "answers-1.json").write_text(
            json.dumps(
                {
                    "mapping": {"What is 2+2?": "question1", "What is a DAG?": "question2"},
                    "answers": [
                        {"question": "question1", "points": 1},
                        {"question": "question2", "points": 1},
                    ],
                }
            ),
            encoding="utf-8",
        )
        _write_csv(
            data / "processed" / "project-a.csv",
            ["email", "project_total", "evaluation_score", "project_passed", "total_score"],
            [
                {
                    "email": self.HASHES[0],
                    "project_total": 10,
                    "evaluation_score": 3,
                    "project_passed": "true",
                    "total_score": 13,
                }
            ],
        )
        _write_csv(
            data / "graduates.csv",
            ["email", "name"],
            [{"email": "graduate@example.invalid", "name": "Imported Graduate"}],
        )
        certificates = self.root / "courses" / "mlopszoomcamp-2022"
        certificates.mkdir(parents=True, exist_ok=True)
        (certificates / "graduates.json").write_text(
            json.dumps(
                [
                    {
                        "variables": {
                            "text": {"name": "Imported Graduate"},
                            "links": {"certificate-id": "https://certificates.invalid/abc"},
                        }
                    }
                ]
            ),
            encoding="utf-8",
        )

    def _run(self) -> dict:
        from scripts.prod.import_legacy_zoomcamp import run

        return run(
            source_repo=self.root,
            editions=["mlops-zoomcamp-2022"],
            course_repos_dir=None,
        )

    def test_it_bootstraps_an_empty_database_and_reports_counts(self) -> None:
        from courses.models import Cohort, Course, Enrollment, Homework, Project, Submission

        self.assertEqual(Cohort.objects.count(), 0)

        report = self._run()

        self.assertEqual(report["editions_imported"], 1)
        self.assertEqual(report["homework_submissions"], 2)
        self.assertEqual(report["project_submissions"], 1)
        self.assertEqual(report["graduates"], 1)

        cohort = Cohort.objects.get(slug="mlops-zoomcamp-2022")
        # The course family is resolved from the slug, so no catalogue had to exist.
        self.assertEqual(cohort.course.slug, "mlops-zoomcamp")
        self.assertEqual(Course.objects.count(), 1)
        self.assertEqual(Homework.objects.filter(course=cohort).count(), 1)
        self.assertEqual(Project.objects.filter(course=cohort).count(), 1)
        self.assertEqual(Submission.objects.count(), 2)
        # Two scored learners plus the graduate recovered from graduates.csv.
        self.assertEqual(Enrollment.objects.filter(course=cohort).count(), 3)

    def test_the_certificate_url_is_joined_by_name(self) -> None:
        self._run()

        from courses.models import Enrollment

        certified = Enrollment.objects.filter(certificate_url="https://certificates.invalid/abc")
        self.assertEqual(certified.count(), 1)

    def test_a_displayed_identity_is_a_generated_placeholder(self) -> None:
        """The real address picks the account; it never becomes the public name."""

        self._run()

        from courses.models import Enrollment

        names = [
            enrollment.certificate_name
            for enrollment in Enrollment.objects.all()
            if enrollment.certificate_name
        ]
        self.assertEqual(len(names), 1)
        for name in names:
            self.assertNotIn("@", name)

    def test_created_accounts_carry_no_usable_password(self) -> None:
        """The recovered-email account is a real learner identity, not a
        password-authenticatable one -- it must be neutralised the same way
        the CMP importer neutralises its own rows."""

        self._run()

        from courses.models import User

        accounts = User.objects.filter(email="graduate@example.invalid")
        self.assertTrue(accounts.exists())
        for account in accounts:
            self.assertFalse(account.has_usable_password())

    def test_replaying_writes_no_second_row(self) -> None:
        from django.contrib.auth import get_user_model

        from courses.models import Cohort, Enrollment, Homework, Project, Submission

        first = self._run()
        counts = {
            "users": get_user_model().objects.count(),
            "cohorts": Cohort.objects.count(),
            "homework": Homework.objects.count(),
            "projects": Project.objects.count(),
            "submissions": Submission.objects.count(),
            "enrollments": Enrollment.objects.count(),
        }

        second = self._run()

        self.assertEqual(second, first)
        self.assertEqual(
            {
                "users": get_user_model().objects.count(),
                "cohorts": Cohort.objects.count(),
                "homework": Homework.objects.count(),
                "projects": Project.objects.count(),
                "submissions": Submission.objects.count(),
                "enrollments": Enrollment.objects.count(),
            },
            counts,
        )

    def test_an_unknown_edition_is_refused_by_name(self) -> None:
        from scripts.prod.import_legacy_zoomcamp import (
            LegacyZoomcampImportError,
            discover,
            select,
        )

        with self.assertRaises(LegacyZoomcampImportError) as caught:
            select(discover(self.root), ["de-zoomcamp-1999"])
        self.assertIn("unknown_edition", str(caught.exception))

    def test_an_absent_source_repository_is_refused_without_a_path(self) -> None:
        from scripts.prod.import_legacy_zoomcamp import LegacyZoomcampImportError, discover

        with self.assertRaises(LegacyZoomcampImportError) as caught:
            discover(self.root / "not-a-checkout")
        self.assertEqual(str(caught.exception), "source_repo_unavailable")
