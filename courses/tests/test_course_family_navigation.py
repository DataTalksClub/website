from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Cohort, Course, Enrollment, Project, ProjectState

User = get_user_model()


class CourseFamilyNavigationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.family = Course.objects.create(
            slug="data-engineering",
            title="Data Engineering Zoomcamp",
            description="A practical data engineering course.",
        )
        cls.previous = Cohort.objects.create(
            course=cls.family,
            slug="data-engineering-2025",
            identifier="2025",
            year=2025,
            title="Data Engineering Zoomcamp 2025",
            description="The 2025 cohort.",
        )
        cls.current = Cohort.objects.create(
            course=cls.family,
            slug="data-engineering-2026",
            identifier="2026",
            year=2026,
            title="Data Engineering Zoomcamp 2026",
            description="The 2026 cohort.",
            first_homework_scored=False,
        )
        cls.hidden = Cohort.objects.create(
            course=cls.family,
            slug="data-engineering-private",
            identifier="private",
            year=2027,
            title="Private edition",
            description="Not public.",
            visible=False,
        )
        due_date = timezone.now() + timezone.timedelta(days=7)
        cls.previous_project = Project.objects.create(
            course=cls.previous,
            slug="pipeline-project",
            title="2025 Pipeline Project",
            submission_due_date=due_date,
            peer_review_due_date=due_date,
        )
        cls.current_project = Project.objects.create(
            course=cls.current,
            slug="warehouse-project",
            title="2026 Warehouse Project",
            state=ProjectState.PEER_REVIEWING.value,
            submission_due_date=due_date,
            peer_review_due_date=due_date,
        )
        cls.hidden_project = Project.objects.create(
            course=cls.hidden,
            slug="private-project",
            title="Private Project",
            submission_due_date=due_date,
            peer_review_due_date=due_date,
        )

    def course_url(self, cohort):
        return reverse(
            "course",
            kwargs={
                "course_slug": self.family.slug,
                "cohort_year": cohort.identifier,
            },
        )

    def project_url(self, cohort, project):
        return reverse(
            "project",
            kwargs={
                "course_slug": self.family.slug,
                "cohort_year": cohort.identifier,
                "project_slug": project.slug,
            },
        )

    def test_family_lists_visible_editions_and_edition_project_routes(self):
        response = self.client.get(
            reverse("course_family", kwargs={"course_slug": self.family.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "courses/course_family.html")
        self.assertEqual(
            [edition.cohort for edition in response.context["cohort_editions"]],
            [self.current, self.previous],
        )
        self.assertContains(response, self.course_url(self.current))
        self.assertContains(response, self.course_url(self.previous))
        self.assertContains(response, self.project_url(self.current, self.current_project))
        self.assertContains(response, self.project_url(self.previous, self.previous_project))
        self.assertNotContains(response, self.hidden.title)
        self.assertNotContains(response, self.hidden_project.title)

    def test_cohort_page_exposes_other_visible_editions_and_family_breadcrumb(self):
        response = self.client.get(self.course_url(self.current))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'<a href="{reverse("course_list")}">Courses</a>',
        )
        self.assertContains(
            response,
            (
                f'<a href="{reverse("course_family", kwargs={"course_slug": self.family.slug})}">'
                f'{self.family.title}</a>'
            ),
        )
        self.assertContains(response, f'<li aria-current="page">{self.current.identifier}</li>')
        self.assertContains(response, f'<h1 id="course-heading">{self.current.title}</h1>')
        self.assertContains(response, self.course_url(self.previous))
        self.assertNotContains(response, self.course_url(self.hidden))

    def test_public_cohort_navigation_keeps_dashboard_and_leaderboard_before_scoring(self):
        response = self.client.get(self.course_url(self.current))

        self.assertEqual(response.status_code, 200)
        for route_name in (
            "dashboard",
            "leaderboard",
            "list_all_project_submissions",
            "course_calendar",
        ):
            with self.subTest(route=route_name):
                route_url = reverse(
                    route_name,
                    kwargs={
                        "course_slug": self.family.slug,
                        "cohort_year": self.current.identifier,
                    },
                )
                self.assertContains(response, f'href="{route_url}"')

        self.assertContains(response, "Course dashboard")
        self.assertContains(response, "Course leaderboard")
        self.assertContains(response, "Project submissions")
        self.assertContains(response, "Calendar feed")

    def test_learner_only_navigation_is_hidden_from_anonymous_visitors(self):
        response = self.client.get(self.course_url(self.current))
        enrollment_url = reverse(
            "enrollment",
            kwargs={
                "course_slug": self.family.slug,
                "cohort_year": self.current.identifier,
            },
        )

        self.assertNotContains(response, "Edit course profile")
        self.assertNotContains(response, enrollment_url)
        self.assertNotContains(response, "Download Certificate")

    def test_learner_only_navigation_is_visible_for_the_enrolled_learner(self):
        user = User.objects.create_user(
            username="navigation@example.com",
            email="navigation@example.com",
            password="password",
        )
        enrollment = Enrollment.objects.create(
            student=user,
            course=self.current,
            certificate_url="https://example.com/certificate.pdf",
        )
        self.client.force_login(user)
        response = self.client.get(self.course_url(self.current))
        enrollment_url = reverse(
            "enrollment",
            kwargs={
                "course_slug": self.family.slug,
                "cohort_year": self.current.identifier,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{enrollment_url}"')
        self.assertContains(response, "Edit course profile")
        self.assertContains(response, enrollment.certificate_url)
        self.assertContains(response, "Download Certificate")
