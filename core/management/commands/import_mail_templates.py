"""Mirror ``email_templates/`` into the Relay catalog (D1.2a).

Runs in the deploy's migration task after ``sync_relay_schedules``. For each
purpose template the step upserts the draft and publishes a new version only
when the draft differs from what Relay already stores, so a deploy with
unchanged templates publishes nothing.
"""

from django.core.management.base import BaseCommand

from core import mail_templates


def relay_client():
    from community_base.mail.relay import configured_client

    return configured_client()


class Command(BaseCommand):
    help = "Mirror email_templates/ into the Relay template catalog"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the mirror actions without contacting Relay",
        )

    def handle(self, *args, **options):
        templates = mail_templates.load_templates()
        if not templates:
            self.stderr.write("no templates found in MAIL_TEMPLATE_DIR")
            return

        if options["dry_run"]:
            for template in templates:
                context = ", ".join(template.required_context) or "none"
                self.stdout.write(f"would mirror: {template.key} (context: {context})")
            return

        client = relay_client()
        remote = {template["key"]: template for template in client.templates()}
        for template in templates:
            draft = template.draft()
            stored = remote.get(template.key)
            if stored is not None and not _draft_differs(stored, draft):
                self.stdout.write(f"unchanged: {template.key}")
                continue
            client.put_template(template.key, draft)
            client.publish_template(template.key)
            self.stdout.write(f"published: {template.key}")


def _draft_differs(stored: dict, draft: dict) -> bool:
    return any(stored.get(field) != value for field, value in draft.items())
