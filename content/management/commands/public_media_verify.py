"""Compare the configured public media store against ``media.json``.

    uv run python manage.py public_media_verify

Exits non-zero when any recorded object is missing, unreadable, or checksum-mismatched,
or when the store holds an object that has no record.
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from content.media_store import media_store
from content.media_tooling import MediaToolingError, verify_media


class Command(BaseCommand):
    help = "Verify that the configured public media store matches the checked records"

    def add_arguments(self, parser: CommandParser) -> None:
        del parser

    def handle(self, *args: Any, **options: Any) -> None:
        del args, options
        try:
            report = verify_media(store=media_store())
        except MediaToolingError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(json.dumps(report.as_dict(), sort_keys=True))
        if not report.clean:
            raise CommandError("the public media store does not match the checked records")
