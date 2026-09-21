"""Cohort-owned homework through the shared draft reader."""

from community_base.homework_steps.models import HomeworkDraft
from django.test import Client
from django.utils import timezone

from courses.models import Answer, Enrollment, HomeworkState, Submission
from courses.tests.homework_view_base import HomeworkDetailViewTestBase, credentials
from courses.views.homework_steps import assignment_key


class HomeworkStepsTests(HomeworkDetailViewTestBase):
    def setUp(self):
        super().setUp()
        self.homework.homework_url_field = False
        self.homework.learning_in_public_cap = 0
        self.homework.time_spent_lectures_field = False
        self.homework.time_spent_homework_field = False
        self.homework.faq_contribution_field = False
        self.homework.save()
        self.client.login(**credentials)

    def step_url(self, step):
        return f"{self.homework_url()}?homework_step={step}"

    def draft_token(self):
        return str(
            HomeworkDraft.objects.get(
                user=self.user, assignment_key=assignment_key(self.homework)
            ).token
        )

    def save(self, question, answer, revision, *, client=None):
        client = client or self.client
        return client.post(
            self.homework_url(),
            {
                "assignment_key": assignment_key(self.homework),
                "draft_token": self.draft_token(),
                "homework_step": f"q-{question.pk}",
                "revision": revision,
                "answer": answer,
                "intent": "save",
            },
        )

    def test_open_homework_shows_intro_and_single_question_steps_in_shell(self):
        self.homework.instructions_markdown = "## Before you start\n\nRead the course notes."
        self.homework.save(update_fields=["instructions_markdown"])
        intro = self.client.get(self.homework_url())
        self.assertEqual(intro.status_code, 200)
        self.assertTemplateUsed(intro, "homework/steps.html")
        self.assertContains(intro, "Introduction")
        self.assertContains(intro, "<h2>Before you start</h2>", html=True)
        self.assertContains(intro, "Review &amp; submit")
        self.assertContains(intro, self.course.title)
        self.assertNotContains(intro, 'name="answer"')

        question = self.client.get(self.step_url(f"q-{self.question1.pk}"))
        self.assertContains(question, self.question1.text)
        self.assertContains(question, 'value="1"')
        self.assertNotContains(question, self.question2.text + "</legend>")
        self.assertEqual(Enrollment.objects.count(), 0)
        self.assertEqual(Submission.objects.count(), 0)

    def test_answer_survives_new_session_without_submission_and_conflict_is_rejected(self):
        self.client.get(self.homework_url())
        first = self.save(self.question1, "2", 0)
        self.assertEqual(first.status_code, 302)
        second_client = Client()
        second_client.login(**credentials)
        resumed = second_client.get(self.step_url(f"q-{self.question1.pk}"))
        self.assertContains(resumed, 'value="2" checked')

        conflict = self.save(self.question1, "1", 0)
        self.assertEqual(conflict.status_code, 409)
        self.assertContains(conflict, 'value="1" checked', status_code=409)
        draft = HomeworkDraft.objects.get(
            user=self.user, assignment_key=assignment_key(self.homework)
        )
        self.assertEqual(draft.answers[f"q-{self.question1.pk}"], "2")
        self.assertEqual(Enrollment.objects.count(), 0)
        self.assertEqual(Submission.objects.count(), 0)
        self.assertEqual(Answer.objects.count(), 0)

    def test_review_final_submit_uses_existing_submission_and_clears_draft(self):
        self.client.get(self.homework_url())
        self.save(self.question1, "3", 0)
        self.save(self.question2, "answer from draft", 1)
        review = self.client.get(self.step_url("review"))
        self.assertContains(review, "answer from draft")
        self.assertEqual(Submission.objects.count(), 0)
        response = self.client.post(
            self.homework_url(),
            {
                "assignment_key": assignment_key(self.homework),
                "draft_token": self.draft_token(),
                "homework_step": "review",
                "revision": 2,
                "intent": "submit",
            },
        )
        self.assertEqual(response.status_code, 302)
        submission = Submission.objects.get(student=self.user, homework=self.homework)
        self.assertEqual(submission.answer_set.get(question=self.question1).answer_text, "3")
        self.assertEqual(
            submission.answer_set.get(question=self.question2).answer_text, "answer from draft"
        )
        self.assertEqual(submission.answer_set.count(), 6)
        self.assertFalse(
            HomeworkDraft.objects.filter(
                user=self.user, assignment_key=assignment_key(self.homework)
            ).exists()
        )

    def test_classic_post_invalidates_old_draft(self):
        self.client.get(self.homework_url())
        self.save(self.question1, "2", 0)
        response = self.client.post(self.homework_url(), self.answer_post_data())
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            HomeworkDraft.objects.filter(
                user=self.user, assignment_key=assignment_key(self.homework)
            ).exists()
        )

    def test_closed_homework_rejects_stale_review_and_keeps_draft(self):
        self.client.get(self.homework_url())
        self.save(self.question1, "2", 0)
        self.homework.state = HomeworkState.CLOSED.value
        self.homework.save(update_fields=["state"])
        response = self.client.post(
            self.homework_url(),
            {
                "assignment_key": assignment_key(self.homework),
                "draft_token": self.draft_token(),
                "homework_step": "review",
                "revision": 1,
                "intent": "submit",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Submission.objects.exists())
        self.assertTrue(
            HomeworkDraft.objects.filter(
                user=self.user, assignment_key=assignment_key(self.homework)
            ).exists()
        )

    def test_source_option_reorder_preserves_draft_choice_at_submit(self):
        self.question1.source_option_ids = ["paris", "london", "berlin"]
        self.question1.save(update_fields=["source_option_ids"])
        self.client.get(self.homework_url())
        self.save(self.question1, "paris", 0)
        self.question1.possible_answers = "London\nParis\nBerlin"
        self.question1.source_option_ids = ["london", "paris", "berlin"]
        self.question1.save(update_fields=["possible_answers", "source_option_ids"])
        question = self.client.get(self.step_url(f"q-{self.question1.pk}"))
        self.assertContains(question, 'value="paris" checked')
        response = self.client.post(
            self.homework_url(),
            {
                "assignment_key": assignment_key(self.homework),
                "draft_token": self.draft_token(),
                "homework_step": "review",
                "revision": 1,
                "intent": "submit",
            },
        )
        self.assertEqual(response.status_code, 302)
        answer = Answer.objects.get(submission__student=self.user, question=self.question1)
        self.assertEqual(answer.answer_text, "2")

    def test_existing_submission_metadata_survives_one_question_update(self):
        self.create_submission_with_answers()
        self.submission.homework_link = "https://github.com/example/homework"
        self.submission.learning_in_public_links = ["https://example.org/progress"]
        self.submission.time_spent_lectures = 2.5
        self.submission.time_spent_homework = 4.0
        self.submission.problems_comments = "Original feedback"
        self.submission.faq_contribution_url = "https://github.com/DataTalksClub/faq/issues/281"
        self.submission.save()
        self.course.homework_problems_comments_field = True
        self.course.save(update_fields=["homework_problems_comments_field"])
        self.homework.homework_url_field = True
        self.homework.learning_in_public_cap = 2
        self.homework.time_spent_lectures_field = True
        self.homework.time_spent_homework_field = True
        self.homework.faq_contribution_field = True
        self.homework.due_date = timezone.now() - timezone.timedelta(days=1)
        self.homework.save()
        old_submitted_at = self.submission.submitted_at
        intro = self.client.get(self.homework_url())
        self.assertEqual(intro.status_code, 200)
        self.save(self.question1, "2", 0)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.submitted_at, old_submitted_at)
        review = self.client.get(self.step_url("review"))
        self.assertContains(review, 'value="https://github.com/example/homework"')
        response = self.client.post(
            self.homework_url(),
            {
                "assignment_key": assignment_key(self.homework),
                "draft_token": self.draft_token(),
                "homework_step": "review",
                "revision": 1,
                "intent": "submit",
                "final_homework_url": "https://github.com/example/homework",
                "final_learning_in_public_link_1": "https://example.org/progress",
                "final_learning_in_public_link_2": "",
                "final_time_spent_lectures": "2.5",
                "final_time_spent_homework": "4.0",
                "final_problems_comments": "Original feedback",
                "final_faq_contribution_url": "https://github.com/DataTalksClub/faq/issues/281",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(
            Submission.objects.filter(student=self.user, homework=self.homework).count(), 1
        )
        self.assertEqual(self.submission.answer_set.get(question=self.question1).answer_text, "2")
        self.assertEqual(self.submission.homework_link, "https://github.com/example/homework")
        self.assertEqual(self.submission.learning_in_public_links, ["https://example.org/progress"])
        self.assertEqual(self.submission.time_spent_lectures, 2.5)
        self.assertEqual(self.submission.time_spent_homework, 4.0)
        self.assertEqual(self.submission.problems_comments, "Original feedback")
        self.assertEqual(
            self.submission.faq_contribution_url, "https://github.com/DataTalksClub/faq/issues/281"
        )

    def test_replayed_final_post_does_not_update_submission_twice(self):
        self.client.get(self.homework_url())
        self.save(self.question1, "1", 0)
        payload = {
            "assignment_key": assignment_key(self.homework),
            "draft_token": self.draft_token(),
            "homework_step": "review",
            "revision": 1,
            "intent": "submit",
        }
        first = self.client.post(self.homework_url(), payload)
        self.assertEqual(first.status_code, 302)
        submission = Submission.objects.get(student=self.user, homework=self.homework)
        submitted_at = submission.submitted_at
        second = self.client.post(self.homework_url(), payload)
        self.assertEqual(second.status_code, 409)
        submission.refresh_from_db()
        self.assertEqual(submission.submitted_at, submitted_at)
        self.assertEqual(
            Submission.objects.filter(student=self.user, homework=self.homework).count(), 1
        )
