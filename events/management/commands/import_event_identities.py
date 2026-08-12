from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from events.identity import EventIdentityError, import_identity_manifest


class Command(BaseCommand):
    help = "Validate and atomically import the reviewed Event identity/alias manifest."

    def add_arguments(self, parser) -> None:
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate/report without changing Event or alias rows (the default).",
        )
        mode.add_argument(
            "--apply",
            action="store_true",
            help="Apply the validated manifest atomically.",
        )
        parser.add_argument("--manifest", type=Path, default=None)

    def handle(self, *args, **options):
        try:
            report = import_identity_manifest(
                path=options["manifest"],
                dry_run=not options["apply"],
            )
        except (EventIdentityError, OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            json.dumps(
                {
                    "events": report.event_total,
                    "aliases": report.alias_total,
                    "events_created": report.events_created,
                    "events_updated": report.events_updated,
                    "aliases_created": report.aliases_created,
                    "replayed": report.replayed,
                    "dry_run": report.dry_run,
                },
                sort_keys=True,
            )
        )
