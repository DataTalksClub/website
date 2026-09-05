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
    Answer,
    CmpHistoryImportProgress,
    Cohort,
    Course,
    CourseRegistration,
    Enrollment,
    Homework,
    PeerReview,
    Project,
    ProjectSubmission,
    Question,
    RegistrationCampaign,
    Submission,
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
CREATE TABLE courses_homework (id INTEGER, course_id INTEGER, slug TEXT);
CREATE TABLE courses_question (id INTEGER, homework_id INTEGER, text TEXT);
CREATE TABLE courses_submission (
    id INTEGER, homework_link TEXT, learning_in_public_links TEXT, time_spent_lectures REAL,
    time_spent_homework REAL, problems_comments TEXT, faq_contribution TEXT, submitted_at TEXT,
    questions_score INTEGER, faq_score INTEGER, learning_in_public_score INTEGER,
    total_score INTEGER, enrollment_id INTEGER, homework_id INTEGER, student_id INTEGER,
    faq_contribution_url TEXT
);
CREATE TABLE courses_answer (
    id INTEGER, answer_text TEXT, is_correct INTEGER, question_id INTEGER, submission_id INTEGER
);
CREATE TABLE courses_project (id INTEGER, course_id INTEGER, slug TEXT);
CREATE TABLE courses_projectsubmission (
    id INTEGER, github_link TEXT, commit_id TEXT, learning_in_public_links TEXT,
    faq_contribution TEXT, time_spent REAL, problems_comments TEXT, submitted_at TEXT,
    enrollment_id INTEGER, project_id INTEGER, student_id INTEGER, passed INTEGER,
    peer_review_learning_in_public_score INTEGER, peer_review_score INTEGER,
    project_faq_score INTEGER, project_learning_in_public_score INTEGER, project_score INTEGER,
    reviewed_enough_peers INTEGER, total_score INTEGER, faq_contribution_url TEXT,
    volunteer_review_only INTEGER
);
CREATE TABLE courses_peerreview (
    id INTEGER, note_to_peer TEXT, learning_in_public_links TEXT, time_spent_reviewing REAL,
    problems_comments TEXT, reviewer_id INTEGER, submission_under_evaluation_id INTEGER,
    optional INTEGER, state TEXT, submitted_at TEXT
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


def submission_row(
    source_id: int,
    *,
    enrollment_id: int = 1,
    homework_id: int = 1,
    student_id: int = 1,
    links: str | None = '["https://post.invalid/one"]',
) -> tuple:
    return (
        source_id,
        "https://homework.invalid/repo",
        links,
        3.5,
        2.0,
        "",
        "",
        MOMENT,
        6,
        1,
        2,
        9,
        enrollment_id,
        homework_id,
        student_id,
        None,
    )


def answer_row(source_id: int, *, question_id: int = 1, submission_id: int = 1) -> tuple:
    return (source_id, f"answer {source_id}", 1, question_id, submission_id)


def project_submission_row(
    source_id: int,
    *,
    enrollment_id: int = 1,
    project_id: int = 1,
    student_id: int = 1,
) -> tuple:
    return (
        source_id,
        "https://github.invalid/project",
        "abc1234",
        "[]",
        "",
        4.0,
        "",
        MOMENT,
        enrollment_id,
        project_id,
        student_id,
        1,
        0,
        5,
        1,
        2,
        12,
        1,
        20,
        None,
        0,
    )


def peer_review_row(
    source_id: int,
    *,
    reviewer_id: int = 1,
    submission_under_evaluation_id: int = 2,
) -> tuple:
    return (
        source_id,
        "A note to the peer.",
        "[]",
        1.5,
        "",
        reviewer_id,
        submission_under_evaluation_id,
        0,
        "SU",
        MOMENT,
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
        self.homework = Homework.objects.create(
            course=self.cohort, slug="module-1", title="Module 1", due_date=MOMENT_UTC
        )
        self.question = Question.objects.create(
            homework=self.homework, text="How many rows?", question_type="MC", answer_type="INT"
        )
        self.project = Project.objects.create(
            course=self.cohort,
            slug="capstone",
            title="Capstone",
            submission_due_date=MOMENT_UTC,
            peer_review_due_date=MOMENT_UTC,
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
        tables.setdefault("courses_homework", [(1, 1, "module-1")])
        tables.setdefault("courses_question", [(1, 1, "How many rows?")])
        tables.setdefault("courses_project", [(1, 1, "capstone")])
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


class SubmissionImportTests(HistoryImportFixture):
    def test_a_submission_lands_on_the_enrollment_the_previous_stage_claimed(self) -> None:
        source = self.source(
            courses_enrollment=[enrollment_row(1)],
            courses_submission=[submission_row(1)],
        )
        self.run_import(source)

        submission = Submission.objects.get()
        self.assertEqual(submission.enrollment, Enrollment.objects.get())
        self.assertEqual(submission.homework, self.homework)
        self.assertEqual(submission.student, self.learner)
        self.assertEqual(submission.learning_in_public_links, ["https://post.invalid/one"])
        self.assertEqual(submission.total_score, 9)
        self.assertEqual(submission.submitted_at, MOMENT_UTC)

    def test_a_submission_whose_enrollment_was_never_imported_is_bucketed(self) -> None:
        """The enrollment stage skipped it, so this stage has nothing to attach
        to -- and must not invent one."""

        source = self.source(courses_submission=[submission_row(1)])
        result = self.run_import(source)

        self.assertEqual(self.report(result, "courses_submission")["unresolved"], {"enrollment": 1})
        self.assertEqual(Submission.objects.count(), 0)
        self.assertEqual(Enrollment.objects.count(), 0)

    def test_a_submission_for_a_homework_the_catalogue_lacks_is_bucketed(self) -> None:
        source = self.source(
            courses_homework=[(1, 1, "module-1"), (2, 1, "module-never-imported")],
            courses_enrollment=[enrollment_row(1)],
            courses_submission=[submission_row(1, homework_id=2)],
        )
        result = self.run_import(source)

        self.assertEqual(self.report(result, "courses_submission")["unresolved"], {"homework": 1})
        self.assertEqual(Homework.objects.count(), 1)

    def test_a_malformed_json_column_is_bucketed_not_stored_as_a_guess(self) -> None:
        source = self.source(
            courses_enrollment=[enrollment_row(1)],
            courses_submission=[submission_row(1, links="not json at all")],
        )
        result = self.run_import(source)

        report = self.report(result, "courses_submission")
        self.assertEqual(report["unresolved"], {"source_json_invalid": 1})
        self.assertEqual(Submission.objects.count(), 0)


class AnswerImportTests(HistoryImportFixture):
    def _source(self, **overrides) -> Path:
        tables = {
            "courses_enrollment": [enrollment_row(1)],
            "courses_submission": [submission_row(1)],
            "courses_answer": [answer_row(1)],
        }
        tables.update(overrides)
        return self.source(**tables)

    def test_an_answer_lands_on_its_submission_and_its_question(self) -> None:
        self.run_import(self._source())

        answer = Answer.objects.get()
        self.assertEqual(answer.submission, Submission.objects.get())
        self.assertEqual(answer.question, self.question)
        self.assertEqual(answer.answer_text, "answer 1")
        self.assertTrue(answer.is_correct)

    def test_a_question_the_catalogue_lacks_bucketed_not_matched_to_another(self) -> None:
        """Attaching an answer to whichever question happened to be nearby
        would render as a page that looks fine and is wrong."""

        source = self._source(
            courses_question=[(1, 1, "How many rows?"), (2, 1, "A question CMP alone has")],
            courses_answer=[answer_row(1), answer_row(2, question_id=2)],
        )
        result = self.run_import(source)

        report = self.report(result, "courses_answer")
        self.assertEqual(report["created"], 1)
        self.assertEqual(report["unresolved"], {"question": 1})
        self.assertEqual(Answer.objects.get().question, self.question)

    def test_a_homework_repeating_a_question_text_pairs_them_in_source_order(self) -> None:
        """One homework in the real export asks the same text four times.
        ``import_cmp_content`` writes questions as a set in source-id order, so
        the repeats pair in that order rather than all landing on the first."""

        second = Question.objects.create(
            homework=self.homework, text="How many rows?", question_type="MC", answer_type="INT"
        )
        source = self._source(
            courses_question=[(1, 1, "How many rows?"), (2, 1, "How many rows?")],
            courses_answer=[answer_row(1), answer_row(2, question_id=2)],
        )
        self.run_import(source)

        by_source = {
            source_id: Answer.objects.get(pk=pk).question_id
            for source_id, pk in self.claims("courses_answer").items()
        }
        self.assertEqual(by_source, {1: self.question.pk, 2: second.pk})


class ProjectSubmissionImportTests(HistoryImportFixture):
    def test_a_project_submission_keeps_every_score_the_export_computed(self) -> None:
        source = self.source(
            courses_enrollment=[enrollment_row(1)],
            courses_projectsubmission=[project_submission_row(1)],
        )
        self.run_import(source)

        submission = ProjectSubmission.objects.get()
        self.assertEqual(submission.project, self.project)
        self.assertEqual(submission.enrollment, Enrollment.objects.get())
        self.assertEqual(submission.project_score, 12)
        self.assertEqual(submission.peer_review_score, 5)
        self.assertEqual(submission.total_score, 20)
        self.assertTrue(submission.passed)
        self.assertTrue(submission.reviewed_enough_peers)
        self.assertFalse(submission.volunteer_review_only)
        self.assertEqual(submission.submitted_at, MOMENT_UTC)

    def test_a_project_the_catalogue_lacks_is_bucketed(self) -> None:
        source = self.source(
            courses_project=[(1, 1, "capstone"), (2, 1, "unimported-project")],
            courses_enrollment=[enrollment_row(1)],
            courses_projectsubmission=[project_submission_row(1, project_id=2)],
        )
        result = self.run_import(source)

        report = self.report(result, "courses_projectsubmission")
        self.assertEqual(report["unresolved"], {"project": 1})
        self.assertEqual(Project.objects.count(), 1)


class PeerReviewImportTests(HistoryImportFixture):
    def _two_project_submissions(self, **overrides) -> Path:
        other = CustomUser.objects.create_user(username="learner-two", email="two@example.invalid")
        self.user_claims = {1: self.learner.pk, 2: other.pk}
        tables = {
            "courses_enrollment": [enrollment_row(1), enrollment_row(2, student_id=2)],
            "courses_projectsubmission": [
                project_submission_row(1),
                project_submission_row(2, enrollment_id=2, student_id=2),
            ],
            "courses_peerreview": [peer_review_row(1)],
        }
        tables.update(overrides)
        return self.source(**tables)

    def test_both_ends_of_a_review_are_project_submissions(self) -> None:
        self.run_import(self._two_project_submissions())

        review = PeerReview.objects.get()
        claims = self.claims("courses_projectsubmission")
        self.assertEqual(review.reviewer_id, claims[1])
        self.assertEqual(review.submission_under_evaluation_id, claims[2])
        self.assertEqual(review.state, "SU")
        self.assertEqual(review.note_to_peer, "A note to the peer.")
        self.assertEqual(review.submitted_at, MOMENT_UTC)

    def test_a_review_of_a_project_submission_that_never_imported_is_bucketed(self) -> None:
        source = self._two_project_submissions(
            courses_peerreview=[peer_review_row(1, submission_under_evaluation_id=404)]
        )
        result = self.run_import(source)

        report = self.report(result, "courses_peerreview")
        self.assertEqual(report["unresolved"], {"project_submission": 1})
        self.assertEqual(PeerReview.objects.count(), 0)
