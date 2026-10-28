from typing import Any

from community_base.jobs.models import JobIntent
from community_base.mail.models import EmailDelivery
from community_base.mail.service import MailConflict, MailError
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.dateparse import parse_date

from accounts.models import User
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
from courses.services.enrollment import enroll_learner

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
    return User.objects.create_user(
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
            delivery.context_data["course_url"].endswith("/2026"),
        )

    @override_settings(**FLOW_SETTINGS)
    def test_replaying_the_registration_returns_the_original_delivery(self):
        cohort = create_cohort()
        registration = create_registration(cohort)
        first = send_registration_confirmation_mail(registration)

        second = send_registration_confirmation_mail(registration)

        self.assertEqual(second.pk, first.pk)
        self.assertEqual(EmailDelivery.objects.count(), 1)


class RegistrationConfirmationRelayContextTests(TestCase):
    """Every confirmation send must satisfy Relay's required template context.

    Relay rejects an empty string for any name in the template's
    ``required_context`` (prod answered 400 validation_error on
    ``context.start_date`` + ``context.verify_email_prompt`` for a cohort
    with no start date and an anonymous registrant). These tests mirror
    that rule against the committed template: with no course at all, and
    with a course that announces no start date, the recorded delivery
    context must still carry a non-empty value for every required name.
    Only ``start_date`` and ``verify_email_prompt`` may stay empty, and
    the template declares exactly those two optional.
    """

    @staticmethod
    def _template():
        from core import mail_templates

        templates = {template.key: template for template in mail_templates.load_templates()}
        return templates["course-registration-confirmation"]

    def _assert_relay_accepts(self, delivery):
        # Mirrors Relay's validate_context_requirements
        # (relay mailing/services/transactional_catalog.py): every required
        # name must be present and neither None nor "". Extra context keys
        # the template never uses are ignored by that validator.
        missing = [
            name
            for name in self._template().required_context
            if not delivery.context_data.get(name)
        ]
        self.assertEqual(missing, [])

    @override_settings(**FLOW_SETTINGS)
    def test_optional_keys_are_exactly_start_date_and_verify_prompt(self):
        template = self._template()
        self.assertEqual(
            template.optional_context,
            ("start_date", "suggested_courses", "verify_email_prompt"),
        )
        self.assertNotIn("start_date", template.required_context)
        self.assertNotIn("verify_email_prompt", template.required_context)

    @override_settings(**FLOW_SETTINGS)
    def test_registration_without_any_course_falls_back_to_campaign(self):
        from courses.registration_confirmation import issue_confirmation_token

        campaign = RegistrationCampaign.objects.create(
            slug="future-camp",
            title="Future Camp",
        )
        registration = CourseRegistration.objects.create(
            campaign=campaign,
            course=None,
            email="future@example.com",
            name="Future Learner",
            country="Germany",
            region="Europe",
            role=CourseRegistration.Role.DATA_ENGINEER,
            accepted_newsletter=True,
        )
        self.assertIsNone(registration.course)
        issue_confirmation_token(registration)

        delivery = send_registration_confirmation_mail(registration)

        self.assertIsNotNone(delivery)
        self.assertEqual(delivery.context_data["course_title"], "Future Camp")
        self.assertTrue(delivery.context_data["course_url"])
        self.assertTrue(delivery.context_data["confirmation_url"])
        self.assertEqual(delivery.context_data["verify_email_prompt"], "")
        self._assert_relay_accepts(delivery)

    @override_settings(**FLOW_SETTINGS)
    def test_registration_with_dateless_course_is_accepted(self):
        from courses.registration_confirmation import issue_confirmation_token

        cohort = create_cohort(start_date=None)
        registration = create_registration(cohort)
        issue_confirmation_token(registration)

        delivery = send_registration_confirmation_mail(registration)

        self.assertIsNotNone(delivery)
        self.assertEqual(delivery.context_data["course_title"], cohort.title)
        self.assertEqual(delivery.context_data["start_date"], "")
        self.assertEqual(delivery.context_data["verify_email_prompt"], "")
        self.assertTrue(delivery.context_data["confirmation_url"])
        self._assert_relay_accepts(delivery)

    @override_settings(**FLOW_SETTINGS)
    def test_cleared_course_falls_back_to_campaign_current_course(self):
        # The write-time backfill sets course from current_course, so a
        # registration with no course but a promoting campaign only exists
        # for rows cleared afterwards (legacy rows, rotation): the
        # send-time fallback must still resolve the current course.
        from courses.registration_confirmation import issue_confirmation_token

        cohort = create_cohort(start_date=None)
        campaign = RegistrationCampaign.objects.create(
            slug="rotating-camp",
            title="Rotating Camp",
            current_course=cohort,
        )
        registration = CourseRegistration.objects.create(
            campaign=campaign,
            course=cohort,
            email="rotating@example.com",
            name="Rotating Learner",
            country="Germany",
            region="Europe",
            role=CourseRegistration.Role.DATA_ENGINEER,
            accepted_newsletter=True,
        )
        CourseRegistration.objects.filter(pk=registration.pk).update(course=None)
        registration.refresh_from_db()
        self.assertIsNone(registration.course)
        issue_confirmation_token(registration)

        delivery = send_registration_confirmation_mail(registration)

        self.assertIsNotNone(delivery)
        self.assertEqual(delivery.context_data["course_title"], cohort.title)
        self.assertTrue(delivery.context_data["course_url"])
        self.assertTrue(delivery.context_data["confirmation_url"])
        self._assert_relay_accepts(delivery)


