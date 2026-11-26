"""The shared learner homework status on the homework page, course rows and module rail."""

from community_base.homework_steps.models import HomeworkDraft
from django.contrib.auth import get_user_model
from django.urls import reverse

from courses.models import HomeworkState, Question, QuestionTypes, Submission
from courses.tests.homework_view_base import HomeworkDetailViewTestBase, credentials
from courses.tests.test_shared_course_routes import SharedWorldTestCase
from courses.views.homework_states import learner_homework_state
from courses.views.homework_steps import assignment_key


def state_label(value, label):
    return (
        f'<span role="status" aria-label="Homework status: {label}" '
        f'data-homework-state="{value}">{label}</span>'
    )


class LearnerHomeworkStateTests(HomeworkDetailViewTestBase):
    def setUp(self):
        super().setUp()
        self.homework.learning_in_public_cap = 0
        self.homework.save()
        self.client.login(**credentials)

    def cohort_url(self):
        return reverse(
            "cohort",
            kwargs={
                "course_slug": self.course.course.slug,
                "cohort_identifier": self.course.identifier,
            },
        )

    def test_states_follow_the_shared_model(self):
        self.assertEqual(learner_homework_state(self.user, self.homework).value, "not_submitted")
        HomeworkDraft.objects.create(
            user=self.user,
            assignment_key=assignment_key(self.homework),
            answers={f"q-{self.question1.pk}": "2"},
        )
        self.assertEqual(learner_homework_state(self.user, self.homework).value, "draft")

        HomeworkDraft.objects.all().delete()
        self.create_submission_with_answers()
        self.assertEqual(learner_homework_state(self.user, self.homework).value, "submitted")

        HomeworkDraft.objects.create(
            user=self.user,
            assignment_key=assignment_key(self.homework),
            answers={f"q-{self.question1.pk}": "1"},
        )
        self.assertEqual(
            learner_homework_state(self.user, self.homework).value, "unsubmitted_changes"
        )

        self.homework.state = HomeworkState.SCORED.value
        self.homework.save(update_fields=["state"])
        self.assertEqual(learner_homework_state(self.user, self.homework).value, "scored")

    def test_closed_without_submission(self):
        self.homework.state = HomeworkState.CLOSED.value
        self.homework.save(update_fields=["state"])

        state = learner_homework_state(self.user, self.homework)

        self.assertEqual(state.value, "closed_not_submitted")
        self.assertEqual(state.label, "Closed — not submitted")

    def test_anonymous_reader_has_no_state(self):
        self.client.logout()
        from django.contrib.auth.models import AnonymousUser

        self.assertIsNone(learner_homework_state(AnonymousUser(), self.homework))

    def test_homework_page_states_the_learner_status(self):
        stepper = self.client.get(self.homework_url())
        self.assertTemplateUsed(stepper, "homework/steps.html")
        self.assertContains(stepper, state_label("not_submitted", "Not submitted"))

        classic = self.client.get(f"{self.homework_url()}?homework_view=classic")
        self.assertContains(classic, state_label("not_submitted", "Not submitted"))

    def test_course_homework_rows_show_the_learner_status(self):
        self.create_submission_with_answers()

        response = self.client.get(self.cohort_url())

        self.assertContains(
            response,
            '<span class="status-pill status-pill-live">' + state_label("submitted", "Submitted"),
        )

    def test_course_rows_batch_their_queries(self):
        for index in range(3):
            other = type(self.homework).objects.create(
                course=self.course,
                title=f"Extra {index}",
                slug=f"extra-{index}",
                due_date=self.homework.due_date,
            )
            Question.objects.create(
                homework=other, text="Q", question_type=QuestionTypes.FREE_FORM.value
            )
        from courses.views.homework_states import learner_homework_states

        homeworks = list(type(self.homework).objects.filter(course=self.course))
        with self.assertNumQueries(4):  # no submissions, so no answers query
            states = learner_homework_states(self.user, self.course, homeworks)
        self.assertEqual(len(states), 4)


class RailHomeworkStateTests(SharedWorldTestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="rail-learner", email="rail-learner@example.test", password="pw-12345"
        )
        self.question = Question.objects.create(
            homework=self.homework, text="Pick one", question_type=QuestionTypes.FREE_FORM.value
        )

    def test_lesson_rail_shows_the_homework_status_for_a_signed_in_learner(self):
        anonymous = self.client.get(self.lesson_url() + "?cohort=2026")
        self.assertNotContains(anonymous, 'data-homework-state="')

        self.client.force_login(self.user)
        Submission.objects.create(
            homework=self.homework,
            student=self.user,
            enrollment=self.cohort_2026.enrollment_set.create(student=self.user),
        )
        response = self.client.get(self.lesson_url() + "?cohort=2026")

        self.assertContains(response, 'class="rail-unit-status"')
        self.assertContains(response, state_label("submitted", "Submitted"))


class RailHomeworkSubstepTests(RailHomeworkStateTests):
    def homework_step_url(self, step):
        return reverse(
            "cohort_homework_step",
            kwargs={
                "course_slug": self.course.slug,
                "cohort_identifier": self.cohort_2026.identifier,
                "homework_slug": self.homework.slug,
                "homework_step": step,
            },
        )

    def test_homework_page_rail_lists_the_steps_with_current_and_done(self):
        self.client.force_login(self.user)
        self.client.get(self.homework_step_url("intro"))
        HomeworkDraft.objects.filter(user=self.user).update(
            answers={f"q-{self.question.pk}": "my answer"}
        )

        response = self.client.get(self.homework_step_url("review"))

        html = response.content.decode()
        self.assertIn('<ol class="rail-substeps" aria-label="Homework steps"', html)
        self.assertIn('class="rail-substep is-done" data-step="q-', html)
        self.assertIn('Question 1<span class="sr-only">, answered</span>', html)
        self.assertIn('class="rail-substep is-current" data-step="review"', html)
        # The header carries the status; the rail row does not repeat it.
        self.assertNotIn('class="rail-unit-status"', html)

    def test_lesson_rail_has_no_substeps(self):
        # Off the homework page the rail's homework is one row (title, status,
        # due date); its steps open on the homework page itself, as in AISL,
        # so a long module's rail still fits the screen.
        self.client.force_login(self.user)
        response = self.client.get(self.lesson_url() + "?cohort=2026")
        self.assertNotContains(response, 'class="rail-substeps"')
