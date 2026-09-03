"""The orchestrator runs the upstream before the thing that reconciles against it.

CMP reconciles: it matches its rows against what the course repositories wrote.
Running it first is not merely out of order, it is a different result -- the
CMP-first arrangement worked only while no cohort was described by both sources.
"""

from __future__ import annotations

from pathlib import Path

from django.test import TestCase

import scripts.prod

PROD_ROOT = Path(scripts.prod.__file__).resolve().parent


class LocalPreparationOrderTests(TestCase):
    """The orchestrator must run the upstream before the thing that reconciles."""

    def test_the_course_repositories_are_pulled_before_cmp_is_reconciled(self) -> None:
        source = (PROD_ROOT.parents[1] / "scripts" / "prepare_local_data.py").read_text(
            encoding="utf-8"
        )
        pull = source.index("pull_course_repositories(")
        cmp_import = source.index("import_cmp_course_content(cmp_source_db")
        self.assertLess(
            pull,
            cmp_import,
            "CMP reconciles against what the repositories wrote, so it runs second",
        )
