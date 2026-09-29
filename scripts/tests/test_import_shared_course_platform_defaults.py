"""Reviewed target defaults preserve importer refusal and replay behavior."""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from community_base.coursework import models as cw
from django.test import TestCase

from scripts.prod import import_shared_course_platform as importer
from scripts.tests.test_import_shared_course_platform import _import_family_graph


def _concrete_fields_with(model, names):
    fields = list(model._meta.concrete_fields)
    existing = {field.name for field in fields}
    for name in names:
        if name not in existing:
            fields.append(SimpleNamespace(name=name, primary_key=False))
    return fields


class SharedCoursePlatformDefaultGuardTests(TestCase):
    def test_reviewed_concrete_fields_are_accepted(self):
        additions = (
            (cw.Homework, ("stepper_enabled",)),
            (cw.Question, ("authored_position", "step_label")),
        )
        with ExitStack() as patches:
            for model, names in additions:
                fields = _concrete_fields_with(model, names)
                patches.enter_context(patch.object(model._meta, "concrete_fields", fields))
            importer._refuse_mapping_drift()

    def test_unknown_concrete_field_refuses_before_any_family_write(self):
        for model in (cw.Homework, cw.Question):
            with self.subTest(model=model._meta.label):
                fields = _concrete_fields_with(model, ("unreviewed_setting",))
                with patch.object(model._meta, "concrete_fields", fields):
                    with patch.object(importer, "_import_courses") as first_write:
                        with self.assertRaises(importer.MappingCoverageDrift) as caught:
                            importer.import_course_platform(apply=True)
                        first_write.assert_not_called()
                self.assertIn(f"{model._meta.label}: unreviewed_setting", caught.exception.models)


class SharedCoursePlatformAuthoredDefaultTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _import_family_graph()

    def setUp(self):
        expected = (
            (cw.Homework, {"stepper_enabled"}),
            (cw.Question, {"authored_position", "step_label"}),
        )
        for model, names in expected:
            actual = {field.name for field in model._meta.concrete_fields}
            if not names <= actual:
                self.skipTest("Requires real C5.4c models; mandatory in disposable P16 check")

    def assert_family_counts_equal(self, report):
        for family, entry in report["verification"].items():
            self.assertTrue(entry["equal"], family)

    def test_fresh_import_keeps_defaults_copied_values_and_id_order(self):
        from community_base.coursework.question_order import ordered_questions

        report = importer.import_course_platform(apply=True)
        self.assert_family_counts_equal(report)
        homework = cw.Homework.objects.get(slug="hw1")
        self.assertFalse(homework.stepper_enabled)
        self.assertEqual(homework.title, "Homework 1")
        questions = list(ordered_questions(homework))
        self.assertEqual([row.pk for row in questions], sorted(row.pk for row in questions))
        self.assertEqual([row.text for row in questions], ["What is 2+2?", "Free reflection"])
        self.assertEqual(questions[0].correct_answer, "2")
        self.assertEqual(questions[0].scores_for_correct_answer, 2)
        for question in questions:
            self.assertIsNone(question.authored_position)
            self.assertEqual(question.step_label, "")

    def test_replay_preserves_identity_and_target_only_metadata(self):
        importer.import_course_platform(apply=True)
        models = (cw.Homework, cw.Question, cw.Submission, cw.Answer)
        identities = {
            model: list(model.objects.order_by("pk").values_list("pk", flat=True))
            for model in models
        }
        homework = cw.Homework.objects.get(slug="hw1")
        homework.stepper_enabled = True
        homework.save(update_fields=["stepper_enabled"])
        questions = list(homework.questions.order_by("pk"))
        for position, question in enumerate(questions, start=5):
            question.authored_position = position
            question.step_label = f"Authored step {position}"
            question.save(update_fields=["authored_position", "step_label"])
        report = importer.import_course_platform(apply=True)
        self.assert_family_counts_equal(report)
        homework.refresh_from_db()
        self.assertTrue(homework.stepper_enabled)
        for model, expected in identities.items():
            self.assertEqual(
                list(model.objects.order_by("pk").values_list("pk", flat=True)), expected
            )
        for position, question in enumerate(questions, start=5):
            question.refresh_from_db()
            self.assertEqual(question.authored_position, position)
            self.assertEqual(question.step_label, f"Authored step {position}")
