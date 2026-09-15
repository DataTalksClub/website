from datetime import datetime, timedelta

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


class SiteProjectGalleryTestBase(TestCase):
    due: datetime
    de_family: Course
    de_2023: Cohort
    de_2024: Cohort
    de_project_2023: Project
    de_project_2024: Project
    ml_family: Course
    ml_2025: Cohort
    ml_project_2025: Project
    empty_family: Course
    hidden_family: Course
    hidden_family_cohort: Cohort
    hidden_family_project: Project
    hidden_cohort: Cohort
    hidden_cohort_project: Project
    submission_de_2023: ProjectSubmission
    submission_de_2024: ProjectSubmission
    submission_ml_2025: ProjectSubmission
    volunteer_only_submission: ProjectSubmission
    hidden_family_submission: ProjectSubmission
    hidden_cohort_submission: ProjectSubmission

    @classmethod
    def setUpTestData(cls):
        cls.due = timezone.now() + timedelta(days=7)

        cls.de_family = Course.objects.create(slug="de-zoomcamp", title="Data Engineering Zoomcamp")
        cls.de_2023 = cls._cohort(cls.de_family, 2023)
        cls.de_2024 = cls._cohort(cls.de_family, 2024)
        cls.de_project_2023 = cls._project(cls.de_2023, "pipeline-2023")
        cls.de_project_2024 = cls._project(cls.de_2024, "pipeline-2024")

        cls.ml_family = Course.objects.create(slug="ml-zoomcamp", title="ML Zoomcamp")
        cls.ml_2025 = cls._cohort(cls.ml_family, 2025)
        cls.ml_project_2025 = cls._project(cls.ml_2025, "capstone-2025")

        cls.empty_family = Course.objects.create(slug="empty-zoomcamp", title="Empty Zoomcamp")
        cls._cohort(cls.empty_family, 2025)

        cls.hidden_family = Course.objects.create(
            slug="secret-zoomcamp", title="Secret Zoomcamp", visible=False
        )
        cls.hidden_family_cohort = cls._cohort(cls.hidden_family, 2025)
        cls.hidden_family_project = cls._project(cls.hidden_family_cohort, "hidden-family-capstone")

        cls.hidden_cohort = cls._cohort(cls.ml_family, 2026, visible=False)
        cls.hidden_cohort_project = cls._project(cls.hidden_cohort, "hidden-capstone")

        cls.submission_de_2024 = cls._submission(
            cls.de_project_2024, cls.de_2024, "https://github.com/example/pipeline-2024"
        )
        cls.submission_de_2023 = cls._submission(
            cls.de_project_2023, cls.de_2023, "https://github.com/example/pipeline-2023"
        )
        cls.submission_ml_2025 = cls._submission(
            cls.ml_project_2025, cls.ml_2025, "https://github.com/example/capstone-2025"
        )
        cls.volunteer_only_submission = cls._submission(
            cls.ml_project_2025,
            cls.ml_2025,
            "https://github.com/example/volunteer",
            volunteer_review_only=True,
        )
        cls.hidden_family_submission = cls._submission(
            cls.hidden_family_project,
            cls.hidden_family_cohort,
            "https://github.com/example/hidden-family",
        )
        cls.hidden_cohort_submission = cls._submission(
            cls.hidden_cohort_project,
            cls.hidden_cohort,
            "https://github.com/example/hidden-cohort",
        )

    @classmethod
    def _cohort(cls, family, year, visible=True):
        return Cohort.objects.create(
            course=family,
            slug=f"{family.slug}-{year}",
            identifier=str(year),
            year=year,
            title=f"{family.title} {year}",
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

    @classmethod
    def _submission(cls, project, cohort, github_link, **kwargs):
        user = User.objects.create_user(
            username=f"learner-{ProjectSubmission.objects.count()}-{project.slug}",
            email=f"learner-{ProjectSubmission.objects.count()}-{project.slug}@example.com",
            password="x",
        )
        enrollment = Enrollment.objects.create(student=user, course=cohort)
        return ProjectSubmission.objects.create(
            project=project,
            student=user,
            enrollment=enrollment,
            github_link=github_link,
            **kwargs,
        )

    def gallery_url(self):
        return reverse("all_projects")


class SiteProjectGallerySubmissionListTests(SiteProjectGalleryTestBase):
    def test_route_renders_the_gallery_template(self):
        response = self.client.get(self.gallery_url())

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/site_gallery.html")

    def test_lists_individual_submissions_newest_cohort_first(self):
        response = self.client.get(self.gallery_url())

        submission_ids = [submission.id for submission in response.context["submissions"]]
        self.assertEqual(
            submission_ids,
            [
                self.submission_ml_2025.id,
                self.submission_de_2024.id,
                self.submission_de_2023.id,
            ],
        )

    def test_each_submission_is_tagged_with_its_own_family_and_cohort(self):
        response = self.client.get(self.gallery_url())

        tags_by_id = {
            submission.id: (submission.family.slug, submission.cohort.identifier)
            for submission in response.context["submissions"]
        }

        self.assertEqual(tags_by_id[self.submission_ml_2025.id], ("ml-zoomcamp", "2025"))
        self.assertEqual(tags_by_id[self.submission_de_2024.id], ("de-zoomcamp", "2024"))
        self.assertContains(response, "ML Zoomcamp")
        self.assertContains(response, "Data Engineering Zoomcamp")

    def test_excludes_a_family_with_no_submissions_anywhere(self):
        response = self.client.get(self.gallery_url())
        families = {submission.family.slug for submission in response.context["submissions"]}

        self.assertNotIn("empty-zoomcamp", families)

    def test_excludes_submissions_from_a_hidden_family(self):
        response = self.client.get(self.gallery_url())

        submission_ids = [submission.id for submission in response.context["submissions"]]
        self.assertNotIn(self.hidden_family_submission.id, submission_ids)
        self.assertNotContains(response, "hidden-family")

    def test_excludes_submissions_from_a_hidden_cohort(self):
        response = self.client.get(self.gallery_url())

        submission_ids = [submission.id for submission in response.context["submissions"]]
        self.assertNotIn(self.hidden_cohort_submission.id, submission_ids)
        self.assertNotContains(response, "hidden-cohort")

    def test_excludes_volunteer_review_only_submissions(self):
        response = self.client.get(self.gallery_url())

        submission_ids = [submission.id for submission in response.context["submissions"]]
        self.assertNotIn(self.volunteer_only_submission.id, submission_ids)
        self.assertNotContains(response, "volunteer")


class SiteProjectGalleryLinkTests(SiteProjectGalleryTestBase):
    def test_links_to_the_submitters_leaderboard_breakdown(self):
        response = self.client.get(self.gallery_url())
        breakdown_url = reverse(
            "cohort_leaderboard_score_breakdown",
            kwargs={
                "course_slug": "ml-zoomcamp",
                "cohort_identifier": "2025",
                "enrollment_id": self.submission_ml_2025.enrollment_id,
            },
        )

        self.assertContains(response, breakdown_url)

    def test_courses_list_page_links_here(self):
        response = self.client.get(reverse("course_list"))

        self.assertContains(response, self.gallery_url())


class SiteProjectGalleryPaginationTests(SiteProjectGalleryTestBase):
    def test_paginates_submissions_like_the_family_gallery(self):
        bulk_family = Course.objects.create(slug="bulk-zoomcamp", title="Bulk Zoomcamp")
        bulk_cohort = self._cohort(bulk_family, 2030)
        bulk_project = self._project(bulk_cohort, "capstone-2030")
        for index in range(30):
            self._submission(
                bulk_project,
                bulk_cohort,
                f"https://github.com/example/bulk-{index}",
            )

        response = self.client.get(self.gallery_url())

        self.assertEqual(len(response.context["submissions"]), 25)
        self.assertGreaterEqual(response.context["submissions_page"].paginator.count, 33)
        self.assertContains(response, "Project submission pages")


class SiteProjectGalleryEmptyCaseTests(TestCase):
    def test_no_submissions_anywhere_shows_the_empty_state(self):
        Course.objects.create(slug="quiet-zoomcamp", title="Quiet Zoomcamp")

        response = self.client.get(reverse("all_projects"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["submissions"]), [])
        self.assertContains(response, "No project submissions yet")
