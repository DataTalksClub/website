from datetime import datetime, timedelta

from django.contrib.auth.base_user import AbstractBaseUser
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
    due: datetime
    family: Course
    cohort_2022: Cohort
    cohort_2023: Cohort
    cohort_2024: Cohort
    cohort_2025: Cohort
    cohort_hidden: Cohort
    project_2023: Project
    project_2024: Project
    project_2025_a: Project
    project_2025_b: Project
    project_hidden: Project
    user: AbstractBaseUser
    enrollment: Enrollment
    enrollment_2023: Enrollment
    enrollment_hidden: Enrollment
    submission: ProjectSubmission
    submission_2023: ProjectSubmission
    volunteer_only_submission: ProjectSubmission
    hidden_submission: ProjectSubmission

    @classmethod
    def setUpTestData(cls):
        cls.due = timezone.now() + timedelta(days=7)
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
        cls.enrollment = Enrollment.objects.create(student=cls.user, course=cls.cohort_2025)
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

        cls.enrollment_2023 = Enrollment.objects.create(student=cls.user, course=cls.cohort_2023)
        cls.submission_2023 = ProjectSubmission.objects.create(
            project=cls.project_2023,
            student=cls.user,
            enrollment=cls.enrollment_2023,
            github_link="https://github.com/example/repo-2023",
        )

        cls.enrollment_hidden = Enrollment.objects.create(
            student=cls.user, course=cls.cohort_hidden
        )
        cls.hidden_submission = ProjectSubmission.objects.create(
            project=cls.project_hidden,
            student=cls.user,
            enrollment=cls.enrollment_hidden,
            github_link="https://github.com/example/hidden-repo",
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


class FamilyProjectGallerySubmissionListTests(FamilyProjectGalleryTestBase):
    def test_route_renders_the_gallery_template(self):
        response = self.client.get(self.gallery_url())

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/family_gallery.html")

    def test_lists_individual_submissions_newest_cohort_first(self):
        response = self.client.get(self.gallery_url())

        submission_ids = [submission.id for submission in response.context["submissions"]]
        # The volunteer-only 2025 submission and the hidden cohort's
        # submission are both excluded; only two real submissions remain.
        self.assertEqual(submission_ids, [self.submission.id, self.submission_2023.id])

    def test_each_submission_is_tagged_with_its_own_cohort(self):
        response = self.client.get(self.gallery_url())

        cohorts_by_id = {
            submission.id: submission.cohort.identifier
            for submission in response.context["submissions"]
        }

        self.assertEqual(cohorts_by_id[self.submission.id], "2025")
        self.assertEqual(cohorts_by_id[self.submission_2023.id], "2023")
        self.assertContains(response, "2025")
        self.assertContains(response, "2023")

    def test_excludes_the_hidden_cohorts_submission(self):
        response = self.client.get(self.gallery_url())

        submission_ids = [submission.id for submission in response.context["submissions"]]
        self.assertNotIn(self.hidden_submission.id, submission_ids)
        self.assertNotContains(response, "hidden-repo")

    def test_excludes_volunteer_review_only_submissions(self):
        response = self.client.get(self.gallery_url())

        submission_ids = [submission.id for submission in response.context["submissions"]]
        self.assertNotIn(self.volunteer_only_submission.id, submission_ids)
        self.assertNotContains(response, "repo2")

    def test_shows_the_submitters_name_and_repository_link(self):
        response = self.client.get(self.gallery_url())

        self.assertContains(response, self.enrollment.display_name)
        self.assertContains(response, "https://github.com/example/repo")

    def test_the_page_shows_no_per_cohort_fold(self):
        response = self.client.get(self.gallery_url())

        self.assertNotContains(response, "data-gallery-cohort")


class FamilyProjectGalleryLinkTests(FamilyProjectGalleryTestBase):
    def test_links_to_the_submitters_leaderboard_breakdown(self):
        response = self.client.get(self.gallery_url())
        breakdown_url = reverse(
            "cohort_leaderboard_score_breakdown",
            kwargs={
                "course_slug": self.family.slug,
                "cohort_identifier": "2025",
                "enrollment_id": self.enrollment.id,
            },
        )

        self.assertContains(response, breakdown_url)

    def test_links_to_the_site_wide_gallery(self):
        response = self.client.get(self.gallery_url())

        self.assertContains(response, reverse("all_projects"))

    def test_family_page_links_to_this_gallery(self):
        response = self.client.get(
            reverse("course_family", kwargs={"course_slug": self.family.slug})
        )

        self.assertContains(response, self.gallery_url())


class FamilyProjectGalleryPaginationTests(FamilyProjectGalleryTestBase):
    def test_paginates_submissions_like_the_per_cohort_catalogue(self):
        cohort = self._cohort(2030)
        project = self._project(cohort, "capstone-2030")
        for index in range(30):
            user = User.objects.create_user(
                username=f"bulk-learner-{index}",
                email=f"bulk-learner-{index}@example.com",
                password="x",
            )
            enrollment = Enrollment.objects.create(student=user, course=cohort)
            ProjectSubmission.objects.create(
                project=project,
                student=user,
                enrollment=enrollment,
                github_link=f"https://github.com/example/bulk-{index}",
            )

        response = self.client.get(self.gallery_url())

        self.assertEqual(len(response.context["submissions"]), 25)
        self.assertEqual(response.context["submissions_page"].paginator.count, 32)
        self.assertContains(response, "Project submission pages")


class FamilyProjectGalleryEmptyCaseTests(TestCase):
    def test_a_family_with_no_submissions_anywhere_shows_the_empty_state(self):
        family = Course.objects.create(slug="empty-zoomcamp", title="Empty Zoomcamp")
        Cohort.objects.create(
            course=family,
            slug="empty-zoomcamp-2025",
            identifier="2025",
            year=2025,
            title="Empty Zoomcamp 2025",
            description="",
        )

        response = self.client.get(reverse("family_projects", kwargs={"course_slug": family.slug}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["submissions"]), [])
        self.assertContains(response, "No project submissions yet")

    def test_an_unknown_family_404s(self):
        response = self.client.get(
            reverse("family_projects", kwargs={"course_slug": "does-not-exist"})
        )

        self.assertEqual(response.status_code, 404)

    def test_a_non_visible_family_404s(self):
        Course.objects.create(slug="secret-zoomcamp", title="Secret Zoomcamp", visible=False)

        response = self.client.get(
            reverse("family_projects", kwargs={"course_slug": "secret-zoomcamp"})
        )

        self.assertEqual(response.status_code, 404)
