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
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from core import mail_templates
from core.models import MailTemplatePublication

#: The five DTC purposes the plan commits as the initial catalog, plus the
#: application-review purposes mirroring CMP intake 4d0452a8.
EXPECTED_KEYS = frozenset(
    {
        "application-approved",
        "application-pending-operator",
        "application-rejected",
        "certificate-ready",
        "course-announcement",
        "course-registration-confirmation",
        "deadline-reminder",
        "enrollment-confirmation",
        "homework-score-notification",
        "homework-submission-confirmation",
        "peer-review-assignment",
        "project-score-notification",
        "project-submission-confirmation",
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


class ImportMailTemplatesTests(TestCase):
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

    @staticmethod
    def _seed_published_content(client, transport):
        # Real Relay stores each published version with its rendered
        # contract; the fake's publish only bumps the number. Seed the real
        # shape so the unchanged check compares published content.
        drafts = {template.key: template.draft() for template in mail_templates.load_templates()}
        for key, draft in drafts.items():
            stored = transport.templates[key]
            stored["versions"] = [
                {
                    "template_key": key,
                    "version": stored["latest_version"],
                    "status": "published",
                    "subject": draft["subject"],
                    "markdown_body": draft["markdown_body"],
                    "required_context": draft["required_context"],
                }
            ]

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
        # The catalog answers with the versions it assigned; the mirror
        # records one row per shipped purpose so durable sends pin exactly
        # what a Relay render will validate against.
        self.assertEqual(
            {row.key: row.version for row in MailTemplatePublication.objects.all()},
            {key: 1 for key in EXPECTED_KEYS},
        )

    def test_second_mirror_publishes_nothing(self):
        client, transport = self._client()
        self._run(client)
        self._seed_published_content(client, transport)
        puts_after_first = [call for call in transport.calls if call[0] == "PUT"]

        output = self._run(client)

        self.assertEqual([call for call in transport.calls if call[0] == "PUT"], puts_after_first)
        self.assertNotIn("published:", output)
        for template in mail_templates.load_templates():
            self.assertIn(f"unchanged: {template.key}", output)
        self.assertEqual(
            {row.key: row.version for row in MailTemplatePublication.objects.all()},
            {key: 1 for key in EXPECTED_KEYS},
        )

    def test_draft_matches_the_relay_transactional_shape(self):
        # Relay's transactional template API stores ``markdown_body`` (what
        # a send renders) and has no ``body`` key. Sending ``body`` would
        # blank every template and differ forever, republishing each run.
        for template in mail_templates.load_templates():
            with self.subTest(template=template.key):
                draft = template.draft()
                self.assertEqual(
                    set(draft),
                    {"name", "subject", "markdown_body", "required_context"},
                )
                self.assertEqual(draft["markdown_body"], template.body)

    def test_mirror_is_stable_against_real_relay_stored_shape(self):
        # The fake stores the PUT verbatim; real Relay answers with
        # template_payload (extra keys, ``markdown_body``). Seed the real
        # shape and require the mirror to publish nothing.
        client, transport = self._client()
        self._run(client)
        for key, stored in transport.templates.items():
            transport.templates[key] = {
                "key": key,
                "client": "website",
                "name": stored["name"],
                "description": "",
                "subject": stored["subject"],
                "html_body": "",
                "text_body": "",
                "markdown_body": stored["markdown_body"],
                "category": "",
                "required_context": stored["required_context"],
                "example_context": {},
                "default_sender_id": "",
                "latest_version": 1,
                "is_transactional": True,
                "is_active": True,
            }
        self._seed_published_content(client, transport)
        puts_after_first = [call for call in transport.calls if call[0] == "PUT"]

        output = self._run(client)

        self.assertEqual([call for call in transport.calls if call[0] == "PUT"], puts_after_first)
        self.assertNotIn("published:", output)
        for template in mail_templates.load_templates():
            self.assertIn(f"unchanged: {template.key}", output)

    def test_a_drifted_remote_draft_is_republished(self):
        client, transport = self._client()
        self._run(client)
        self._seed_published_content(client, transport)
        transport.templates["slack-access"]["subject"] = "stale subject"

        output = self._run(client)

        self.assertIn("published: slack-access", output)
        stored = {template["key"]: template for template in client.templates()}
        self.assertNotEqual(stored["slack-access"]["subject"], "stale subject")
        self.assertIn("unchanged: deadline-reminder", output)
        # The republished purpose resolves to the new catalog version; the
        # purposes the mirror left untouched keep their recorded versions.
        self.assertEqual(
            {row.key: row.version for row in MailTemplatePublication.objects.all()}["slack-access"],
            2,
        )
        self.assertEqual(
            {row.key: row.version for row in MailTemplatePublication.objects.all()}[
                "deadline-reminder"
            ],
            1,
        )

    def test_dry_run_never_contacts_relay(self):
        client, transport = self._client()
        output = self._run(client, "--dry-run")

        self.assertEqual(transport.calls, [])
        for template in mail_templates.load_templates():
            self.assertIn(f"would mirror: {template.key}", output)

    def test_a_matching_draft_over_a_stale_published_version_is_republished(self):
        # A run that died between PUT and publish leaves a draft matching
        # the shipped file over a published version carrying the retired
        # contract. The next run must republish from the published
        # snapshot's mismatch, not from the draft's, or sends keep pinning
        # the retired required-context forever.
        client, transport = self._client()
        self._run(client)
        self._seed_published_content(client, transport)
        transport.templates["slack-access"]["versions"][0]["required_context"] = [
            "start_date",
            "verify_email_prompt",
        ]

        output = self._run(client)

        self.assertIn("published: slack-access", output)
        self.assertEqual(
            {row.key: row.version for row in MailTemplatePublication.objects.all()}["slack-access"],
            2,
        )

    def test_a_shipped_purpose_missing_from_the_catalog_fails_the_import(self):
        # A shipped purpose the catalog listing never answers for has no
        # version a send could pin. The import must fail the deploy (exit
        # 23) instead of silently leaving sends on the unresolved default.
        client, transport = self._client()
        self._run(client)
        real_templates = client.templates

        def catalog_without_slack_access():
            return tuple(row for row in real_templates() if row["key"] != "slack-access")

        with mock.patch.object(client, "templates", catalog_without_slack_access):
            with self.assertRaises(CommandError):
                self._run(client)
        # The failed import recorded nothing new: the row is the successful
        # first import's, and the deploy stays failed until Relay answers
        # for every shipped purpose.
        self.assertEqual(
            {row.key: row.version for row in MailTemplatePublication.objects.all()}["slack-access"],
            1,
        )
