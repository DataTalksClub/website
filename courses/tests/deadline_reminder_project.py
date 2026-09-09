from dataclasses import dataclass
from datetime import datetime, timedelta

from courses.models import Cohort, Project, ProjectState


@dataclass(frozen=True)
class ProjectReminderData:
    course: Cohort
    now: datetime
    slug: str
    title: str
    submission_delta: timedelta
    state: str


def create_project(data):
    return Project.objects.create(
        course=data.course,
        slug=data.slug,
        title=data.title,
        submission_due_date=data.now + data.submission_delta,
        peer_review_due_date=data.now + timedelta(days=10),
        state=data.state,
    )


def create_project_submission_reminder_fixture(test_case, now):
    course = test_case.create_course()
    user = test_case.create_user("student", "student@example.com")
    opted_out_user = test_case.create_user(
        "opted-out",
        "opted-out@example.com",
    )
    test_case.create_enrollment(user, course)
    test_case.create_enrollment(opted_out_user, course)
    project_week_delta = timedelta(days=8, hours=2)
    project_week = ProjectReminderData(
        course=course,
        now=now,
        slug="project-week",
        title="Project Week",
        submission_delta=project_week_delta,
        state=ProjectState.COLLECTING_SUBMISSIONS.value,
    )
    create_project(project_week)
    project_day_delta = timedelta(days=1, hours=14)
    project_day = ProjectReminderData(
        course=course,
        now=now,
        slug="project-day",
        title="Project Day",
        submission_delta=project_day_delta,
        state=ProjectState.COLLECTING_SUBMISSIONS.value,
    )
    create_project(project_day)


def assert_project_reminder_deliveries(test_case):
    from courses.tests.deadline_reminder_base import deliveries_for_purpose

    deliveries = deliveries_for_purpose(test_case, "deadline-reminder")
    test_case.assertEqual(
        set(deliveries),
        {"student@example.com", "opted-out@example.com"},
    )
    reminder_keys = {
        row.idempotency_key.split(":")[3] for row in deliveries.values()
    }
    test_case.assertEqual(reminder_keys, {"24h", "7d"})
    for row in deliveries.values():
        test_case.assertEqual(row.category, "email_deadline_reminders")
        test_case.assertTrue(
            row.idempotency_key.startswith("deadline-reminder:project-submission:"),
        )
        test_case.assertIn("enrollment:", row.idempotency_key)
