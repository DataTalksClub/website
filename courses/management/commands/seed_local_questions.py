from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError, CommandParser

from courses.services.local_question_seed import (
    DEFAULT_COHORT_SLUG,
    DEFAULT_HOMEWORK_SLUG,
    LocalQuestionSeedError,
    check_local_question_seed,
    seed_local_questions,
)


class Command(BaseCommand):
    help = "Seed representative homework questions for a local development form."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--cohort-slug",
            default=DEFAULT_COHORT_SLUG,
            help=f"Cohort containing the homework (default: {DEFAULT_COHORT_SLUG}).",
        )
        parser.add_argument(
            "--homework-slug",
            default=DEFAULT_HOMEWORK_SLUG,
            help=f"Homework to seed (default: {DEFAULT_HOMEWORK_SLUG}).",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Validate the environment, target, and question definitions without writing rows.",
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        cohort_slug = str(options["cohort_slug"])
        homework_slug = str(options["homework_slug"])
        try:
            if options["check"]:
                check_local_question_seed(
                    cohort_slug=cohort_slug,
                    homework_slug=homework_slug,
                )
                summary: dict[str, object] = {
                    "checked": True,
                    "cohort_slug": cohort_slug,
                    "homework_slug": homework_slug,
                    "written": False,
                }
            else:
                summary = {
                    **seed_local_questions(
                        cohort_slug=cohort_slug,
                        homework_slug=homework_slug,
                    ).summary(),
                    "written": True,
                }
        except LocalQuestionSeedError as error:
            raise CommandError(str(error)) from None
        self.stdout.write(json.dumps(summary, sort_keys=True))
