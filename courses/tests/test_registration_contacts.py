"""Registration consent, leased Relay requests and sanitized failure boundaries."""

import ast
import inspect
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from community_base.jobs.models import JobIntent
from community_base.jobs.runner import run_intent
from django.db import transaction
from django.test import override_settings

from courses.models import CourseRegistration
from courses.services.registration_contacts import HANDLER, queue_registration_contacts
from courses.tests.registration_campaign_base import RegistrationCampaignBase

CONTACT = {
    "contact_id": 19,
    "exists": True,
    "verified": True,
    "verified_at": None,
    "email_validation": {"status": "externally_validated"},
    "global_unsubscribed": False,
    "hard_bounced": False,
    "complained": False,
    "audience": {"status": "subscribed"},
    "client": {"status": "subscribed"},
    "can_send_marketing": True,
    "can_send_transactional": True,
    "tags": ["course-llm-zoomcamp", "course-cohort-llm-zoomcamp-2026"],
}
RELAY_SETTINGS = {
    "SITE_KEY": "dtc",
    "JOBS_BACKEND": "sync",
    "MAIL_BACKEND": "memory",
    "RELAY_BASE_URL": "https://relay.example.invalid",
    "RELAY_API_KEY": "synthetic-contacts-key",
}


@override_settings(COMMUNITY_BASE=RELAY_SETTINGS)
class RegistrationContactsTests(RegistrationCampaignBase):
    def submit(self, **changes):
        payload = self.registration_payload(email="Contact@Example.invalid")
        payload.update(changes)
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.campaign_url(), payload)

    @patch("community_base.mail.relay.RequestsTransport.request")
    def test_registration_reaches_relay_contacts_with_website_scope_and_cmp_payload(self, request):
        request.return_value = Mock(status_code=200)
        request.return_value.json.return_value = CONTACT
        response = self.submit()
        self.assertEqual(response.status_code, 200)
        registration = CourseRegistration.objects.get()
        intent = JobIntent.objects.get(handler=HANDLER)
        self.assertEqual(intent.payload, {"registration_id": registration.pk})
        self.assertEqual(intent.status, JobIntent.Status.SUCCEEDED)
        args, kwargs = request.call_args
        self.assertEqual(args, ("POST", "https://relay.example.invalid/api/contacts"))
        self.assertEqual(
            kwargs["json"],
            {
                "email": "contact@example.invalid",
                "audience": "dtc",
                "client": "dtc-website",
                "status": "subscribed",
                "verified": True,
                "email_validation": {"status": "externally_validated"},
                "tags": CONTACT["tags"],
            },
        )
        self.assertTrue(registration.accepted_newsletter)
        self.assertFalse(registration.accepted_nebius_sharing)
        self.assertEqual(run_intent(intent.pk), "not_claimed")
        request.assert_called_once()

    @patch("courses.services.registration_contacts.notify_operator_alert")
    @patch("community_base.mail.relay.RequestsTransport.request")
    def test_timeout_retains_registration_and_retryable_job_with_log_and_alert(
        self, request, alert
    ):
        private = "private recipient and provider credential"
        request.side_effect = requests.Timeout(private)
        with self.assertLogs("courses.services.registration_contacts", level="ERROR") as logs:
            response = self.submit()
        registration = CourseRegistration.objects.get()
        self.assertEqual(response.status_code, 200)
        intent = JobIntent.objects.get(handler=HANDLER)
        self.assertEqual(intent.status, JobIntent.Status.FAILED)
        self.assertEqual(intent.last_error, "registration_contacts_failed")
        alert.assert_called_once_with(
            "registration_contacts_failed", {"registration_id": registration.pk}
        )
        self.assertNotIn(private, str(logs.output))
        self.assertNotIn(registration.email, str(logs.output))
        self.assertTrue(all(record.exc_info is None for record in logs.records))

    @patch("courses.services.registration_contacts.notify_operator_alert")
    @patch("community_base.jobs.backends.relay.configured_client")
    @override_settings(COMMUNITY_BASE={**RELAY_SETTINGS, "JOBS_BACKEND": "relay"})
    def test_scheduler_submission_failure_is_logged_and_alerted(self, scheduler, alert):
        scheduler.return_value.submit_webhook.side_effect = RuntimeError("private scheduler data")
        with self.assertLogs("courses.services.registration_contacts", level="ERROR") as logs:
            response = self.submit()
        registration = CourseRegistration.objects.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(JobIntent.objects.get(handler=HANDLER).status, JobIntent.Status.PENDING)
        alert.assert_called_once_with(
            "registration_contacts_failed", {"registration_id": registration.pk}
        )
        self.assertNotIn("private", str(logs.output))

    @patch("courses.services.registration_contacts.notify_operator_alert")
    @patch("courses.services.registration_contacts.dispatch_after_commit")
    def test_enqueue_failure_and_alert_failure_do_not_break_submit(self, dispatch, alert):
        dispatch.side_effect = RuntimeError("private queue details")
        alert.side_effect = RuntimeError("private alert details")
        with self.assertLogs("courses.services.registration_contacts", level="ERROR") as logs:
            response = self.submit()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CourseRegistration.objects.count(), 1)
        self.assertIn("registration_contacts_alert_failed", str(logs.output))
        self.assertNotIn("private", str(logs.output))

    @patch("courses.services.registration_contacts.notify_operator_alert")
    @override_settings(COMMUNITY_BASE={**RELAY_SETTINGS, "RELAY_API_KEY": ""})
    def test_missing_config_is_not_silent_or_registration_failure(self, alert):
        with self.assertLogs("courses.services.registration_contacts", level="ERROR"):
            response = self.submit()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(JobIntent.objects.get(handler=HANDLER).status, JobIntent.Status.FAILED)
        alert.assert_called_once()

    @patch("community_base.mail.relay.RequestsTransport.request")
    def test_missing_newsletter_consent_never_saves_or_calls_relay(self, request):
        response = self.submit(accepted_newsletter="")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(CourseRegistration.objects.exists())
        self.assertFalse(JobIntent.objects.filter(handler=HANDLER).exists())
        request.assert_not_called()

    @patch("community_base.mail.relay.RequestsTransport.request")
    def test_application_consent_and_pending_status_are_preserved(self, request):
        request.return_value = Mock(status_code=200)
        request.return_value.json.return_value = CONTACT
        self.campaign.requires_approval = True
        self.campaign.save(update_fields=["requires_approval"])
        self.submit(experience="Synthetic experience", accepted_nebius_sharing="on")
        registration = CourseRegistration.objects.get()
        self.assertEqual(registration.status, CourseRegistration.Status.PENDING)
        self.assertTrue(registration.accepted_newsletter)
        self.assertTrue(registration.accepted_nebius_sharing)
        self.assertEqual(request.call_args.kwargs["json"]["client"], "dtc-website")

    @patch("community_base.mail.relay.RequestsTransport.request")
    def test_rollback_never_calls_relay_and_import_does_not_enqueue(self, request):
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    queue_registration_contacts(42)
                    raise RuntimeError("rollback")
            except RuntimeError:
                pass
        self.assertFalse(JobIntent.objects.filter(handler=HANDLER).exists())
        request.assert_not_called()

    def test_registration_path_has_no_datamailer_imports_or_configuration(self):
        from courses import registration_jobs
        from courses.services import registration_contacts
        from courses.views import registration, registration_form

        # Walk actual first-party imports, including imports inside functions.
        root = Path(__file__).resolve().parents[2]
        pending = [
            Path(inspect.getfile(module))
            for module in (
                registration,
                registration_form,
                registration_contacts,
                registration_jobs,
            )
        ]
        seen = set()
        while pending:
            path = pending.pop()
            if path in seen:
                continue
            seen.add(path)
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or "", *(alias.name for alias in node.names)]
                elif isinstance(node, ast.Attribute):
                    names = [node.attr]
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    # Docstrings may describe retired history, never runtime config.
                    names = [node.value] if node.value.isidentifier() else []
                for name in names:
                    self.assertNotIn("datamailer", name.lower(), f"{path.name}: {name}")
                if isinstance(node, ast.ImportFrom) and node.module:
                    candidate = root / (node.module.replace(".", "/") + ".py")
                    if candidate.is_file():
                        pending.append(candidate)
