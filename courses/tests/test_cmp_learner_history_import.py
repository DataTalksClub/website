"""Tests for the CMP learner-history importer.

The source is a synthetic SQLite database built here, shaped like the real CMP
export (columns verified against the read-only export at
``/data/tmp/rds-export/cmp/rds-prod-20260905-182754.db`` while building the
importer).  No real export is read and no personal data reaches this suite.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.test import TestCase

from accounts.models import CustomUser
from courses.models import (
    CmpHistoryImportProgress,
    Cohort,
    Course,
    CourseRegistration,
    Enrollment,
    RegistrationCampaign,
)
from courses.services.cmp_learner_history_import import (
    CONTENT_TABLES,
    FORBIDDEN_TABLES,
    LEARNER_TABLES,
    READ_TABLES,
    TABLE_ORDER,
    CmpHistoryClaims,
    CmpHistoryImportError,
    dry_run_counts,
    import_cmp_learner_history,
    progress_status,
)

SOURCE_SCHEMA = """
CREATE TABLE courses_course (id INTEGER, slug TEXT);
CREATE TABLE courses_registrationcampaign (id INTEGER, slug TEXT, current_course_id INTEGER);
CREATE TABLE courses_courseregistration (
    id INTEGER, email TEXT, email_normalized TEXT, name TEXT, country TEXT, region TEXT,
    role TEXT, comment TEXT, accepted_newsletter INTEGER, created_at TEXT, updated_at TEXT,
    course_id INTEGER, user_id INTEGER, campaign_id INTEGER, company_name TEXT
);
CREATE TABLE courses_enrollment (
    id INTEGER, enrollment_date TEXT, display_name TEXT, display_on_leaderboard INTEGER,
    certificate_name TEXT, total_score INTEGER, course_id INTEGER, student_id INTEGER,
    position_on_leaderboard INTEGER, certificate_url TEXT, disable_learning_in_public INTEGER,
    display_public_profile INTEGER
);
"""

MOMENT = "2025-03-04 09:15:22.123456+00"
MOMENT_UTC = datetime(2025, 3, 4, 9, 15, 22, 123456, tzinfo=UTC)


def registration_row(
    source_id: int,
    *,
    campaign_id: int = 1,
    course_id: int | None = 1,
    user_id: int | None = 1,
    email: str | None = None,
) -> tuple:
    address = email if email is not None else f"learner{source_id}@example.invalid"
    return (
        source_id,
        address,
        address,
        f"Learner {source_id}",
        "NL",
        "Europe",
        "data_engineer",
        "",
        1,
        MOMENT,
        MOMENT,
        course_id,
        user_id,
        campaign_id,
        "",
    )


def enrollment_row(
    source_id: int,
    *,
    course_id: int = 1,
    student_id: int = 1,
    certificate_url: str | None = None,
) -> tuple:
    return (
        source_id,
        MOMENT,
        f"Pseudonym {source_id}",
        1,
        None,
        7,
        course_id,
        student_id,
        None,
        certificate_url,
        0,
        1,
    )


def build_source(path: Path, **tables: list[tuple]) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(SOURCE_SCHEMA)
    for table, rows in tables.items():
        if not rows:
            continue
        placeholders = ",".join("?" * len(rows[0]))
        connection.executemany(f"insert into {table} values ({placeholders})", rows)  # noqa: S608
    connection.commit()
    connection.close()


class HistoryImportFixture(TestCase):
    """A target database holding exactly what the earlier importers would leave."""

    def setUp(self) -> None:
        super().setUp()
        self.claims_directory = Path(tempfile.mkdtemp(prefix="cmp-history-claims-"))
        self.family = Course.objects.create(slug="de-zoomcamp", title="Data Engineering Zoomcamp")
        self.cohort = Cohort.objects.create(
            course=self.family, slug="de-zoomcamp-2025", identifier="2025", year=2025
        )
        self.campaign = RegistrationCampaign.objects.create(
            slug="de-zoomcamp", title="Data Engineering Zoomcamp"
        )
        self.learner = CustomUser.objects.create_user(
            username="learner-one", email="one@example.invalid"
        )
        # The account claims import_cmp_learners.py leaves behind: CMP id -> pk.
        self.user_claims = {1: self.learner.pk}

    def source(self, **tables: list[tuple]) -> Path:
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        path = Path(handle.name)
        self.addCleanup(path.unlink, missing_ok=True)
        tables.setdefault("courses_course", [(1, "de-zoomcamp-2025")])
        tables.setdefault("courses_registrationcampaign", [(1, "de-zoomcamp", 1)])
        build_source(path, **tables)
        return path

    def run_import(self, source: Path, *, batch_size: int = 10, **kwargs: Any):
        return import_cmp_learner_history(
            source,
            user_claims=kwargs.pop("user_claims", self.user_claims),
            batch_size=batch_size,
            claims_directory=self.claims_directory,
            **kwargs,
        )

    def claims(self, table: str) -> dict[int, int]:
        return CmpHistoryClaims(directory=self.claims_directory).table(table)

    def report(self, result: Any, table: str) -> dict[str, Any]:
        return next(row for row in result.summary()["tables"] if row["table"] == table)


class ForbiddenTableTests(HistoryImportFixture):
    def test_the_never_import_set_is_this_import_s_own_security_boundary(self) -> None:
        # The five tables present in the export plus socialaccount_socialtoken,
        # which is absent today and named so a future export cannot introduce it
        # quietly. Deliberately not review_import's SENSITIVE_TABLES, which
        # excludes the entire payload this importer moves.
        self.assertEqual(
            FORBIDDEN_TABLES,
            frozenset(
                {
                    "django_session",
                    "socialaccount_socialaccount",
                    "socialaccount_socialapp",
                    "socialaccount_socialapp_sites",
                    "socialaccount_socialtoken",
                    "accounts_token",
                }
            ),
        )
        self.assertEqual(READ_TABLES & FORBIDDEN_TABLES, set())
        self.assertEqual(READ_TABLES, LEARNER_TABLES | CONTENT_TABLES)
        self.assertEqual(LEARNER_TABLES, frozenset(TABLE_ORDER))

    def test_a_source_without_the_forbidden_tables_imports_cleanly(self) -> None:
        """If this importer ever queried one of them, this fails with a real
        ``no such table`` from SQLite rather than a mocked assertion."""

        source = self.source(courses_enrollment=[enrollment_row(1)])
        result = self.run_import(source)
        self.assertEqual(self.report(result, "courses_enrollment")["created"], 1)

    def test_a_missing_source_file_is_a_safe_refusal(self) -> None:
        with self.assertRaises(CmpHistoryImportError):
            self.run_import(Path("/nonexistent/no-such-export.db"))


class CourseRegistrationImportTests(HistoryImportFixture):
    def test_a_registration_is_copied_with_its_own_timestamps(self) -> None:
        source = self.source(courses_courseregistration=[registration_row(1)])
        self.run_import(source)

        registration = CourseRegistration.objects.get()
        self.assertEqual(registration.campaign, self.campaign)
        self.assertEqual(registration.course, self.cohort)
        self.assertEqual(registration.user, self.learner)
        self.assertEqual(registration.email_normalized, "learner1@example.invalid")
        self.assertEqual(registration.role, "data_engineer")
        self.assertTrue(registration.accepted_newsletter)
        # Not the import's own clock: these are historical facts.
        self.assertEqual(registration.created_at, MOMENT_UTC)
        self.assertEqual(registration.updated_at, MOMENT_UTC)

    def test_a_registration_with_no_cohort_is_not_repointed_at_the_campaign_s(self) -> None:
        """``CourseRegistration.save()`` fills a blank course from the campaign's
        current one. 17,380 of the export's rows have none, and inventing a
        cohort choice for them would be this importer deciding what CMP did
        not record."""

        source = self.source(courses_courseregistration=[registration_row(1, course_id=None)])
        self.campaign.current_course = self.cohort
        self.campaign.save(update_fields=["current_course"])

        self.run_import(source)

        self.assertIsNone(CourseRegistration.objects.get().course)

    def test_an_anonymous_registration_keeps_no_user(self) -> None:
        source = self.source(courses_courseregistration=[registration_row(1, user_id=None)])
        self.run_import(source)

        self.assertIsNone(CourseRegistration.objects.get().user)

    def test_an_unresolvable_parent_is_bucketed_and_skipped_not_invented(self) -> None:
        source = self.source(
            courses_course=[(1, "de-zoomcamp-2025"), (2, "ai-hero-2026")],
            courses_registrationcampaign=[(1, "de-zoomcamp", 1), (2, "unknown-campaign", 2)],
            courses_courseregistration=[
                registration_row(1, course_id=2),
                registration_row(2, user_id=404),
                registration_row(3, campaign_id=2),
                registration_row(4),
            ],
        )
        result = self.run_import(source)

        report = self.report(result, "courses_courseregistration")
        self.assertEqual(report["created"], 1)
        self.assertEqual(report["unresolved"], {"campaign": 1, "cohort": 1, "user": 1})
        self.assertEqual(report["unresolved_total"], 3)
        self.assertEqual(CourseRegistration.objects.count(), 1)
        # Nothing was invented to hang the skipped rows off.
        self.assertEqual(Cohort.objects.count(), 1)
        self.assertEqual(RegistrationCampaign.objects.count(), 1)
        self.assertEqual(CustomUser.objects.count(), 1)


class EnrollmentImportTests(HistoryImportFixture):
    def test_an_enrollment_keeps_its_certificate_and_its_leaderboard_name(self) -> None:
        """There is no certificate table in the export: 2,636 certificates
        exist only as ``courses_enrollment.certificate_url``. And
        ``Enrollment.save()`` would generate a random leaderboard name over a
        blank one, so the copy must not go through it."""

        source = self.source(
            courses_enrollment=[
                enrollment_row(1, certificate_url="https://certificate.invalid/abc")
            ]
        )
        self.run_import(source)

        enrollment = Enrollment.objects.get()
        self.assertEqual(enrollment.certificate_url, "https://certificate.invalid/abc")
        self.assertEqual(enrollment.display_name, "Pseudonym 1")
        self.assertEqual(enrollment.total_score, 7)
        self.assertEqual(enrollment.enrollment_date, MOMENT_UTC)
        self.assertTrue(enrollment.display_public_profile)

    def test_an_enrollment_for_an_unknown_learner_or_cohort_is_bucketed(self) -> None:
        source = self.source(
            courses_course=[(1, "de-zoomcamp-2025"), (2, "ai-hero-2026")],
            courses_enrollment=[
                enrollment_row(1, student_id=404),
                enrollment_row(2, course_id=2),
                enrollment_row(3),
            ],
        )
        result = self.run_import(source)

        report = self.report(result, "courses_enrollment")
        self.assertEqual(report["created"], 1)
        self.assertEqual(report["unresolved"], {"cohort": 1, "user": 1})
        self.assertEqual(Enrollment.objects.count(), 1)


class NaturalKeyCollapseTests(HistoryImportFixture):
    def test_two_cmp_enrollments_for_one_target_account_collapse_onto_one_row(self) -> None:
        """``import_cmp_learners`` folds two CMP accounts onto one target
        account whenever another importer wrote it first. Their two CMP
        enrollments in one cohort are then the same person's single
        enrollment, and must attach rather than collide on the unique key."""

        source = self.source(
            courses_enrollment=[enrollment_row(1), enrollment_row(2, student_id=2)]
        )
        result = self.run_import(source, user_claims={1: self.learner.pk, 2: self.learner.pk})

        report = self.report(result, "courses_enrollment")
        self.assertEqual(report["created"], 1)
        self.assertEqual(report["attached"], 1)
        self.assertEqual(Enrollment.objects.count(), 1)
        # Both CMP ids resolve to that one row, so a submission of either
        # account's finds the enrollment it belongs to.
        claims = self.claims("courses_enrollment")
        self.assertEqual(claims[1], claims[2])


class ReplayAndResumeTests(HistoryImportFixture):
    def _source(self, rows: int) -> Path:
        learners = {
            index: CustomUser.objects.create_user(
                username=f"learner-{index}", email=f"learner{index}@example.invalid"
            ).pk
            for index in range(1, rows + 1)
        }
        self.user_claims = learners
        return self.source(
            courses_courseregistration=[registration_row(i) for i in range(1, rows + 1)],
            courses_enrollment=[enrollment_row(i, student_id=i) for i in range(1, rows + 1)],
        )

    def test_a_second_run_creates_nothing_and_reports_it(self) -> None:
        source = self._source(12)
        first = self.run_import(source, batch_size=5)
        second = self.run_import(source, batch_size=5)

        self.assertEqual(self.report(first, "courses_courseregistration")["created"], 12)
        # Cumulative counters, so "created" does not grow. A table whose
        # watermark says completed is not walked at all on a second run, which
        # is why nothing is even counted as skipped.
        self.assertEqual(self.report(second, "courses_courseregistration")["created"], 12)
        self.assertEqual(second.summary()["skipped_total"], 0)
        self.assertEqual(CourseRegistration.objects.count(), 12)
        self.assertEqual(Enrollment.objects.count(), 12)

    def test_a_mid_batch_failure_rolls_the_batch_back_and_a_resume_completes(self) -> None:
        source = self._source(25)

        import courses.services.cmp_learner_history_import as module

        real_save = module._save_progress
        calls = {"n": 0}

        def fail_on_third_batch(progress):
            calls["n"] += 1
            if calls["n"] == 3:
                raise RuntimeError("simulated kill mid-batch")
            real_save(progress)

        module._save_progress = fail_on_third_batch
        try:
            with self.assertRaises(RuntimeError):
                self.run_import(source, batch_size=5)
        finally:
            module._save_progress = real_save

        # Two batches committed; the third's rows and its watermark rolled back
        # together, and the claims file was never reached for it.
        progress = CmpHistoryImportProgress.objects.get(table="courses_courseregistration")
        self.assertEqual(progress.last_source_id, 10)
        self.assertEqual(progress.rows_created, 10)
        self.assertFalse(progress.completed)
        self.assertEqual(CourseRegistration.objects.count(), 10)
        self.assertEqual(len(self.claims("courses_courseregistration")), 10)

        result = self.run_import(source, batch_size=5)

        self.assertEqual(CourseRegistration.objects.count(), 25)
        self.assertEqual(self.report(result, "courses_courseregistration")["created"], 25)
        self.assertEqual(sorted(self.claims("courses_courseregistration")), list(range(1, 26)))

    def test_a_row_already_claimed_is_skipped_even_off_the_watermark(self) -> None:
        source = self._source(5)
        self.run_import(source, batch_size=100)

        progress = CmpHistoryImportProgress.objects.get(table="courses_courseregistration")
        progress.last_source_id = 0
        progress.completed = False
        progress.save()

        result = self.run_import(source, batch_size=100)
        self.assertEqual(CourseRegistration.objects.count(), 5)
        self.assertEqual(self.report(result, "courses_courseregistration")["skipped"], 5)

    def test_a_lost_claims_file_attaches_to_the_existing_rows_not_duplicates(self) -> None:
        """The residual risk the module docstring names: claims lost while the
        rows survive. Each row's natural key finds what is already there."""

        source = self._source(3)
        self.run_import(source, batch_size=100)

        (self.claims_directory / "courses_courseregistration.json").unlink()
        progress = CmpHistoryImportProgress.objects.get(table="courses_courseregistration")
        progress.last_source_id = 0
        progress.completed = False
        progress.save()

        result = self.run_import(source, batch_size=100)

        self.assertEqual(CourseRegistration.objects.count(), 3)
        report = self.report(result, "courses_courseregistration")
        self.assertEqual(report["created"], 3)
        self.assertEqual(report["attached"], 3)
        self.assertEqual(len(self.claims("courses_courseregistration")), 3)


