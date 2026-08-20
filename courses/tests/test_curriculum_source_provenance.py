from __future__ import annotations

import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from courses.models import (
    Cohort,
    Course,
    CourseCurriculumImportRun,
    CurriculumFormat,
    Homework,
    Module,
    Question,
    QuestionTypes,
    Unit,
)

COMMIT_SHA = "a" * 40
CHECKSUM = "b" * 64


def provenance(path: str, *, content_id: uuid.UUID | None = None) -> dict[str, object]:
    return {
        "source_content_id": content_id or uuid.uuid4(),
        "source_path": path,
        "source_commit_sha": COMMIT_SHA,
        "source_checksum": CHECKSUM,
    }


class CurriculumSourceProvenanceTests(TestCase):
    def make_curriculum(self):
        course = Course.objects.create(slug="source-course", title="Source course")
        cohort = Cohort.objects.create(
            course=course,
            slug="source-course-2026",
            identifier="2026",
            title="Source course 2026",
            description="A cohort.",
            curriculum_format=CurriculumFormat.MODULES,
        )
        homework = Homework.objects.create(
            course=cohort,
            slug="homework-1",
            title="Homework 1",
            due_date=timezone.now() + timedelta(days=7),
        )
        module = Module.objects.create(
            cohort=cohort,
            position=0,
            slug="module-1",
            title="Module 1",
            terminal_homework=homework,
        )
        unit = Unit.objects.create(
            module=module,
            position=0,
            slug="unit-1",
            title="Unit 1",
        )
        question = Question.objects.create(
            homework=homework,
            text="Legacy question",
            question_type=QuestionTypes.FREE_FORM.value,
            correct_answer="legacy plaintext answer",
        )
        return course, cohort, module, unit, homework, question

    def test_legacy_rows_keep_nullable_defaults_and_plaintext_answers(self):
        rows = self.make_curriculum()

        for row in rows:
            with self.subTest(model=type(row).__name__):
                self.assertIsNone(row.source_content_id)
                self.assertIsNone(row.source_path)
                self.assertIsNone(row.source_commit_sha)
                self.assertIsNone(row.source_checksum)
        course, _cohort, _module, _unit, _homework, question = rows
        self.assertIsNone(course.source_stable_id)
        self.assertIsNone(question.source_question_id)
        self.assertIsNone(question.answer_envelope)
        self.assertEqual(question.correct_answer, "legacy plaintext answer")

    def test_complete_source_provenance_validates_for_each_curriculum_model(self):
        course = Course(
            slug="managed-course",
            title="Managed course",
            source_stable_id="llm-zoomcamp-source",
            **provenance("course.yaml"),
        )
        course.full_clean()
        course.save()
        cohort = Cohort(
            course=course,
            slug="managed-course-2026",
            identifier="2026",
            title="Managed course 2026",
            description="Managed cohort.",
            curriculum_format=CurriculumFormat.MODULES,
            **provenance("cohorts/2026/cohort.yaml"),
        )
        cohort.full_clean()
        cohort.save()
        homework = Homework(
            course=cohort,
            slug="homework-1",
            title="Homework 1",
            due_date=timezone.now() + timedelta(days=7),
            **provenance("cohorts/2026/module-1/homework.yaml"),
        )
        homework.full_clean()
        homework.save()
        module = Module(
            cohort=cohort,
            position=0,
            slug="module-1",
            title="Module 1",
            terminal_homework=homework,
            **provenance("module-1/module.yaml"),
        )
        module.full_clean()
        module.save()
        unit = Unit(
            module=module,
            position=0,
            slug="unit-1",
            title="Unit 1",
            **provenance("module-1/lessons/01-intro.md"),
        )
        unit.full_clean()
        unit.save()
        question = Question(
            homework=homework,
            text="Managed question",
            question_type=QuestionTypes.FREE_FORM.value,
            source_question_id="question-1",
            answer_envelope={"version": 1, "ciphertext": "opaque"},
            **provenance("cohorts/2026/module-1/homework.yaml"),
        )
        question.full_clean()
        question.save()

        self.assertEqual(question.answer_envelope["ciphertext"], "opaque")

    def test_partial_or_invalid_source_identity_is_rejected(self):
        partial = Course(
            slug="partial-source",
            title="Partial source",
            source_content_id=uuid.uuid4(),
        )
        with self.assertRaises(ValidationError) as partial_error:
            partial.full_clean()
        self.assertIn("source_path", partial_error.exception.message_dict)
        self.assertIn("source_stable_id", partial_error.exception.message_dict)

        invalid_path = Course(
            slug="invalid-path",
            title="Invalid path",
            source_stable_id="course-source",
            **provenance("cohorts/../secret.yaml"),
        )
        with self.assertRaises(ValidationError) as path_error:
            invalid_path.full_clean()
        self.assertIn("__all__", path_error.exception.message_dict)

        invalid_commit = Course(
            slug="invalid-commit",
            title="Invalid commit",
            source_stable_id="another-source",
            **{**provenance("course.yaml"), "source_commit_sha": "ABC"},
        )
        with self.assertRaises(ValidationError) as commit_error:
            invalid_commit.full_clean()
        self.assertIn("source_commit_sha", commit_error.exception.message_dict)

    def test_source_content_identity_is_unique_in_its_owner_scope(self):
        course, cohort, _module, _unit, homework, _question = self.make_curriculum()
        shared_id = uuid.uuid4()
        first = Question.objects.create(
            homework=homework,
            text="First",
            question_type=QuestionTypes.FREE_FORM.value,
            source_question_id="question-1",
            **provenance("homework.yaml", content_id=shared_id),
        )
        self.assertIsNotNone(first.pk)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Question.objects.create(
                homework=homework,
                text="Duplicate",
                question_type=QuestionTypes.FREE_FORM.value,
                source_question_id="question-2",
                **provenance("homework.yaml", content_id=shared_id),
            )

        other_homework = Homework.objects.create(
            course=cohort,
            slug="homework-2",
            title="Homework 2",
            due_date=timezone.now() + timedelta(days=14),
        )
        other = Question.objects.create(
            homework=other_homework,
            text="Same stable content in another homework",
            question_type=QuestionTypes.FREE_FORM.value,
            source_question_id="question-1",
            **provenance("homework-2.yaml", content_id=shared_id),
        )
        self.assertEqual(other.homework.course, cohort)
        self.assertEqual(course.slug, "source-course")


