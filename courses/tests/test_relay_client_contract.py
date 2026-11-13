import ast
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from courses.models import Announcement, Cohort, Course, Enrollment


class RelayClientContractTest(TestCase):
    def test_no_bulk_loop_calling_transactional_or_package_send(self):
        root = Path(__file__).resolve().parents[2]
        for rel in (
            "courses/services/launch_transport.py",
            "courses/management/commands/send_deadline_reminders.py",
            "courses/package_notifications.py",
        ):
            tree = ast.parse((root / rel).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.For, ast.AsyncFor)):
                    body_src = ast.unparse(node)
                    self.assertNotIn("send_package_mail", body_src, msg=rel)
                    self.assertNotIn("send_deadline_reminder_mail", body_src, msg=rel)
                    self.assertNotIn("/api/transactional/send", body_src, msg=rel)
                    self.assertNotIn("community_base.mail", body_src, msg=rel)

    @override_settings(
        COURSE_ANNOUNCEMENTS_ENABLED=True,
        ENVIRONMENT="production",
    )
    def test_launch_transport_submits_campaign_in_one_request(self):
        from courses.services.launch_transport import submit_email

        user1 = get_user_model().objects.create_user(
            username="u1", email="u1@example.com", password="x"
        )
        user2 = get_user_model().objects.create_user(
            username="u2", email="u2@example.com", password="x"
        )
        course = Course.objects.create(slug="ml", title="ML")
        cohort = Cohort.objects.create(
            course=course, slug="ml-2026", title="ML 2026"
        )
        Enrollment.objects.create(course=cohort, student=user1)
        Enrollment.objects.create(course=cohort, student=user2)
        announcement = Announcement.objects.create(
            cohort=cohort,
            channel="email",
            status="dispatching",
        )

        with (
            patch(
                "courses.services.launch_transport.preview",
                return_value={"subject": "Welcome", "body": "Hello"},
            ),
            patch(
                "courses.services.launch_transport.submit_campaign"
            ) as mock_submit,
        ):
            submit_email(announcement)

        mock_submit.assert_called_once()
        kwargs = mock_submit.call_args.kwargs
        self.assertEqual(len(kwargs["recipients"]), 2)

    def test_course_admin_displays_relay_campaign_status(self):
        from django.contrib.auth.models import Group, Permission

        staff = get_user_model().objects.create_user(
            username="staff",
            email="staff@example.com",
            password="pass",
            is_staff=True,
            is_superuser=True,
        )
        group, _ = Group.objects.get_or_create(name="site_admin")
        staff.groups.add(group)
        studio_perm = Permission.objects.get(
            content_type__app_label="core", codename="access_studio"
        )
        staff.user_permissions.add(studio_perm)
        self.client.force_login(staff)
        course = Course.objects.create(slug="de", title="DE")
        cohort = Cohort.objects.create(
            course=course, slug="de-2026", title="DE 2026"
        )
        announcement = Announcement.objects.create(
            cohort=cohort,
            channel="email",
            status="submitted",
        )
        relay_status = {
            "external_key": f"announcement-{announcement.pk}",
            "status": "sending",
            "recipient_count": 50,
            "sent_count": 30,
            "delivered_count": 28,
            "pending_count": 20,
            "skipped_count": 0,
        }
        with patch(
            "course_management.relay_campaigns.get_campaign_status",
            return_value=relay_status,
        ):
            response = self.client.get(
                reverse(
                    "studio_courses_course",
                    kwargs={"course_slug": cohort.slug},
                )
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-relay-campaign-status")
        self.assertContains(response, "Sent: 30")
        self.assertContains(response, "Delivered: 28")
        self.assertContains(response, "Pending: 20")
