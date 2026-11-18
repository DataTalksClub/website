"""Integration tests for community-base v0.5.38 peer-review, criteria sets & submission wiring."""

import subprocess
import tempfile
import uuid
from datetime import timedelta
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

from community_base.content_sync.models import ContentSource, SyncStatus
from community_base.content_sync.orchestration import sync_content_source
from community_base.coursework.models import ReviewCriteriaSet
from community_base.coursework.project_criteria import parse_criteria
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models.cohort import Cohort, Course, Enrollment, User
from courses.models.project import (
    PeerReview,
    PeerReviewState,
    Project,
    ProjectState,
    ProjectSubmission,
    project_yaml_criteria,
)
from courses.project_assignment import ProjectActionStatus
from courses.project_scoring import score_project
from courses.services.project_form_adapter import build_project_submission_form
from management_auth.constants import DIGEST_ALGORITHM, DIGEST_VERSION
from management_auth.models import APICredential, APIPrincipal
from management_auth.services import create_principal
from management_auth.tokens import encode_secret, generate_token

SAMPLE_YAML_CRITERIA = parse_criteria(
    [
        {
            "id": "problem-description",
            "title": "Problem description",
            "description": "Is the problem clearly described?",
            "type": "scale",
            "options": [
                {"points": 0, "text": "Missing"},
                {"points": 2, "text": "Clear"},
            ],
        },
        {
            "id": "bonus-tests",
            "title": "Unit tests",
            "type": "checklist",
            "options": [
                {"points": 1, "text": "Has unit tests"},
            ],
        },
    ],
    where="test",
)