class CourseCurriculumImportRunTests(TestCase):
    def run_values(self) -> dict[str, object]:
        return {
            "source_uuid": uuid.uuid4(),
            "source_stable_id": "llm-zoomcamp",
            "repository_owner": "DataTalksClub",
            "repository_name": "llm-zoomcamp",
            "repository_branch": "main",
            "commit_sha": COMMIT_SHA,
            "schema_version": 1,
            "parser_version": "course-yaml-v1",
            "manifest_checksum": CHECKSUM,
            "diagnostics": [{"code": "example", "source_path": "course.yaml"}],
            "counts": {"courses": 1, "cohorts": 1},
        }

    def test_import_run_identity_is_unique_by_source_commit_and_parser(self):
        values = self.run_values()
        first = CourseCurriculumImportRun(**values)
        first.full_clean()
        first.save()

        with self.assertRaises(IntegrityError), transaction.atomic():
            CourseCurriculumImportRun.objects.create(**values)

        values["parser_version"] = "course-yaml-v2"
        second = CourseCurriculumImportRun.objects.create(**values)
        self.assertNotEqual(first.pk, second.pk)

    def test_import_run_validates_identity_and_bounded_evidence(self):
        invalid_identity = CourseCurriculumImportRun(
            **{
                **self.run_values(),
                "source_stable_id": "Not Canonical",
                "commit_sha": "short",
            }
        )
        with self.assertRaises(ValidationError) as identity_error:
            invalid_identity.full_clean()
        self.assertIn("source_stable_id", identity_error.exception.message_dict)
        self.assertIn("commit_sha", identity_error.exception.message_dict)

        too_many_diagnostics = CourseCurriculumImportRun(
            **{
                **self.run_values(),
                "diagnostics": [
                    {"code": f"diagnostic-{index}"}
                    for index in range(CourseCurriculumImportRun.MAX_DIAGNOSTICS + 1)
                ],
            }
        )
        with self.assertRaises(ValidationError) as diagnostics_error:
            too_many_diagnostics.full_clean()
        self.assertIn("diagnostics", diagnostics_error.exception.message_dict)

        invalid_counts = CourseCurriculumImportRun(
            **{**self.run_values(), "counts": {"questions": -1}}
        )
        with self.assertRaises(ValidationError) as counts_error:
            invalid_counts.full_clean()
        self.assertIn("counts", counts_error.exception.message_dict)
