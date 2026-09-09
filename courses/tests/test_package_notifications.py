"""Score and peer-review notifications send through the package mail app.

Each recipient gets one durable delivery with a replay-safe key derived
from the business objects; a Studio retry of the same score run replays
instead of duplicating mail.
"""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import CustomUser
from accounts.models import CustomUser
from community_base.mail.models import EmailDelivery
from community_base.testing import mail_outbox
from courses.models import (
    Cohort,
    Enrollment,
    Homework,
    Project,
    ProjectState,
    ProjectSubmission,
    Submission,
)


def create_user(email):
    return CustomUser.objects.create_user(
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



from courses.package_notifications import send_homework_score_notification


from courses.package_notifications import send_homework_score_notification


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

        with mail_outbox() as outbox:
            with self.captureOnCommitCallbacks(execute=True):
                sent = send_homework_score_notification(homework)

            self.assertEqual(sent, 2)
            self.assertEqual(len(outbox), 2)
            latest = EmailDelivery.objects.get(
                idempotency_key=(
                    f"homework-score:ml-zoomcamp-2026:homework-1:{resubmission.pk}"
                ),
            )
        self.assertEqual(latest.purpose, "homework-score-notification")
        self.assertEqual(latest.category, "submission-results")
        self.assertEqual(latest.context_data["total_score"], 8)
        self.assertEqual(latest.context_data["scores_url"],
                         "https://courses.example.com/courses/ml-zoomcamp/cohorts/2026/homework/homework-1")
        stale = EmailDelivery.objects.filter(
            recipient_email="scored@example.com",
        )
        self.assertEqual(stale.count(), 1)

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_score_run_replays_instead_of_duplicating(self):
        homework, _submissions, _resubmission = self.create_scored_homework()

        with mail_outbox() as outbox:
            with self.captureOnCommitCallbacks(execute=True):
                send_homework_score_notification(homework)
                send_homework_score_notification(homework)

            self.assertEqual(len(outbox), 2)
            self.assertEqual(EmailDelivery.objects.count(), 2)

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

        with mail_outbox() as outbox:
            with self.captureOnCommitCallbacks(execute=True):
                sent = send_homework_score_notification(homework)

            self.assertEqual(sent, 1)
            self.assertEqual(len(outbox), 1)
            delivery = EmailDelivery.objects.get()
            self.assertEqual(delivery.context_data["total_score"], 0)


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

        with mail_outbox() as outbox:
            with self.captureOnCommitCallbacks(execute=True):
                sent = send_project_score_notification(project)

            self.assertEqual(sent, 1)
            self.assertEqual(len(outbox), 1)
            delivery = EmailDelivery.objects.get()
        self.assertEqual(delivery.purpose, "project-score-notification")
        self.assertEqual(
            delivery.idempotency_key,
            f"project-score:ml-zoomcamp-2026:midterm:{submission.pk}",
        )
        context = delivery.context_data
        self.assertEqual(context["total_score"], 98)
        self.assertEqual(context["github_link"], "https://github.com/example/project")
        self.assertEqual(context["scores_url"],
                         "https://courses.example.com/courses/ml-zoomcamp/cohorts/2026/project/midterm/results")


class PeerReviewAssignmentNotificationTest(PackageNotificationBase):

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_review_assignment_names_the_reviews_and_deadline(self):
        from courses.package_notifications import (
            send_peer_review_assignment_notification,
        )

        project, reviewer_submission = self.create_review_assignment()

        with mail_outbox() as outbox:
            with self.captureOnCommitCallbacks(execute=True):
                sent = send_peer_review_assignment_notification(project)

            # Every submitter is notified with their own review list; the
            # author's list is empty because this fixture assigned one review.
            self.assertEqual(sent, 2)
            self.assertEqual(len(outbox), 2)
            reviewer_delivery = EmailDelivery.objects.get(
                idempotency_key=(
                    "peer-review-assignment:ml-zoomcamp-2026:capstone:"
                    f"{reviewer_submission.pk}"
                ),
            )
            self.assertEqual(
                reviewer_delivery.purpose,
                "peer-review-assignment",
            )
            context = reviewer_delivery.context_data
            self.assertEqual(context["number_of_peers_to_evaluate"], 2)
            self.assertEqual(len(context["assigned_reviews"]), 1)
            self.assertIn("/eval", context["assigned_reviews"][0]["eval_url"])
            self.assertIn("deadline_summary", context)
            author_delivery = EmailDelivery.objects.exclude(
                pk=reviewer_delivery.pk,
            ).get()
            self.assertEqual(
                author_delivery.context_data["assigned_reviews"],
                [],
            )


class PreviewCommandTest(PackageNotificationBase):
    def test_preview_command_runs_without_datamailer_settings(self):
        project, _submission = self.create_review_assignment()

        out = StringIO()
        call_command(
            "preview_peer_review_email",
            "ml-zoomcamp-2026",
            "capstone",
            stdout=out,
        )
        self.assertIn("recipient(s) would be emailed", out.getvalue())
