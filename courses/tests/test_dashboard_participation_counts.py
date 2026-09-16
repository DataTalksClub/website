from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from courses.models import Cohort, Enrollment, Homework, HomeworkState, Submission

User = get_user_model()


class DashboardParticipationCountTestCase(TestCase):
    def setUp(self):
        self.course = Cohort.objects.create(
            slug="participation-course",
            title="Participation Course",
            first_homework_scored=True,
        )
        self.homework_one = Homework.objects.create(
            course=self.course,
            slug="homework-one",
            title="Homework One",
            due_date=timezone.now() + timedelta(days=7),
            state=HomeworkState.SCORED.value,
        )
        self.homework_two = Homework.objects.create(
            course=self.course,
            slug="homework-two",
            title="Homework Two",
            due_date=timezone.now() + timedelta(days=14),
            state=HomeworkState.SCORED.value,
        )
        self.enrollments = []
        for index in range(3):
            user = User.objects.create_user(
                username=f"participation-{index}",
                email=f"participation-{index}@example.com",
                password="test-password",
            )
            self.enrollments.append(
                Enrollment.objects.create(student=user, course=self.course)
            )

    def dashboard_url(self):
        return reverse(
            "cohort_dashboard",
            kwargs={
                "course_slug": self.course.course.slug,
                "cohort_identifier": self.course.identifier,
            },
        )

    def submit(self, enrollment, homework):
        return Submission.objects.create(
            homework=homework,
            student=enrollment.student,
            enrollment=enrollment,
        )

    def test_registered_and_enrolled_are_distinct_people_at_separate_funnel_steps(self):
        self.submit(self.enrollments[0], self.homework_one)
        self.submit(self.enrollments[0], self.homework_two)
        self.submit(self.enrollments[1], self.homework_one)

        response = self.client.get(self.dashboard_url())

        self.assertEqual(response.context["registered_count"], 3)
        self.assertEqual(response.context["enrolled_count"], 2)
        self.assertContains(response, "Registered")
        self.assertContains(response, "Enrolled")
        self.assertContains(response, "People who submitted at least one homework.")
        self.assertContains(response, 'aria-describedby="enrolled-definition"')

    def test_participation_rates_use_people_who_started_as_the_denominator(self):
        self.submit(self.enrollments[0], self.homework_one)
        self.submit(self.enrollments[0], self.homework_two)
        self.submit(self.enrollments[1], self.homework_one)

        response = self.client.get(self.dashboard_url())
        stats = {
            row["homework"].slug: row["completion_rate"]
            for row in response.context["homework_stats"]
        }

        self.assertEqual(stats["homework-one"], 100.0)
        self.assertEqual(stats["homework-two"], 50.0)
        self.assertEqual(response.context["overall_completion_rate"], 75.0)

    def test_unstarted_registrations_are_not_enrolled(self):
        response = self.client.get(self.dashboard_url())

        self.assertEqual(response.context["registered_count"], 3)
        self.assertEqual(response.context["enrolled_count"], 0)
        self.assertContains(response, "3</strong> people registered")
        self.assertContains(response, "0</strong> started the coursework")
