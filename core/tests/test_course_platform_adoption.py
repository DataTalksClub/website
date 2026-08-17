import csv
import hashlib
from pathlib import Path
from typing import Protocol, cast

from django.apps import apps
from django.core.management import get_commands, load_command_class
from django.template.loader import get_template
from django.test import SimpleTestCase
from django.urls import resolve, reverse

from scripts.render_course_platform_inventory import (
    SOURCE_APP_LABELS,
    command_entries,
    migration_names,
    render_inventory,
    route_entries,
)
from scripts.verify_course_platform_adoption import verify_cadmin_reference_allowlist

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
    "import_development_course_content": "courses",
    "monitoring_datamailer_health": "data",
    "preview_peer_review_email": "courses",
    "process_datamailer_outbox": "data",
    "reconcile_accounts": "accounts",
    "send_deadline_reminders": "courses",
    "sync_datamailer_contacts": "courses",
    "sync_datamailer_recipient_lists": "courses",
    "upsert_datamailer_templates": "courses",
    "verify_development_course_content": "courses",
}
EXPECTED_APP_MODULES = {
    "accounts": "accounts",
    "api": "api",
    "studio_courses": "studio_courses",
    "courses": "courses",
    "data": "data",
}
EXPECTED_MIGRATION_COUNTS = {
    "accounts": 12,
    "api": 0,
    "studio_courses": 0,
    "courses": 41,
    "data": 5,
}
EXPECTED_UNIFIED_ROUTE_CALLBACK_OVERRIDES: dict[tuple[str, str], str] = {}
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
PROTECTED_COURSE_TEMPLATE_PREFIX = "courses/templates/"
EXPECTED_PROTECTED_COURSE_TEMPLATE_COUNT = 25
EXPECTED_COURSE_LIST_SHA256 = "26e391ffdd2c90b89a668c41118f4a8e43efd2b5dde015097f893aee707984ef"


class TemplateOrigin(Protocol):
    name: str


class ResolvedTemplate(Protocol):
    origin: TemplateOrigin | None


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as input_file:
        return list(csv.DictReader(input_file, delimiter="\t"))


def _protected_course_template_rows() -> list[dict[str, str]]:
    return [
        row
        for row in _read_tsv(MANIFEST_PATH)
        if row["source_path"].startswith(PROTECTED_COURSE_TEMPLATE_PREFIX)
    ]


