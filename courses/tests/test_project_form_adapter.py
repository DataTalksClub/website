"""The site adapter for the shared community-base project form (issue #456).

Unit-level coverage of the adapter contract: the project toggle mapping, the
shared validation rules, the site persistence semantics (enrollment inside the
save transaction, ``submitted_at`` refresh, learner-profile certificate name),
and the FAQ field enforcement.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from courses.models import Enrollment, ProjectState, ProjectSubmission
from courses.models.learner_profile import LearnerProfile
from courses.services.project_form_adapter import (
    ProjectSubmissionTarget,
    build_project_submission_form,
)
from courses.tests.project_view_base import ProjectViewTestBase


class ProjectSubmissionTargetTest(TestCase):
    def setUp(self):
        base = ProjectViewTestBase()
        base.setUp()
        self.project = base.project

    def test_commit_id_is_always_collected_and_pooled_review_never_applies(self):
        target = ProjectSubmissionTarget(self.project)

        self.assertTrue(target.commit_id_field)
        self.assertFalse(target.uses_pooled_review)

    def test_remaining_toggles_delegate_to_the_project(self):
        self.project.learning_in_public_cap_project = 5
        self.project.time_spent_project_field = False
        self.project.faq_contribution_field = False

        target = ProjectSubmissionTarget(self.project)

        self.assertEqual(target.learning_in_public_cap_project, 5)
        self.assertFalse(target.time_spent_project_field)
        self.assertFalse(target.faq_contribution_field)
        self.assertEqual(target.state, ProjectState.COLLECTING_SUBMISSIONS.value)

    def test_the_state_machine_owns_closure_not_the_deadline(self):
        # The dated project presents as undated to the package deadline lock;
        # save/update/remove stay state-gated (issue #456).
        self.project.submission_due_date = timezone.now() - timedelta(days=1)

        target = ProjectSubmissionTarget(self.project)

        self.assertIsNone(target.submission_due_date)


class ProjectSubmissionFormContractTest(ProjectViewTestBase):
    def form_data(self, **overrides):
        data = {
            "github_link": "https://github.com/learner/project",
            "commit_id": "abc1234",
            "certificate_name": "Learner Name",
        }
        data.update(overrides)
        return data

    def build(self, data=None, **kwargs):
        return build_project_submission_form(self.project, user=self.user, data=data, **kwargs)

    def test_render_contract_shares_the_aisl_field_set(self):
        form = self.build()

        self.assertIn("github_link", form.fields)
        self.assertIn("commit_id", form.fields)
        self.assertIn("learning_in_public_links", form.fields)
        self.assertIn("time_spent", form.fields)
        self.assertIn("certificate_name", form.fields)
        self.assertIn("faq_contribution_url", form.fields)
        self.assertTrue(form.fields["commit_id"].required)
        self.assertFalse(form.fields["faq_contribution_url"].required)

    def test_faq_field_disappears_when_the_project_toggle_is_off(self):
        self.project.faq_contribution_field = False

        form = self.build()

        self.assertNotIn("faq_contribution_url", form.fields)

    def test_github_link_must_be_a_github_repository_url(self):
        form = self.build(self.form_data(github_link="https://httpbin.org/status/200"))

        self.assertFalse(form.is_valid())
        self.assertIn("github_link", form.errors)

    def test_commit_id_must_look_like_a_commit(self):
        form = self.build(self.form_data(commit_id="not-a-commit"))

        self.assertFalse(form.is_valid())
        self.assertIn("commit_id", form.errors)

    def test_faq_url_must_be_a_datatalksclub_faq_issue_or_pull_request(self):
        form = self.build(self.form_data(faq_contribution_url="https://gist.github.com/some/one"))

        self.assertFalse(form.is_valid())
        self.assertIn("faq_contribution_url", form.errors)

    def test_certificate_name_prefers_the_profile_value(self):
        LearnerProfile.objects.update_or_create(
            user=self.user,
            defaults={"certificate_name": "Profile Name"},
        )
        self.enrollment.display_name = "Random Name"
        self.enrollment.save()

        form = self.build()

        self.assertEqual(form.initial["certificate_name"], "Profile Name")

    def test_certificate_name_falls_back_to_the_display_name(self):
        self.enrollment.display_name = "Random Name"
        self.enrollment.save()

        form = self.build()

        self.assertEqual(form.initial["certificate_name"], "Random Name")


class ProjectSubmissionFormSaveTest(ProjectViewTestBase):
    def valid_data(self, **overrides):
        data = {
            "github_link": "https://github.com/learner/project",
            "commit_id": "abc1234",
            "time_spent": "2.5",
            "learning_in_public_links": ["https://example.com/one"],
            "faq_contribution_url": "https://github.com/DataTalksClub/faq/issues/281",
            "certificate_name": "Learner Name",
        }
        data.update(overrides)
        return data

    def save_valid(self, **overrides):
        data = self.valid_data(**overrides)
        form = build_project_submission_form(self.project, user=self.user, data=data)
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def test_save_creates_the_submission_enrollment_and_certificate_name(self):
        self.enrollment.delete()

        submission, created = self.save_valid()

        self.assertTrue(created)
        self.assertEqual(submission.github_link, "https://github.com/learner/project")
        self.assertEqual(submission.commit_id, "abc1234")
        self.assertEqual(submission.time_spent, 2.5)
        self.assertEqual(
            submission.learning_in_public_links,
            ["https://example.com/one"],
        )
        self.assertEqual(
            submission.faq_contribution_url,
            "https://github.com/DataTalksClub/faq/issues/281",
        )
        self.assertIsNotNone(submission.enrollment_id)
        profile = LearnerProfile.objects.get(user=self.user)
        self.assertEqual(profile.certificate_name, "Learner Name")

    def test_update_refreshes_submitted_at_on_the_same_row(self):
        existing = self.save_valid()[0]
        before = existing.submitted_at

        form = build_project_submission_form(
            self.project,
            user=self.user,
            data=self.valid_data(commit_id="ffffff1"),
        )
        self.assertTrue(form.is_valid(), form.errors)
        submission, created = form.save()

        self.assertFalse(created)
        self.assertEqual(submission.pk, existing.pk)
        self.assertGreater(submission.submitted_at, before)
        self.assertEqual(submission.commit_id, "ffffff1")

    def test_rejected_save_writes_nothing(self):
        enrollments_before = Enrollment.objects.filter(
            course=self.project.course,
        ).count()
        profile_before = LearnerProfile.objects.filter(user=self.user).exists()

        form = build_project_submission_form(
            self.project,
            user=self.user,
            data=self.valid_data(commit_id="not-a-commit"),
        )
        self.assertFalse(form.is_valid())

        self.assertEqual(
            Enrollment.objects.filter(course=self.project.course).count(),
            enrollments_before,
        )
        self.assertEqual(
            LearnerProfile.objects.filter(user=self.user).exists(),
            profile_before,
        )
        self.assertFalse(ProjectSubmission.objects.filter(project=self.project).exists())

    def test_blank_certificate_name_keeps_the_profile_value(self):
        LearnerProfile.objects.update_or_create(
            user=self.user,
            defaults={"certificate_name": "Before"},
        )

        self.save_valid(certificate_name="")

        profile = LearnerProfile.objects.get(user=self.user)
        self.assertEqual(profile.certificate_name, "Before")

    def test_time_spent_blank_on_update_clears_the_stored_value(self):
        self.save_valid()
        existing = self.save_valid(time_spent="")[0]

        self.assertIsNone(existing.time_spent)

    def test_faq_url_error_carries_the_enforced_rule_text(self):
        form = build_project_submission_form(
            self.project,
            user=self.user,
            data=self.valid_data(faq_contribution_url="https://example.com/not-faq"),
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(
            form.errors["faq_contribution_url"],
            [
                "FAQ contribution must be a DataTalksClub/faq issue or pull "
                "request URL, for example "
                "https://github.com/DataTalksClub/faq/issues/281."
            ],
        )
