from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError, CommandParser

from courses.services.development_content_import import (
    DevelopmentContentImportError,
    development_course_content_evidence,
)


class Command(BaseCommand):
    help = "Verify the development course content, route callbacks, and template origins"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--representative-slug", default="de-zoomcamp-2026")

    def handle(self, *args: object, **options: object) -> None:
        del args
        try:
            evidence = development_course_content_evidence(
                representative_slug=options["representative_slug"]
            )
        except DevelopmentContentImportError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(json.dumps(evidence, sort_keys=True))
