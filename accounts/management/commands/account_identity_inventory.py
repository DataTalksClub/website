from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from accounts.identity_inventory import account_inventory


class Command(BaseCommand):
    help = "Emit the redacted single-account inventory as JSON."

    def handle(self, *args, **options):
        del args, options
        self.stdout.write(
            json.dumps(
                account_inventory(),
                indent=2,
                sort_keys=True,
            )
        )
