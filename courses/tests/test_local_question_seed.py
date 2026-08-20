from __future__ import annotations

import json
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.bootstrap import RuntimeEnvironment
from courses.models import AnswerTypes, Cohort, Homework, Question, QuestionTypes
from courses.services.local_question_seed import (
    DEFAULT_COHORT_SLUG,
    DEFAULT_HOMEWORK_SLUG,
    LocalQuestionSeedError,
    seed_local_questions,
)


class LocalQuestionSeedTests(TestCase):
    def setUp(self) -> None:
        self.cohort = Cohort.objects.create(
            slug=DEFAULT_COHORT_SLUG,
            title="Data Engineering Zoomcamp 2026",
            description="Local course fixture",
        )
        self.homework = Homework.objects.create(
            course=self.cohort,
            slug=DEFAULT_HOMEWORK_SLUG,
            title="Homework 1: Docker, SQL and Terraform",
            description="Local homework fixture",
            due_date=timezone.now() + timezone.timedelta(days=7),
        )

    def test_seed_creates_form_ready_question_types_and_answers(self) -> None:
        result = seed_local_questions()

        self.assertEqual(result.question_count, 3)
        self.assertEqual(result.questions_created, 3)
        questions = list(Question.objects.filter(homework=self.homework).order_by("id"))
        self.assertEqual(
            [question.question_type for question in questions],
            [
                QuestionTypes.MULTIPLE_CHOICE.value,
                QuestionTypes.CHECKBOXES.value,
                QuestionTypes.FREE_FORM.value,
            ],
        )

        multiple_choice, checkboxes, free_form = questions
        self.assertEqual(
            multiple_choice.get_possible_answers(),
            ["psql", "Terraform", "Docker Compose"],
        )
        self.assertEqual(multiple_choice.get_correct_answer_indices(), {1})
        self.assertEqual(multiple_choice.get_correct_answer(), {"psql"})
        self.assertEqual(
            checkboxes.get_possible_answers(),
            ["Docker", "PostgreSQL", "Terraform", "Kubernetes"],
        )
        self.assertEqual(checkboxes.get_correct_answer_indices(), {1, 2, 3})
        self.assertEqual(
            checkboxes.get_correct_answer(),
            {"Docker", "PostgreSQL", "Terraform"},
        )
        self.assertEqual(free_form.answer_type, AnswerTypes.EXACT_STRING.value)
        self.assertIsNone(free_form.possible_answers)
        self.assertEqual(free_form.correct_answer, "psql")

    def test_seed_is_idempotent(self) -> None:
        first = seed_local_questions()
        first_ids = list(Question.objects.values_list("id", flat=True).order_by("id"))

        second = seed_local_questions()

        self.assertEqual(Question.objects.filter(homework=self.homework).count(), 3)
        self.assertEqual(
            list(Question.objects.values_list("id", flat=True).order_by("id")),
            first_ids,
        )
        self.assertEqual(first.questions_created, 3)
        self.assertEqual(second.questions_created, 0)

    def test_seed_can_target_another_existing_homework(self) -> None:
        other_cohort = Cohort.objects.create(
            slug="local-other-cohort",
            title="Other local cohort",
            description="Local course fixture",
        )
        other_homework = Homework.objects.create(
            course=other_cohort,
            slug="local-other-homework",
            title="Other local homework",
            due_date=timezone.now() + timezone.timedelta(days=7),
        )

        seed_local_questions(
            cohort_slug=other_cohort.slug,
            homework_slug=other_homework.slug,
        )

        self.assertEqual(Question.objects.filter(homework=self.homework).count(), 0)
        self.assertEqual(Question.objects.filter(homework=other_homework).count(), 3)

    def test_seeded_questions_render_in_the_existing_homework_form(self) -> None:
        seed_local_questions()

        response = self.client.get(
            reverse(
                "homework",
                kwargs={
                    "course_slug": self.cohort.slug,
                    "homework_slug": self.homework.slug,
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'type="radio"')
        self.assertContains(response, 'type="checkbox"')
        self.assertContains(response, 'type="text"')
        self.assertContains(response, "Which tool runs SQL commands against a PostgreSQL database?")


class LocalQuestionSeedRefusalTests(TestCase):
    def setUp(self) -> None:
        self.cohort = Cohort.objects.create(
            slug=DEFAULT_COHORT_SLUG,
            title="Data Engineering Zoomcamp 2026",
            description="Local course fixture",
        )
        Homework.objects.create(
            course=self.cohort,
            slug=DEFAULT_HOMEWORK_SLUG,
            title="Homework 1: Docker, SQL and Terraform",
            due_date=timezone.now() + timezone.timedelta(days=7),
        )

    def test_non_local_environment_is_refused(self) -> None:
        for environment in (RuntimeEnvironment.DEVELOPMENT, RuntimeEnvironment.PRODUCTION):
            with self.subTest(environment=environment):
                with override_settings(RUNTIME_ENVIRONMENT=environment):
                    with self.assertRaises(LocalQuestionSeedError) as refusal:
                        seed_local_questions()

                self.assertEqual(str(refusal.exception), "environment-not-local")
                self.assertEqual(Question.objects.count(), 0)

    def test_non_sqlite_database_is_refused(self) -> None:
        databases = {"default": {**settings.DATABASES["default"]}}
        databases["default"]["ENGINE"] = "django.db.backends.postgresql"

        with override_settings(DATABASES=databases):
            with self.assertRaises(LocalQuestionSeedError) as refusal:
                seed_local_questions()

        self.assertEqual(str(refusal.exception), "database-not-local-sqlite")
        self.assertEqual(Question.objects.count(), 0)

    def test_missing_homework_is_refused_without_writing(self) -> None:
        with self.assertRaises(LocalQuestionSeedError) as refusal:
            seed_local_questions(homework_slug="missing-homework")

        self.assertEqual(str(refusal.exception), "homework-not-found")
        self.assertEqual(Question.objects.count(), 0)


class SeedLocalQuestionsCommandTests(TestCase):
    def setUp(self) -> None:
        cohort = Cohort.objects.create(
            slug=DEFAULT_COHORT_SLUG,
            title="Data Engineering Zoomcamp 2026",
            description="Local course fixture",
        )
        Homework.objects.create(
            course=cohort,
            slug=DEFAULT_HOMEWORK_SLUG,
            title="Homework 1: Docker, SQL and Terraform",
            due_date=timezone.now() + timezone.timedelta(days=7),
        )

    def test_command_reports_what_it_wrote(self) -> None:
        stdout = StringIO()

        call_command("seed_local_questions", stdout=stdout)

        summary = json.loads(stdout.getvalue())
        self.assertTrue(summary["written"])
        self.assertEqual(summary["questions"], 3)
        self.assertEqual(summary["questions_created"], 3)
        self.assertEqual(summary["cohort_slug"], DEFAULT_COHORT_SLUG)
        self.assertEqual(summary["homework_slug"], DEFAULT_HOMEWORK_SLUG)

    def test_check_validates_without_writing(self) -> None:
        stdout = StringIO()

        call_command("seed_local_questions", "--check", stdout=stdout)

        summary = json.loads(stdout.getvalue())
        self.assertFalse(summary["written"])
        self.assertTrue(summary["checked"])
        self.assertEqual(Question.objects.count(), 0)

    @override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.PRODUCTION)
    def test_command_fails_closed_outside_local_development(self) -> None:
        with self.assertRaises(CommandError) as refusal:
            call_command("seed_local_questions", stdout=StringIO())

        self.assertEqual(str(refusal.exception), "environment-not-local")
        self.assertEqual(Question.objects.count(), 0)
