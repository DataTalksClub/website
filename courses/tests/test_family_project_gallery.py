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
        # cohort_2022 stays projectless: it must not add an empty entry.

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


class FamilyProjectGalleryFlatListTests(FamilyProjectGalleryTestBase):
    def test_route_renders_the_gallery_template(self):
        response = self.client.get(self.gallery_url())

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/family_gallery.html")

    def test_lists_every_project_flat_newest_cohort_first(self):
        response = self.client.get(self.gallery_url())

        slugs = [project.slug for project in response.context["projects"]]
        # 2025 holds two projects, in their existing (creation) order.
        self.assertEqual(
            slugs,
            ["midterm-2025", "capstone-2025", "midterm-2024", "midterm-2023"],
        )

    def test_each_project_is_tagged_with_its_own_cohort(self):
        response = self.client.get(self.gallery_url())

        cohorts_by_slug = {
            project.slug: project.cohort.identifier
            for project in response.context["projects"]
        }

        self.assertEqual(cohorts_by_slug["midterm-2025"], "2025")
        self.assertEqual(cohorts_by_slug["capstone-2025"], "2025")
        self.assertEqual(cohorts_by_slug["midterm-2024"], "2024")
        self.assertEqual(cohorts_by_slug["midterm-2023"], "2023")
        self.assertContains(response, '<span class="gallery-project-row-meta mono-note">2025</span>')

    def test_excludes_the_hidden_cohorts_project(self):
        response = self.client.get(self.gallery_url())

        slugs = [project.slug for project in response.context["projects"]]
        self.assertNotIn("hidden-project", slugs)
        self.assertNotContains(response, "hidden-project")

    def test_a_cohort_with_no_projects_adds_nothing(self):
        response = self.client.get(self.gallery_url())

        cohorts = {
            project.cohort.identifier for project in response.context["projects"]
        }
        self.assertNotIn("2022", cohorts)

    def test_submission_counts_exclude_volunteer_review_only(self):
        response = self.client.get(self.gallery_url())
        projects_by_slug = {
            project.slug: project for project in response.context["projects"]
        }

        self.assertEqual(projects_by_slug["midterm-2025"].submissions_count, 1)
        self.assertEqual(projects_by_slug["capstone-2025"].submissions_count, 0)

    def test_the_page_shows_no_per_cohort_fold(self):
        response = self.client.get(self.gallery_url())

        self.assertNotContains(response, "data-gallery-cohort")


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

    def test_links_to_the_site_wide_gallery(self):
        response = self.client.get(self.gallery_url())

        self.assertContains(response, reverse("all_projects"))

    def test_family_page_links_to_this_gallery(self):
        response = self.client.get(
            reverse("course_family", kwargs={"course_slug": self.family.slug})
        )

        self.assertContains(response, self.gallery_url())


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
        self.assertEqual(response.context["projects"], [])
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
