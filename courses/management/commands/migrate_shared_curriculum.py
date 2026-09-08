"""Operator command for the shared-curriculum backfill (W2/W7).

    uv run --frozen python manage.py migrate_shared_curriculum \
        --course llm-zoomcamp --dry-run

The default is a dry run; ``--apply`` performs the idempotent backfill.  The
report is bounded and content-free: counts, stable IDs, and conflict rows
only -- never learner identities beyond counts, never Markdown or emails.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from courses.services.migrate_shared_curriculum import (
    SharedCurriculumBackfillError,
    backfill_shared_curriculum,
)


class Command(BaseCommand):
    help = "Backfill the one current shared curriculum graph from cohort-owned rows."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--course",
            required=True,
            help="Course family slug, for example llm-zoomcamp.",
        )
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute the run inside a rolled-back transaction and report.",
        )
        group.add_argument(
            "--apply",
            action="store_true",
            help="Apply the idempotent backfill. Conflicts still stop the run.",
        )

    def handle(self, *args, **options) -> None:
        apply = options["apply"]
        try:
            result = backfill_shared_curriculum(
                course_slug=options["course"], apply=apply
            )
        except SharedCurriculumBackfillError as error:
            raise CommandError(
                "backfill stopped: "
                + json.dumps({"conflicts": error.conflicts}, sort_keys=True)
            ) from error
        report = {
            "mode": "apply" if apply else "dry-run",
            "course": result.course.slug,
            "shared_curriculum_id": result.shared_curriculum_id,
            "counts": dict(result.counts),
            "conflicts": result.conflicts,
        }
        self.stdout.write(json.dumps(report, sort_keys=True, indent=2))
