"""Reviewed target defaults preserve importer refusal and replay behavior."""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from community_base.coursework import models as cw
from community_base.curriculum import models as curriculum
from django.test import TestCase

from courses import models as site
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
            (curriculum.Module, ("source_sibling_position",)),
            (curriculum.Unit, ("source_sibling_position",)),
            (cw.Homework, ("stepper_enabled",)),
            (cw.Question, ("authored_position", "step_label")),
            (cw.Project, ("module", "commit_id_field")),
        )
        with ExitStack() as patches:
            for model, names in additions:
                fields = _concrete_fields_with(model, names)
                patches.enter_context(patch.object(model._meta, "concrete_fields", fields))
            importer._refuse_mapping_drift()

    def test_unknown_concrete_field_refuses_before_any_family_write(self):
        models = (cw.Homework, cw.Question, cw.Project, curriculum.Module, curriculum.Unit)
        for model in (*models, site.SharedModule, site.SharedLesson):
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


class SharedCoursePlatformProjectDefaultTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _import_family_graph()

    def setUp(self):
        names = {field.name for field in cw.Project._meta.concrete_fields}
        if not {"module", "commit_id_field"} <= names:
            self.skipTest("Requires real C5.2m models; mandatory in disposable P16 check")

    def test_fresh_import_keeps_project_defaults_and_copied_families(self):
        report = importer.import_course_platform(apply=True)
        for family, entry in report["verification"].items():
            self.assertTrue(entry["equal"], family)
        project = cw.Project.objects.get(slug="final")
        self.assertIsNone(project.module_id)
        self.assertTrue(project.commit_id_field)
        self.assertEqual(project.title, "Final project")
        self.assertEqual(cw.ProjectSubmission.objects.filter(project=project).count(), 2)
        self.assertEqual(cw.PeerReview.objects.count(), 1)
        self.assertEqual(cw.ProjectEvaluationScore.objects.count(), 1)

    def test_replay_keeps_metadata_identities_and_updates_source_fields(self):
        importer.import_course_platform(apply=True)
        project = cw.Project.objects.get(slug="final")
        module = curriculum.Module.objects.get(slug="intro")
        identities = {}
        for model in (cw.Project, cw.ProjectSubmission, cw.PeerReview, cw.ProjectEvaluationScore):
            identities[model] = list(model.objects.order_by("pk").values_list("pk", flat=True))
        project.module = module
        project.commit_id_field = False
        project.save(update_fields=["module", "commit_id_field"])
        site.Project.objects.filter(slug="final").update(title="Updated final project")
        report = importer.import_course_platform(apply=True)
        for family, entry in report["verification"].items():
            self.assertTrue(entry["equal"], family)
        project.refresh_from_db()
        self.assertEqual(project.module_id, module.pk)
        self.assertFalse(project.commit_id_field)
        self.assertEqual(project.title, "Updated final project")
        for model, expected in identities.items():
            self.assertEqual(
                list(model.objects.order_by("pk").values_list("pk", flat=True)), expected
            )


def _curriculum_order_graph():
    _import_family_graph()
    intro = site.SharedModule.objects.get(slug="intro")
    intro.position = 17
    intro.save(update_fields=["position"])
    early = site.SharedModule.objects.create(
        curriculum=intro.curriculum, slug="early", title="Early", position=5
    )
    site.SharedLesson.objects.filter(slug="welcome").update(position=19)
    site.SharedLesson.objects.create(module=intro, slug="second", title="Second", position=7)
    site.SharedLesson.objects.create(module=early, slug="start", title="Start", position=3)
    cohort = site.Cohort.objects.get(identifier="2026")
    site.CohortSharedModule.objects.create(cohort=cohort, shared_module=early, position=9)


def _curriculum_identity_snapshot():
    fields = (
        (curriculum.Module, ("pk", "course_id", "parent_id", "slug")),
        (curriculum.Unit, ("pk", "module_id", "slug")),
        (curriculum.CohortModule, ("pk", "cohort_id", "module_id", "sort_order")),
        (curriculum.UnitProgress, ("pk", "user_id", "unit_id", "completed_at")),
    )
    result = {}
    for model, names in fields:
        result[model._meta.label] = list(model.objects.order_by("pk").values_list(*names))
    return result


class SharedCoursePlatformSourceOrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _curriculum_order_graph()

    def setUp(self):
        for model in (curriculum.Module, curriculum.Unit):
            names = {field.name for field in model._meta.concrete_fields}
            if "source_sibling_position" not in names:
                self.skipTest("Requires real C5.4 models; mandatory in disposable P16 check")

    def import_and_check_counts(self):
        report = importer.import_course_platform(apply=True)
        for family, entry in report["verification"].items():
            self.assertTrue(entry["equal"], family)
        self.assertEqual(curriculum.Module.objects.count(), 2)
        self.assertEqual(curriculum.Unit.objects.count(), 3)
        self.assertEqual(curriculum.CohortModule.objects.count(), 2)
        self.assertEqual(curriculum.UnitProgress.objects.count(), 1)

    def assert_copied_positions(self):
        pairs = (
            (site.SharedModule.objects.filter(published=True), curriculum.Module),
            (
                site.SharedLesson.objects.filter(
                    published=True, retired_at=None, module__published=True
                ),
                curriculum.Unit,
            ),
        )
        for source, target in pairs:
            self.assertEqual(
                list(source.order_by("slug").values_list("slug", "position")),
                list(target.objects.order_by("slug").values_list("slug", "sort_order")),
            )

    def test_fresh_null_defaults_preserve_copied_values_and_order(self):
        from community_base.curriculum.projection import CourseTree

        self.import_and_check_counts()
        self.assert_copied_positions()
        for model in (curriculum.Module, curriculum.Unit):
            for row in model.objects.all():
                self.assertIsNone(row.source_sibling_position)
        intro = curriculum.Module.objects.get(slug="intro")
        welcome = curriculum.Unit.objects.get(slug="welcome")
        self.assertEqual(welcome.module_id, intro.pk)
        self.assertIsNone(intro.parent_id)
        self.assertEqual(intro.overview, "Module overview text.")
        self.assertEqual(intro.overview_html, "<p>Module overview text.</p>\n")
        self.assertEqual(welcome.body, "Lesson body.")
        self.assertEqual(welcome.body_html, "<p>Lesson body.</p>\n")
        tree = CourseTree(intro.course)
        self.assertEqual([row.slug for row in tree.roots], ["early", "intro"])
        self.assertEqual([row.slug for row in tree.ordered_units()], ["start", "second", "welcome"])

    def test_replay_preserves_non_null_metadata_identity_and_progress(self):
        self.import_and_check_counts()
        before = _curriculum_identity_snapshot()
        metadata = []
        for model in (curriculum.Module, curriculum.Unit):
            for ordinal, row in enumerate(model.objects.order_by("pk"), start=11):
                row.source_sibling_position = ordinal
                row.save(update_fields=["source_sibling_position"])
                metadata.append((row, ordinal))
        site.SharedModule.objects.filter(slug="intro").update(position=31)
        site.SharedLesson.objects.filter(slug="welcome").update(position=29)
        self.import_and_check_counts()
        self.assert_copied_positions()
        self.assertEqual(_curriculum_identity_snapshot(), before)
        for row, ordinal in metadata:
            row.refresh_from_db()
            self.assertEqual(row.source_sibling_position, ordinal)
        self.assertEqual(curriculum.Module.objects.get(slug="intro").sort_order, 31)
        self.assertEqual(curriculum.Unit.objects.get(slug="welcome").sort_order, 29)

    def test_null_metadata_preserves_target_only_sort_order_ties(self):
        from community_base.curriculum.projection import CourseTree

        self.import_and_check_counts()
        self.assert_copied_positions()
        # Site sibling positions are unique; tie only the permitted package rows.
        curriculum.Module.objects.update(sort_order=8)
        curriculum.Unit.objects.update(sort_order=8)
        modules = list(curriculum.Module.objects.order_by("pk"))
        expected = []
        for module in modules:
            expected.extend(module.units.order_by("pk").values_list("pk", flat=True))
        tree = CourseTree(modules[0].course)
        self.assertEqual([row.pk for row in tree.roots], [row.pk for row in modules])
        self.assertEqual([row.pk for row in tree.ordered_units()], expected)
