from __future__ import annotations

import hashlib
import unittest
from datetime import UTC, datetime

from django.conf import settings
from django.db import connections
from django.db.migrations.executor import MigrationExecutor


class CurriculumSourceProvenanceMigrationTests(unittest.TestCase):
    migrate_from = ("courses", "0047_alter_cohort_identifier")
    migrate_to = (
        "courses",
        "0048_coursecurriculumimportrun_cohort_source_checksum_and_more",
    )

    def setUp(self) -> None:
        super().setUp()
        digest = hashlib.sha256(self.id().encode("utf-8")).hexdigest()[:12]
        self.worker_id = f"courses-provenance-{digest}"
        layout = settings.TEST_RUNTIME.worker(self.worker_id)
        database_path = settings.TEST_RUNTIME.assert_database_path(
            layout.database,
            worker_id=self.worker_id,
        )
        self.connection = connections["default"]
        self.connection.close()
        self.original_name = self.connection.settings_dict["NAME"]
        self.original_test = self.connection.settings_dict["TEST"]
        self.original_worker = self.connection.settings_dict["DTC_WORKER_ID"]
        self.connection.settings_dict["NAME"] = database_path
        self.connection.settings_dict["TEST"] = {"NAME": database_path}
        self.connection.settings_dict["DTC_WORKER_ID"] = self.worker_id

    def tearDown(self) -> None:
        self.connection.close()
        self.connection.settings_dict["NAME"] = self.original_name
        self.connection.settings_dict["TEST"] = self.original_test
        self.connection.settings_dict["DTC_WORKER_ID"] = self.original_worker
        self.connection.connect()
        super().tearDown()

    def test_existing_curriculum_rows_survive_forward_and_reverse_migration(self):
        executor, apps = self._migrate([self.migrate_from])
        Course = apps.get_model("courses", "Course")
        Cohort = apps.get_model("courses", "Cohort")
        Homework = apps.get_model("courses", "Homework")
        Question = apps.get_model("courses", "Question")
        Module = apps.get_model("courses", "Module")
        Unit = apps.get_model("courses", "Unit")

        course = Course.objects.create(slug="legacy-family", title="Legacy family")
        cohort = Cohort.objects.create(
            course=course,
            slug="legacy-cohort",
            identifier="legacy",
            title="Legacy cohort",
            description="Legacy data",
            curriculum_format="modules",
        )
        homework = Homework.objects.create(
            course=cohort,
            slug="homework-1",
            title="Homework 1",
            due_date=datetime(2026, 9, 1, tzinfo=UTC),
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
            question_type="FF",
            correct_answer="legacy plaintext answer",
        )
        primary_keys = {
            "course": course.pk,
            "cohort": cohort.pk,
            "homework": homework.pk,
            "module": module.pk,
            "unit": unit.pk,
            "question": question.pk,
        }

        executor, apps = self._migrate([self.migrate_to])
        for model_name, key in primary_keys.items():
            model = apps.get_model("courses", model_name.capitalize())
            row = model.objects.get(pk=key)
            self.assertIsNone(row.source_content_id)
            self.assertIsNone(row.source_path)
            self.assertIsNone(row.source_commit_sha)
            self.assertIsNone(row.source_checksum)
        migrated_question = apps.get_model("courses", "Question").objects.get(pk=question.pk)
        self.assertEqual(migrated_question.correct_answer, "legacy plaintext answer")
        self.assertIsNone(migrated_question.answer_envelope)
        self.assertEqual(
            apps.get_model("courses", "CourseCurriculumImportRun").objects.count(),
            0,
        )

        executor, apps = self._migrate([self.migrate_from])
        reversed_question = apps.get_model("courses", "Question").objects.get(pk=question.pk)
        self.assertEqual(reversed_question.correct_answer, "legacy plaintext answer")
        self.assertTrue(apps.get_model("courses", "Unit").objects.filter(pk=unit.pk).exists())

        self._migrate([self.migrate_to])

    def _migrate(self, targets: list[tuple[str, str]]):
        executor = MigrationExecutor(self.connection)
        executor.migrate(targets)
        apps = executor.loader.project_state(targets).apps
        return executor, apps
