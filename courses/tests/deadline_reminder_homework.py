from dataclasses import dataclass
from datetime import timedelta

from courses.models import Enrollment, Homework, Submission


@dataclass(frozen=True)
class HomeworkReminderFixture:
    homework: Homework
    eligible_enrollment: Enrollment
    opted_out_enrollment: Enrollment


@dataclass(frozen=True)
class HomeworkReminderUsers:
    eligible_user: object
    submitted_user: object
    opted_out_user: object


@dataclass(frozen=True)
class HomeworkReminderEnrollments:
    eligible_enrollment: Enrollment
    submitted_enrollment: Enrollment
    opted_out_enrollment: Enrollment


def create_homework(course, now):
    return Homework.objects.create(
        course=course,
        slug="homework-1",
        title="Homework 1",
        due_date=now + timedelta(days=1, hours=14),
    )


def create_homework_reminder_users(test_case):
    eligible_user = test_case.create_user(
        "eligible",
        "eligible@example.com",
        preferred_timezone="Europe/Berlin",
    )
    submitted_user = test_case.create_user(
        "submitted",
        "submitted@example.com",
    )
    opted_out_user = test_case.create_user(
        "opted-out",
        "opted-out@example.com",
    )
    return HomeworkReminderUsers(
        eligible_user=eligible_user,
        submitted_user=submitted_user,
        opted_out_user=opted_out_user,
    )


def create_homework_reminder_enrollments(test_case, course, users):
    eligible_enrollment = test_case.create_enrollment(
        users.eligible_user,
        course,
    )
    submitted_enrollment = test_case.create_enrollment(
        users.submitted_user,
        course,
    )
    opted_out_enrollment = test_case.create_enrollment(
        users.opted_out_user,
        course,
    )
    return HomeworkReminderEnrollments(
        eligible_enrollment=eligible_enrollment,
        submitted_enrollment=submitted_enrollment,
        opted_out_enrollment=opted_out_enrollment,
    )


def create_submitted_homework(homework, user, enrollment):
    Submission.objects.create(
        homework=homework,
        student=user,
        enrollment=enrollment,
    )


def create_homework_reminder_fixture(test_case, now):
    course = test_case.create_course()
    homework = create_homework(course, now)
    users = create_homework_reminder_users(test_case)
    enrollments = create_homework_reminder_enrollments(
        test_case,
        course,
        users,
    )
    create_submitted_homework(
        homework,
        users.submitted_user,
        enrollments.submitted_enrollment,
    )
    return HomeworkReminderFixture(
        homework=homework,
        eligible_enrollment=enrollments.eligible_enrollment,
        opted_out_enrollment=enrollments.opted_out_enrollment,
    )


def assert_homework_reminder_deliveries(test_case, expectation):
    from courses.tests.deadline_reminder_base import deliveries_for_purpose

    deliveries = deliveries_for_purpose(test_case, "deadline-reminder")
    test_case.assertEqual(
        set(deliveries),
        {"eligible@example.com", "opted-out@example.com"},
    )
    eligible = deliveries["eligible@example.com"]
    opted_out = deliveries["opted-out@example.com"]
    test_case.assertEqual(
        eligible.idempotency_key,
        (
            f"deadline-reminder:homework:{expectation.homework.pk}:24h"
            f":enrollment:{expectation.eligible_enrollment.pk}"
        ),
    )
    test_case.assertEqual(
        opted_out.idempotency_key,
        (
            f"deadline-reminder:homework:{expectation.homework.pk}:24h"
            f":enrollment:{expectation.opted_out_enrollment.pk}"
        ),
    )
    for row in (eligible, opted_out):
        test_case.assertEqual(row.category, "email_deadline_reminders")
        test_case.assertEqual(row.context_data["course_title"], "ML Zoomcamp 2026")
        test_case.assertEqual(
            row.context_data["course_url"],
            "https://courses.example.com/courses/ml-zoomcamp/cohorts/2026/homework/homework-1",
        )
    test_case.assertEqual(
        eligible.context_data["deadline"],
        "Thursday, 18 June 2026, 01:00 Europe/Berlin",
    )
    test_case.assertEqual(
        opted_out.context_data["deadline"],
        "Wednesday, 17 June 2026, 23:00 UTC",
    )
