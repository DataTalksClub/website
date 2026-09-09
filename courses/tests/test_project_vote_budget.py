"""The per-project vote budget holds under serialized mutation (audit BE-12).

A learner gets three votes across distinct submissions in one project.  The
count-then-insert used to run without a transaction or lock; the budget
decision is now one serialized unit on the project row.  These tests pin the
observable contract: the cap is enforced across distinct submissions,
re-voting one submission never multiplies, and unvoting releases capacity.
"""

from courses.models import Enrollment, ProjectSubmission, ProjectVote, User
from courses.tests.project_eval_base import ProjectEvaluationTestBase
from courses.votes import (
    PROJECT_VOTES_PER_PROJECT,
    get_project_vote_counts,
    get_voted_submission_ids,
    update_project_vote,
)


class ProjectVoteBudgetTests(ProjectEvaluationTestBase):
    def extra_submission(self, index: int):
        student = User.objects.create_user(
            username=f"voted-student-{index}",
            email=f"voted-{index}@email.com",
            password="12345",
        )
        enrollment = Enrollment.objects.create(student=student, course=self.course)
        return self.create_project_submission(
            student,
            enrollment,
            f"https://github.com/student-{index}/project",
        )

    def setUp(self):
        super().setUp()
        # At least four distinct *votable* submissions (the voter's own is
        # excluded) so the three-vote cap has a fourth target to refuse.
        while len(self.submissions_for_voting()) < PROJECT_VOTES_PER_PROJECT + 1:
            self.extra_submission(ProjectSubmission.objects.count())

    def submissions_for_voting(self):
        return list(ProjectSubmission.objects.exclude(student=self.user).order_by("pk"))

    def test_the_fourth_distinct_vote_is_refused(self):
        submissions = self.submissions_for_voting()

        for submission in submissions[:PROJECT_VOTES_PER_PROJECT]:
            update_project_vote(self.user, submission, action="vote")
        update_project_vote(self.user, submissions[PROJECT_VOTES_PER_PROJECT], action="vote")

        self.assertEqual(
            ProjectVote.objects.filter(voter=self.user).count(),
            PROJECT_VOTES_PER_PROJECT,
        )
        self.assertFalse(
            ProjectVote.objects.filter(
                voter=self.user,
                submission=submissions[PROJECT_VOTES_PER_PROJECT],
            ).exists()
        )

    def test_repeated_votes_on_one_submission_stay_one_row(self):
        submission = self.submissions_for_voting()[0]

        for _ in range(3):
            update_project_vote(self.user, submission, action="vote")

        self.assertEqual(
            ProjectVote.objects.filter(voter=self.user, submission=submission).count(),
            1,
        )

    def test_unvoting_releases_budget_capacity(self):
        submissions = self.submissions_for_voting()
        for submission in submissions[:PROJECT_VOTES_PER_PROJECT]:
            update_project_vote(self.user, submission, action="vote")

        update_project_vote(self.user, submissions[0], action="remove")
        update_project_vote(self.user, submissions[PROJECT_VOTES_PER_PROJECT], action="vote")

        self.assertEqual(
            ProjectVote.objects.filter(voter=self.user).count(),
            PROJECT_VOTES_PER_PROJECT,
        )
        self.assertTrue(
            ProjectVote.objects.filter(
                voter=self.user,
                submission=submissions[PROJECT_VOTES_PER_PROJECT],
            ).exists()
        )

    def test_vote_counts_read_back_per_project(self):
        submissions = self.submissions_for_voting()
        for submission in submissions[:PROJECT_VOTES_PER_PROJECT]:
            update_project_vote(self.user, submission, action="vote")

        counts = get_project_vote_counts(self.user, self.course)

        # All votes land on one project, so the reader aggregates them there.
        self.assertEqual(counts, {submissions[0].project_id: 3})
        self.assertEqual(
            get_voted_submission_ids(self.user, self.course),
            {submission.id for submission in submissions[:3]},
        )
