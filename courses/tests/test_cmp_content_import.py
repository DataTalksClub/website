"""Tests for the CMP content importer.

The source is a synthetic SQLite database built here.  No real export is read, and no
table carrying personal data is created, so the suite cannot depend on production data
being present on the machine.
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from django.test import TestCase

from courses.models import Cohort, Course, Homework, Project, Question, ReviewCriteria
from courses.services.cmp_content_import import (
    SKIPPED_COHORTS,
    CmpContentImportError,
    import_cmp_course_content,
)

_SCHEMA = """
CREATE TABLE courses_course (
    id INTEGER, slug TEXT, title TEXT, description TEXT, social_media_hashtag TEXT,
    faq_document_url TEXT, first_homework_scored INTEGER, finished INTEGER,
    homework_problems_comments_field INTEGER, project_passing_score INTEGER,
    min_projects_to_pass INTEGER, visible INTEGER, end_date TEXT, registration_url TEXT,
    github_repo_url TEXT, start_date TEXT
);
CREATE TABLE courses_homework (
    id INTEGER, slug TEXT, title TEXT, description TEXT, due_date TEXT,
    learning_in_public_cap INTEGER, homework_url_field INTEGER,
    time_spent_lectures_field INTEGER, time_spent_homework_field INTEGER,
    faq_contribution_field INTEGER, course_id INTEGER, state TEXT, instructions_url TEXT
);
CREATE TABLE courses_question (
    id INTEGER, text TEXT, question_type TEXT, answer_type TEXT, possible_answers TEXT,
    correct_answer TEXT, scores_for_correct_answer INTEGER, homework_id INTEGER
);
CREATE TABLE courses_project (
    id INTEGER, slug TEXT, title TEXT, description TEXT, submission_due_date TEXT,
    learning_in_public_cap_project INTEGER, peer_review_due_date TEXT,
    time_spent_project_field INTEGER, problems_comments_field INTEGER,
    faq_contribution_field INTEGER, learning_in_public_cap_review INTEGER,
    number_of_peers_to_evaluate INTEGER, time_spent_evaluation_field INTEGER, state TEXT,
    course_id INTEGER, points_for_peer_review INTEGER, instructions_url TEXT
);
CREATE TABLE courses_reviewcriteria (
    id INTEGER, description TEXT, options TEXT, review_criteria_type TEXT, course_id INTEGER
);
CREATE TABLE courses_enrollment (id INTEGER, course_id INTEGER);
CREATE TABLE courses_submission (id INTEGER, homework_id INTEGER);
CREATE TABLE courses_projectsubmission (id INTEGER, project_id INTEGER);
"""

_DUE = "2026-01-15 12:00:00+00"


def _build_source(path: Path, *, cohort_slugs: tuple[str, ...]) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(_SCHEMA)
    for index, slug in enumerate(cohort_slugs, start=1):
        connection.execute(
            "INSERT INTO courses_course VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                index,
                slug,
                f"Title {slug}",
                f"Real description for {slug}",
                "hashtag",
                "https://example.com/faq",
                1,
                0,
                0,
                70,
                1,
                1,
                "2026-05-11",
                "",
                "",
                "2026-01-12",
            ),
        )
        connection.execute(
            "INSERT INTO courses_homework VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                index * 10,
                "hw1",
                "Homework 1: Real",
                "Real homework copy",
                _DUE,
                7,
                1,
                1,
                1,
                1,
                index,
                "CL",
                "",
            ),
        )
        connection.execute(
            "INSERT INTO courses_question VALUES (?,?,?,?,?,?,?,?)",
            (index * 100, "A real question", "MC", "ANY", "a,b,c", "1", 1, index * 10),
        )
        connection.execute(
            "INSERT INTO courses_project VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                index * 10,
                "project1",
                "Project Attempt 1",
                "Real project copy",
                _DUE,
                14,
                _DUE,
                1,
                1,
                1,
                2,
                3,
                1,
                "CL",
                index,
                3,
                "",
            ),
        )
        connection.execute(
            "INSERT INTO courses_reviewcriteria VALUES (?,?,?,?,?)",
            (index * 10, "Is it reproducible?", '["no","yes"]', "RS", index),
        )
        connection.execute("INSERT INTO courses_enrollment VALUES (?,?)", (index, index))
    connection.commit()
    connection.close()


class CmpContentImportTests(TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / "source.sqlite3"

    def _cohort(self, slug: str, *, curriculum_format: str = "legacy") -> Cohort:
        family_slug = slug.rsplit("-", 1)[0]
        family, _ = Course.objects.get_or_create(slug=family_slug, defaults={"title": family_slug})
        return Cohort.objects.create(
            course=family,
            slug=slug,
            identifier=slug.rsplit("-", 1)[1],
            year=int(slug.rsplit("-", 1)[1]),
            title=f"Seeded {slug}",
            description="Seeded description",
            curriculum_format=curriculum_format,
        )

    def _seed_placeholder(self, cohort: Cohort) -> tuple[Homework, Project]:
        now = datetime.now(timezone.utc)
        homework = Homework.objects.create(
            course=cohort,
            slug="homework-01-week-1",
            title="Week 1",
            description="Practice assignment for Week 1.",
            due_date=now + timedelta(days=7),
        )
        project = Project.objects.create(
            course=cohort,
            slug="project-01-project-attempt-1",
            title="Project Attempt 1",
            description="Production-like generated project: Project Attempt 1",
            submission_due_date=now + timedelta(days=30),
            peer_review_due_date=now + timedelta(days=37),
        )
        return homework, project

    def test_replaces_seeded_placeholders_with_real_content(self) -> None:
        cohort = self._cohort("de-zoomcamp-2026")
        self._seed_placeholder(cohort)
        _build_source(self.source, cohort_slugs=("de-zoomcamp-2026",))

        result = import_cmp_course_content(self.source)

        self.assertEqual(result.summary()["cohorts_imported"], 1)
        self.assertEqual(
            list(Homework.objects.filter(course=cohort).values_list("slug", flat=True)),
            ["hw1"],
        )
        self.assertEqual(
            list(Project.objects.filter(course=cohort).values_list("slug", flat=True)),
            ["project1"],
        )
        self.assertEqual(Question.objects.filter(homework__course=cohort).count(), 1)
        self.assertEqual(ReviewCriteria.objects.filter(course=cohort).count(), 1)
        self.assertFalse(
            Homework.objects.filter(description__startswith="Practice assignment for").exists()
        )
        self.assertFalse(
            Project.objects.filter(description__startswith="Production-like generated").exists()
        )

    def test_adopts_the_cohort_description_cmp_publishes(self) -> None:
        cohort = self._cohort("de-zoomcamp-2026")
        _build_source(self.source, cohort_slugs=("de-zoomcamp-2026",))

        import_cmp_course_content(self.source)

        cohort.refresh_from_db()
        self.assertEqual(cohort.description, "Real description for de-zoomcamp-2026")
        self.assertEqual(cohort.start_date, date(2026, 1, 12))
        self.assertEqual(cohort.end_date, date(2026, 5, 11))

    def test_running_twice_changes_nothing(self) -> None:
        cohort = self._cohort("de-zoomcamp-2026")
        self._seed_placeholder(cohort)
        _build_source(self.source, cohort_slugs=("de-zoomcamp-2026",))

        import_cmp_course_content(self.source)
        first = self._snapshot(cohort)
        second_result = import_cmp_course_content(self.source)

        self.assertEqual(self._snapshot(cohort), first)
        summary = second_result.summary()
        self.assertEqual(summary["homework_removed"], 0)
        self.assertEqual(summary["projects_removed"], 0)

    def _snapshot(self, cohort: Cohort) -> tuple:
        return (
            sorted(Homework.objects.filter(course=cohort).values_list("slug", "title")),
            sorted(Project.objects.filter(course=cohort).values_list("slug", "title")),
            sorted(Question.objects.filter(homework__course=cohort).values_list("text", flat=True)),
            ReviewCriteria.objects.filter(course=cohort).count(),
        )

    def test_skips_the_cohorts_the_owner_deferred_with_a_reason_each(self) -> None:
        deferred = tuple(SKIPPED_COHORTS)
        _build_source(self.source, cohort_slugs=deferred)

        result = import_cmp_course_content(self.source)

        self.assertEqual(result.summary()["cohorts_imported"], 0)
        skipped = dict(result.skipped_by_owner)
        self.assertEqual(set(skipped), set(deferred))
        for reason in skipped.values():
            self.assertTrue(reason.strip())
        self.assertFalse(Cohort.objects.exists())

    def test_counts_the_dependent_rows_a_skipped_cohort_would_have_dragged_in(self) -> None:
        _build_source(self.source, cohort_slugs=("ai-hero-2025",))

        result = import_cmp_course_content(self.source)

        self.assertEqual(result.skipped_dependent_rows["ai-hero-2025"], 1)

    def test_leaves_a_modules_format_cohort_untouched(self) -> None:
        """A modules cohort binds homework to repository modules by slug.

        Importing CMP's slugs beside the repository's would leave the module rail pointing
        at one set and the real questions on another, which renders as a page that looks
        fine and is wrong.  The pairing belongs in ``homework_slug_overrides`` first.
        """

        cohort = self._cohort("llm-zoomcamp-2026", curriculum_format="modules")
        existing = Homework.objects.create(
            course=cohort,
            slug="homework-01",
            title="Repository homework",
            description="From the course repository",
            due_date=datetime.now(timezone.utc) + timedelta(days=7),
        )
        _build_source(self.source, cohort_slugs=("llm-zoomcamp-2026",))

        result = import_cmp_course_content(self.source)

        self.assertEqual(result.skipped_modules_format, ("llm-zoomcamp-2026",))
        self.assertEqual(
            list(Homework.objects.filter(course=cohort).values_list("slug", flat=True)),
            [existing.slug],
        )

    def test_skips_a_cohort_the_local_catalogue_does_not_have(self) -> None:
        _build_source(self.source, cohort_slugs=("de-zoomcamp-2026",))

        result = import_cmp_course_content(self.source)

        self.assertEqual(result.skipped_not_in_local_catalogue, ("de-zoomcamp-2026",))

    def test_excludes_upstream_fixture_courses(self) -> None:
        _build_source(self.source, cohort_slugs=("fake-course", "fake-course-2"))

        result = import_cmp_course_content(self.source)

        self.assertEqual(result.skipped_fixture, ("fake-course", "fake-course-2"))

    def test_refuses_an_unreadable_source_without_naming_it(self) -> None:
        missing = Path(self.directory.name) / "absent.sqlite3"

        with self.assertRaises(CmpContentImportError) as raised:
            import_cmp_course_content(missing)

        self.assertEqual(str(raised.exception), "source-unreadable")
        self.assertNotIn(str(missing), str(raised.exception))
