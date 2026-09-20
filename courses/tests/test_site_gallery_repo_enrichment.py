"""Gallery rendering of ProjectRepoEnrichment (issue #416 experiment).

The enrichment table is repository-side data ingested out-of-band; the
gallery must annotate submission rows by repo slug (case-insensitively),
fall back to plain rendering when a repository has no row, route moved
repositories to their effective URL, keep gone repositories unlinked but
visible, and exclude them only when the visitor asks for it.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import (
    Cohort,
    Course,
    Enrollment,
    Project,
    ProjectRepoEnrichment,
    ProjectSubmission,
    User,
)

DUE = timezone.now() + timedelta(days=7)


class RepoEnrichmentGalleryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.family = Course.objects.create(slug="de-zoomcamp", title="Data Engineering Zoomcamp")
        cls.cohort = Cohort.objects.create(
            course=cls.family,
            slug="de-zoomcamp-2026",
            identifier="2026",
            year=2026,
            title="Data Engineering Zoomcamp 2026",
            description="",
            visible=True,
        )
        cls.project = Project.objects.create(
            course=cls.cohort,
            slug="capstone-2026",
            title="Capstone 2026",
            submission_due_date=DUE,
            peer_review_due_date=DUE,
        )
        cls.rich = cls._enrichment(
            repo="Learner/rich-project",
            card_summary="Streaming pipeline for city bike trips with dbt models.",
            topics="dbt,kafka,streaming",
            confidence="high",
            effective_url="https://github.com/learner/renamed-project",
            improvements="Documents throughput but never states the cluster size.",
        )
        cls._enrichment(
            repo="learner/low-project",
            card_summary="A write-up the analysts could not ground.",
            confidence="low",
        )
        cls._enrichment(
            repo="learner/gone-project",
            is_unavailable=True,
            availability="repo no longer exists (404)",
        )
        cls._enrichment(
            repo="learner/homework-dumps",
            card_summary="Collection of homework notebooks.",
            confidence="medium",
            is_coursework=True,
        )
        cls.rich_submission = cls._submission(
            "https://github.com/LEARNER/RICH-PROJECT/tree/main"
        )
        cls.low_submission = cls._submission("https://github.com/learner/low-project")
        cls.gone_submission = cls._submission("https://github.com/learner/gone-project")
        cls.coursework_submission = cls._submission(
            "https://github.com/learner/homework-dumps"
        )
        cls.unknown_submission = cls._submission(
            "https://github.com/learner/never-analyzed"
        )

    @classmethod
    def _enrichment(cls, repo, **fields):
        fields.setdefault("confidence", "")
        return ProjectRepoEnrichment.objects.create(
            repo=repo, repo_lower=repo.lower(), **fields
        )

    @classmethod
    def _submission(cls, github_link):
        count = ProjectSubmission.objects.count()
        user = User.objects.create_user(
            username=f"learner-{count}",
            email=f"learner-{count}@example.com",
            password="x",
        )
        enrollment = Enrollment.objects.create(student=user, course=cls.cohort)
        return ProjectSubmission.objects.create(
            project=cls.project,
            student=user,
            enrollment=enrollment,
            github_link=github_link,
            passed=True,
        )

    def gallery(self, query=""):
        return self.client.get(f"{reverse('all_projects')}{query}")

    def submission_by_repo(self, response, submission):
        for row in response.context["submissions"]:
            if row.id == submission.id:
                return row
        self.fail("submission missing from the gallery page")

    def test_high_confidence_row_renders_summary_topics_and_rename(self):
        response = self.gallery()
        row = self.submission_by_repo(response, self.rich_submission)

        self.assertEqual(row.repo_enrichment, self.rich)
        self.assertContains(response, "Streaming pipeline for city bike trips")
        self.assertContains(response, "dbt")
        # The repository moved after submission; the link follows the rename.
        self.assertContains(response, "github.com/learner/renamed-project")

    def test_low_confidence_row_stays_silent(self):
        response = self.gallery()

        self.assertNotContains(response, "A write-up the analysts could not ground")

    def test_gone_repo_is_unlinked_but_kept_visible(self):
        response = self.gallery()
        row = self.submission_by_repo(response, self.gone_submission)

        self.assertTrue(row.repo_enrichment.is_unavailable)
        self.assertContains(response, "Repository no longer available")
        self.assertNotContains(response, "github.com/learner/gone-project")

    def test_coursework_repo_is_labelled(self):
        response = self.gallery()

        self.assertContains(response, "coursework")
        self.assertContains(response, "Collection of homework notebooks")

    def test_unmatched_repo_renders_plainly(self):
        response = self.gallery()
        row = self.submission_by_repo(response, self.unknown_submission)

        self.assertIsNone(row.repo_enrichment)

    def test_availability_filter_hides_only_gone_repos(self):
        response = self.gallery("?hide_unavailable=True")
        listed = {row.id for row in response.context["submissions"]}

        self.assertNotIn(self.gone_submission.id, listed)
        self.assertIn(self.rich_submission.id, listed)
        self.assertIn(self.low_submission.id, listed)

    def test_reviewer_note_is_staff_only(self):
        anonymous = self.gallery()
        self.assertNotContains(anonymous, "Documents throughput but never states")

        self.client.force_login(
            User.objects.create_user(
                username="reviewer",
                email="reviewer@example.com",
                password="x",
                is_staff=True,
            )
        )
        staff = self.gallery()
        self.assertContains(staff, "Documents throughput but never states")
