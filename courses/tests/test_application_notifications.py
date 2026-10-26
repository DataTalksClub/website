"""Operator and applicant notifications for application-style registration.

Mirror of CMP intake 4d0452a8: operator notice on every new pending
registration (approval campaigns), applicant email on operator
approve/reject (single and bulk admin actions), idempotent per decision,
pending vs approved confirmation copy, and a loud Relay failure path.
"""

from community_base.mail.backends.ses_local import render_delivery
from community_base.mail.models import EmailDelivery
from community_base.mail.service import MailConflict, MailError
from django.contrib import admin
from django.contrib.messages.storage.cookie import CookieStorage
from django.db import transaction
from django.test import RequestFactory, TestCase, override_settings

from course_management.package_mail import (
    send_package_mail,
    send_registration_confirmation_mail,
)
from courses.models import CourseRegistration
from courses.services.application_notifications import (
    DEFAULT_OPERATOR_EMAIL,
    operator_recipients,
    queue_operator_notification,
    review_registration,
)
from courses.tests.registration_campaign_base import RegistrationCampaignBase


def application_payload(**overrides):
    payload = {
        "email": "applicant@example.com",
        "name": "Applicant One",
        "country": "Germany",
        "role": CourseRegistration.Role.ML_ENGINEER,
        "experience": "3 years of ML, shipped two LLM services",
        "years_experience": CourseRegistration.YearsExperience.THREE_TO_SIX,
        "prereq_python": "on",
        "prereq_docker": "on",
        "prereq_http_apis": "on",
        "prereq_cli": "on",
        "linkedin_url": "https://www.linkedin.com/in/applicant-one",
        "github_url": "https://github.com/applicant-one",
        "motivation": "I want to run media models on GPUs in production",
        "comment": "Looking forward to it",
        "accepted_newsletter": "on",
        "accepted_nebius_sharing": "on",
    }
    payload.update(overrides)
    return payload


def enable_approval(campaign):
    campaign.requires_approval = True
    campaign.application_notice = "Limited seats, medium-advanced level."
    campaign.save(update_fields=["requires_approval", "application_notice"])


def operator_deliveries():
    return EmailDelivery.objects.filter(purpose="application-pending-operator")


def approved_deliveries():
    return EmailDelivery.objects.filter(purpose="application-approved")


def rejected_deliveries():
    return EmailDelivery.objects.filter(purpose="application-rejected")


class OperatorRecipientTests(TestCase):
    def test_default_operator_is_always_retained(self):
        with self.settings(APPLICATION_NOTIFICATION_OPERATORS=[]):
            self.assertEqual(operator_recipients(), [DEFAULT_OPERATOR_EMAIL])
        self.assertEqual(DEFAULT_OPERATOR_EMAIL, "alexey@datatalks.club")

    @override_settings(
        APPLICATION_NOTIFICATION_OPERATORS=[
            "  Ops@Example.com ",
            "ops@example.com",
            "review@example.com",
            "",
            "alexey@datatalks.club",
        ]
    )
    def test_operator_list_is_normalized_and_deduplicated(self):
        self.assertEqual(
            operator_recipients(),
            [
                "alexey@datatalks.club",
                "ops@example.com",
                "review@example.com",
            ],
        )

    @override_settings(APPLICATION_NOTIFICATION_OPERATORS="one@example.com, two@example.com")
    def test_comma_separated_operator_setting_is_supported(self):
        self.assertEqual(
            operator_recipients(),
            [
                "alexey@datatalks.club",
                "one@example.com",
                "two@example.com",
            ],
        )


