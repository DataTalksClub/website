from __future__ import annotations

import sqlite3
from unittest import TestCase

from django.db import connection as django_connection
from django.test import TestCase as DjangoTestCase

from courses.services.local_cmp_content_import import (
    exclude_fixture_courses,
)
from review_import.manifest import ALLOWLIST, COPY_ORDER
from review_import.workflow import (
    AllowedDataset,
    ImportFailure,
    _assert_required_columns_filled,
    _logical_checksum,
    _relationship_evidence,
    target_columns,
)


def _row(table: str, values: dict[str, object]) -> tuple[object, ...]:
    return tuple(values[column] for column in ALLOWLIST[table])


def _dataset(rows: dict[str, list[tuple[object, ...]]]) -> AllowedDataset:
    filled = {table: list(rows.get(table, [])) for table in COPY_ORDER}
    return AllowedDataset(
        rows=filled,
        counts={table: len(filled[table]) for table in COPY_ORDER},
        relationships=_relationship_evidence(filled),
        logical_checksum=_logical_checksum(filled),
    )


def _course(*, course_id: int, slug: str) -> tuple[object, ...]:
    return _row(
        "courses_course",
        {
            "id": course_id,
            "slug": slug,
            "title": slug,
            "description": "Public course content.",
            "start_date": "2026-01-01",
            "end_date": "2026-06-01",
            "registration_url": "",
            "github_repo_url": "",
            "social_media_hashtag": "",
            "first_homework_scored": 0,
            "finished": 0,
            "faq_document_url": "",
            "min_projects_to_pass": 1,
            "homework_problems_comments_field": 0,
            "project_passing_score": 0,
            "visible": 1,
        },
    )


def _homework(*, homework_id: int, course_id: int, slug: str) -> tuple[object, ...]:
    return _row(
        "courses_homework",
        {
            "id": homework_id,
            "slug": slug,
            "course_id": course_id,
            "title": slug,
            "description": "Public homework description.",
            "instructions_url": "",
            "due_date": "2026-02-01T22:00:00+00:00",
            "learning_in_public_cap": 7,
            "homework_url_field": 1,
            "time_spent_lectures_field": 1,
            "time_spent_homework_field": 1,
            "faq_contribution_field": 1,
            "state": "OP",
        },
    )


def _question(*, question_id: int, homework_id: int, text: str) -> tuple[object, ...]:
    return _row(
        "courses_question",
        {
            "id": question_id,
            "homework_id": homework_id,
            "text": text,
            "question_type": "MC",
            "answer_type": "",
            "possible_answers": "A\nB",
            "correct_answer": "1",
            "scores_for_correct_answer": 1,
        },
    )


class ExcludeFixtureCourseTests(TestCase):
    def test_drops_fixture_courses_and_dependent_rows(self) -> None:
        dataset = _dataset(
            {
                "courses_course": [
                    _course(course_id=1, slug="llm-zoomcamp-2026"),
                    _course(course_id=2, slug="fake-course"),
                    _course(course_id=3, slug="fake-course-2"),
                ],
                "courses_homework": [
                    _homework(homework_id=10, course_id=1, slug="hw1"),
                    _homework(homework_id=11, course_id=2, slug="hw1"),
                ],
                "courses_question": [
                    _question(question_id=100, homework_id=10, text="Real question"),
                    _question(question_id=101, homework_id=11, text="Fixture question"),
                ],
            }
        )

        filtered, excluded = exclude_fixture_courses(dataset)

        self.assertEqual(excluded, ("fake-course", "fake-course-2"))
        self.assertEqual(filtered.counts["courses_course"], 1)
        self.assertEqual(filtered.counts["courses_homework"], 1)
        self.assertEqual(filtered.counts["courses_question"], 1)
        question = dict(
            zip(ALLOWLIST["courses_question"], filtered.rows["courses_question"][0], strict=True)
        )
        self.assertEqual(question["text"], "Real question")


class CopiedColumnsCoverTheMigratedSchemaTests(DjangoTestCase):
    """The copy writes explicit column lists, so a migration can silently outgrow it.

    Migration ``0054`` added ``Homework.instructions_source_path`` as NOT NULL with no
    database-level default. Every rebuild then died with an opaque
    ``sqlite3.IntegrityError`` after the migrate step, which is a bad way to learn that a
    column list needs updating.
    """

    def test_every_required_column_is_written_by_the_copy(self) -> None:
        missing: list[str] = []
        with django_connection.cursor() as cursor:
            for table in COPY_ORDER:
                written = set(target_columns(table))
                cursor.execute(f'PRAGMA table_info("{table}")')
                for _cid, name, _type, notnull, default, primary_key in cursor.fetchall():
                    if name in written or not notnull or primary_key:
                        continue
                    if default is not None:
                        continue
                    missing.append(f"{table}.{name}")

        self.assertEqual(missing, [])

    def test_a_required_column_the_copy_never_writes_is_refused_before_insert(self) -> None:
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.execute(
            'CREATE TABLE "courses_homework" '
            "(id INTEGER PRIMARY KEY, slug TEXT NOT NULL, added_later TEXT NOT NULL)"
        )

        with self.assertRaises(ImportFailure) as refusal:
            _assert_required_columns_filled(connection, "courses_homework", ("id", "slug"))

        self.assertEqual(refusal.exception.category, "schema-unwritten-required-column")
        self.assertEqual(refusal.exception.column, "added_later")
