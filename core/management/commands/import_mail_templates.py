"""Mirror ``email_templates/`` into the Relay catalog (D1.2a).

Runs in the deploy's migration task after ``sync_relay_schedules``. For each
purpose template the step upserts the draft and publishes a new version only
when the draft differs from what Relay already stores, so a deploy with
unchanged templates publishes nothing. It then records the published catalog
version per purpose (``core.MailTemplatePublication``) for the durable mail
pipeline's version pin.
"""

from django.core.management.base import BaseCommand, CommandError

from core import mail_templates
from core.models import MailTemplatePublication


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
            # "Unchanged" must compare the *published* snapshot, not only
            # the mutable draft: a run that died between PUT and publish
            # leaves a matching draft over a stale published version, and
            # skipping the publish then would keep pinning sends to the
            # retired contract forever.
            if (
                stored is not None
                and not _draft_differs(stored, draft)
                and _published_matches(client, template.key, draft)
            ):
                self.stdout.write(f"unchanged: {template.key}")
                continue
            client.put_template(template.key, draft)
            client.publish_template(template.key)
            self.stdout.write(f"published: {template.key}")
        _record_publications(client, tuple(template.key for template in templates))


def _published_matches(client, key, draft) -> bool:
    """Whether the latest published version carries the shipped contract."""

    versions = client.template_versions(key)
    if not versions:
        return False
    latest = versions[0]
    return (
        latest.get("subject") == draft["subject"]
        and latest.get("markdown_body") == draft["markdown_body"]
        and latest.get("required_context") == draft["required_context"]
    )


def _record_publications(client, keys) -> None:
    """Record the catalog version each shipped purpose resolves to.

    Relay assigns the numbers at publish time and a durable delivery pins
    one of them (``MAIL_TEMPLATE_VERSION_RESOLVER``). A shipped purpose
    with no published version has no contract to pin, so the import fails
    instead of leaving sends on the package's unresolved default 1.
    """

    published = {row["key"]: row.get("latest_version") for row in client.templates()}
    missing = sorted(key for key in keys if published.get(key) is None)
    if missing:
        raise CommandError(
            "Relay published no transactional template version for: " + ", ".join(missing)
        )
    for key in keys:
        MailTemplatePublication.objects.update_or_create(
            key=key, defaults={"version": published[key]}
        )


def _draft_differs(stored: dict, draft: dict) -> bool:
    return any(stored.get(field) != value for field, value in draft.items())
