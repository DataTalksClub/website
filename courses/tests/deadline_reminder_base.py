from datetime import datetime, timezone as datetime_timezone

from community_base.mail.models import EmailDelivery
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from accounts.models import CustomUser
from courses.models import Cohort, Enrollment


#: The fake datamailer URLs these tests configure must not be consulted
#: for preference lookups: the site resolver would try HTTP against them.
#: Overriding the hook with the package's allow-all default keeps these
#: command tests about delivery recording, not preference filtering; the
#: datamailer never applied preferences at send time before D1.2b either,
#: it did so server-side.
NO_PREFERENCE_LOOKUP = {
    "COMMUNITY_BASE": {
        **settings.COMMUNITY_BASE,
        "MAIL_PREFERENCE_RESOLVER": "community_base.mail.preferences.allow_all",
    },
}


def deliveries_for_purpose(test_case, purpose):
    rows = EmailDelivery.objects.filter(purpose=purpose).order_by(
        "recipient_email",
    )
    test_case.assertTrue(rows.exists(), f"no {purpose} deliveries recorded")
    return {row.recipient_email: row for row in rows}


DATAMAILER_SETTINGS = {
    "DATAMAILER_URL": "https://datamailer.example.com",
    "DATAMAILER_API_KEY": "secret-token",
    "DATAMAILER_CLIENT": "dtc-courses",
    "DATAMAILER_AUDIENCE": "dtc-courses",
}


class DeadlineReminderTestBase(TestCase):
    def reminder_run_time(self):
        return datetime(2026, 6, 16, 9, tzinfo=datetime_timezone.utc)

    def create_user(
        self,
        username,
        email,
        *,
        preferred_timezone="",
    ):
        user = CustomUser.objects.create_user(
            username=username,
            email=email,
            password="password",
        )
        user.preferred_timezone = preferred_timezone
        user.save(update_fields=["preferred_timezone"])
        return user

    def create_enrollment(self, user, course):
        return Enrollment.objects.create(student=user, course=course)

    def create_course(self):
        return Cohort.objects.create(
            slug="ml-zoomcamp-2026",
            title="ML Zoomcamp 2026",
            description="Machine learning",
        )

    def run_deadline_reminders(
        self,
        now,
        stdout=None,
        dry_run=False,
        stderr=None,
    ):
        now_value = now.isoformat()
        args = ["send_deadline_reminders", "--now", now_value]
        if dry_run:
            args.append("--dry-run")
        call_command(*args, stdout=stdout, stderr=stderr)

    def members_by_email(self, payload):
        members_by_email = {}
        members = payload["members"]
        for member in members:
            email = member["email"]
            members_by_email[email] = member
        return members_by_email