@override_settings(REGISTRATION_REQUIRES_ACCOUNT=False)
class OperatorNotificationTests(RegistrationCampaignBase):
    def setUp(self):
        super().setUp()
        enable_approval(self.campaign)

    def post_application(self, **overrides):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.campaign_url(),
                application_payload(**overrides),
            )
        self.assertEqual(response.status_code, 200)
        return response, CourseRegistration.objects.get(
            email_normalized=application_payload(**overrides)["email"].lower()
        )

    @override_settings(
        APPLICATION_NOTIFICATION_OPERATORS=["ops@example.com"],
        PUBLIC_BASE_URL="https://courses.example.com",
    )
    def test_pending_application_notifies_default_and_configured_operators_once(self):
        _, registration = self.post_application()

        deliveries = list(operator_deliveries().order_by("recipient_email"))
        self.assertEqual(
            [delivery.recipient_email for delivery in deliveries],
            ["alexey@datatalks.club", "ops@example.com"],
        )
        keys = {delivery.idempotency_key for delivery in deliveries}
        self.assertEqual(len(keys), 2)
        for delivery in deliveries:
            self.assertTrue(
                delivery.idempotency_key.startswith(
                    f"application-pending-operator:{registration.pk}:"
                )
            )
            context = delivery.context_data
            self.assertEqual(context["applicant_email"], "applicant@example.com")
            self.assertEqual(context["applicant_name"], "Applicant One")
            self.assertEqual(context["campaign_title"], self.campaign.title)
            self.assertIn(self.campaign.slug, context["review_url"])
            self.assertIn("status=pending", context["review_url"])
            self.assertTrue(context["review_url"].startswith("https://"))

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_operator_notification_replay_sends_nothing_new(self):
        _, registration = self.post_application()

        before = list(operator_deliveries().values_list("pk", flat=True))
        again = queue_operator_notification(registration)

        self.assertEqual([delivery.pk for delivery in again], before)
        self.assertEqual(operator_deliveries().count(), len(before))

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_open_campaign_registrations_notify_no_operator(self):
        self.campaign.requires_approval = False
        self.campaign.save(update_fields=["requires_approval"])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.campaign_url(), self.registration_payload())
        self.assertEqual(response.status_code, 200)
        registration = CourseRegistration.objects.get()
        self.assertEqual(registration.status, CourseRegistration.Status.APPROVED)
        self.assertEqual(operator_deliveries().count(), 0)

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_rolled_back_registration_notifies_nobody(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                registration = CourseRegistration.objects.create(
                    campaign=self.campaign,
                    email="rollback@example.com",
                    name="Rollback",
                    country="Germany",
                    region="Europe",
                    role=CourseRegistration.Role.ML_ENGINEER,
                )
                queue_operator_notification(registration)
                raise RuntimeError("roll back")

        self.assertEqual(CourseRegistration.objects.count(), 0)
        self.assertEqual(operator_deliveries().count(), 0)

    @override_settings(
        APPLICATION_NOTIFICATION_OPERATORS=["not-an-email"],
        PUBLIC_BASE_URL="https://courses.example.com",
    )
    def test_invalid_operator_address_fails_loudly_without_silent_send(self):
        # A broken operator address must propagate instead of being
        # swallowed: the registration still exists, but no operator
        # delivery was recorded.
        with self.assertRaises(MailError):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(
                    self.campaign_url(),
                    application_payload(email="loud@example.com"),
                )
        self.assertTrue(
            CourseRegistration.objects.filter(email_normalized="loud@example.com").exists()
        )
        self.assertEqual(operator_deliveries().count(), 0)


class ReviewDecisionTests(RegistrationCampaignBase):
    def setUp(self):
        super().setUp()
        enable_approval(self.campaign)
        self.registration = CourseRegistration.objects.create(
            campaign=self.campaign,
            course=self.course,
            email="Applicant@Example.com",
            name="Applicant One",
            country="Germany",
            region="Europe",
            role=CourseRegistration.Role.ML_ENGINEER,
            status=CourseRegistration.Status.PENDING,
        )

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_approve_notifies_applicant_once(self):
        registration, changed, delivery = review_registration(
            self.registration, CourseRegistration.Status.APPROVED
        )

        self.assertTrue(changed)
        self.assertEqual(registration.status, CourseRegistration.Status.APPROVED)
        self.assertEqual(delivery.purpose, "application-approved")
        self.assertEqual(delivery.recipient_email, "applicant@example.com")
        self.assertEqual(
            delivery.idempotency_key,
            f"application-approved:{self.registration.pk}",
        )
        self.assertIn("course_url", delivery.context_data)
        self.assertIn("login_url", delivery.context_data)
        self.assertEqual(approved_deliveries().count(), 1)

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_repeated_approve_replays_the_original_delivery(self):
        _, _, first = review_registration(self.registration, CourseRegistration.Status.APPROVED)
        registration, changed, second = review_registration(
            self.registration, CourseRegistration.Status.APPROVED
        )

        self.assertFalse(changed)
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(approved_deliveries().count(), 1)

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_changed_decision_sends_anew(self):
        review_registration(self.registration, CourseRegistration.Status.APPROVED)
        registration, changed, delivery = review_registration(
            self.registration, CourseRegistration.Status.REJECTED
        )

        self.assertTrue(changed)
        self.assertEqual(registration.status, CourseRegistration.Status.REJECTED)
        self.assertEqual(delivery.purpose, "application-rejected")
        self.assertEqual(approved_deliveries().count(), 1)
        self.assertEqual(rejected_deliveries().count(), 1)

    def test_invalid_decision_is_rejected(self):
        with self.assertRaises(ValueError):
            review_registration(self.registration, CourseRegistration.Status.PENDING)
        self.assertEqual(approved_deliveries().count(), 0)
        self.assertEqual(rejected_deliveries().count(), 0)

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_rolled_back_decision_sends_nothing(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                review_registration(self.registration, CourseRegistration.Status.APPROVED)
                raise RuntimeError("roll back")

        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, CourseRegistration.Status.PENDING)
        self.assertEqual(approved_deliveries().count(), 0)

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_conflicting_replay_key_is_rejected_loudly(self):
        review_registration(self.registration, CourseRegistration.Status.APPROVED)
        with self.assertRaises(MailConflict):
            send_package_mail(
                purpose="application-rejected",
                to="applicant@example.com",
                context={"course_title": "Other"},
                idempotency_key=f"application-approved:{self.registration.pk}",
                category="email_course_updates",
            )

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_admin_bulk_approve_notifies_each_applicant_once(self):
        second = CourseRegistration.objects.create(
            campaign=self.campaign,
            course=self.course,
            email="second@example.com",
            name="Second",
            country="Germany",
            region="Europe",
            role=CourseRegistration.Role.ML_ENGINEER,
            status=CourseRegistration.Status.PENDING,
        )
        model_admin = admin.site._registry[CourseRegistration]
        request = RequestFactory().post("/admin/")
        request._messages = CookieStorage(request)  # type: ignore[attr-defined]

        queryset = CourseRegistration.objects.filter(pk__in=[self.registration.pk, second.pk])
        getattr(model_admin, "approve_registrations")(request, queryset)

        self.assertEqual(
            CourseRegistration.objects.filter(status=CourseRegistration.Status.APPROVED).count(),
            2,
        )
        self.assertEqual(approved_deliveries().count(), 2)
        # Re-running the bulk action replays instead of double-sending.
        getattr(model_admin, "approve_registrations")(request, queryset)
        self.assertEqual(approved_deliveries().count(), 2)

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_admin_bulk_reject_notifies(self):
        model_admin = admin.site._registry[CourseRegistration]
        request = RequestFactory().post("/admin/")
        request._messages = CookieStorage(request)  # type: ignore[attr-defined]

        getattr(model_admin, "reject_registrations")(
            request, CourseRegistration.objects.filter(pk=self.registration.pk)
        )

        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, CourseRegistration.Status.REJECTED)
        self.assertEqual(rejected_deliveries().count(), 1)

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_admin_single_edit_status_change_notifies(self):
        model_admin = admin.site._registry[CourseRegistration]
        request = RequestFactory().post("/admin/")
        request._messages = CookieStorage(request)  # type: ignore[attr-defined]

        self.registration.status = CourseRegistration.Status.APPROVED
        model_admin.save_model(request, self.registration, form=None, change=True)

        self.assertEqual(approved_deliveries().count(), 1)

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_admin_single_edit_without_status_change_sends_nothing(self):
        model_admin = admin.site._registry[CourseRegistration]
        request = RequestFactory().post("/admin/")
        request._messages = CookieStorage(request)  # type: ignore[attr-defined]

        self.registration.name = "Renamed"
        model_admin.save_model(request, self.registration, form=None, change=True)

        self.assertEqual(approved_deliveries().count(), 0)
        self.assertEqual(rejected_deliveries().count(), 0)


