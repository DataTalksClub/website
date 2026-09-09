from typing import Any

from community_base.mail.models import EmailDelivery
from community_base.mail.service import MailConflict, MailError
from django.test import TestCase, override_settings
from django.utils.dateparse import parse_date

from accounts.models import CustomUser
from course_management.package_mail import (
    mail_idempotency_key,
    send_certificate_ready_mail,
    send_package_mail,
    send_registration_confirmation_mail,
)
from courses.models import (
    Cohort,
    Course,
    CourseRegistration,
    Enrollment,
    RegistrationCampaign,
)

FLOW_SETTINGS = {
    "PUBLIC_BASE_URL": "https://courses.example.com",
}


def create_cohort(**overrides):
    course = Course.objects.create(slug="ml", title="ML")
    defaults = {
        "slug": "ml-zoomcamp-2026",
        "title": "ML Zoomcamp 2026",
        "course": course,
        "identifier": "2026",
        "start_date": parse_date("2026-09-14"),
    }
    defaults.update(overrides)
    return Cohort.objects.create(**defaults)


def create_registration(cohort, **overrides):
    campaign = RegistrationCampaign.objects.create(
        slug="ml-zoomcamp",
        title="ML Zoomcamp",
        current_course=cohort,
    )
    defaults = {
        "campaign": campaign,
        "course": cohort,
        "email": "Student@Example.com",
        "name": "Student One",
        "company_name": "Acme Data",
        "country": "Germany",
        "region": "Europe",
        "role": CourseRegistration.Role.DATA_ENGINEER,
        "accepted_newsletter": True,
    }
    defaults.update(overrides)
    return CourseRegistration.objects.create(**defaults)


def create_user(email):
    return CustomUser.objects.create_user(
        username=email.split("@")[0],
        email=email,
        password="password",
    )


class RegistrationConfirmationMailTest(TestCase):
    @override_settings(**FLOW_SETTINGS)
    def test_registration_confirmation_is_recorded_for_the_purpose(self):
        cohort = create_cohort()
        registration = create_registration(cohort)

        delivery = send_registration_confirmation_mail(registration)

        self.assertIsNotNone(delivery)
        self.assertEqual(delivery.purpose, "course-registration-confirmation")
        self.assertEqual(delivery.recipient_email, "student@example.com")
        self.assertEqual(
            delivery.idempotency_key,
            f"registration-confirmation:{registration.pk}",
        )
        self.assertEqual(delivery.category, "email_course_updates")
        self.assertEqual(
            delivery.context_data["course_title"],
            "ML Zoomcamp 2026",
        )
        self.assertEqual(
            delivery.context_data["start_date"],
            "September 14, 2026",
        )
        self.assertTrue(
            delivery.context_data["course_url"].endswith("/cohorts/2026"),
        )

    @override_settings(**FLOW_SETTINGS)
    def test_replaying_the_registration_returns_the_original_delivery(self):
        cohort = create_cohort()
        registration = create_registration(cohort)
        first = send_registration_confirmation_mail(registration)

        second = send_registration_confirmation_mail(registration)

        self.assertEqual(second.pk, first.pk)
        self.assertEqual(EmailDelivery.objects.count(), 1)


class EnrollmentConfirmationMailTest(TestCase):
    @override_settings(**FLOW_SETTINGS)
    def test_enrolling_records_a_confirmation_delivery(self):
        cohort = create_cohort()
        user = create_user("student@example.com")

        with self.captureOnCommitCallbacks(execute=True):
            enrollment = Enrollment.objects.create(student=user, course=cohort)

        delivery = EmailDelivery.objects.get()
        self.assertEqual(delivery.purpose, "enrollment-confirmation")
        self.assertEqual(delivery.recipient_email, "student@example.com")
        self.assertEqual(
            delivery.idempotency_key,
            f"enrollment-confirmation:{enrollment.pk}",
        )
        self.assertEqual(delivery.recipient_user, user)
        self.assertEqual(
            delivery.context_data["cohort_name"],
            "ML Zoomcamp 2026 (2026)",
        )

    @override_settings(**FLOW_SETTINGS)
    def test_updating_an_enrollment_does_not_reconfirm(self):
        cohort = create_cohort()
        user = create_user("student@example.com")
        with self.captureOnCommitCallbacks(execute=True):
            enrollment = Enrollment.objects.create(student=user, course=cohort)

        with self.captureOnCommitCallbacks(execute=True):
            enrollment.display_name = "Student"
            enrollment.save()

        self.assertEqual(EmailDelivery.objects.count(), 1)


