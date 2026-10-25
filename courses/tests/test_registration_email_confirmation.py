"""Tests for double opt-in registration email confirmation.

Mirror of CMP c2f7d87 behaviour on the website stack: typo suggestion,
MX checks (incl. null MX, DNS failure non-blocking), pending ->
confirmed transition, token handling (idempotent, expired, invalid),
resend rate limiting, fix-email flow, backfill migration, and the full
register -> pending -> confirm E2E.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import dns.resolver
from django.apps import apps
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from courses.email_validation import (
    DOMAIN_TYPO_MAP,
    domain_has_mx,
    email_domain_has_mx,
    suggest_email_correction,
)
from courses.models import CourseRegistration
from courses.registration_confirmation import (
    EMAIL_CONFIRMATION_MAX_RESENDS,
    confirm_registration_by_token,
    management_token,
)
from courses.tests.registration_campaign_base import RegistrationCampaignBase


def _fake_mx(host="mail.example.net.", preference=10):
    return [SimpleNamespace(exchange=host, preference=preference)]


class TypoSuggestionTests(TestCase):
    def test_common_typos_suggest_correction(self):
        cases = {
            "user@hmail.com": "user@gmail.com",
            "user@gmial.com": "user@gmail.com",
            "user@gmai.com": "user@gmail.com",
            "user@gmail.co": "user@gmail.com",
            "user@gmail.con": "user@gmail.com",
            "user@yahooo.com": "user@yahoo.com",
            "user@hotmial.com": "user@hotmail.com",
            "user@outlok.com": "user@outlook.com",
        }
        for typed, expected in cases.items():
            with self.subTest(typed=typed):
                self.assertEqual(suggest_email_correction(typed), expected)

    def test_intake_examples_all_covered(self):
        for typo in [
            "hmail.com",
            "gmial.com",
            "gmai.com",
            "gmail.co",
            "gmail.con",
            "yahooo.com",
            "hotmial.com",
            "outlok.com",
        ]:
            self.assertIn(typo, DOMAIN_TYPO_MAP)

    def test_clean_domain_returns_none(self):
        self.assertIsNone(suggest_email_correction("user@gmail.com"))
        self.assertIsNone(suggest_email_correction("not-an-email"))
        self.assertIsNone(suggest_email_correction(""))


class MxCheckTests(TestCase):
    def test_null_mx_rejected(self):
        with patch(
            "courses.email_validation._mx_records_for_domain",
            return_value=_fake_mx(host=".", preference=0),
        ):
            self.assertFalse(domain_has_mx("rejects-all.example"))
            self.assertFalse(email_domain_has_mx("user@rejects-all.example"))

    def test_nxdomain_and_noanswer_rejected(self):
        with patch(
            "courses.email_validation._mx_records_for_domain",
            side_effect=dns.resolver.NXDOMAIN(),
        ):
            self.assertFalse(domain_has_mx("no-mail-here.example"))
        with patch(
            "courses.email_validation._mx_records_for_domain",
            side_effect=dns.resolver.NoAnswer(),
        ):
            self.assertFalse(domain_has_mx("no-mx.example"))

    def test_timeout_and_failure_do_not_block(self):
        with patch(
            "courses.email_validation._mx_records_for_domain",
            side_effect=dns.resolver.LifetimeTimeout(),
        ):
            self.assertIsNone(domain_has_mx("flaky-dns.com"))
            self.assertIsNone(email_domain_has_mx("user@flaky-dns.com"))
        with patch(
            "courses.email_validation._mx_records_for_domain",
            side_effect=Exception("dns down"),
        ):
            self.assertIsNone(domain_has_mx("flaky-dns.com"))

    def test_malformed_returns_false(self):
        self.assertFalse(email_domain_has_mx("not-an-email"))
        self.assertFalse(domain_has_mx(""))


class RegistrationPendingFlowTests(RegistrationCampaignBase):
    def test_register_creates_pending_registration(self):
        url = self.campaign_url()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url, self.registration_payload(email="New@Example.com")
            )
        self.assertEqual(response.status_code, 200)
        registration = CourseRegistration.objects.get()
        self.assertFalse(registration.email_confirmed)
        self.assertTrue(registration.email_confirmation_token)
        self.assertIsNotNone(registration.email_confirmation_sent_at)
        self.assertEqual(registration.email_confirmation_resend_count, 1)
        # Pending page: shows the exact address typed and the must-click
        # instruction, plus fix-email and resend options.
        self.assertContains(response, "Check your inbox")
        self.assertContains(response, "New@Example.com")
        self.assertContains(response, "MUST click the confirmation link")
        self.assertContains(response, "Wrong email? Fix it")
        self.assertContains(response, "Resend confirmation email")
        # The secret token is never exposed in the pending page.
        self.assertNotContains(
            response, registration.email_confirmation_token
        )

    def test_typo_address_rejected_with_suggestion(self):
        url = self.campaign_url()
        payload = self.registration_payload(email="user@gmial.com")
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Did you mean user@gmail.com?")
        self.assertEqual(CourseRegistration.objects.count(), 0)

    def test_domain_without_mx_rejected(self):
        url = self.campaign_url()
        payload = self.registration_payload(email="user@no-mx-here.com")
        with patch(
            "courses.email_validation._mx_records_for_domain",
            side_effect=dns.resolver.NXDOMAIN(),
        ):
            response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "does not accept mail")
        self.assertEqual(CourseRegistration.objects.count(), 0)

    def test_dns_failure_does_not_block_registration(self):
        url = self.campaign_url()
        payload = self.registration_payload(email="user@flaky-dns.com")
        with patch(
            "courses.email_validation._mx_records_for_domain",
            side_effect=Exception("dns down"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CourseRegistration.objects.count(), 1)

    def test_duplicate_post_shows_pending_block_with_resend(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.campaign_url(),
                self.registration_payload(email="Again@Example.com"),
            )
        response = self.client.post(
            self.campaign_url(),
            self.registration_payload(email="Again@Example.com"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Check your inbox")
        self.assertContains(response, "Again@Example.com")
        self.assertContains(response, "Resend confirmation email")
        self.assertEqual(CourseRegistration.objects.count(), 1)

    def test_confirm_link_marks_confirmed_idempotently(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.campaign_url(),
                self.registration_payload(email="confirm@example.com"),
            )
        registration = CourseRegistration.objects.get()
        token = registration.email_confirmation_token
        confirm_url = reverse("registration_confirm", kwargs={"token": token})
        response = self.client.get(confirm_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your email is confirmed")
        registration.refresh_from_db()
        self.assertTrue(registration.email_confirmed)
        # Replay is idempotent, not an error.
        replay = self.client.get(confirm_url)
        self.assertEqual(replay.status_code, 200)
        self.assertContains(replay, "already confirmed")

    def test_expired_link_offers_resend(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.campaign_url(),
                self.registration_payload(email="old@example.com"),
            )
        registration = CourseRegistration.objects.get()
        registration.email_confirmation_sent_at = (
            timezone.now() - timedelta(days=8)
        )
        registration.save(update_fields=["email_confirmation_sent_at"])
        token = registration.email_confirmation_token
        response = self.client.get(
            reverse("registration_confirm", kwargs={"token": token})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "expired")
        registration.refresh_from_db()
        self.assertFalse(registration.email_confirmed)

    def test_invalid_link_shows_recovery(self):
        response = self.client.get(
            reverse("registration_confirm", kwargs={"token": "bad-token"})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "invalid")

    def test_resend_rate_limited_by_cooldown(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.campaign_url(),
                self.registration_payload(email="cool@example.com"),
            )
        registration = CourseRegistration.objects.get()
        token = management_token(registration)
        # Immediate resend hits the 60s cooldown.
        response = self.client.post(
            reverse("registration_confirmation_resend"), {"token": token}
        )
        self.assertEqual(response.status_code, 429)

    def test_resend_cap_and_reset(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.campaign_url(),
                self.registration_payload(email="cap@example.com"),
            )
        registration = CourseRegistration.objects.get()
        registration.email_confirmation_resend_count = (
            EMAIL_CONFIRMATION_MAX_RESENDS
        )
        registration.save(update_fields=["email_confirmation_resend_count"])
        token = management_token(registration)
        # Move the clock past the cooldown so the cap (not cooldown) fires.
        registration.email_confirmation_sent_at = timezone.now() - timedelta(
            minutes=5
        )
        registration.save(update_fields=["email_confirmation_sent_at"])
        response = self.client.post(
            reverse("registration_confirmation_resend"), {"token": token}
        )
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many", status_code=429)

    def test_fix_email_rotates_token_and_sends_to_corrected(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.campaign_url(),
                self.registration_payload(email="wrong@example.com"),
            )
        registration = CourseRegistration.objects.get()
        old_token = registration.email_confirmation_token
        old_management = management_token(registration)
        # Wait out the cooldown.
        registration.email_confirmation_sent_at = timezone.now() - timedelta(
            minutes=2
        )
        registration.save(update_fields=["email_confirmation_sent_at"])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("registration_confirmation_fix_email"),
                {"token": old_management, "email": "fixed@example.com"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email updated")
        self.assertContains(response, "fixed@example.com")
        registration.refresh_from_db()
        self.assertEqual(registration.email_normalized, "fixed@example.com")
        self.assertNotEqual(
            registration.email_confirmation_token, old_token
        )
        # Old management capability no longer works (address changed).
        stale = self.client.post(
            reverse("registration_confirmation_resend"),
            {"token": old_management},
        )
        self.assertContains(stale, "invalid", status_code=200)

    def test_fix_email_validates_typo_and_mx(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.campaign_url(),
                self.registration_payload(email="fixme@example.com"),
            )
        registration = CourseRegistration.objects.get()
        registration.email_confirmation_sent_at = timezone.now() - timedelta(
            minutes=2
        )
        registration.save(update_fields=["email_confirmation_sent_at"])
        token = management_token(registration)
        typo = self.client.post(
            reverse("registration_confirmation_fix_email"),
            {"token": token, "email": "user@gmial.com"},
        )
        self.assertContains(typo, "Did you mean")
        with patch(
            "courses.email_validation._mx_records_for_domain",
            side_effect=dns.resolver.NXDOMAIN(),
        ):
            bad = self.client.post(
                reverse("registration_confirmation_fix_email"),
                {"token": token, "email": "user@no-mx-here.com"},
            )
        self.assertContains(bad, "does not accept mail")

    def test_public_recovery_discloses_nothing(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.campaign_url(),
                self.registration_payload(email="private@example.com"),
            )
        response = self.client.post(
            reverse("registration_confirmation_resend"),
            {
                "email": "nobody-else@example.com",
                "campaign_slug": self.campaign.slug,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Check your inbox")

    def test_confirmation_mail_carries_confirm_link_and_hashed_key(self):
        from community_base.mail.models import EmailDelivery

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.campaign_url(),
                self.registration_payload(email="keyed@example.com"),
            )
        delivery = EmailDelivery.objects.get()
        context = delivery.context_data
        self.assertIn("confirmation_url", context)
        self.assertIn("/register/confirm/", context["confirmation_url"])
        first_key = delivery.idempotency_key
        self.assertIn("registration-confirmation:", first_key)
        # Re-issue rotates the token generation in the key.
        registration = CourseRegistration.objects.get()
        registration.email_confirmation_sent_at = timezone.now() - timedelta(
            minutes=2
        )
        registration.save(update_fields=["email_confirmation_sent_at"])
        from courses.registration_confirmation import issue_confirmation_token

        issue_confirmation_token(registration)
        from course_management.package_mail import (
            send_registration_confirmation_mail,
        )

        with self.captureOnCommitCallbacks(execute=True):
            send_registration_confirmation_mail(registration)
        keys = sorted(
            EmailDelivery.objects.values_list("idempotency_key", flat=True)
        )
        self.assertEqual(len(keys), 2)
        self.assertNotEqual(keys[0], keys[1])


class RegistrationConfirmationMigrationTests(TestCase):
    def test_backfill_marks_existing_as_confirmed(self):
        apps.get_model("courses", "CourseRegistration")
        from courses.models import Cohort, RegistrationCampaign
        from django.utils import timezone as _tz

        course = Cohort.objects.create(
            slug="mig-2026", title="Mig 2026", description="x"
        )
        campaign = RegistrationCampaign.objects.create(
            slug="mig-camp",
            title="Mig Camp",
            current_course=course,
        )
        from courses.models import CourseRegistration as Reg

        reg = Reg.objects.create(
            campaign=campaign,
            course=course,
            email="legacy@example.com",
            name="Legacy",
            country="Germany",
            region="Europe",
            role=Reg.Role.DATA_ENGINEER,
            accepted_newsletter=True,
        )
        reg.refresh_from_db()
        self.assertFalse(reg.email_confirmed)
        self.assertIsNone(reg.email_confirmation_token)
        # Run the same update the 0030 backfill migration performs.
        from django.utils import timezone

        Reg.objects.filter(
            email_confirmed=False, email_confirmation_token__isnull=True
        ).update(
            email_confirmed=True,
            email_confirmation_confirmed_at=timezone.now(),
        )
        reg.refresh_from_db()
        self.assertTrue(reg.email_confirmed)
        self.assertIsNotNone(reg.email_confirmation_confirmed_at)


class RegistrationConfirmationProfileTests(RegistrationCampaignBase):
    def test_account_settings_shows_unconfirmed_with_resend(self):
        user = self.create_signed_user()
        self.client.force_login(user)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.campaign_url(), self.blank_optional_logged_in_payload()
            )
        response = self.client.get(reverse("account_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Course registrations")
        self.assertContains(response, "Unconfirmed")
        self.assertContains(response, "Resend confirmation")

    def test_member_home_lists_unconfirmed_with_resend(self):
        user = self.create_signed_user()
        self.client.force_login(user)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.campaign_url(), self.blank_optional_logged_in_payload()
            )
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm your email")

    def test_confirm_by_token_unit_idempotent(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                self.campaign_url(),
                self.registration_payload(email="unit@example.com"),
            )
        registration = CourseRegistration.objects.get()
        token = registration.email_confirmation_token
        _, outcome = confirm_registration_by_token(
            CourseRegistration, token
        )
        self.assertEqual(outcome, "confirmed")
        _, replay = confirm_registration_by_token(
            CourseRegistration, token
        )
        self.assertEqual(replay, "already_confirmed")
        _, invalid = confirm_registration_by_token(
            CourseRegistration, "nope"
        )
        self.assertEqual(invalid, "invalid")
