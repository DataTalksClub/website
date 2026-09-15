"""The query layer behind the family-wide and site-wide project galleries.

These exercise ``courses/views/project_gallery_groups.py`` directly against
the database -- cohort/family grouping, newest-first ordering, dropping
cohorts and families that hold no project, and excluding hidden cohorts --
independent of either view or template.
"""

from django.test import TestCase
from django.utils import timezone

from courses.models import Cohort, Course, Enrollment, Project, ProjectSubmission, User
from courses.views.project_gallery_groups import (
    cohort_project_groups,
    family_project_groups,
    family_project_submissions,
    site_project_groups,
)


class ProjectGalleryGroupsTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.due = timezone.now() + timezone.timedelta(days=7)

    @classmethod
    def make_family(cls, slug, title=None):
        return Course.objects.create(slug=slug, title=title or slug)

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


class CohortProjectGroupsTests(ProjectGalleryGroupsTestBase):
    def test_pairs_each_cohort_with_its_own_projects(self):
        family = self.make_family("de-zoomcamp")
        cohort_2024 = self.make_cohort(family, 2024)
        cohort_2025 = self.make_cohort(family, 2025)
        project_2024 = self.make_project(cohort_2024, "pipeline")
        project_2025 = self.make_project(cohort_2025, "warehouse")

        groups = cohort_project_groups([cohort_2025, cohort_2024])

        self.assertEqual([group.cohort for group in groups], [cohort_2025, cohort_2024])
        self.assertEqual([group.projects for group in groups], [[project_2025], [project_2024]])

    def test_drops_cohorts_with_no_projects(self):
        family = self.make_family("de-zoomcamp")
        empty_cohort = self.make_cohort(family, 2024)
        full_cohort = self.make_cohort(family, 2025)
        self.make_project(full_cohort, "warehouse")

        groups = cohort_project_groups([full_cohort, empty_cohort])

        self.assertEqual([group.cohort for group in groups], [full_cohort])

    def test_no_cohorts_at_all_is_no_groups(self):
        self.assertEqual(cohort_project_groups([]), [])


class FamilyProjectGroupsTests(ProjectGalleryGroupsTestBase):
    def test_orders_visible_cohorts_newest_first(self):
        family = self.make_family("ml-zoomcamp")
        cohort_2021 = self.make_cohort(family, 2021)
        cohort_2023 = self.make_cohort(family, 2023)
        cohort_2025 = self.make_cohort(family, 2025)
        self.make_project(cohort_2021, "capstone-2021")
        self.make_project(cohort_2023, "capstone-2023")
        self.make_project(cohort_2025, "capstone-2025")

        groups = family_project_groups(family)

        self.assertEqual(
            [group.cohort.identifier for group in groups], ["2025", "2023", "2021"]
        )

    def test_excludes_hidden_cohorts_even_with_projects(self):
        family = self.make_family("ml-zoomcamp")
        hidden = self.make_cohort(family, 2025, visible=False)
        self.make_project(hidden, "capstone")

        groups = family_project_groups(family)

        self.assertEqual(groups, [])

    def test_a_family_with_no_projects_anywhere_is_no_groups(self):
        family = self.make_family("ml-zoomcamp")
        self.make_cohort(family, 2025)

        self.assertEqual(family_project_groups(family), [])

    def test_only_reads_this_familys_own_cohorts(self):
        family = self.make_family("ml-zoomcamp")
        other_family = self.make_family("de-zoomcamp")
        cohort = self.make_cohort(family, 2025)
        other_cohort = self.make_cohort(other_family, 2025)
        self.make_project(cohort, "capstone")
        self.make_project(other_cohort, "pipeline")

        groups = family_project_groups(family)

        self.assertEqual([group.cohort for group in groups], [cohort])


class FamilyProjectSubmissionsTests(ProjectGalleryGroupsTestBase):
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
        submission_a = self.make_submission(
            capstone_a, cohort_2025, "https://github.com/example/a"
        )
        submission_b = self.make_submission(
            capstone_b, cohort_2025, "https://github.com/example/b"
        )

        submissions = list(family_project_submissions(family))

        self.assertEqual(
            [submission.id for submission in submissions],
            [submission_a.id, submission_b.id, submission_2023.id],
        )

    def test_each_submission_is_tagged_with_its_own_cohort_via_project_course(self):
        family = self.make_family("ml-zoomcamp")
        cohort_2025 = self.make_cohort(family, 2025)
        capstone = self.make_project(cohort_2025, "capstone")
        submission = self.make_submission(
            capstone, cohort_2025, "https://github.com/example/repo"
        )

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


class SiteProjectGroupsTests(ProjectGalleryGroupsTestBase):
    def test_groups_by_family_then_cohort(self):
        de_family = self.make_family("de-zoomcamp", "Data Engineering Zoomcamp")
        ml_family = self.make_family("ml-zoomcamp", "ML Zoomcamp")
        de_cohort = self.make_cohort(de_family, 2024)
        ml_cohort = self.make_cohort(ml_family, 2025)
        self.make_project(de_cohort, "pipeline")
        self.make_project(ml_cohort, "capstone")

        groups = {group.family.slug: group for group in site_project_groups()}

        self.assertEqual(set(groups), {"de-zoomcamp", "ml-zoomcamp"})
        self.assertEqual(
            [g.cohort.identifier for g in groups["de-zoomcamp"].cohort_groups], ["2024"]
        )
        self.assertEqual(
            [g.cohort.identifier for g in groups["ml-zoomcamp"].cohort_groups], ["2025"]
        )

    def test_orders_families_by_most_recent_project_activity(self):
        stale_family = self.make_family("stale-zoomcamp", "Stale Zoomcamp")
        fresh_family = self.make_family("fresh-zoomcamp", "Fresh Zoomcamp")
        stale_cohort = self.make_cohort(stale_family, 2021)
        fresh_cohort = self.make_cohort(fresh_family, 2026)
        self.make_project(stale_cohort, "capstone")
        self.make_project(fresh_cohort, "capstone")

        groups = site_project_groups()

        self.assertEqual([g.family.slug for g in groups], ["fresh-zoomcamp", "stale-zoomcamp"])

    def test_a_family_with_no_projects_anywhere_is_excluded_entirely(self):
        empty_family = self.make_family("empty-zoomcamp")
        self.make_cohort(empty_family, 2025)
        full_family = self.make_family("full-zoomcamp")
        full_cohort = self.make_cohort(full_family, 2025)
        self.make_project(full_cohort, "capstone")

        groups = site_project_groups()

        self.assertEqual([g.family.slug for g in groups], ["full-zoomcamp"])

    def test_counts_projects_and_submissions_across_cohorts(self):
        family = self.make_family("ml-zoomcamp")
        cohort_a = self.make_cohort(family, 2024)
        cohort_b = self.make_cohort(family, 2025)
        self.make_project(cohort_a, "midterm")
        self.make_project(cohort_b, "capstone-a")
        self.make_project(cohort_b, "capstone-b")

        group = site_project_groups()[0]

        self.assertEqual(group.project_count, 3)
        self.assertEqual(group.submission_count, 0)

    def test_nothing_anywhere_is_an_empty_list(self):
        self.assertEqual(site_project_groups(), [])
