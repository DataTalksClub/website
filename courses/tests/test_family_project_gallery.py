from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import (
    Cohort,
    Course,
    Enrollment,
    Project,
    ProjectSubmission,
    User,
)


class FamilyProjectGalleryTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.due = timezone.now() + timezone.timedelta(days=7)
        cls.family = Course.objects.create(slug="dtc-zoomcamp", title="DTC Zoomcamp")

        cls.cohort_2022 = cls._cohort(2022)
        cls.cohort_2023 = cls._cohort(2023)
        cls.cohort_2024 = cls._cohort(2024)
        cls.cohort_2025 = cls._cohort(2025)
        cls.cohort_hidden = cls._cohort(2026, visible=False)

        cls.project_2023 = cls._project(cls.cohort_2023, "midterm-2023")
        cls.project_2024 = cls._project(cls.cohort_2024, "midterm-2024")
        cls.project_2025_a = cls._project(cls.cohort_2025, "midterm-2025")
        cls.project_2025_b = cls._project(cls.cohort_2025, "capstone-2025")
        cls.project_hidden = cls._project(cls.cohort_hidden, "hidden-project")
        # cohort_2022 stays projectless: it must not become a group of its own.

        cls.user = User.objects.create_user(
            username="learner", email="learner@example.com", password="x"
        )
        cls.enrollment = Enrollment.objects.create(
            student=cls.user, course=cls.cohort_2025
        )
        cls.submission = ProjectSubmission.objects.create(
            project=cls.project_2025_a,
            student=cls.user,
            enrollment=cls.enrollment,
            github_link="https://github.com/example/repo",
        )
        cls.volunteer_only_submission = ProjectSubmission.objects.create(
            project=cls.project_2025_b,
            student=cls.user,
            enrollment=cls.enrollment,
            github_link="https://github.com/example/repo2",
            volunteer_review_only=True,
        )

    @classmethod
    def _cohort(cls, year, visible=True):
        return Cohort.objects.create(
            course=cls.family,
            slug=f"dtc-zoomcamp-{year}",
            identifier=str(year),
            year=year,
            title=f"DTC Zoomcamp {year}",
            description="",
            visible=visible,
        )

    @classmethod
    def _project(cls, cohort, slug):
        return Project.objects.create(
            course=cohort,
            slug=slug,
            title=slug.replace("-", " ").title(),
            submission_due_date=cls.due,
            peer_review_due_date=cls.due,
        )

    def gallery_url(self):
        return reverse("family_projects", kwargs={"course_slug": self.family.slug})


class FamilyProjectGalleryGroupingTests(FamilyProjectGalleryTestBase):
    def test_route_renders_the_gallery_template(self):
        response = self.client.get(self.gallery_url())

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/family_gallery.html")

    def test_groups_projects_by_cohort_newest_first(self):
        response = self.client.get(self.gallery_url())

        identifiers = [
            group.cohort.identifier for group in response.context["cohort_groups"]
        ]
        self.assertEqual(identifiers, ["2025", "2024", "2023"])

    def test_projects_are_attributed_to_the_right_cohort(self):
        response = self.client.get(self.gallery_url())
        groups = {
            group.cohort.identifier: group
            for group in response.context["cohort_groups"]
        }

        self.assertEqual(
            sorted(project.slug for project in groups["2025"].projects),
            ["capstone-2025", "midterm-2025"],
        )
        self.assertEqual(
            [project.slug for project in groups["2024"].projects], ["midterm-2024"]
        )
        self.assertEqual(
            [project.slug for project in groups["2023"].projects], ["midterm-2023"]
        )

    def test_excludes_the_hidden_cohort_and_its_project(self):
        response = self.client.get(self.gallery_url())
        identifiers = [
            group.cohort.identifier for group in response.context["cohort_groups"]
        ]

        self.assertNotIn("2026", identifiers)
        self.assertNotContains(response, "hidden-project")

    def test_a_cohort_with_no_projects_is_not_a_group(self):
        response = self.client.get(self.gallery_url())
        identifiers = [
            group.cohort.identifier for group in response.context["cohort_groups"]
        ]

        self.assertNotIn("2022", identifiers)

    def test_submission_counts_exclude_volunteer_review_only(self):
        response = self.client.get(self.gallery_url())
        groups = {
            group.cohort.identifier: group
            for group in response.context["cohort_groups"]
        }
        projects_2025 = {project.slug: project for project in groups["2025"].projects}

        self.assertEqual(projects_2025["midterm-2025"].submissions_count, 1)
        self.assertEqual(projects_2025["capstone-2025"].submissions_count, 0)


class FamilyProjectGalleryLinkTests(FamilyProjectGalleryTestBase):
    def test_links_to_each_projects_cohort_scoped_list(self):
        response = self.client.get(self.gallery_url())
        project_list_url = reverse(
            "cohort_project_list",
            kwargs={
                "course_slug": self.family.slug,
                "cohort_identifier": "2025",
                "project_slug": "midterm-2025",
            },
        )

        self.assertContains(response, project_list_url)

    def test_links_to_the_cohorts_full_submission_catalogue(self):
        response = self.client.get(self.gallery_url())
        cohort_projects_url = reverse(
            "cohort_projects",
            kwargs={"course_slug": self.family.slug, "cohort_identifier": "2025"},
        )

        self.assertContains(response, cohort_projects_url)

    def test_links_to_the_site_wide_gallery(self):
        response = self.client.get(self.gallery_url())

        self.assertContains(response, reverse("all_projects"))

    def test_family_page_links_to_this_gallery(self):
        response = self.client.get(
            reverse("course_family", kwargs={"course_slug": self.family.slug})
        )

        self.assertContains(response, self.gallery_url())


class FamilyProjectGalleryDisclosureTests(FamilyProjectGalleryTestBase):
    def test_the_two_newest_cohorts_are_open_by_default(self):
        response = self.client.get(self.gallery_url())

        self.assertContains(response, 'data-gallery-cohort="2025" open>')
        self.assertContains(response, 'data-gallery-cohort="2024" open>')

    def test_older_cohorts_are_folded_by_default(self):
        response = self.client.get(self.gallery_url())

        self.assertContains(response, 'data-gallery-cohort="2023" >')
        self.assertNotContains(response, 'data-gallery-cohort="2023" open>')


class FamilyProjectGalleryEmptyCaseTests(TestCase):
    def test_a_family_with_no_projects_anywhere_shows_the_empty_state(self):
        family = Course.objects.create(slug="empty-zoomcamp", title="Empty Zoomcamp")
        Cohort.objects.create(
            course=family,
            slug="empty-zoomcamp-2025",
            identifier="2025",
            year=2025,
            title="Empty Zoomcamp 2025",
            description="",
        )

        response = self.client.get(
            reverse("family_projects", kwargs={"course_slug": family.slug})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["cohort_groups"], [])
        self.assertContains(response, "No project submissions yet")

    def test_an_unknown_family_404s(self):
        response = self.client.get(
            reverse("family_projects", kwargs={"course_slug": "does-not-exist"})
        )

        self.assertEqual(response.status_code, 404)

    def test_a_non_visible_family_404s(self):
        Course.objects.create(
            slug="secret-zoomcamp", title="Secret Zoomcamp", visible=False
        )

        response = self.client.get(
            reverse("family_projects", kwargs={"course_slug": "secret-zoomcamp"})
        )

        self.assertEqual(response.status_code, 404)
