"""The email_templates/ source of truth and its Relay mirror (D1.2a).

Every DTC purpose must render from the committed markdown through the
package renderer with nothing more than the documented synthetic context,
and the deploy's import step must publish exactly the drafts those files
define - once, not on every deploy.
"""

from __future__ import annotations

from io import StringIO
from unittest import mock

from community_base.mail.backends.ses_local import TEMPLATE_KEY_PATTERN, render_delivery
from community_base.mail.models import EmailDelivery
from community_base.mail.relay import RelayMailClient
from community_base.testing import FakeRelay
from django.core.management import call_command
from django.test import SimpleTestCase

from core import mail_templates

#: The five DTC purposes the plan commits as the initial catalog.
EXPECTED_KEYS = frozenset(
    {
        "certificate-ready",
        "course-registration-confirmation",
        "deadline-reminder",
        "enrollment-confirmation",
        "homework-score-notification",
        "homework-submission-confirmation",
        "peer-review-assignment",
        "project-score-notification",
        "slack-access",
    }
)


class TemplateCatalogTests(SimpleTestCase):
    def test_the_five_purposes_are_committed_under_valid_keys(self):
        templates = mail_templates.load_templates()
        self.assertEqual({template.key for template in templates}, EXPECTED_KEYS)
        for template in templates:
            with self.subTest(template=template.key):
                self.assertGreater(len(template.subject), 0)
                self.assertGreater(len(template.body), 0)
                self.assertGreater(len(template.name), 0)
                self.assertTrue(TEMPLATE_KEY_PATTERN.fullmatch(template.key))

    def test_every_purpose_declares_its_send_context(self):
        for template in mail_templates.load_templates():
            with self.subTest(template=template.key):
                self.assertGreater(
                    len(template.required_context),
                    0,
                    "a purpose without extra context must say so deliberately",
                )
                self.assertEqual(
                    template.required_context,
                    tuple(sorted(template.required_context)),
                )


class RenderTests(SimpleTestCase):
    def _sentinel_context(self, template):
        context = {"user_name": "Q1Z"}
        for position, key in enumerate(template.required_context, start=2):
            context[key] = f"Q{position}Z"
        return context

    def test_every_purpose_renders_from_email_templates(self):
        for template in mail_templates.load_templates():
            with self.subTest(template=template.key):
                context = self._sentinel_context(template)
                delivery = EmailDelivery(
                    template_key=template.key,
                    recipient_email="learner@example.com",
                )
                rendered = render_delivery(delivery, context)
                combined = f"{rendered.subject}\n{rendered.body_html}"
                for key, sentinel in context.items():
                    self.assertIn(sentinel, combined, f"{{{{ {key} }}}} never rendered")
                self.assertIn("DataTalks.Club", rendered.html)


class ImportMailTemplatesTests(SimpleTestCase):
    def _client(self):
        transport = FakeRelay()
        client = RelayMailClient("https://relay.example.com", "relay-test-key", transport=transport)
        return client, transport

    def _run(self, client, *arguments):
        with mock.patch(
            "core.management.commands.import_mail_templates.relay_client",
            return_value=client,
        ):
            output = StringIO()
            call_command("import_mail_templates", *arguments, stdout=output)
        return output.getvalue()

    def test_first_mirror_publishes_every_purpose(self):
        client, _ = self._client()
        output = self._run(client)

        drafts = {template.key: template.draft() for template in mail_templates.load_templates()}
        self.assertEqual(set(drafts), EXPECTED_KEYS)
        for key, draft in drafts.items():
            self.assertIn(f"published: {key}", output)
            stored = {template["key"]: template for template in client.templates()}[key]
            for field, value in draft.items():
                self.assertEqual(stored[field], value)
        published = client.template_versions("deadline-reminder")
        self.assertEqual(published[0]["version"], 1)

    def test_second_mirror_publishes_nothing(self):
        client, transport = self._client()
        self._run(client)
        puts_after_first = [call for call in transport.calls if call[0] == "PUT"]

        output = self._run(client)

        self.assertEqual([call for call in transport.calls if call[0] == "PUT"], puts_after_first)
        self.assertNotIn("published:", output)
        for template in mail_templates.load_templates():
            self.assertIn(f"unchanged: {template.key}", output)

    def test_a_drifted_remote_draft_is_republished(self):
        client, transport = self._client()
        self._run(client)
        transport.templates["slack-access"]["subject"] = "stale subject"

        output = self._run(client)

        self.assertIn("published: slack-access", output)
        stored = {template["key"]: template for template in client.templates()}
        self.assertNotEqual(stored["slack-access"]["subject"], "stale subject")
        self.assertIn("unchanged: deadline-reminder", output)

    def test_dry_run_never_contacts_relay(self):
        client, transport = self._client()
        output = self._run(client, "--dry-run")

        self.assertEqual(transport.calls, [])
        for template in mail_templates.load_templates():
            self.assertIn(f"would mirror: {template.key}", output)
