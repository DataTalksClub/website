"""Report learner-table duplicates and broken references before a constraint
migration (audit BE-12).  Read-only; IDs and counts only."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand

from courses.services.learner_duplicate_preflight import (
    run_learner_duplicate_preflight,
)


class Command(BaseCommand):
    help = (
        "Read-only preflight for the learner logical identities: reports "
        "duplicate groups and inconsistent references by record/user ID. "
        "Never prints learner payload."
    )

    def handle(self, *args, **options) -> None:
        report = run_learner_duplicate_preflight().summary()
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        if not report["clean"]:
            self.stdout.write(self.style.ERROR("PREFLIGHT: duplicates or broken references found"))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS("PREFLIGHT: clean"))
