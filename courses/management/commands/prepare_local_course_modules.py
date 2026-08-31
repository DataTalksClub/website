from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError, CommandParser

from courses.services.local_course_modules import (
    LocalCourseModulesError,
    prepare_local_course_modules,
)


class Command(BaseCommand):
    help = (
        "Validate explicit course-repository snapshots and prepare the three reviewed "
        "2026 cohorts as module curricula in local/test SQLite only."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--input",
            required=True,
            dest="manifest_path",
            help=(
                "JSON snapshot manifest. It must name each absolute checkout, full commit, "
                "per-file SHA-256, and aggregate snapshot checksum."
            ),
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Validate all snapshots and cohort selections without writing database rows.",
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        try:
            result = prepare_local_course_modules(
                options["manifest_path"],
                write=not bool(options["check"]),
            )
        except LocalCourseModulesError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(json.dumps(result.summary(), sort_keys=True))
