"""Gallery rendering of the structured course-fields chips (issue #416).

ProjectRepoEnrichment.structured holds the course-structured-v1 record
written by the ingest run. The gallery shows a bounded set of evidence-backed
pipeline fields (orchestration, warehouse, ...) as chips on cards whose
write-up confidence is not low or baseline, and stays silent otherwise.
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

PIPELINE = {
    "schema": "course-structured-v1",
    "course_fields": {
        "orchestration": {
            "value": "Prefect",
            "confidence": "high",
            "evidence": "README: Prefect orchestrates the pipeline",
        },
        "warehouse": {
            "value": "BigQuery",
            "confidence": "high",
            "evidence": "README: BigQuery serves as the warehouse",
        },
    },
}


class RepoStructuredFieldsTests(TestCase):
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
        cls._enrichment(repo="learner/structured-project", structured=PIPELINE)
        cls._enrichment(repo="learner/structured-low", confidence="low", structured=PIPELINE)
        cls._enrichment(repo="learner/structured-null")
        for repo, suffix in (
            ("learner/structured-project", "one"),
            ("learner/structured-low", "two"),
            ("learner/structured-null", "three"),
        ):
            cls._submission(f"https://github.com/{repo}", suffix)

    @classmethod
    def _enrichment(cls, repo, **fields):
        fields.setdefault("confidence", "high")
        return ProjectRepoEnrichment.objects.create(
            repo=repo, repo_lower=repo.lower(), **fields
        )

    @classmethod
    def _submission(cls, github_link, suffix):
        user = User.objects.create_user(
            username=f"learner-structured-{suffix}",
            email=f"learner-structured-{suffix}@example.com",
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

    def test_pipeline_fields_render_as_chips(self):
        response = self.client.get(reverse("all_projects"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "warehouse: BigQuery")
        self.assertContains(response, "orchestration: Prefect")

    def test_low_confidence_and_null_structured_stay_silent(self):
        response = self.client.get(reverse("all_projects"))

        # The chip markup appears for the high-confidence card only: the
        # low-confidence row suppresses it and the null row has nothing.
        self.assertEqual(response.content.decode().count("warehouse: BigQuery"), 1)
