from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError, CommandParser

from courses.services.local_course_seed import (
    LocalCourseSeedError,
    assert_catalog_matches_projection,
    assert_local_database,
    load_catalog_specs,
    load_projected_courses,
    seed_local_courses,
)


class Command(BaseCommand):
    help = (
        "Seed the local development database with the pinned public course catalog "
        "so / and /courses show the same real courses."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--check",
            action="store_true",
            help="Validate the environment and the pinned catalog source without writing rows.",
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        try:
            if options["check"]:
                assert_local_database()
                specs = load_catalog_specs()
                assert_catalog_matches_projection(specs, load_projected_courses())
                summary: dict[str, object] = {"checked": len(specs), "written": False}
            else:
                summary = {**seed_local_courses().summary(), "written": True}
        except LocalCourseSeedError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(json.dumps(summary, sort_keys=True))
