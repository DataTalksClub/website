"""The absent-model facade and the present-model contract use the pinned source."""

import os
import subprocess
import sys
from pathlib import Path

from django.apps import apps
from django.test import SimpleTestCase

from accounts.identity_inventory import (
    ACCOUNT_RELATIONS,
    account_inventory,
    stale_account_relations,
    unclassified_account_relations,
)


class CourseEnrollmentCatalogTests(SimpleTestCase):
    def test_installed_catalog_and_inventory_agree(self):
        model = apps.get_app_config("cb_curriculum").models.get("courseenrollment")
        keys = [spec.key for spec in ACCOUNT_RELATIONS]
        expected = 0
        if model is not None:
            expected = 1
        self.assertEqual(keys.count("cb_curriculum.CourseEnrollment.user"), expected)
        self.assertEqual(stale_account_relations(), [])
        self.assertEqual(unclassified_account_relations(), [])
        inventory = account_inventory()
        self.assertEqual(inventory, account_inventory())
        self.assertEqual(len(inventory["dependent_relations"]), len(keys))

    def test_present_model_history_contract_in_clean_process(self):
        environment = os.environ.copy()
        environment.pop("DTC_TEST_RUN_ID", None)
        environment.pop("DTC_TEST_OWNER_TOKEN", None)
        environment.pop("DTC_SQLITE_PATH", None)
        environment["DJANGO_SETTINGS_MODULE"] = "website.settings.test"
        result = subprocess.run(
            [
                sys.executable,
                "manage.py",
                "test",
                "accounts.tests.course_enrollment_identity_fixture",
                "--noinput",
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Ran 5 tests", result.stderr)
