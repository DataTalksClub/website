import csv
import hashlib
from pathlib import Path

from django.apps import apps
from django.core.management import get_commands, load_command_class
from django.test import SimpleTestCase
from django.urls import resolve, reverse

from scripts.render_course_platform_inventory import (
    SOURCE_APP_LABELS,
    command_entries,
    migration_names,
    render_inventory,
    route_entries,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ADOPTION_DIR = REPO_ROOT / "_docs/adoption/course-platform"
MANIFEST_PATH = ADOPTION_DIR / "copied-files.tsv"
PATCH_MANIFEST_PATH = ADOPTION_DIR / "integration-patched-files.tsv"
TARGET_INTEGRATION_MANIFEST_PATH = ADOPTION_DIR / "target-owned-compatibility-shims.tsv"
INVENTORY_PATH = ADOPTION_DIR / "behavior-inventory.md"
EXPECTED_COMMANDS = {
    "account_identity_inventory": "accounts",
    "audit_datamailer_recipient_lists": "courses",
    "bootstrap_development_owner": "accounts",
    "datamailer_callback_status": "data",
    "datamailer_campaign": "courses",
    "datamailer_outbox_status": "data",
    "datamailer_send_status": "data",
    "datamailer_status": "courses",
    "monitoring_datamailer_health": "data",
    "preview_peer_review_email": "courses",
    "process_datamailer_outbox": "data",
    "reconcile_accounts": "accounts",
    "send_deadline_reminders": "courses",
    "sync_datamailer_contacts": "courses",
    "sync_datamailer_recipient_lists": "courses",
    "upsert_datamailer_templates": "courses",
}
EXPECTED_APP_MODULES = {
    "accounts": "accounts",
    "api": "api",
    "cadmin": "cadmin",
    "courses": "courses",
    "data": "data",
}
EXPECTED_MIGRATION_COUNTS = {
    "accounts": 12,
    "api": 0,
    "cadmin": 0,
    "courses": 40,
    "data": 5,
}
EXPECTED_UNIFIED_ROUTE_CALLBACK_OVERRIDES = {
    ("Public courses", "course_list"): "content.public_views.course_hub",
}
ORIGINAL_ACCOUNTS_MIGRATIONS = (
    "0001_initial",
    "0002_token",
    "0003_customuser_certificate_name",
    "0004_customuser_dark_mode",
    "0005_backfill_certificate_name_from_enrollment",
    "0006_customuser_country_customuser_region_and_more",
    "0007_customuser_email_deadline_reminders_and_more",
    "0008_customuser_email_course_updates",
    "0009_customuser_preferred_timezone",
    "0010_remove_customuser_email_course_updates_and_more",
)


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as input_file:
        return list(csv.DictReader(input_file, delimiter="\t"))


class CoursePlatformAdoptionContractTests(SimpleTestCase):
    def test_all_recorded_copies_exist_with_recorded_integration_state(self):
        copied_rows = _read_tsv(MANIFEST_PATH)
        patch_rows = _read_tsv(PATCH_MANIFEST_PATH)
        patches = {row["destination_path"]: row for row in patch_rows}

        self.assertEqual(len(copied_rows), 768)
        self.assertEqual(
            set(patches),
            {
                "api/openapi/course_schemas.py",
                "api/views/health.py",
                "accounts/admin.py",
                "accounts/auth.py",
                "accounts/forms.py",
                "accounts/models.py",
                "accounts/templates/accounts/login.html",
                "accounts/tests_account_settings.py",
                "accounts/tests_auth.py",
                "accounts/tests_token_admin.py",
                "accounts/views/login.py",
                "course_management/datamailer_outbox_dispatch.py",
                "course_management/observability/events.py",
                "course_platform_templates/base.html",
                "courses/static/courses.css",
                "data/tests/test_observability.py",
                "scripts/generate_production_like_leaderboard_data.py",
                "scripts/load_rds_export.py",
                "scripts/score_project_dev.py",
            },
        )
        for row in copied_rows:
            destination_path = row["destination_path"]
            destination = REPO_ROOT / destination_path
            expected = patches.get(destination_path, row)
            with self.subTest(destination=destination_path):
                self.assertTrue(destination.is_file())
                self.assertEqual(destination.stat().st_size, int(expected["size_bytes"]))
                self.assertEqual(_digest(destination), expected["sha256"])

    def test_target_owned_compatibility_shims_are_recorded_and_verified(self):
        rows = _read_tsv(TARGET_INTEGRATION_MANIFEST_PATH)
        expected_paths = {
            "website/admin_api_urls.py",
            "website/admin_api_views.py",
        }

        self.assertEqual({row["destination_path"] for row in rows}, expected_paths)
        for row in rows:
            destination_path = row["destination_path"]
            destination = REPO_ROOT / destination_path
            with self.subTest(destination=destination_path):
                self.assertEqual(row["classification"], "target-owned compatibility shim")
                self.assertTrue(row["rationale"].strip())
                self.assertTrue(destination.is_file())
                self.assertEqual(destination.stat().st_size, int(row["size_bytes"]))
                self.assertEqual(_digest(destination), row["sha256"])

    def test_generated_behavior_inventory_is_current(self):
        self.assertEqual(INVENTORY_PATH.read_text(encoding="utf-8"), render_inventory())

    def test_every_adopted_route_resolves_through_the_unified_urlconf(self):
        routes = route_entries()

        self.assertEqual(len(routes), 89)
        for route in routes:
            with self.subTest(route=route.route, name=route.name):
                example_path = route.example_path()
                if route.surface == "Public courses" and route.name == "course_list":
                    example_path = reverse("course_list")
                match = resolve(example_path)
                self.assertEqual(match.url_name, route.name or None)
                callback_name = f"{match.func.__module__}.{match.func.__name__}"
                expected_callback = EXPECTED_UNIFIED_ROUTE_CALLBACK_OVERRIDES.get(
                    (route.surface, route.name), route.callback
                )
                self.assertEqual(callback_name, expected_callback)

    def test_every_adopted_management_command_loads(self):
        registered = get_commands()

        self.assertEqual(
            {entry.name: entry.app for entry in command_entries()},
            EXPECTED_COMMANDS,
        )
        for command_name, app_label in EXPECTED_COMMANDS.items():
            with self.subTest(command=command_name):
                self.assertEqual(registered[command_name], app_label)
                command = load_command_class(app_label, command_name)
                parser = command.create_parser("manage.py", command_name)
                self.assertIn(command_name, parser.format_help())

    def test_original_app_and_migration_identities_are_preserved(self):
        self.assertEqual(set(SOURCE_APP_LABELS), set(EXPECTED_APP_MODULES))
        for app_label, app_module in EXPECTED_APP_MODULES.items():
            with self.subTest(app=app_label):
                self.assertEqual(apps.get_app_config(app_label).name, app_module)
                self.assertEqual(
                    len(migration_names(app_label)),
                    EXPECTED_MIGRATION_COUNTS[app_label],
                )

        self.assertEqual(
            tuple(migration_names("accounts")[:10]),
            ORIGINAL_ACCOUNTS_MIGRATIONS,
        )
        self.assertEqual(
            migration_names("accounts")[-1],
            "0012_backfill_normalized_identity",
        )
        self.assertEqual(migration_names("courses")[0], "0001_initial")
        self.assertEqual(
            migration_names("courses")[-1],
            "0040_courseregistration_company_name",
        )
        self.assertEqual(migration_names("data")[0], "0001_initial")
        self.assertEqual(migration_names("data")[-1], "0005_datamailersendaudit")