class CoursePlatformAdoptionContractTests(SimpleTestCase):
    def test_pinned_course_templates_have_no_shadow_and_resolve_to_adopted_files(self):
        rows = _protected_course_template_rows()
        logical_names = [
            row["source_path"].removeprefix(PROTECTED_COURSE_TEMPLATE_PREFIX) for row in rows
        ]

        self.assertEqual(len(rows), EXPECTED_PROTECTED_COURSE_TEMPLATE_COUNT)
        self.assertEqual(len(logical_names), len(set(logical_names)))
        self.assertEqual(
            [
                logical_name
                for logical_name in logical_names
                if (REPO_ROOT / "templates" / logical_name).exists()
            ],
            [],
        )

        for row, logical_name in zip(rows, logical_names, strict=True):
            adopted_destination = (REPO_ROOT / row["destination_path"]).resolve()
            with self.subTest(template=logical_name):
                self.assertTrue(adopted_destination.is_file())
                resolved = cast(ResolvedTemplate, get_template(logical_name))
                origin = resolved.origin
                if origin is None:
                    self.fail(f"template has no loader origin: {logical_name}")
                self.assertEqual(Path(origin.name).resolve(), adopted_destination)

    def test_course_list_template_records_its_design_5a_rebuild_against_the_cmp_source(self):
        """The courses index left the byte-exact CMP copy with issue #179.

        The copied ledger still records the pinned CMP source it started from, and the
        patched ledger records what the repository now ships and why, exactly as it does
        for the adopted base template and stylesheet.
        """

        rows = {
            row["source_path"].removeprefix(PROTECTED_COURSE_TEMPLATE_PREFIX): row
            for row in _protected_course_template_rows()
        }
        row = rows["courses/course_list.html"]
        destination = REPO_ROOT / row["destination_path"]
        patches = {patch["destination_path"]: patch for patch in _read_tsv(PATCH_MANIFEST_PATH)}
        patch = patches[row["destination_path"]]

        self.assertEqual(row["sha256"], EXPECTED_COURSE_LIST_SHA256)
        self.assertNotEqual(patch["sha256"], EXPECTED_COURSE_LIST_SHA256)
        self.assertIn("design 5a", patch["rationale"])
        self.assertEqual(_digest(destination), patch["sha256"])
        self.assertEqual(destination.stat().st_size, int(patch["size_bytes"]))

    def test_all_recorded_copies_exist_with_recorded_integration_state(self):
        copied_rows = _read_tsv(MANIFEST_PATH)
        patch_rows = _read_tsv(PATCH_MANIFEST_PATH)
        patches = {row["destination_path"]: row for row in patch_rows}

        self.assertEqual(len(copied_rows), 768)
        self.assertEqual(
            set(patches),
            {
                "api/openapi/course_schemas.py",
                "api/openapi/content_paths/homeworks.py",
                "api/openapi/content_paths/projects.py",
                "api/openapi/spec.py",
                "api/views/health.py",
                "accounts/admin.py",
                "accounts/auth.py",
                "accounts/forms.py",
                "accounts/models.py",
                "accounts/templates/accounts/login.html",
                "accounts/tests_account_settings.py",
                "accounts/tests_auth.py",
                "accounts/tests_token_admin.py",
                "accounts/views/impersonation.py",
                "accounts/views/login.py",
                "studio_courses/deadline_extension.py",
                "studio_courses/apps.py",
                "studio_courses/templates/studio_courses/_extend_deadline_menu.html",
                "studio_courses/templates/studio_courses/base.html",
                "studio_courses/templates/studio_courses/campaign_form.html",
                "studio_courses/templates/studio_courses/campaign_registrations.html",
                "studio_courses/templates/studio_courses/cloudwatch_dashboard.html",
                "studio_courses/templates/studio_courses/course_admin.html",
                "studio_courses/templates/studio_courses/course_list.html",
                "studio_courses/templates/studio_courses/datamailer_events.html",
                "studio_courses/templates/studio_courses/datamailer_operations.html",
                "studio_courses/templates/studio_courses/enrollment_edit.html",
                "studio_courses/templates/studio_courses/enrollments.html",
                "studio_courses/templates/studio_courses/homework_submission_edit.html",
                "studio_courses/templates/studio_courses/homework_submissions.html",
                "studio_courses/templates/studio_courses/leaderboard_complaints.html",
                "studio_courses/templates/studio_courses/project_submission_edit.html",
                "studio_courses/templates/studio_courses/project_submissions.html",
                "studio_courses/templates/studio_courses/include/campaign_field.html",
                "studio_courses/tests/campaign_view_base.py",
                "studio_courses/tests/homework_view_base.py",
                "studio_courses/tests/impersonation_base.py",
                "studio_courses/tests/project_view_base.py",
                "studio_courses/tests/__init__.py",
                "studio_courses/tests/test_campaign_datamailer_views.py",
                "studio_courses/tests/test_campaign_views.py",
                "studio_courses/tests/test_cloudwatch_dashboard_views.py",
                "studio_courses/tests/test_course_views.py",
                "studio_courses/tests/test_datamailer_views.py",
                "studio_courses/tests/test_homework_views.py",
                "studio_courses/tests/test_homework_submission_edit_views.py",
                "studio_courses/tests/test_impersonation_enrollment_views.py",
                "studio_courses/tests/test_impersonation_stop_views.py",
                "studio_courses/tests/test_impersonation_views.py",
                "studio_courses/tests/test_leaderboard_views.py",
                "studio_courses/tests/test_project_action_views.py",
                "studio_courses/tests/test_project_submission_edit_views.py",
                "studio_courses/tests/test_project_views.py",
                "studio_courses/tests/test_view_models.py",
                "studio_courses/urls.py",
                "studio_courses/views/campaign_forms.py",
                "studio_courses/views/campaigns.py",
                "studio_courses/views/course_admin.py",
                "studio_courses/views/datamailer.py",
                "studio_courses/views/datamailer_operations.py",
                "studio_courses/views/enrollment.py",
                "studio_courses/views/enrollment_complaints.py",
                "studio_courses/views/enrollment_edit.py",
                "studio_courses/views/homework.py",
                "studio_courses/views/homework_submission_edit.py",
                "studio_courses/views/homework_submission_list.py",
                "studio_courses/views/observability.py",
                "studio_courses/views/helpers.py",
                "studio_courses/views/project_submission_edit.py",
                "studio_courses/views/project_submission_list.py",
                "studio_courses/views/projects.py",
                "course_management/datamailer_templates/README.md",
                "course_management/datamailer_templates/definitions/peer_review.py",
                "course_management/datamailer_templates/definitions/scores.py",
                "course_management/datamailer_outbox_dispatch.py",
                "course_management/datamailer_templates/definitions/common.py",
                "course_management/observability/events.py",
                "course_management/settings.py",
                "course_management/urls.py",
                "course_platform_templates/account/logout.html",
                "course_platform_templates/accounts/account_settings.html",
                "course_platform_templates/base.html",
                "course_platform_templates/include/pagination.html",
                "course_platform_templates/socialaccount/authentication_error.html",
                "course_platform_templates/socialaccount/connections.html",
                "course_platform_templates/socialaccount/login_cancelled.html",
                "course_platform_templates/socialaccount/signup.html",
                "courses/migrations/0004_update_correct_answer_indexes.py",
                "courses/migrations/0005_update_answers_with_indexes.py",
                "courses/migrations/0006_course_first_homework_scored.py",
                "courses/models/__init__.py",
                "courses/models/project.py",
                "courses/templates/courses/course.html",
                "courses/templates/courses/course_list.html",
                "courses/templatetags/custom_filters.py",
                "courses/templates/courses/dashboard.html",
                "courses/templates/courses/enrollment.html",
                "courses/templates/courses/leaderboard.html",
                "courses/templates/courses/leaderboard_complaint.html",
                "courses/templates/courses/leaderboard_score_breakdown.html",
                "courses/templates/courses/register.html",
                "courses/templates/courses/user_wrapped.html",
                "courses/templates/courses/wrapped.html",
                "courses/templates/homework/homework.html",
                "courses/templates/homework/stats.html",
                "courses/templates/homework/submissions.html",
                "courses/templates/include/faq_contribution_field.html",
                "courses/templates/include/learning_in_public_links.html",
                "courses/templates/index.html",
                "courses/templates/projects/eval.html",
                "courses/templates/projects/eval_submit.html",
                "courses/templates/projects/list.html",
                "courses/templates/projects/list_all.html",
                "courses/templates/projects/project.html",
                "courses/templates/projects/results.html",
                "courses/templates/projects/stats.html",
                "courses/templates/projects/submissions.html",
                "courses/static/courses.css",
                "courses/static/time_left.js",
                "courses/static/settings_toggles.js",
                "courses/static/user_menu.js",
                "courses/tests/homework_submissions_base.py",
                "courses/tests/homework_submission_validation_base.py",
                "courses/tests/leaderboard_base.py",
                "courses/tests/test_course_list_metadata.py",
                "courses/tests/test_course_list_ordering.py",
                "courses/tests/test_datamailer_certificates.py",
                "courses/tests/test_datamailer_registration.py",
                "courses/tests/test_datamailer_signals.py",
                "courses/tests/test_datamailer_transactional.py",
                "courses/tests/test_homework.py",
                "courses/tests/test_homework_submission_closed.py",
                "courses/tests/test_homework_submissions_hidden_answers.py",
                "courses/tests/test_homework_submissions.py",
                "courses/tests/test_homework_submissions_admin_link.py",
                "courses/tests/test_homework_question_stats.py",
                "courses/tests/test_homework_submission_validation.py",
                "courses/tests/test_leaderboard_score_breakdown_admin.py",
                "courses/tests/test_course_homework_display.py",
                "courses/tests/test_course_links.py",
                "courses/tests/test_noindex.py",
                "courses/tests/test_registration_campaign_course_page.py",
                "courses/tests/test_project_submissions_view.py",
                "courses/tests/test_project_eval.py",
                "courses/tests/test_project_submission_invalid.py",
                "courses/tests/test_project_view.py",
                "courses/tests/test_registration_campaigns.py",
                "courses/tests/test_unit_url_validation.py",
                "courses/views/homework_submissions.py",
                "courses/views/homework_learning_links.py",
                "courses/views/course_calendar_events.py",
                "courses/views/course_list.py",
                "courses/views/course_page_context.py",
                "courses/views/project_submissions.py",
                "courses/views/registration.py",
                "courses/validators/custom_url_validators.py",
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
            "cadmin/__init__.py",
            "cadmin/legacy_urls.py",
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

    def test_legacy_cadmin_references_match_the_reviewed_allowlist(self):
        verify_cadmin_reference_allowlist(REPO_ROOT)

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
                if route.surface == "Public courses" and route.name == "course":
                    example_path = reverse(
                        "course",
                        kwargs={"course_slug": "example-course"},
                    )
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
            "0041_courseregistrationcountsourcerun_and_more",
        )
        self.assertEqual(migration_names("data")[0], "0001_initial")
        self.assertEqual(migration_names("data")[-1], "0005_datamailersendaudit")
