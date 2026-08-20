from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from courses.models import (
    Cohort,
    Course,
    CurriculumFlowItem,
    CurriculumFormat,
    Homework,
    Module,
    Project,
    ProjectCriteriaAssignment,
    ReviewCriteria,
    ReviewCriteriaTypes,
    Unit,
    criteria_for_project,
)


class CurriculumModelTests(TestCase):
    def make_cohort(self, suffix, *, curriculum_format=CurriculumFormat.LEGACY):
        family = Course.objects.create(
            slug=f"curriculum-family-{suffix}",
            title=f"Curriculum Family {suffix}",
        )
        return Cohort.objects.create(
            course=family,
            slug=f"curriculum-cohort-{suffix}",
            title=f"Curriculum Cohort {suffix}",
            description="Curriculum model test cohort.",
            curriculum_format=curriculum_format,
        )

    def make_homework(self, cohort, slug):
        return Homework.objects.create(
            course=cohort,
            slug=slug,
            title=slug.replace("-", " ").title(),
            due_date=timezone.now() + timedelta(days=7),
        )

    def make_project(self, cohort, slug):
        now = timezone.now()
        return Project.objects.create(
            course=cohort,
            slug=slug,
            title=slug.replace("-", " ").title(),
            submission_due_date=now + timedelta(days=7),
            peer_review_due_date=now + timedelta(days=8),
        )

    def make_criteria(self, *, course=None, description="Criterion"):
        return ReviewCriteria.objects.create(
            course=course,
            description=description,
            options=[{"criteria": "Good", "score": 1}],
            review_criteria_type=ReviewCriteriaTypes.RADIO_BUTTONS.value,
        )

    def save_assignment(self, project, criteria, position):
        assignment = ProjectCriteriaAssignment(
            project=project,
            criteria=criteria,
            position=position,
        )
        assignment.full_clean()
        assignment.save()
        return assignment

    def test_cohort_curriculum_format_defaults_to_legacy(self):
        cohort = self.make_cohort("default")

        self.assertEqual(cohort.curriculum_format, CurriculumFormat.LEGACY)
        self.assertEqual(
            Cohort._meta.get_field("curriculum_format").default,
            CurriculumFormat.LEGACY,
        )
        self.assertEqual(
            {value for value, _label in Cohort._meta.get_field("curriculum_format").choices},
            {CurriculumFormat.LEGACY, CurriculumFormat.MODULES},
        )

    def test_project_criteria_assignments_are_ordered_and_can_share_definitions(self):
        cohort = self.make_cohort("criteria", curriculum_format=CurriculumFormat.MODULES)
        first_project = self.make_project(cohort, "project-a")
        second_project = self.make_project(cohort, "project-b")
        first = self.make_criteria(course=cohort, description="C1")
        shared = self.make_criteria(course=cohort, description="C2")
        third = self.make_criteria(course=cohort, description="C3")

        self.save_assignment(first_project, shared, 0)
        self.save_assignment(first_project, first, 1)
        self.save_assignment(second_project, shared, 0)
        self.save_assignment(second_project, third, 1)

        self.assertEqual(
            list(criteria_for_project(first_project)),
            [shared, first],
        )
        self.assertEqual(
            list(second_project.criteria_for_project()),
            [shared, third],
        )
        self.assertEqual(
            list(first_project.criteria_assignments.values_list("position", flat=True)),
            [0, 1],
        )
        self.assertEqual(
            shared.project_assignments.count(),
            2,
        )

    def test_flow_item_requires_exactly_one_target(self):
        cohort = self.make_cohort("flow", curriculum_format=CurriculumFormat.MODULES)
        homework = self.make_homework(cohort, "homework-a")
        module = Module.objects.create(
            cohort=cohort,
            position=0,
            slug="module-a",
            title="Module A",
            terminal_homework=homework,
        )
        project = self.make_project(cohort, "project-a")

        module_item = CurriculumFlowItem(cohort=cohort, position=0, module=module)
        module_item.full_clean()
        module_item.save()
        project_item = CurriculumFlowItem(cohort=cohort, position=1, project=project)
        project_item.full_clean()
        project_item.save()

        with self.assertRaises(ValidationError):
            CurriculumFlowItem(cohort=cohort, position=2).full_clean()
        with self.assertRaises(ValidationError):
            CurriculumFlowItem(
                cohort=cohort,
                position=3,
                module=module,
                project=project,
            ).full_clean()

        with self.assertRaises(IntegrityError):
            CurriculumFlowItem.objects.create(
                cohort=cohort,
                position=4,
                module=module,
                project=project,
            )

    def test_curriculum_and_criteria_targets_must_stay_in_one_cohort(self):
        first_cohort = self.make_cohort("ownership-a", curriculum_format=CurriculumFormat.MODULES)
        second_cohort = self.make_cohort("ownership-b", curriculum_format=CurriculumFormat.MODULES)
        first_homework = self.make_homework(first_cohort, "homework-a")
        second_homework = self.make_homework(second_cohort, "homework-b")
        Module.objects.create(
            cohort=first_cohort,
            position=0,
            slug="module-a",
            title="Module A",
            terminal_homework=first_homework,
        )
        second_module = Module.objects.create(
            cohort=second_cohort,
            position=0,
            slug="module-b",
            title="Module B",
            terminal_homework=second_homework,
        )
        first_project = self.make_project(first_cohort, "project-a")
        second_project = self.make_project(second_cohort, "project-b")

        with self.assertRaises(ValidationError):
            Module(
                cohort=first_cohort,
                position=1,
                slug="module-cross-cohort",
                title="Cross cohort module",
                terminal_homework=second_homework,
            ).full_clean()
        with self.assertRaises(ValidationError):
            CurriculumFlowItem(
                cohort=first_cohort,
                position=1,
                module=second_module,
            ).full_clean()
        with self.assertRaises(ValidationError):
            CurriculumFlowItem(
                cohort=first_cohort,
                position=1,
                project=second_project,
            ).full_clean()

        criterion = self.make_criteria(course=first_cohort)
        with self.assertRaises(ValidationError):
            ProjectCriteriaAssignment(
                project=second_project,
                criteria=criterion,
                position=0,
            ).full_clean()

        unscoped_criterion = self.make_criteria(description="Shared only in first cohort")
        self.save_assignment(first_project, unscoped_criterion, 0)
        with self.assertRaises(ValidationError):
            ProjectCriteriaAssignment(
                project=second_project,
                criteria=unscoped_criterion,
                position=0,
            ).full_clean()

    def test_module_units_are_ordered_and_link_only(self):
        cohort = self.make_cohort("units", curriculum_format=CurriculumFormat.MODULES)
        homework = self.make_homework(cohort, "homework-a")
        module = Module.objects.create(
            cohort=cohort,
            position=0,
            slug="module-a",
            title="Module A",
            link="https://example.test/module-a",
            terminal_homework=homework,
        )
        first = Unit.objects.create(
            module=module,
            position=1,
            slug="unit-a-2",
            title="Unit A2",
            link="https://example.test/unit-a-2",
        )
        second = Unit.objects.create(
            module=module,
            position=0,
            slug="unit-a-1",
            title="Unit A1",
            link="https://example.test/unit-a-1",
        )

        self.assertEqual(list(module.units.all()), [second, first])
        self.assertEqual(module.homework, homework)