@override_settings(REGISTRATION_REQUIRES_ACCOUNT=False)
class ConfirmationCopyTests(RegistrationCampaignBase):
    """Pending vs approved confirmation copy (AC3).

    The confirmed campaign-page state is pending-aware ("Application
    received", no implication of acceptance) on approval campaigns and
    keeps the current "you're registered" text otherwise; the
    registration confirmation email carries the same distinction.
    """

    def setUp(self):
        super().setUp()
        enable_approval(self.campaign)

    def confirmed_registration(self, **overrides):
        defaults = {
            "campaign": self.campaign,
            "course": self.course,
            "email": "applicant@example.com",
            "name": "Applicant One",
            "country": "Germany",
            "region": "Europe",
            "role": CourseRegistration.Role.ML_ENGINEER,
            "status": CourseRegistration.Status.PENDING,
            "email_confirmed": True,
        }
        defaults.update(overrides)
        return CourseRegistration.objects.create(**defaults)

    def test_pending_post_submit_screen_promises_review_not_acceptance(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.campaign_url(), application_payload())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "your application will be reviewed")
        self.assertNotContains(response, "You are registered")

    def test_open_post_submit_screen_has_no_review_copy(self):
        self.campaign.requires_approval = False
        self.campaign.save(update_fields=["requires_approval"])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.campaign_url(), self.registration_payload())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Check your inbox to finish registering")
        self.assertNotContains(response, "will be reviewed")

    def test_confirmed_pending_state_says_application_received(self):
        user = self.create_signed_user()
        self.client.force_login(user)
        self.confirmed_registration(user=user, email=user.email)

        response = self.client.get(self.campaign_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Application received")
        self.assertContains(response, "acceptance is not automatic")
        self.assertNotContains(response, "You are registered")

    def test_confirmed_approved_state_says_you_are_registered(self):
        self.campaign.requires_approval = False
        self.campaign.save(update_fields=["requires_approval"])
        user = self.create_signed_user()
        self.client.force_login(user)
        self.confirmed_registration(
            user=user,
            email=user.email,
            status=CourseRegistration.Status.APPROVED,
        )

        response = self.client.get(self.campaign_url())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You are already registered")
        self.assertNotContains(response, "Application received")

    def rendered_confirmation_body(self, registration):
        from courses.registration_confirmation import issue_confirmation_token

        issue_confirmation_token(registration)
        with transaction.atomic():
            delivery = send_registration_confirmation_mail(registration)
        rendered = render_delivery(delivery, dict(delivery.context_data))
        return delivery, rendered.body_html

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_pending_confirmation_email_promises_review(self):
        registration = self.confirmed_registration(email_confirmed=False)

        delivery, body = self.rendered_confirmation_body(registration)

        self.assertTrue(delivery.context_data["requires_approval"])
        self.assertIn("wait for review", body)
        self.assertIn("email you once it is approved", body)

    @override_settings(PUBLIC_BASE_URL="https://courses.example.com")
    def test_open_confirmation_email_confirms_a_place(self):
        self.campaign.requires_approval = False
        self.campaign.save(update_fields=["requires_approval"])
        registration = self.confirmed_registration(
            email="student@example.com",
            email_confirmed=False,
            status=CourseRegistration.Status.APPROVED,
        )

        delivery, body = self.rendered_confirmation_body(registration)

        self.assertFalse(delivery.context_data["requires_approval"])
        self.assertNotIn("wait for review", body)


class RelayFailurePathTests(TestCase):
    def test_invalid_recipient_is_a_loud_programming_error(self):
        with self.assertRaises(MailError):
            send_package_mail(
                purpose="application-approved",
                to="not-an-email",
                context={
                    "course_title": "Course",
                    "course_url": "https://x",
                    "login_url": "https://x",
                    "applicant_email": "a@x",
                },
                idempotency_key="application-approved:failure-probe",
                category="email_course_updates",
            )
        self.assertEqual(approved_deliveries().count(), 0)