def enrollment_confirmations():
    return EmailDelivery.objects.filter(purpose="enrollment-confirmation")


def mail_jobs():
    return JobIntent.objects.filter(handler="cb_mail.deliver")


def assert_enrollment_confirmation(test, enrollment, user):
    """One delivery with the enrollment-confirmation contract (#460)."""

    delivery = enrollment_confirmations().get()
    test.assertEqual(delivery.recipient_email, user.email)
    test.assertEqual(
        delivery.idempotency_key,
        f"enrollment-confirmation:{enrollment.pk}",
    )
    test.assertEqual(delivery.category, "email_course_updates")
    test.assertEqual(delivery.recipient_user, user)
    test.assertEqual(
        delivery.context_data["course_title"],
        enrollment.course.title,
    )
    test.assertIn("cohort_name", delivery.context_data)
    test.assertIn("course_url", delivery.context_data)
    return delivery


class EnrollmentConfirmationMailTest(TestCase):
    """The confirmation is an effect of the live enrollment action (#460)."""

    def assert_confirmation_for(self, enrollment, user):
        delivery = assert_enrollment_confirmation(self, enrollment, user)
        self.assertEqual(delivery.recipient_email, "student@example.com")
        self.assertEqual(delivery.context_data["course_title"], "ML Zoomcamp 2026")
        self.assertEqual(
            delivery.context_data["cohort_name"],
            "ML Zoomcamp 2026 (2026)",
        )
        self.assertTrue(delivery.context_data["course_url"].endswith("/2026"))
        return delivery

    @override_settings(**FLOW_SETTINGS)
    def test_creating_an_enrollment_row_sends_no_mail(self):
        cohort = create_cohort()
        user = create_user("student@example.com")

        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            Enrollment.objects.create(student=user, course=cohort)

        self.assertEqual(callbacks, [])
        self.assertEqual(EmailDelivery.objects.count(), 0)
        self.assertEqual(mail_jobs().count(), 0)

    @override_settings(**FLOW_SETTINGS)
    def test_enrolling_records_a_confirmation_delivery_on_commit(self):
        cohort = create_cohort()
        user = create_user("student@example.com")

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            enrollment, created = enroll_learner(user, cohort)
            self.assertEqual(EmailDelivery.objects.count(), 0)

        self.assertTrue(created)
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        self.assert_confirmation_for(enrollment, user)
        self.assertEqual(mail_jobs().count(), 1)

    @override_settings(**FLOW_SETTINGS)
    def test_enrolling_again_returns_the_enrollment_without_mail(self):
        cohort = create_cohort()
        user = create_user("student@example.com")
        existing = Enrollment.objects.create(student=user, course=cohort)

        with self.captureOnCommitCallbacks(execute=True):
            enrollment, created = enroll_learner(user, cohort)

        self.assertFalse(created)
        self.assertEqual(enrollment.pk, existing.pk)
        self.assertEqual(EmailDelivery.objects.count(), 0)

    @override_settings(**FLOW_SETTINGS)
    def test_a_rolled_back_enrollment_sends_nothing(self):
        cohort = create_cohort()
        user = create_user("student@example.com")

        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    enroll_learner(user, cohort)
                    raise RuntimeError("roll back")

        self.assertEqual(Enrollment.objects.count(), 0)
        self.assertEqual(EmailDelivery.objects.count(), 0)
        self.assertEqual(mail_jobs().count(), 0)

    @override_settings(**FLOW_SETTINGS)
    def test_a_learner_without_email_is_enrolled_without_mail(self):
        cohort = create_cohort()
        user = User.objects.create_user(username="no-email", email="", password="password")

        with self.captureOnCommitCallbacks(execute=True):
            _, created = enroll_learner(user, cohort)

        self.assertTrue(created)
        self.assertEqual(Enrollment.objects.filter(student=user).count(), 1)
        self.assertEqual(EmailDelivery.objects.count(), 0)

    @override_settings(**FLOW_SETTINGS)
    def test_first_enrollment_form_post_confirms_and_an_update_does_not(self):
        cohort = create_cohort()
        user = create_user("student@example.com")
        self.client.force_login(user)
        url = reverse(
            "cohort_enrollment",
            kwargs={"course_slug": "ml", "cohort_identifier": "2026"},
        )

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Enrollment.objects.count(), 0)
        self.assertEqual(EmailDelivery.objects.count(), 0)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                url,
                {"display_name": "Student", "display_on_leaderboard": "on"},
            )
        self.assertEqual(response.status_code, 302)
        enrollment = Enrollment.objects.get(student=user, course=cohort)
        self.assert_confirmation_for(enrollment, user)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, {"display_name": "Renamed"})
        self.assertEqual(response.status_code, 302)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.display_name, "Renamed")
        self.assertEqual(EmailDelivery.objects.count(), 1)
        self.assertEqual(mail_jobs().count(), 1)


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
