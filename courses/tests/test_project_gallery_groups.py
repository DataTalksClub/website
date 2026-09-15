"""The query layer behind the family-wide and site-wide project galleries.

These exercise ``courses/views/project_gallery_groups.py`` directly against
the database -- newest-cohort-first ordering, cross-family generalization,
tagging each submission with its cohort (and, site-wide, its family),
excluding hidden cohorts/families, and excluding volunteer-review-only
submissions -- independent of either view or template.
"""

from datetime import datetime, timedelta

from django.test import TestCase
from django.utils import timezone

from courses.models import Cohort, Course, Enrollment, Project, ProjectSubmission, User
from courses.views.project_gallery_groups import (
    family_project_submissions,
    site_project_submissions,
)


class ProjectGalleryGroupsTestBase(TestCase):
    due: datetime

    @classmethod
    def setUpTestData(cls):
        cls.due = timezone.now() + timedelta(days=7)

    @classmethod
    def make_family(cls, slug, title=None, visible=True):
        return Course.objects.create(slug=slug, title=title or slug, visible=visible)

    @classmethod
    def make_cohort(cls, family, year, visible=True):
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
    def make_project(cls, cohort, slug):
        return Project.objects.create(
            course=cohort,
            slug=slug,
            title=slug.replace("-", " ").title(),
            submission_due_date=cls.due,
            peer_review_due_date=cls.due,
        )

    @classmethod
    def make_submission(cls, project, cohort, github_link, **kwargs):
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


class FamilyProjectSubmissionsTests(ProjectGalleryGroupsTestBase):
    def test_flattens_every_cohorts_submissions_newest_cohort_first(self):
        family = self.make_family("ml-zoomcamp")
        cohort_2023 = self.make_cohort(family, 2023)
        cohort_2025 = self.make_cohort(family, 2025)
        midterm = self.make_project(cohort_2023, "midterm")
        capstone_a = self.make_project(cohort_2025, "capstone-a")
        capstone_b = self.make_project(cohort_2025, "capstone-b")

        submission_2023 = self.make_submission(
            midterm, cohort_2023, "https://github.com/example/midterm"
        )
        submission_a = self.make_submission(capstone_a, cohort_2025, "https://github.com/example/a")
        submission_b = self.make_submission(capstone_b, cohort_2025, "https://github.com/example/b")

        submissions = list(family_project_submissions(family))

        self.assertEqual(
            [submission.id for submission in submissions],
            [submission_a.id, submission_b.id, submission_2023.id],
        )

    def test_each_submission_is_tagged_with_its_own_cohort_via_project_course(self):
        family = self.make_family("ml-zoomcamp")
        cohort_2025 = self.make_cohort(family, 2025)
        capstone = self.make_project(cohort_2025, "capstone")
        submission = self.make_submission(capstone, cohort_2025, "https://github.com/example/repo")

        submissions = list(family_project_submissions(family))

        self.assertEqual(submissions[0].id, submission.id)
        self.assertEqual(submissions[0].project.course, cohort_2025)

    def test_excludes_the_hidden_cohorts_submissions(self):
        family = self.make_family("ml-zoomcamp")
        hidden = self.make_cohort(family, 2025, visible=False)
        project = self.make_project(hidden, "capstone")
        self.make_submission(project, hidden, "https://github.com/example/repo")

        self.assertEqual(list(family_project_submissions(family)), [])

    def test_excludes_volunteer_review_only_submissions(self):
        family = self.make_family("ml-zoomcamp")
        cohort_2025 = self.make_cohort(family, 2025)
        project = self.make_project(cohort_2025, "capstone")
        self.make_submission(
            project,
            cohort_2025,
            "https://github.com/example/repo",
            volunteer_review_only=True,
        )

        self.assertEqual(list(family_project_submissions(family)), [])

    def test_annotates_vote_count_and_display_score(self):
        family = self.make_family("ml-zoomcamp")
        cohort_2025 = self.make_cohort(family, 2025)
        project = self.make_project(cohort_2025, "capstone")
        self.make_submission(project, cohort_2025, "https://github.com/example/repo")

        submission = list(family_project_submissions(family))[0]

        self.assertEqual(submission.vote_count, 0)
        # The project is not COMPLETED, so the score is not yet public.
        self.assertEqual(submission.display_score, -1)

    def test_a_family_with_no_submissions_is_empty(self):
        family = self.make_family("ml-zoomcamp")
        self.make_cohort(family, 2025)

        self.assertEqual(list(family_project_submissions(family)), [])


