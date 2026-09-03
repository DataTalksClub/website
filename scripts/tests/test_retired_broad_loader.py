"""The broad CMP export loader is gone, not merely switched off.

Its ``main()`` was disabled, but its copy plan skipped only ``sqlite_sequence`` and
``django_migrations``, so re-enabling it would have copied live sessions and OAuth
rows straight out of a production export.
"""

from __future__ import annotations

from pathlib import Path

from django.test import TestCase

import scripts.prod

PROD_ROOT = Path(scripts.prod.__file__).resolve().parent


class RetiredBroadLoaderTests(TestCase):
    """The disabled CMP export loader is gone, not merely switched off."""

    def test_the_broad_rds_loader_is_removed(self) -> None:
        repository_root = PROD_ROOT.parents[1]

        self.assertFalse((repository_root / "scripts" / "load_rds_export.py").exists())
        self.assertFalse(
            (repository_root / "courses" / "tests" / "test_load_rds_export_script.py").exists()
        )

    def test_its_retirement_is_recorded_for_the_adoption_ledger(self) -> None:
        """The manifest is derived from upstream, so the target says it went on purpose."""

        from scripts.verify_course_platform_adoption import RETIRED_ADOPTION_DESTINATIONS

        self.assertIn("scripts/load_rds_export.py", RETIRED_ADOPTION_DESTINATIONS)
        self.assertIn("courses/tests/test_load_rds_export_script.py", RETIRED_ADOPTION_DESTINATIONS)
