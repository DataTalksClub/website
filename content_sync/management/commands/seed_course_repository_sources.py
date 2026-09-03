"""Give a database the course-repository sources the pinned input describes.

Registered ``ContentSource`` rows decide which repositories exist.  A fresh
database has none, so nothing can be pulled or pushed into it; this command is
how it gets them.  It is idempotent and never rewrites a row that is already
registered, because an operator who changed a source's identity meant it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import IntegrityError

from content_sync.course_repository_registration import (
    CourseRepositoryRegistrationError,
    load_registration_input,
    seed_course_repository_sources,
)


class Command(BaseCommand):
    help = (
        "Register the pinned course-repository content sources. Idempotent: sources "
        "that already exist are reported and left untouched."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--input",
            default=None,
            help=(
                "Registration input JSON. Defaults to content_sync/course_repository_sources.json."
            ),
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        raw_input_path = cast("str | None", options["input"])
        try:
            registrations = load_registration_input(
                Path(raw_input_path).expanduser() if raw_input_path else None
            )
            report = seed_course_repository_sources(registrations)
        except CourseRepositoryRegistrationError as error:
            raise CommandError(f"course repository registration refused: {error}") from None
        except (IntegrityError, ValueError) as error:
            raise CommandError("course repository registration failed") from error
        self.stdout.write(json.dumps({"sources": report}, sort_keys=True))