class SiteProjectSubmissionsTests(ProjectGalleryGroupsTestBase):
    def test_flattens_every_familys_submissions_newest_cohort_first(self):
        de_family = self.make_family("de-zoomcamp", "Data Engineering Zoomcamp")
        ml_family = self.make_family("ml-zoomcamp", "ML Zoomcamp")
        de_cohort = self.make_cohort(de_family, 2024)
        ml_cohort = self.make_cohort(ml_family, 2025)
        de_project = self.make_project(de_cohort, "pipeline")
        ml_project = self.make_project(ml_cohort, "capstone")

        de_submission = self.make_submission(
            de_project, de_cohort, "https://github.com/example/pipeline"
        )
        ml_submission = self.make_submission(
            ml_project, ml_cohort, "https://github.com/example/capstone"
        )

        submissions = list(site_project_submissions())

        self.assertEqual(
            [submission.id for submission in submissions],
            [ml_submission.id, de_submission.id],
        )

    def test_each_submission_is_tagged_with_its_own_family_via_project_course_course(
        self,
    ):
        family = self.make_family("ml-zoomcamp", "ML Zoomcamp")
        cohort = self.make_cohort(family, 2025)
        project = self.make_project(cohort, "capstone")
        submission = self.make_submission(project, cohort, "https://github.com/example/repo")

        submissions = list(site_project_submissions())

        self.assertEqual(submissions[0].id, submission.id)
        self.assertEqual(submissions[0].project.course, cohort)
        self.assertEqual(submissions[0].project.course.course, family)

    def test_excludes_hidden_cohorts_submissions_even_in_a_visible_family(self):
        family = self.make_family("ml-zoomcamp")
        hidden = self.make_cohort(family, 2025, visible=False)
        project = self.make_project(hidden, "capstone")
        self.make_submission(project, hidden, "https://github.com/example/repo")

        self.assertEqual(list(site_project_submissions()), [])

    def test_excludes_submissions_from_a_hidden_family(self):
        family = self.make_family("secret-zoomcamp", visible=False)
        cohort = self.make_cohort(family, 2025)
        project = self.make_project(cohort, "capstone")
        self.make_submission(project, cohort, "https://github.com/example/repo")

        self.assertEqual(list(site_project_submissions()), [])

    def test_excludes_volunteer_review_only_submissions(self):
        family = self.make_family("ml-zoomcamp")
        cohort = self.make_cohort(family, 2025)
        project = self.make_project(cohort, "capstone")
        self.make_submission(
            project,
            cohort,
            "https://github.com/example/repo",
            volunteer_review_only=True,
        )

        self.assertEqual(list(site_project_submissions()), [])

    def test_annotates_vote_count_and_display_score(self):
        family = self.make_family("ml-zoomcamp")
        cohort = self.make_cohort(family, 2025)
        project = self.make_project(cohort, "capstone")
        self.make_submission(project, cohort, "https://github.com/example/repo")

        submission = list(site_project_submissions())[0]

        self.assertEqual(submission.vote_count, 0)
        # The project is not COMPLETED, so the score is not yet public.
        self.assertEqual(submission.display_score, -1)

    def test_nothing_anywhere_is_empty(self):
        self.assertEqual(list(site_project_submissions()), [])

    def test_same_year_cohorts_across_families_break_ties_by_family_title(self):
        zebra_family = self.make_family("zebra-zoomcamp", "Zebra Zoomcamp")
        alpha_family = self.make_family("alpha-zoomcamp", "Alpha Zoomcamp")
        zebra_cohort = self.make_cohort(zebra_family, 2025)
        alpha_cohort = self.make_cohort(alpha_family, 2025)
        zebra_project = self.make_project(zebra_cohort, "capstone")
        alpha_project = self.make_project(alpha_cohort, "capstone")

        zebra_submission = self.make_submission(
            zebra_project, zebra_cohort, "https://github.com/example/zebra"
        )
        alpha_submission = self.make_submission(
            alpha_project, alpha_cohort, "https://github.com/example/alpha"
        )

        submissions = list(site_project_submissions())

        self.assertEqual(
            [submission.id for submission in submissions],
            [alpha_submission.id, zebra_submission.id],
        )
