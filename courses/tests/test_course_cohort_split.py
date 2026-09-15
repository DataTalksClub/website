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
                "cohort",
                kwargs={
                    "course_slug": family.slug,
                    "cohort_identifier": cohort.identifier,
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
        self.assertContains(
            response, "/courses/ml-zoomcamp/spring-2026/homework/homework-01"
        )
        self.assertContains(
            response, "/courses/ml-zoomcamp/spring-2026/project/project-01"
        )
        self.assertContains(
            response,
            '<link rel="canonical" href="https://datatalks.club/courses/ml-zoomcamp/spring-2026">',
        )

        # "project" (singular) only ever appears as a literal 3-segment
        # route shim (``<family>/project/<slug>``); it never sits at the
        # bare 2-segment position, so it's not a reserved identifier -- a
        # cohort may still be identified "project" without ambiguity.
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
            "cohort_project",
            kwargs={
                "course_slug": family.slug,
                "cohort_identifier": reserved.identifier,
                "project_slug": "project-01",
            },
        )
        self.assertEqual(
            reserved_project_url,
            "/courses/ml-zoomcamp/project/project/project-01",
        )
        self.assertEqual(self.client.get(reserved_project_url).status_code, 200)


class CanonicalCourseRouteTests(TestCase):
    """The flat two-segment shape is canonical (issue #320, 2026-09-15).

    ``cohort``/``cohort_*`` are the one name per concept: they used to carry
    a literal ``cohorts/`` segment and a parallel, inbound-only plain-named
    set covered the flat shape; the two are unified now, so every one of
    these names reverses to the flat path directly.
    """

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

    def test_cohort_route_names_reverse_to_the_flat_canonical_paths(self):
        cohort_kwargs = {
            "course_slug": "de-zoomcamp",
            "cohort_identifier": 2026,
        }
        expected = {
            "cohort": "/courses/de-zoomcamp/2026",
            "cohort_calendar": "/courses/de-zoomcamp/2026/calendar.ics",
            "cohort_dashboard": "/courses/de-zoomcamp/2026/dashboard",
            "cohort_enrollment": "/courses/de-zoomcamp/2026/enrollment",
            "cohort_leaderboard": "/courses/de-zoomcamp/2026/leaderboard",
            "cohort_projects": "/courses/de-zoomcamp/2026/projects",
            "cohort_homework": "/courses/de-zoomcamp/2026/homework/hw-01",
            "cohort_project": "/courses/de-zoomcamp/2026/project/project-01",
        }
        route_kwargs = {
            **cohort_kwargs,
            "homework_slug": "hw-01",
        }
        self.assertEqual(reverse("cohort", kwargs=cohort_kwargs), expected["cohort"])
        for name in (
            "cohort_calendar",
            "cohort_dashboard",
            "cohort_enrollment",
            "cohort_leaderboard",
            "cohort_projects",
        ):
            with self.subTest(route=name):
                self.assertEqual(reverse(name, kwargs=cohort_kwargs), expected[name])
        self.assertEqual(
            reverse("cohort_homework", kwargs=route_kwargs), expected["cohort_homework"]
        )
        self.assertEqual(
            reverse(
                "cohort_project",
                kwargs={**cohort_kwargs, "project_slug": "project-01"},
            ),
            expected["cohort_project"],
        )
        self.assertEqual(
            reverse("course_family", kwargs={"course_slug": "de-zoomcamp"}),
            "/courses/de-zoomcamp",
        )

    def test_old_cohorts_prefixed_path_is_retired_not_redirected(self):
        response = self.client.get("/courses/de-zoomcamp/cohorts/2026/dashboard")
        self.assertEqual(response.status_code, 404)

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
        # Cards link the family page (the owner's "no special treatment"
        # ask): one CTA per card, and the family page is the one place that
        # shows each family's real, current state. The card stays a
        # keyboard-reachable whole-card link; exact attribute indentation
        # is template formatting.
        self.assertRegex(content, r'role="link"\s+tabindex="0"')
        self.assertIn('href="/courses/de-zoomcamp"', content)
        self.assertIn("event.key === 'Enter'", content)
        self.assertIn("event.key === ' '", content)
        self.assertNotIn("Open course", content)
