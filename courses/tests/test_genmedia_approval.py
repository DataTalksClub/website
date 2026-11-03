import csv
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from community_base.jobs.models import JobIntent
from django.test import override_settings
from django.urls import reverse

from accounts.models import User
from courses.genmedia_approval import approve_pending_registrations
from courses.genmedia_policy import (
    AUTO_APPROVAL_LIMIT,
    CAMPAIGN_SLUG,
    EXCLUDED_UKRAINE_REGIONS,
    RESTRICTED_COUNTRIES,
    RESTRICTED_ENTRIES,
    excluded_reason,
)
from courses.models import CourseRegistration
from courses.tests.registration_campaign_base import RegistrationCampaignBase


class GenmediaPolicyTests(RegistrationCampaignBase):
    def test_restricted_configuration_has_exact_nineteen_entries(self):
        self.assertEqual(
            RESTRICTED_ENTRIES,
            (
                "Belarus",
                "Benin",
                "Bolivia",
                "Cameroon",
                "China",
                "Costa Rica",
                "Crimea (Ukraine)",
                "Cuba",
                "DPR / LPR (Ukraine)",
                "Georgia",
                "Hong Kong",
                "Indonesia",
                "Iran",
                "Kyrgyzstan",
                "Moldova",
                "Nigeria",
                "North Korea",
                "Pakistan",
                "Russia",
            ),
        )
        self.assertEqual(len(RESTRICTED_ENTRIES), 19)
        self.assertNotIn("Ukraine", RESTRICTED_COUNTRIES)
        self.assertEqual(AUTO_APPROVAL_LIMIT, 2000)

    def test_region_policy_excludes_only_crimea_and_occupied_east(self):
        self.assertEqual(
            EXCLUDED_UKRAINE_REGIONS,
            {"crimea", "donetsk_occupied", "luhansk_occupied"},
        )
        self.assertEqual(
            excluded_reason(SimpleNamespace(country="Ukraine", ukraine_region="")),
            "",
        )
        self.assertEqual(
            excluded_reason(
                SimpleNamespace(country="Ukraine", ukraine_region="donetsk_occupied")
            ),
            "Ukraine region",
        )
        self.assertEqual(
            excluded_reason(SimpleNamespace(country="Russia", ukraine_region="")),
            "country",
        )


@override_settings(REGISTRATION_REQUIRES_ACCOUNT=False)
class GenmediaNightlyApprovalTests(RegistrationCampaignBase):
    def setUp(self):
        super().setUp()
        self.campaign.slug = CAMPAIGN_SLUG
        self.campaign.requires_approval = True
        self.campaign.save(update_fields=["slug", "requires_approval"])

    def registration(self, *, email, country, ukraine_region=""):
        return CourseRegistration.objects.create(
            campaign=self.campaign,
            course=self.course,
            email=email,
            name="Test Applicant",
            country=country,
            region="",
            ukraine_region=ukraine_region,
            role=CourseRegistration.Role.ML_ENGINEER,
            status=CourseRegistration.Status.PENDING,
        )

    def test_nightly_approval_excludes_restricted_applicants_and_writes_preview(self):
        eligible = self.registration(
            email="eligible@example.test", country="Ukraine", ukraine_region="lviv"
        )
        excluded_country = self.registration(email="country@example.test", country="Belarus")
        excluded_region = self.registration(
            email="region@example.test", country="Ukraine", ukraine_region="crimea"
        )
        berlin_now = datetime(2026, 11, 3, 20, tzinfo=ZoneInfo("Europe/Berlin"))

        with TemporaryDirectory(dir=Path(".tmp")) as scratch:
            destination = Path(scratch) / "nightly.csv"
            mail_before = JobIntent.objects.filter(handler="cb_mail.deliver").count()
            result = approve_pending_registrations(now=berlin_now, output_path=destination)
            rows = list(csv.DictReader(destination.open(encoding="utf-8", newline="")))
            mail_after = JobIntent.objects.filter(handler="cb_mail.deliver").count()

        self.assertEqual(result["approved"], 1)
        self.assertEqual(result["excluded"], 2)
        eligible.refresh_from_db()
        excluded_country.refresh_from_db()
        excluded_region.refresh_from_db()
        self.assertEqual(eligible.status, CourseRegistration.Status.APPROVED)
        self.assertIsNotNone(eligible.auto_approved_at)
        self.assertEqual(excluded_country.status, CourseRegistration.Status.PENDING)
        self.assertEqual(excluded_region.status, CourseRegistration.Status.PENDING)
        self.assertEqual([row["email"] for row in rows], ["eligible@example.test"])
        self.assertEqual(mail_after, mail_before)

    def test_non_approval_utc_window_does_not_touch_pending_rows(self):
        pending = self.registration(email="pending@example.test", country="Germany")
        before_window = datetime(2026, 11, 3, 19, tzinfo=ZoneInfo("Europe/Berlin"))

        result = approve_pending_registrations(
            now=before_window,
            output_path=Path(".tmp") / "not-written.csv",
        )

        self.assertEqual(result["approved"], 0)
        pending.refresh_from_db()
        self.assertEqual(pending.status, CourseRegistration.Status.PENDING)

    def test_limit_stops_approval_and_preserves_pending_rows(self):
        pending = self.registration(email="pending@example.test", country="Germany")
        self.registration(email="already@example.test", country="Germany").status = (
            CourseRegistration.Status.APPROVED
        )
        CourseRegistration.objects.filter(email_normalized="already@example.test").update(
            status=CourseRegistration.Status.APPROVED
        )

        with patch("courses.genmedia_approval.AUTO_APPROVAL_LIMIT", 2):
            result = approve_pending_registrations(
                now=datetime(2026, 11, 3, 20, tzinfo=ZoneInfo("Europe/Berlin")),
                output_path=Path(".tmp") / "not-written.csv",
            )

        self.assertTrue(result["stopped_at_limit"])
        pending.refresh_from_db()
        self.assertEqual(pending.status, CourseRegistration.Status.PENDING)

    def test_latest_preview_is_staff_only_and_downloadable_as_csv(self):
        approved = self.registration(email="approved@example.test", country="Germany")
        approved.status = CourseRegistration.Status.APPROVED
        approved.auto_approved_at = datetime(2026, 11, 3, 19, tzinfo=ZoneInfo("UTC"))
        approved.save(update_fields=["status", "auto_approved_at"])
        staff = User.objects.create_superuser(
            username="preview-admin", email="admin@example.test", password="test-password"
        )
        url = reverse("admin:genmedia_approval_preview")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(staff)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "approved@example.test")
        csv_response = self.client.get(url, {"format": "csv"})
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("approved@example.test", csv_response.content.decode("utf-8"))
        changelist = self.client.get(
            reverse("admin:courses_courseregistration_changelist")
        )
        self.assertContains(changelist, "Preview latest nightly approvals")
