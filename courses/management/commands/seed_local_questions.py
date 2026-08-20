from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError, CommandParser

from courses.services.local_question_seed import (
    DEFAULT_COHORT_SLUG,
    HOMEWORK_SLUGS,
    LOCAL_QUESTION_SPECS,
    LocalQuestionSeedError,
    check_all_local_question_seed,
    check_local_question_seed,
    seed_all_local_questions,
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
            default=None,
            help="Homework to seed; omit to seed Homeworks 1 through 4.",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Validate the environment, target, and question definitions without writing rows.",
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        cohort_slug = str(options["cohort_slug"])
        homework_slug_option = options["homework_slug"]
        homework_slug = None if homework_slug_option is None else str(homework_slug_option)
        try:
            if homework_slug is None and options["check"]:
                check_all_local_question_seed(cohort_slug=cohort_slug)
                summary: dict[str, object] = {
                    "checked": True,
                    "cohort_slug": cohort_slug,
                    "homework_slugs": list(HOMEWORK_SLUGS),
                    "homeworks": len(HOMEWORK_SLUGS),
                    "questions": len(LOCAL_QUESTION_SPECS),
                    "written": False,
                }
            elif homework_slug is None:
                results = seed_all_local_questions(cohort_slug=cohort_slug)
                summary = {
                    "cohort_slug": cohort_slug,
                    "homework_slugs": list(HOMEWORK_SLUGS),
                    "homeworks": len(results),
                    "questions": sum(result.question_count for result in results),
                    "questions_created": sum(result.questions_created for result in results),
                    "question_types": [
                        question.question_type
                        for result in results
                        for question in result.questions
                    ],
                    "written": True,
                }
            elif options["check"]:
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
