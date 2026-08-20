import json

from django.core.management.base import BaseCommand, CommandError, CommandParser

from courses.services.local_course_seed import LocalCourseSeedError
from courses.services.local_project_review_seed import (
    DEFAULT_COURSE_SLUG,
    DEFAULT_PROJECT_SLUG,
    seed_local_project_review,
)


class Command(BaseCommand):
    help = (
        "Seed a synthetic DE Zoomcamp Project 1 scenario and assign peer reviews "
        "in local/test SQLite only."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--course-slug", default=DEFAULT_COURSE_SLUG)
        parser.add_argument("--project-slug", default=DEFAULT_PROJECT_SLUG)

    def handle(self, *args: object, **options: object) -> None:
        del args
        try:
            result = seed_local_project_review(
                course_slug=str(options["course_slug"]),
                project_slug=str(options["project_slug"]),
            )
        except LocalCourseSeedError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(json.dumps(result.summary(), sort_keys=True))
