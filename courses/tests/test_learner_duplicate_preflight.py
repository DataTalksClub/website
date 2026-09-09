"""The BE-12 preflight reports duplicates and broken references by ID only.

Before any uniqueness constraint can be added, this read-only preflight must
find where existing data violates the intended logical identities, keyed only
by record and user IDs -- an answer, a name, or an address never appears in
its output.
"""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.utils import timezone

from courses.models import (
    Answer,
    CriteriaResponse,
    Enrollment,
    PeerReview,
    ProjectSubmission,
    Question,
    Submission,
    User,
)
from courses.services.learner_duplicate_preflight import (
    run_learner_duplicate_preflight,
)
from courses.tests.project_eval_base import ProjectEvaluationTestBase


def make_student(index: int) -> User:  # type: ignore[valid-type]
    return User.objects.create_user(
        username=f"preflight-student-{index}",
        email=f"preflight-{index}@email.com",
        password="12345",
    )


def make_enrollment(student: User, course) -> Enrollment:  # type: ignore[valid-type]
    return Enrollment.objects.create(student=student, course=course)


class CleanDatabasePreflightTests(ProjectEvaluationTestBase):
    def test_a_clean_database_reports_clean(self) -> None:
        report = run_learner_duplicate_preflight()

        self.assertTrue(report.clean)
        self.assertEqual(report.summary()["duplicates"], {})
        self.assertEqual(set(report.summary()["inconsistent_references"].values()), {0})


class DuplicateDetectionTests(ProjectEvaluationTestBase):
    def test_duplicate_project_submissions_are_grouped_by_identity(self) -> None:
        extra = make_student(1)
        enrollment = make_enrollment(extra, self.course)
        first = ProjectSubmission.objects.create(
            project=self.project,
            student=extra,
            enrollment=enrollment,
            github_link="https://github.com/one/project",
        )
        second = ProjectSubmission.objects.create(
            project=self.project,
            student=extra,
            enrollment=enrollment,
            github_link="https://github.com/two/project",
        )

        report = run_learner_duplicate_preflight()

        groups = report.duplicate_groups["project_submission"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["record_count"], 2)
        self.assertEqual(sorted(groups[0]["record_ids"]), sorted([first.pk, second.pk]))
        self.assertFalse(report.clean)

    def test_duplicate_peer_review_pairs_are_grouped(self) -> None:
        evaluated_owner = make_student(2)
        evaluated = ProjectSubmission.objects.create(
            project=self.project,
            student=evaluated_owner,
            enrollment=make_enrollment(evaluated_owner, self.course),
            github_link="https://github.com/evaluated/project",
        )
        first = PeerReview.objects.create(
            reviewer=self.submission,
            submission_under_evaluation=evaluated,
        )
        second = PeerReview.objects.create(
            reviewer=self.submission,
            submission_under_evaluation=evaluated,
        )

        report = run_learner_duplicate_preflight()

        groups = report.duplicate_groups["peer_review_pair"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(groups[0]["record_ids"]), sorted([first.pk, second.pk]))

    def test_duplicate_criteria_responses_are_grouped(self) -> None:
        first = CriteriaResponse.objects.create(
            review=self.peer_review, criteria=self.criteria1, answer="1"
        )
        second = CriteriaResponse.objects.create(
            review=self.peer_review, criteria=self.criteria1, answer="2"
        )

        report = run_learner_duplicate_preflight()

        groups = report.duplicate_groups["criteria_response"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(sorted(groups[0]["record_ids"]), sorted([first.pk, second.pk]))

    def test_duplicate_homework_submissions_and_answers_are_grouped(self) -> None:
        from courses.models import Homework

        homework = Homework.objects.create(
            course=self.course,
            slug="preflight-homework",
            title="Preflight Homework",
            due_date=timezone.now(),
        )
        question = Question.objects.create(
            homework=homework, text="Preflight question?", question_type="FF"
        )
        student = make_student(3)
        enrollment = make_enrollment(student, self.course)
        first = Submission.objects.create(homework=homework, student=student, enrollment=enrollment)
        Submission.objects.create(homework=homework, student=student, enrollment=enrollment)
        answer_one = Answer.objects.create(submission=first, question=question, answer_text="one")
        answer_two = Answer.objects.create(submission=first, question=question, answer_text="two")

        report = run_learner_duplicate_preflight()

        submission_groups = report.duplicate_groups["homework_submission"]
        self.assertEqual(
            sorted(submission_groups[0]["record_ids"]), sorted([first.pk, first.pk + 1])
        )
        answer_groups = report.duplicate_groups["answer"]
        self.assertEqual(
            sorted(answer_groups[0]["record_ids"]), sorted([answer_one.pk, answer_two.pk])
        )


class InconsistentReferenceTests(ProjectEvaluationTestBase):
    def test_cross_project_review_pair_is_reported(self) -> None:
        from datetime import timedelta

        from django.utils import timezone

        from courses.models import Project, ProjectState

        evaluated_owner = make_student(4)
        other_project = Project.objects.create(
            course=self.course,
            slug="preflight-other-project",
            title="Other",
            submission_due_date=timezone.now() - timedelta(hours=1),
            peer_review_due_date=timezone.now() + timedelta(hours=1),
            state=ProjectState.PEER_REVIEWING.value,
        )
        evaluated = ProjectSubmission.objects.create(
            project=other_project,
            student=evaluated_owner,
            enrollment=make_enrollment(evaluated_owner, self.course),
            github_link="https://github.com/elsewhere/project",
        )
        # A review whose two sides live in different projects: exactly the
        # reference inconsistency the foreign keys cannot express.
        PeerReview.objects.create(
            reviewer=self.submission,
            submission_under_evaluation=evaluated,
        )

        report = run_learner_duplicate_preflight()

        self.assertEqual(report.inconsistent_references["peer_review_cross_project"], 1)
        self.assertFalse(report.clean)

    def test_a_mismatched_enrollment_is_reported(self) -> None:
        other_student = make_student(6)
        submission = ProjectSubmission.objects.create(
            project=self.project,
            student=self.user,
            enrollment=make_enrollment(other_student, self.course),
            github_link="https://github.com/mismatched/project",
        )

        report = run_learner_duplicate_preflight()

        self.assertEqual(
            report.inconsistent_references["project_submission_enrollment_mismatch"],
            1,
        )
        self.assertIsNotNone(submission.pk)


class PreflightCommandTests(ProjectEvaluationTestBase):
    def test_the_command_exits_nonzero_on_duplicates_and_prints_ids_only(self) -> None:
        extra = make_student(5)
        enrollment = make_enrollment(extra, self.course)
        ProjectSubmission.objects.create(
            project=self.project,
            student=extra,
            enrollment=enrollment,
            github_link="https://github.com/one/project",
        )
        ProjectSubmission.objects.create(
            project=self.project,
            student=extra,
            enrollment=enrollment,
            github_link="https://github.com/two/project",
        )

        stdout = StringIO()
        with self.assertRaises(SystemExit) as exited:
            call_command("learner_duplicate_preflight", stdout=stdout)

        self.assertEqual(exited.exception.code, 1)
        output = stdout.getvalue()
        self.assertIn("project_submission", output)
        # The github links are payload; only IDs may appear.
        self.assertNotIn("github.com", output)

    def test_the_command_exits_zero_when_clean(self) -> None:
        stdout = StringIO()

        call_command("learner_duplicate_preflight", stdout=stdout)

        self.assertIn("PREFLIGHT: clean", stdout.getvalue())
