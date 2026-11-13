"""Score and peer-review notifications send through the package mail app.

Each recipient gets one durable delivery with a replay-safe key derived
from the business objects; a Studio retry of the same score run replays
instead of duplicating mail.
"""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from community_base.mail.models import EmailDelivery
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from courses.models import (
    Cohort,
    Enrollment,
    Homework,
    Project,
    ProjectState,
    ProjectSubmission,
    Submission,
)
from courses.package_notifications import send_homework_score_notification


def create_user(email):
    return User.objects.create_user(
        username=email,
        email=email,
        password="test",
    )


class PackageNotificationBase(TestCase):
    def create_course(self):
        return Cohort.objects.create(
            slug="ml-zoomcamp-2026",
            title="ML Zoomcamp 2026",
            description="Machine learning",
        )

    def create_enrollment(self, user, course):
        return Enrollment.objects.create(student=user, course=course)

    def create_review_assignment(self):
        from courses.models.project import PeerReview

        course = self.create_course()
        project = Project.objects.create(
            course=course,
            slug="capstone",
            title="Capstone",
            submission_due_date=timezone.now() + timedelta(days=3),
            peer_review_due_date=timezone.now() + timedelta(days=6),
            number_of_peers_to_evaluate=2,
            state=ProjectState.PEER_REVIEWING.value,
        )
        author = create_user("author@example.com")
        reviewer = create_user("reviewer@example.com")
        author_enrollment = self.create_enrollment(author, course)
        reviewer_enrollment = self.create_enrollment(reviewer, course)
        author_submission = ProjectSubmission.objects.create(
            project=project,
            student=author,
            enrollment=author_enrollment,
            submitted_at=timezone.now(),
        )
        reviewer_submission = ProjectSubmission.objects.create(
            project=project,
            student=reviewer,
            enrollment=reviewer_enrollment,
            submitted_at=timezone.now(),
        )
        PeerReview.objects.create(
            submission_under_evaluation=author_submission,
            reviewer=reviewer_submission,
            note_to_peer="",
        )
        return project, reviewer_submission


class HomeworkScoreNotificationTest(PackageNotificationBase):
    def create_scored_homework(self):
        course = self.create_course()
        homework = Homework.objects.create(
            course=course,
            slug="homework-1",
            title="Homework 1",
            due_date=timezone.now() + timedelta(days=3),
        )
        submissions = []
        for email, total in (
            ("scored@example.com", 9),
            ("also-scored@example.com", 7),
        ):
            user = create_user(email)
            self.create_enrollment(user, course)
            submissions.append(
                Submission.objects.create(
                    homework=homework,
                    student=user,
                    enrollment=Enrollment.objects.get(student=user),
                    submitted_at=timezone.now(),
                    questions_score=6,
                    learning_in_public_score=2,
                    faq_score=1,
                    total_score=total,
                )
            )
        # A later resubmission for the first student: only the latest scores.
        resubmission = Submission.objects.create(
            homework=homework,
            student=submissions[0].student,
            enrollment=submissions[0].enrollment,
            submitted_at=timezone.now() + timedelta(minutes=5),
            questions_score=5,
            learning_in_public_score=2,
            faq_score=1,
            total_score=8,
        )
        return homework, submissions, resubmission

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_score_run_sends_one_delivery_per_latest_submission(self):
        homework, submissions, resubmission = self.create_scored_homework()

        with patch("course_management.relay_campaigns.submit_transient_list") as submit_mock:
            sent = send_homework_score_notification(homework)

        self.assertEqual(sent, 2)
        submit_mock.assert_called_once()
        kwargs = submit_mock.call_args.kwargs
        self.assertEqual(kwargs["template_key"], "homework-score-notification")
        self.assertEqual(kwargs["category"], "submission-results")
        self.assertEqual(
            kwargs["idempotency_key"],
            "homework-score:ml-zoomcamp-2026:homework-1",
        )
        members_by_email = {m["email"]: m for m in kwargs["members"]}
        self.assertEqual(set(members_by_email), {"scored@example.com", "also-scored@example.com"})
        latest = members_by_email["scored@example.com"]
        self.assertEqual(latest["source_object_key"], f"submission-{resubmission.pk}")
        self.assertEqual(latest["metadata"]["total_score"], 8)
        self.assertEqual(
            kwargs["context"]["scores_url"],
            "https://courses.example.com/courses/ml-zoomcamp/2026/homework/homework-1",
        )

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_score_run_replays_instead_of_duplicating(self):
        homework, _submissions, _resubmission = self.create_scored_homework()

        with patch("course_management.relay_campaigns.submit_transient_list") as submit_mock:
            send_homework_score_notification(homework)
            send_homework_score_notification(homework)

        self.assertEqual(submit_mock.call_count, 2)
        first_key = submit_mock.call_args_list[0].kwargs["idempotency_key"]
        second_key = submit_mock.call_args_list[1].kwargs["idempotency_key"]
        self.assertEqual(first_key, second_key)

    def test_a_zero_score_submission_is_still_notified(self):
        course = self.create_course()
        homework = Homework.objects.create(
            course=course,
            slug="homework-2",
            title="Homework 2",
            due_date=timezone.now() + timedelta(days=3),
        )
        user = create_user("unscored@example.com")
        self.create_enrollment(user, course)
        Submission.objects.create(
            homework=homework,
            student=user,
            enrollment=Enrollment.objects.get(student=user),
            submitted_at=timezone.now(),
        )

        with patch("course_management.relay_campaigns.submit_transient_list") as submit_mock:
            sent = send_homework_score_notification(homework)

        self.assertEqual(sent, 1)
        submit_mock.assert_called_once()
        members = submit_mock.call_args.kwargs["members"]
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]["metadata"]["total_score"], 0)