class CertificateReadyMailTest(TestCase):
    @override_settings(**FLOW_SETTINGS)
    def test_certificate_ready_is_recorded_for_the_graduate(self):
        cohort = create_cohort()
        user = create_user("graduate@example.com")
        enrollment = Enrollment.objects.create(student=user, course=cohort)
        enrollment.certificate_url = "certificates/student.pdf"
        enrollment.save()

        delivery = send_certificate_ready_mail(enrollment)

        self.assertIsNotNone(delivery)
        self.assertEqual(delivery.purpose, "certificate-ready")
        self.assertEqual(delivery.recipient_email, "graduate@example.com")
        self.assertEqual(
            delivery.idempotency_key,
            f"certificate-available:{enrollment.pk}",
        )
        self.assertEqual(
            delivery.context_data["certificate_url"],
            "https://courses.example.com/certificates/student.pdf",
        )

    @override_settings(**FLOW_SETTINGS)
    def test_enrollment_without_certificate_sends_nothing(self):
        cohort = create_cohort()
        user = create_user("graduate@example.com")
        enrollment = Enrollment.objects.create(student=user, course=cohort)

        self.assertIsNone(send_certificate_ready_mail(enrollment))
        self.assertEqual(EmailDelivery.objects.count(), 0)


class PackageMailAdapterTest(TestCase):
    def test_idempotency_keys_are_sanitized_and_bounded(self):
        key = mail_idempotency_key("deadline-reminder:24h", "enrollment/7", "a b")
        self.assertEqual(key, "deadline-reminder:24h:enrollment-7:a-b")
        self.assertEqual(
            len(mail_idempotency_key("x" * 500)),
            200,
        )

    def test_replay_returns_the_original_delivery(self):
        user = create_user("replay@example.com")
        kwargs = {
            "purpose": "slack-access",
            "to": "replay@example.com",
            "context": {
                "course_title": "ML",
                "slack_url": "https://slack.example.com",
            },
            "idempotency_key": "slack-access:test-1",
            "category": "email_course_updates",
            "user": user,
        }
        first = send_package_mail(**kwargs)

        second = send_package_mail(**kwargs)

        self.assertEqual(second.pk, first.pk)
        self.assertEqual(EmailDelivery.objects.count(), 1)

    def test_conflicting_replay_is_rejected(self):
        create_user("conflict@example.com")
        base: dict[str, Any] = {
            "to": "conflict@example.com",
            "idempotency_key": "slack-access:conflict",
            "context": {
                "course_title": "ML",
                "slack_url": "https://slack.example.com",
            },
        }
        send_package_mail(purpose="slack-access", **base)

        with self.assertRaises(MailConflict):
            send_package_mail(
                purpose="certificate-ready",
                context={
                    "course_title": "ML",
                    "certificate_url": "https://x.example.com",
                },
                to=base["to"],
                idempotency_key=base["idempotency_key"],
            )

    def test_invalid_recipient_is_a_programming_error(self):
        create_user("bad@example.com")
        with self.assertRaises(MailError):
            send_package_mail(
                purpose="slack-access",
                to="not-an-email",
                context={"course_title": "ML", "slack_url": "https://slack.example.com"},
                idempotency_key="slack-access:bad-recipient",
            )
