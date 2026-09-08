"""Operator inventory command for the shared-curriculum rollout (W7).

    uv run --frozen python manage.py shared_curriculum_inventory \
        --course llm-zoomcamp [--decisions decisions.json]

Emits one bounded, content-free JSON inventory of every cohort of the course
family: identifier, delivery, curriculum source, mapped homework, enrollment
and submitted-assessment counts, stable IDs, and route aliases.  With
``--decisions``, validates the operator's reviewed keep-current/become-archive
decisions against the inventory and merges them into the report; any missing,
invalid, or invariant-violating decision stops the command with a bounded
conflict list.  People appear only as counts; nothing here is learner data.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from courses.services.shared_curriculum_inventory import (
    SharedCurriculumInventoryError,
    build_inventory,
    load_decisions,
    validate_decisions,
)


class Command(BaseCommand):
    help = (
        "Build the per-cohort shared-curriculum rollout inventory for one "
        "course family, optionally validating reviewed keep/archive decisions."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--course",
            required=True,
            help="Course family slug, for example llm-zoomcamp.",
        )
        parser.add_argument(
            "--decisions",
            default=None,
            help=(
                "Path to a reviewed decisions JSON file "
                '({"decisions": {<identifier>: "keep-current" | "become-archive"}}).'
            ),
        )

    def handle(self, *args, **options) -> None:
        try:
            inventory = build_inventory(course_slug=options["course"])
            if options["decisions"]:
                decisions = validate_decisions(
                    inventory, load_decisions(options["decisions"])
                )
            else:
                decisions = None
        except SharedCurriculumInventoryError as error:
            raise CommandError(
                "inventory stopped: "
                + json.dumps({"conflicts": error.conflicts}, sort_keys=True)
            ) from error

        report = dict(inventory)
        if decisions is not None:
            report["decisions"] = decisions
        report["decision_coverage"] = (
            "reviewed" if decisions is not None else "pending"
        )
        self.stdout.write(json.dumps(report, sort_keys=True, indent=2))
