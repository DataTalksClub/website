from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import (
    Cohort,
    Course,
    CurriculumFlowItem,
    CurriculumFormat,
    Homework,
    HomeworkState,
    Module,
    Project,
    ProjectState,
    Unit,
)


class CoursePageCurriculumRenderingTests(TestCase):
    def make_cohort(self, slug, curriculum_format):
        course_family = Course.objects.create(
            slug=f"{slug}-family",
            title=f"{slug.title()} Family",
        )
        return Cohort.objects.create(
            course=course_family,
            slug=slug,
            title=f"{slug.title()} Cohort",
            description="A curriculum rendering fixture.",
            curriculum_format=curriculum_format,
        )

    def make_homework(self, cohort, slug, title, days_due):
        return Homework.objects.create(
            course=cohort,
            slug=slug,
            title=title,
            description=f"{title} description.",
            due_date=timezone.now() + timedelta(days=days_due),
            state=HomeworkState.OPEN.value,
        )

    def make_project(self, cohort, slug, title, state, days_due):
        now = timezone.now()
        return Project.objects.create(
            course=cohort,
            slug=slug,
            title=title,
            state=state,
            submission_due_date=now + timedelta(days=days_due),
            peer_review_due_date=now + timedelta(days=days_due + 1),
        )

    def course_response(self, cohort):
        return self.client.get(
            reverse(
                "course",
                kwargs={
                    "course_slug": cohort.course.slug,
                    "cohort_year": cohort.year,
                },
            )
        )

    def test_legacy_cohort_keeps_homework_then_projects_sections(self):
        cohort = self.make_cohort("legacy-rendering", CurriculumFormat.LEGACY)
        self.make_homework(cohort, "legacy-homework", "Legacy Homework", 3)
        self.make_project(
            cohort,
            "legacy-project",
            "Legacy Project",
            ProjectState.COLLECTING_SUBMISSIONS.value,
            5,
        )

        response = self.course_response(cohort)
        page = response.content.decode("utf-8")

        self.assertFalse(response.context["is_module_curriculum"])
        self.assertEqual(response.context["curriculum_flow"], ())
        self.assertContains(response, 'id="homework-heading"')
        self.assertContains(response, 'id="projects-heading"')
        self.assertLess(page.index('id="homework-heading"'), page.index('id="projects-heading"'))
        self.assertNotContains(response, 'id="curriculum-flow-heading"')
        self.assertNotContains(response, 'class="row-list course-flow"')

    def test_module_flow_orders_modules_projects_and_terminal_homework(self):
        cohort = self.make_cohort("module-rendering", CurriculumFormat.MODULES)
        homework_a = self.make_homework(
            cohort,
            "module-a-homework",
            "Module A Homework",
            3,
        )
        homework_b = self.make_homework(
            cohort,
            "module-b-homework",
            "Module B Homework",
            7,
        )
        project_x = self.make_project(
            cohort,
            "project-x",
            "Project X",
            ProjectState.COLLECTING_SUBMISSIONS.value,
            5,
        )
        project_y = self.make_project(
            cohort,
            "project-y",
            "Project Y",
            ProjectState.COMPLETED.value,
            9,
        )

        module_a = Module.objects.create(
            cohort=cohort,
            position=10,
            slug="module-a",
            title="Module A",
            link="https://example.invalid/module-a",
            terminal_homework=homework_a,
        )
        Unit.objects.create(
            module=module_a,
            position=20,
            slug="module-a-unit-2",
            title="Module A Unit 2",
            link="https://example.invalid/module-a-unit-2",
        )
        Unit.objects.create(
            module=module_a,
            position=10,
            slug="module-a-unit-1",
            title="Module A Unit 1",
            link="https://example.invalid/module-a-unit-1",
        )
        module_b = Module.objects.create(
            cohort=cohort,
            position=30,
            slug="module-b",
            title="Module B",
            terminal_homework=homework_b,
        )
        Unit.objects.create(
            module=module_b,
            position=20,
            slug="module-b-unit-2",
            title="Module B Unit 2",
        )
        Unit.objects.create(
            module=module_b,
            position=10,
            slug="module-b-unit-1",
            title="Module B Unit 1",
        )

        CurriculumFlowItem.objects.create(cohort=cohort, position=10, module=module_a)
        CurriculumFlowItem.objects.create(cohort=cohort, position=20, project=project_x)
        CurriculumFlowItem.objects.create(cohort=cohort, position=30, module=module_b)
        CurriculumFlowItem.objects.create(cohort=cohort, position=40, project=project_y)

        response = self.course_response(cohort)
        page = response.content.decode("utf-8")
        main = page[page.index("<main"): page.index("</main>")]
        flow = response.context["curriculum_flow"]

        self.assertTrue(response.context["is_module_curriculum"])
        self.assertEqual(
            [(item.kind, item.position) for item in flow],
            [("module", 10), ("project", 20), ("module", 30), ("project", 40)],
        )
        self.assertEqual(
            [item.title for item in flow[0].units],
            ["Module A Unit 1", "Module A Unit 2"],
        )
        self.assertEqual(
            [item.title for item in flow[2].units],
            ["Module B Unit 1", "Module B Unit 2"],
        )
        self.assertIs(flow[0].homework, response.context["homeworks"][0])
        self.assertIs(flow[2].homework, response.context["homeworks"][1])

        self.assertContains(response, 'id="curriculum-flow-heading"')
        self.assertContains(response, ">Course modules</h2>")
        self.assertContains(response, ">Homework</p>")
        self.assertNotContains(response, "Terminal homework")
        self.assertNotContains(response, 'id="projects-heading"')
        self.assertNotIn(">Projects</h2>", main)

        ordered_titles = (
            "Module A",
            "Module A Homework",
            "Project X",
            "Module B",
            "Module B Homework",
            "Project Y",
        )
        title_positions = [main.index(title) for title in ordered_titles]
        self.assertEqual(title_positions, sorted(title_positions))

        self.assertContains(
            response,
            reverse(
                "homework",
                kwargs={
                    "course_slug": cohort.course.slug,
                    "cohort_year": cohort.year,
                    "homework_slug": homework_a.slug,
                },
            ),
        )
        self.assertContains(
            response,
            reverse(
                "module",
                kwargs={
                    "course_slug": cohort.course.slug,
                    "cohort_identifier": cohort.identifier,
                    "module_slug": module_b.slug,
                },
            ),
        )
        self.assertContains(
            response,
            reverse(
                "project",
                kwargs={
                    "course_slug": cohort.course.slug,
                    "cohort_year": cohort.year,
                    "project_slug": project_x.slug,
                },
            ),
        )
        self.assertContains(
            response,
            reverse(
                "project_results",
                kwargs={
                    "course_slug": cohort.course.slug,
                    "cohort_year": cohort.year,
                    "project_slug": project_y.slug,
                },
            ),
        )
        self.assertContains(response, "Open")
        self.assertContains(response, "Not submitted")
        self.assertContains(response, homework_a.due_date.strftime("%Y-%m-%d"))
        self.assertContains(response, project_x.submission_due_date.strftime("%Y-%m-%d"))
        self.assertContains(response, 'data-deadline=')
        self.assertNotIn("<details", main)