class CommunityBasePeerReviewIntegrationTests(TestCase):
    course: Course
    cohort: Cohort
    student1: Any
    student2: Any
    student3: Any
    enrollment1: Enrollment
    enrollment2: Enrollment
    enrollment3: Enrollment

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            slug="ml-zoomcamp",
            title="ML Zoomcamp",
        )
        cls.cohort = Cohort.objects.create(
            course=cls.course,
            identifier="2026",
            slug="ml-zoomcamp-2026",
            title="ML Zoomcamp 2026",
            project_passing_score=2,
        )
        cls.student1 = User.objects.create_user(
            username="student1",
            email="student1@example.com",
            password="pw",
        )
        cls.student2 = User.objects.create_user(
            username="student2",
            email="student2@example.com",
            password="pw",
        )
        cls.student3 = User.objects.create_user(
            username="student3",
            email="student3@example.com",
            password="pw",
        )
        cls.enrollment1 = Enrollment.objects.create(
            student=cls.student1,
            course=cls.cohort,
            display_name="Eager Hopper",
        )
        cls.enrollment2 = Enrollment.objects.create(
            student=cls.student2,
            course=cls.cohort,
            display_name="Calm Lovelace",
        )
        cls.enrollment3 = Enrollment.objects.create(
            student=cls.student3,
            course=cls.cohort,
            display_name="Bright Turing",
        )

    def _create_project(self, *, state=ProjectState.COLLECTING_SUBMISSIONS.value, criteria=None):
        now = timezone.now()
        return Project.objects.create(
            course=self.cohort,
            slug="capstone",
            title="Capstone Project",
            submission_due_date=now + timedelta(days=2),
            peer_review_due_date=now + timedelta(days=9),
            number_of_peers_to_evaluate=1,
            state=state,
            evaluation_criteria=criteria if criteria is not None else SAMPLE_YAML_CRITERIA,
        )

    def test_project_submission_resolve_commit_and_update_commit_action(self):
        project = self._create_project()
        with patch(
            "community_base.coursework.project_forms.resolve_github_commit",
            return_value="a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
        ):
            form = build_project_submission_form(
                project,
                user=self.student1,
                enrollment=self.enrollment1,
                resolve_commit=True,
                data={
                    "github_link": "https://github.com/student1/capstone",
                    "commit_id": "1111111",
                },
            )
            self.assertTrue(form.is_valid(), form.errors)
            submission, created = form.save()
        self.assertTrue(created)
        self.assertEqual(submission.commit_id, "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2")

        self.client.force_login(self.student1)
        url = reverse(
            "cohort_project",
            kwargs={
                "course_slug": self.course.slug,
                "cohort_identifier": self.cohort.identifier,
                "project_slug": project.slug,
            },
        )
        with patch(
            "community_base.coursework.project_forms.resolve_github_commit",
            return_value="f9e8d7c6b5a4f9e8d7c6b5a4f9e8d7c6b5a4f9e8",
        ):
            response = self.client.post(
                url,
                {
                    "action": "update_commit",
                    "github_link": "https://github.com/student1/capstone",
                },
            )
        self.assertEqual(response.status_code, 302)
        submission.refresh_from_db()
        self.assertEqual(submission.commit_id, "f9e8d7c6b5a4f9e8d7c6b5a4f9e8d7c6b5a4f9e8")

    def test_yaml_criteria_eval_draft_submit_score_and_results(self):
        project = self._create_project(state=ProjectState.PEER_REVIEWING.value)
        project.peer_review_due_date = timezone.now() - timedelta(hours=1)
        project.save(update_fields=["peer_review_due_date"])

        sub1 = ProjectSubmission.objects.create(
            project=project,
            student=self.student1,
            enrollment=self.enrollment1,
            github_link="https://github.com/student1/capstone",
            commit_id="1234567",
        )
        sub2 = ProjectSubmission.objects.create(
            project=project,
            student=self.student2,
            enrollment=self.enrollment2,
            github_link="https://github.com/student2/capstone",
            commit_id="abcdef1",
        )
        review = PeerReview.objects.create(
            submission_under_evaluation=sub1,
            reviewer=sub2,
            note_to_peer="",
        )
        PeerReview.objects.create(
            submission_under_evaluation=sub2,
            reviewer=sub1,
            note_to_peer="Looks good",
            state=PeerReviewState.SUBMITTED.value,
            criteria_scores={"problem-description": 1, "bonus-tests": []},
        )

        self.client.force_login(self.student2)
        eval_url = reverse(
            "cohort_projects_eval_submit",
            kwargs={
                "course_slug": self.course.slug,
                "cohort_identifier": self.cohort.identifier,
                "project_slug": project.slug,
                "review_id": review.id,
            },
        )
        get_resp = self.client.get(eval_url)
        self.assertEqual(get_resp.status_code, 200)
        self.assertContains(get_resp, "Problem description")
        self.assertContains(get_resp, "https://github.com/student1/capstone/commit/1234567")

        # Draft save keeps state TR
        draft_resp = self.client.post(
            eval_url,
            {
                "action": "draft",
                "criterion-problem-description": "1",
                "note_to_peer": "Draft note",
            },
        )
        self.assertEqual(draft_resp.status_code, 302)
        review.refresh_from_db()
        self.assertEqual(review.state, PeerReviewState.TO_REVIEW.value)
        self.assertEqual(review.criteria_scores.get("problem-description"), 1)

        # Full submit saves criteria_scores and marks SU
        submit_resp = self.client.post(
            eval_url,
            {
                "criterion-problem-description": "1",
                "criterion-bonus-tests": ["0"],
                "note_to_peer": "Great work!",
            },
        )
        self.assertEqual(submit_resp.status_code, 302)
        review.refresh_from_db()
        self.assertEqual(review.state, PeerReviewState.SUBMITTED.value)
        self.assertEqual(review.criteria_scores, {"problem-description": 1, "bonus-tests": [0]})

        # Score project via YAML criteria
        status, _msg = score_project(project)
        self.assertEqual(status, ProjectActionStatus.OK)
        sub1.refresh_from_db()
        self.assertEqual(sub1.project_score, 3)
        self.assertTrue(sub1.passed)

        # Results page renders YAML breakdown and peer pseudonym
        self.client.force_login(self.student1)
        results_url = reverse(
            "cohort_project_results",
            kwargs={
                "course_slug": self.course.slug,
                "cohort_identifier": self.cohort.identifier,
                "project_slug": project.slug,
            },
        )
        res_resp = self.client.get(results_url)
        self.assertEqual(res_resp.status_code, 200)
        self.assertContains(res_resp, "Problem description")
        self.assertContains(res_resp, "Great work!")

    def test_set_project_state_and_close_peer_review_commands(self):
        project = self._create_project(state=ProjectState.COLLECTING_SUBMISSIONS.value)
        out = StringIO()
        call_command(
            "set_project_state",
            self.course.slug,
            project.slug,
            "peer_reviewing",
            cohort=self.cohort.identifier,
            stdout=out,
        )
        project.refresh_from_db()
        self.assertEqual(project.state, ProjectState.PEER_REVIEWING.value)

        call_command(
            "close_peer_review",
            self.course.slug,
            project.slug,
            cohort=self.cohort.identifier,
            stdout=out,
        )
        project.refresh_from_db()
        self.assertEqual(project.state, ProjectState.COMPLETED.value)

    def test_admin_peer_review_api_read_and_assign(self):
        project = self._create_project(state=ProjectState.COLLECTING_SUBMISSIONS.value)
        project.submission_due_date = timezone.now() - timedelta(hours=1)
        project.save(update_fields=["submission_due_date"])

        for student, enrollment in (
            (self.student1, self.enrollment1),
            (self.student2, self.enrollment2),
            (self.student3, self.enrollment3),
        ):
            ProjectSubmission.objects.create(
                project=project,
                student=student,
                enrollment=enrollment,
                github_link=f"https://github.com/{student.username}/capstone",
                commit_id="1234567",
            )

        staff_user = User.objects.create_user(
            username="staff-cli",
            email="staff-cli@example.com",
            password="pw",
            is_staff=True,
        )
        perms = Permission.objects.filter(codename__in=["view_project", "change_project"])
        staff_user.user_permissions.set(perms)
        principal = create_principal(
            kind=APIPrincipal.Kind.HUMAN,
            name="peer-review-cli",
            identity_snapshot="peer-review-cli",
            user=staff_user,
            created_by=staff_user,
        )
        principal.permissions.set(perms)
        generated = generate_token()
        APICredential.objects.create(
            principal=principal,
            name="test-token",
            prefix=generated.prefix,
            digest_algorithm=DIGEST_ALGORITHM,
            digest_version=DIGEST_VERSION,
            secret_digest=encode_secret(generated.secret),
            scopes=["courses.peer_reviews.read", "courses.peer_reviews.assign"],
            expires_at=timezone.now() + timedelta(days=1),
            created_by=staff_user,
        )
        headers = {"Authorization": f"Bearer {generated.raw}"}

        read_resp = self.client.get(
            f"/api/v1/admin/courses/{self.course.slug}/peer-reviews",
            headers=headers,
        )
        self.assertEqual(read_resp.status_code, 200)
        self.assertEqual(read_resp.json()["projects"][0]["submissions_count"], 3)

        with patch(
            "courses.services.peer_review_admin.send_peer_review_assignment_notification",
            return_value=3,
        ) as mock_notify:
            assign_resp = self.client.post(
                f"/api/v1/admin/courses/{self.course.slug}/peer-reviews/assignments",
                data="{}",
                content_type="application/json",
                headers=headers,
            )
        self.assertEqual(assign_resp.status_code, 200)
        body = assign_resp.json()
        self.assertEqual(body["batched"], 3)
        self.assertEqual(body["reviews_assigned"], 3)
        mock_notify.assert_called_once_with(project)

    def test_git_yaml_synced_review_criteria_sets_shared_and_distinct_per_project(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = Path(tmpdir)
            (repo_dir / "review_criteria").mkdir(parents=True)
            (repo_dir / "content.yaml").write_text(
                "schema_version: 1\n"
                "collections:\n"
                "  - path: review_criteria\n"
                "    kind: review_criteria\n",
                encoding="utf-8",
            )
            (repo_dir / "review_criteria" / "capstone-rubric.yaml").write_text(
                f"content_id: {uuid.uuid4()}\n"
                "title: Capstone Rubric\n"
                "description: Standard capstone rubric\n"
                "criteria:\n"
                "  - id: problem-description\n"
                "    title: Problem description\n"
                "    description: Is the problem clearly described?\n"
                "    type: scale\n"
                "    options:\n"
                "      - points: 0\n"
                "        text: Missing\n"
                "      - points: 2\n"
                "        text: Clear\n"
                "  - id: bonus-tests\n"
                "    title: Unit tests\n"
                "    type: checklist\n"
                "    options:\n"
                "      - points: 1\n"
                "        text: Has unit tests\n",
                encoding="utf-8",
            )
            (repo_dir / "review_criteria" / "midterm-rubric.yaml").write_text(
                f"content_id: {uuid.uuid4()}\n"
                "title: Midterm Rubric\n"
                "criteria:\n"
                "  - id: reproducibility\n"
                "    title: Reproducibility\n"
                "    type: scale\n"
                "    options:\n"
                "      - points: 0\n"
                "        text: Not reproducible\n"
                "      - points: 3\n"
                "        text: Fully reproducible\n",
                encoding="utf-8",
            )

            subprocess.run(("git", "init", "-q", str(repo_dir)), check=True)
            subprocess.run(("git", "-C", str(repo_dir), "add", "-A"), check=True)
            subprocess.run(
                (
                    "git",
                    "-C",
                    str(repo_dir),
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "commit",
                    "-q",
                    "-m",
                    "Add review criteria sets",
                ),
                check=True,
            )

            source, _ = ContentSource.objects.get_or_create(
                slug="dtc-rubrics",
                defaults={
                    "repo_name": "DataTalksClub/test-rubrics",
                    "webhook_secret": "test-secret",
                    "max_files": 100,
                },
            )
            log = sync_content_source(source, repo_dir=repo_dir, force=True)
            self.assertEqual(log.status, SyncStatus.SUCCESS, log.errors)

        capstone_set = ReviewCriteriaSet.objects.get(slug="capstone-rubric")
        midterm_set = ReviewCriteriaSet.objects.get(slug="midterm-rubric")

        now = timezone.now()
        p1 = Project.objects.create(
            course=self.cohort,
            slug="project-1",
            title="Project 1",
            submission_due_date=now + timedelta(days=2),
            peer_review_due_date=now + timedelta(days=9),
            criteria_set=capstone_set,
        )
        p2 = Project.objects.create(
            course=self.cohort,
            slug="project-2",
            title="Project 2",
            submission_due_date=now + timedelta(days=3),
            peer_review_due_date=now + timedelta(days=10),
            criteria_set=capstone_set,
        )
        p3 = Project.objects.create(
            course=self.cohort,
            slug="project-3",
            title="Project 3",
            submission_due_date=now + timedelta(days=4),
            peer_review_due_date=now + timedelta(days=11),
            criteria_set=midterm_set,
        )
        self.assertEqual(project_yaml_criteria(p1), project_yaml_criteria(p2))
        self.assertEqual(len(project_yaml_criteria(p1)), 2)
        self.assertEqual(project_yaml_criteria(p3)[0]["id"], "reproducibility")

        # Every project must have criteria.
        p_missing = Project(
            course=self.cohort,
            slug="project-no-criteria",
            title="Missing Criteria",
            submission_due_date=now + timedelta(days=2),
            peer_review_due_date=now + timedelta(days=9),
        )
        with self.assertRaises(ValidationError):
            p_missing.full_clean()
        p_missing.save()
        with self.assertRaises(CommandError):
            call_command(
                "set_project_state",
                self.course.slug,
                p_missing.slug,
                "peer_reviewing",
                cohort=self.cohort.identifier,
            )

        # End-to-end eval, save, results, and scoring via project.criteria_set.
        p1.state = ProjectState.PEER_REVIEWING.value
        p1.number_of_peers_to_evaluate = 1
        p1.save(update_fields=["state", "number_of_peers_to_evaluate"])

        sub1 = ProjectSubmission.objects.create(
            project=p1,
            student=self.student1,
            enrollment=self.enrollment1,
            github_link="https://github.com/student1/p1",
            commit_id="1234567",
        )
        sub2 = ProjectSubmission.objects.create(
            project=p1,
            student=self.student2,
            enrollment=self.enrollment2,
            github_link="https://github.com/student2/p1",
            commit_id="7654321",
        )
        review = PeerReview.objects.create(
            reviewer=sub2,
            submission_under_evaluation=sub1,
            state=PeerReviewState.TO_REVIEW.value,
        )
        PeerReview.objects.create(
            reviewer=sub1,
            submission_under_evaluation=sub2,
            state=PeerReviewState.SUBMITTED.value,
            criteria_scores={"problem-description": 1, "bonus-tests": [0]},
        )

        self.client.force_login(self.student2)
        eval_url = reverse(
            "cohort_projects_eval_submit",
            kwargs={
                "course_slug": self.course.slug,
                "cohort_identifier": self.cohort.identifier,
                "project_slug": p1.slug,
                "review_id": review.id,
            },
        )
        get_resp = self.client.get(eval_url)
        self.assertEqual(get_resp.status_code, 200)
        self.assertContains(get_resp, "Problem description")

        post_resp = self.client.post(
            eval_url,
            {
                "criterion-problem-description": "1",
                "criterion-bonus-tests": ["0"],
                "note_to_peer": "Nice work!",
            },
        )
        self.assertEqual(post_resp.status_code, 302)
        review.refresh_from_db()
        self.assertEqual(review.state, PeerReviewState.SUBMITTED.value)
        self.assertEqual(
            review.criteria_scores,
            {"problem-description": 1, "bonus-tests": [0]},
        )

        p1.peer_review_due_date = timezone.now() - timedelta(hours=1)
        p1.save(update_fields=["peer_review_due_date"])
        status, _msg = score_project(p1)
        self.assertEqual(status, ProjectActionStatus.OK)
        sub1.refresh_from_db()
        self.assertEqual(sub1.project_score, 3)
        self.assertTrue(sub1.passed)

        self.client.force_login(self.student1)
        results_url = reverse(
            "cohort_project_results",
            kwargs={
                "course_slug": self.course.slug,
                "cohort_identifier": self.cohort.identifier,
                "project_slug": p1.slug,
            },
        )
        res_resp = self.client.get(results_url)
        self.assertEqual(res_resp.status_code, 200)
        self.assertContains(res_resp, "Problem description")
        self.assertContains(res_resp, "Nice work!")