class ProjectScoreNotificationTest(PackageNotificationBase):
    def create_scored_project(self):
        course = self.create_course()
        project = Project.objects.create(
            course=course,
            slug="midterm",
            title="Midterm Project",
            submission_due_date=timezone.now() + timedelta(days=3),
            peer_review_due_date=timezone.now() + timedelta(days=10),
            state=ProjectState.COLLECTING_SUBMISSIONS.value,
        )
        user = create_user("submitter@example.com")
        self.create_enrollment(user, course)
        submission = ProjectSubmission.objects.create(
            project=project,
            student=user,
            enrollment=Enrollment.objects.get(student=user),
            submitted_at=timezone.now(),
            github_link="https://github.com/example/project",
            commit_id="abc1234",
            project_score=70,
            project_learning_in_public_score=5,
            project_faq_score=1,
            peer_review_score=18,
            peer_review_learning_in_public_score=4,
            total_score=98,
        )
        return project, submission

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_project_score_notification_carries_the_breakdown(self):
        from courses.package_notifications import (
            send_project_score_notification,
        )

        project, submission = self.create_scored_project()

        with patch("course_management.relay_campaigns.submit_transient_list") as submit_mock:
            sent = send_project_score_notification(project)

        self.assertEqual(sent, 1)
        submit_mock.assert_called_once()
        kwargs = submit_mock.call_args.kwargs
        self.assertEqual(kwargs["template_key"], "project-score-notification")
        self.assertEqual(
            kwargs["idempotency_key"],
            "project-score:ml-zoomcamp-2026:midterm",
        )
        member = kwargs["members"][0]
        self.assertEqual(member["source_object_key"], f"project-submission-{submission.pk}")
        self.assertEqual(member["metadata"]["total_score"], 98)
        self.assertEqual(member["metadata"]["github_link"], "https://github.com/example/project")
        self.assertEqual(
            kwargs["context"]["scores_url"],
            "https://courses.example.com/courses/ml-zoomcamp/2026/project/midterm/results",
        )


