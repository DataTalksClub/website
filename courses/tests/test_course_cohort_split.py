from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Course, Cohort, Homework, Project


class CourseCohortModelTests(TestCase):
    def test_course_family_can_own_multiple_years_while_curriculum_stays_on_cohort(self):
        family = Course.objects.create(
            slug="de-zoomcamp",
            title="Data Engineering Zoomcamp",
        )
        first = Cohort.objects.create(
            course=family,
            slug="de-zoomcamp-2025",
            year=2025,
            title="Data Engineering Zoomcamp 2025",
            description="2025 cohort",
        )
        second = Cohort.objects.create(
            course=family,
            slug="de-zoomcamp-2026",
            year=2026,
            title="Data Engineering Zoomcamp 2026",
            description="2026 cohort",
        )

        self.assertEqual(list(family.cohorts.order_by("year")), [first, second])
        self.assertEqual(first.canonical_url_path, "/courses/de-zoomcamp/2025")
        self.assertEqual(second.canonical_url_path, "/courses/de-zoomcamp/2026")
        self.assertIsNotNone(first.uuid)
        self.assertIsNotNone(second.uuid)

        homework = Homework.objects.create(
            course=second,
            slug="homework-01",
            title="Homework",
            description="Practice",
            due_date=timezone.now(),
        )
        project = Project.objects.create(
            course=second,
            slug="project-01",
            title="Project",
            submission_due_date=timezone.now(),
            peer_review_due_date=timezone.now(),
        )
        self.assertEqual(homework.course, second)
        self.assertEqual(project.course, second)
        self.assertFalse(hasattr(family, "students"))

    def test_cohort_identifier_can_be_non_numeric(self):
        family = Course.objects.create(
            slug="ml-zoomcamp",
            title="Machine Learning Zoomcamp",
        )
        cohort = Cohort.objects.create(
            course=family,
            slug="ml-zoomcamp-spring",
            identifier="spring-2026",
            year=2026,
            title="Machine Learning Zoomcamp Spring",
            description="A cohort identified by a season rather than a year.",
        )

        self.assertEqual(cohort.identifier, "spring-2026")
        self.assertEqual(
            cohort.canonical_url_path,
            "/courses/ml-zoomcamp/spring-2026",
        )
        self.assertEqual(
            reverse(
                "course",
                kwargs={
                    "course_slug": family.slug,
                    "cohort_year": cohort.identifier,
                },
            ),
            "/courses/ml-zoomcamp/spring-2026",
        )
        Homework.objects.create(
            course=cohort,
            slug="homework-01",
            title="Homework 1",
            description="Practice",
            due_date=timezone.now() + timezone.timedelta(days=7),
        )
        Project.objects.create(
            course=cohort,
            slug="project-01",
            title="Project 1",
            submission_due_date=timezone.now() + timezone.timedelta(days=7),
            peer_review_due_date=timezone.now() + timezone.timedelta(days=8),
        )
        response = self.client.get("/courses/ml-zoomcamp/spring-2026")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/courses/ml-zoomcamp/spring-2026/homework/homework-01")
        self.assertContains(response, "/courses/ml-zoomcamp/spring-2026/project/project-01")
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club/courses/ml-zoomcamp/spring-2026">',
        )

        reserved = Cohort.objects.create(
            course=family,
            slug="ml-zoomcamp-project",
            identifier="project",
            year=2027,
            title="Machine Learning Project Cohort",
            description="An identifier that is also a legacy route segment.",
        )
        Project.objects.create(
            course=reserved,
            slug="project-01",
            title="Project 1",
            submission_due_date=timezone.now() + timezone.timedelta(days=7),
            peer_review_due_date=timezone.now() + timezone.timedelta(days=8),
        )
        reserved_project_url = reverse(
            "project",
            kwargs={
                "course_slug": family.slug,
                "cohort_year": reserved.identifier,
                "project_slug": "project-01",
            },
        )
        self.assertEqual(
            reserved_project_url,
            "/courses/ml-zoomcamp/project/project/project-01",
        )
        self.assertEqual(self.client.get(reserved_project_url).status_code, 200)


class CanonicalCourseRouteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        family = Course.objects.create(
            slug="de-zoomcamp",
            title="Data Engineering Zoomcamp",
        )
        cls.cohort = Cohort.objects.create(
            course=family,
            slug="de-zoomcamp-2026",
            year=2026,
            title="Data Engineering Zoomcamp 2026",
            description="Practical data engineering.",
        )

    def test_public_route_names_reverse_to_family_year_paths(self):
        cohort_kwargs = {
            "course_slug": "de-zoomcamp",
            "cohort_year": 2026,
        }
        expected = {
            "course": "/courses/de-zoomcamp/2026",
            "course_calendar": "/courses/de-zoomcamp/2026/calendar.ics",
            "dashboard": "/courses/de-zoomcamp/2026/dashboard",
            "enrollment": "/courses/de-zoomcamp/2026/enrollment",
            "leaderboard": "/courses/de-zoomcamp/2026/leaderboard",
            "list_all_project_submissions": "/courses/de-zoomcamp/2026/projects",
            "homework": "/courses/de-zoomcamp/2026/homework/hw-01",
            "project": "/courses/de-zoomcamp/2026/project/project-01",
        }
        route_kwargs = {
            **cohort_kwargs,
            "homework_slug": "hw-01",
        }
        self.assertEqual(reverse("course", kwargs=cohort_kwargs), expected["course"])
        for name in (
            "course_calendar",
            "dashboard",
            "enrollment",
            "leaderboard",
            "list_all_project_submissions",
        ):
            with self.subTest(route=name):
                self.assertEqual(reverse(name, kwargs=cohort_kwargs), expected[name])
        self.assertEqual(reverse("homework", kwargs=route_kwargs), expected["homework"])
        self.assertEqual(
            reverse(
                "project",
                kwargs={**cohort_kwargs, "project_slug": "project-01"},
            ),
            expected["project"],
        )
        self.assertEqual(
            reverse("course_family", kwargs={"course_slug": "de-zoomcamp"}),
            "/courses/de-zoomcamp",
        )

    def test_family_landing_and_cohort_detail_use_the_canonical_contract(self):
        family_response = self.client.get("/courses/de-zoomcamp")
        self.assertEqual(family_response.status_code, 200)
        self.assertContains(family_response, "/courses/de-zoomcamp/2026")

        cohort_response = self.client.get("/courses/de-zoomcamp/2026")
        self.assertEqual(cohort_response.status_code, 200)
        self.assertContains(
            cohort_response,
            '<link rel="canonical" href="https://datatalks.club/courses/de-zoomcamp/2026">',
        )

    def test_course_catalog_cards_are_clickable_and_have_no_redundant_open_button(self):
        response = self.client.get(reverse("course_list"))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        canonical = "/courses/de-zoomcamp/2026"
        self.assertIn(
            f'role="link"\n                             tabindex="0"',
            content,
        )
        self.assertIn(canonical, content)
        self.assertIn("event.key === 'Enter'", content)
        self.assertIn("event.key === ' '", content)
        self.assertNotIn("Open course", content)