class ReportingTests(HistoryImportFixture):
    def test_dry_run_reports_counts_and_writes_nothing(self) -> None:
        source = self.source(
            courses_courseregistration=[registration_row(1), registration_row(2)],
            courses_enrollment=[enrollment_row(1)],
        )
        report = dry_run_counts(source, claims_directory=self.claims_directory)

        self.assertFalse(report["applied"])
        self.assertEqual(report["source_total"], 3)
        self.assertEqual(CourseRegistration.objects.count(), 0)
        self.assertEqual(Enrollment.objects.count(), 0)

    def test_status_reports_progress_without_a_source(self) -> None:
        source = self.source(courses_enrollment=[enrollment_row(1)])
        self.run_import(source)

        status = progress_status(claims_directory=self.claims_directory)
        enrollment = next(row for row in status["progress"] if row["table"] == "courses_enrollment")
        self.assertTrue(enrollment["completed"])
        self.assertEqual(enrollment["created"], 1)
        self.assertEqual(enrollment["claims_recorded"], 1)

    def test_a_report_carries_counts_and_table_names_only(self) -> None:
        """A learner value must never reach a report: this payload is answers,
        names and email addresses."""

        source = self.source(
            courses_courseregistration=[registration_row(1, email="private@example.invalid")],
            courses_enrollment=[enrollment_row(1)],
        )
        rendered = json.dumps(self.run_import(source).summary())

        self.assertNotIn("private@example.invalid", rendered)
        self.assertNotIn("Learner 1", rendered)
        self.assertNotIn("Pseudonym 1", rendered)
