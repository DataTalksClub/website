from __future__ import annotations

from unittest import TestCase

from courses.services.local_cmp_content_import import (
    align_module_homework_slugs,
    exclude_fixture_courses,
)
from review_import.manifest import ALLOWLIST, COPY_ORDER
from review_import.workflow import AllowedDataset, _logical_checksum, _relationship_evidence


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


class AlignModuleHomeworkSlugTests(TestCase):
    def test_rewrites_hw_slugs_on_module_cohorts_only(self) -> None:
        dataset = _dataset(
            {
                "courses_course": [
                    _course(course_id=1, slug="llm-zoomcamp-2026"),
                    _course(course_id=2, slug="llm-zoomcamp-2025"),
                ],
                "courses_homework": [
                    _homework(homework_id=10, course_id=1, slug="hw1"),
                    _homework(homework_id=11, course_id=1, slug="dlt"),
                    _homework(homework_id=12, course_id=2, slug="hw1"),
                ],
            }
        )

        aligned, remapped = align_module_homework_slugs(dataset)

        homework_slugs = [
            dict(zip(ALLOWLIST["courses_homework"], row, strict=True))
            for row in aligned.rows["courses_homework"]
        ]
        by_id = {int(row["id"]): str(row["slug"]) for row in homework_slugs}
        self.assertEqual(remapped, 1)
        self.assertEqual(by_id, {10: "homework-01", 11: "dlt", 12: "hw1"})
