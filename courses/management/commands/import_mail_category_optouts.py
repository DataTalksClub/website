"""Import mail category opt-outs from a Datamailer preferences export.

The Datamailer store held the three category opt-outs remotely; D1.2ca moved
them onto the site user. Run this with the JSON export of the Datamailer
contact preferences before the production cutover (D1.3) so existing
opt-outs survive the retirement:

    {"contacts": [{"email": "...", "categories": [
        {"tag": "deadline-reminders", "enabled": false}, ...]}, ...]}
"""

import json

from django.core.management.base import BaseCommand, CommandError

from accounts.email_preferences import CATEGORY_FIELDS
from accounts.models import CustomUser


class Command(BaseCommand):
    help = (
        "Apply Datamailer category opt-outs to the local user fields "
        "from a preferences JSON export."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "export",
            help="Path to the Datamailer preferences JSON export.",
        )

    def handle(self, *args, **options):
        try:
            with open(options["export"], encoding="utf-8") as export_file:
                document = json.load(export_file)
        except (OSError, json.JSONDecodeError) as error:
            raise CommandError(f"cannot read export: {error}") from error

        contacts = document.get("contacts", [])
        updated = 0
        unknown = 0
        for contact in contacts:
            email = (contact.get("email") or "").strip().lower()
            if not email:
                continue
            user = CustomUser.objects.filter(email__iexact=email).first()
            if user is None:
                unknown += 1
                continue
            changed = []
            for category in contact.get("categories", []):
                field = CATEGORY_FIELDS.get(category.get("tag", ""))
                if field is None:
                    continue
                if category.get("enabled") is False:
                    setattr(user, field, False)
                    changed.append(field)
            if changed:
                user.save(update_fields=changed)
                updated += 1

        self.stdout.write(
            f"opt-outs applied for {updated} user(s); "
            f"{unknown} address(es) had no account"
        )