class PeerReviewAssignmentNotificationTest(PackageNotificationBase):
    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_review_assignment_names_the_reviews_and_deadline(self):
        from courses.package_notifications import (
            send_peer_review_assignment_notification,
        )

        project, reviewer_submission = self.create_review_assignment()

        with patch("course_management.relay_campaigns.submit_transient_list") as submit_mock:
            sent = send_peer_review_assignment_notification(project)

        self.assertEqual(sent, 2)
        submit_mock.assert_called_once()
        kwargs = submit_mock.call_args.kwargs
        self.assertEqual(kwargs["template_key"], "peer-review-assignment")
        self.assertEqual(
            kwargs["idempotency_key"],
            "peer-review-assignment:ml-zoomcamp-2026:capstone",
        )
        by_key = {m["source_object_key"]: m for m in kwargs["members"]}
        reviewer_member = by_key[f"peer-review-{reviewer_submission.pk}"]
        context = reviewer_member["metadata"]
        self.assertEqual(context["number_of_peers_to_evaluate"], 2)
        self.assertEqual(len(context["assigned_reviews"]), 1)
        self.assertIn("/eval", context["assigned_reviews"][0]["eval_url"])
        self.assertIn("deadline_summary", context)
        author_member = next(
            m for k, m in by_key.items() if k != f"peer-review-{reviewer_submission.pk}"
        )
        self.assertEqual(author_member["metadata"]["assigned_reviews"], [])


class PreviewCommandTest(PackageNotificationBase):
    def test_preview_command_runs_without_retired_mail_settings(self):
        project, _submission = self.create_review_assignment()

        out = StringIO()
        call_command(
            "preview_peer_review_email",
            "ml-zoomcamp-2026",
            "capstone",
            stdout=out,
        )
        self.assertIn("recipient(s) would be emailed", out.getvalue())


class SubmissionConfirmationKeyTest(PackageNotificationBase):
    """The confirmation keys must survive the package's key validator.

    Every confirmation test in courses/tests mocks ``send_package_mail``, so a
    key the package would refuse looks fine there and fails only on a real
    send -- in an ``on_commit`` callback, after the learner's submission is
    already saved. These two send for real.
    """

    def _homework_submission(self):
        course = self.create_course()
        homework = Homework.objects.create(
            course=course,
            slug="homework-1",
            title="Homework 1",
            due_date=timezone.now() + timedelta(days=3),
        )
        student = create_user("confirm@example.com")
        enrollment = self.create_enrollment(student, course)
        submission = Submission.objects.create(
            homework=homework,
            student=student,
            enrollment=enrollment,
            submitted_at=timezone.now(),
        )
        return course, homework, submission, student

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_a_homework_confirmation_reaches_the_package_with_a_valid_key(self):
        from courses.views.homework_confirmation import (
            HomeworkConfirmationEmailData,
            send_homework_confirmation_email,
        )

        course, homework, submission, student = self._homework_submission()
        send_homework_confirmation_email(
            HomeworkConfirmationEmailData(
                user=student,
                course=course,
                homework=homework,
                submission=submission,
                update_url="https://courses.example.com/courses/ml-zoomcamp/2026/homework/homework-1",
            )
        )

        delivery = EmailDelivery.objects.get(recipient_email="confirm@example.com")
        self.assertEqual(delivery.purpose, "homework-submission-confirmation")
        self.assertEqual(delivery.category, "submission-results")
        self.assertIn(str(submission.id), delivery.idempotency_key)

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_a_project_confirmation_reaches_the_package_with_a_valid_key(self):
        from courses.views.project_confirmation import (
            ProjectConfirmationEmailData,
            send_project_confirmation_email,
        )

        course = self.create_course()
        project = Project.objects.create(
            course=course,
            slug="midterm",
            title="Midterm",
            submission_due_date=timezone.now() + timedelta(days=3),
            peer_review_due_date=timezone.now() + timedelta(days=6),
            state=ProjectState.COMPLETED.value,
        )
        student = create_user("confirm-project@example.com")
        enrollment = self.create_enrollment(student, course)
        submission = ProjectSubmission.objects.create(
            project=project,
            student=student,
            enrollment=enrollment,
            submitted_at=timezone.now(),
            github_link="https://github.com/example/project",
        )
        send_project_confirmation_email(
            ProjectConfirmationEmailData(
                user=student,
                course=course,
                project=project,
                submission=submission,
                update_url="https://courses.example.com/courses/ml-zoomcamp/2026/project/midterm",
            )
        )

        delivery = EmailDelivery.objects.get(
            recipient_email="confirm-project@example.com",
        )
        self.assertEqual(delivery.purpose, "project-submission-confirmation")
        self.assertEqual(delivery.category, "submission-results")
        self.assertIn(str(submission.id), delivery.idempotency_key)
