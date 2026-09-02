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
from scripts.verify_course_platform_adoption import (
    retired_adoption_destinations,
    verify_cadmin_reference_allowlist,
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
    # The entrance pages draw one button per configured provider, and a fresh
    # local database has none; this writes obviously-fake placeholder apps so
    # the block a reader actually meets can be built and reviewed locally.
    "seed_local_social_providers": "accounts",
    "bootstrap_development_owner": "accounts",
    "datamailer_callback_status": "data",
    "datamailer_campaign": "courses",
    "datamailer_outbox_status": "data",
    "datamailer_send_status": "data",
    "datamailer_status": "courses",
    "import_development_course_content": "courses",
    "monitoring_datamailer_health": "data",
    "prepare_local_course_modules": "courses",
    "preview_peer_review_email": "courses",
    "process_datamailer_outbox": "data",
    "reconcile_accounts": "accounts",
    "seed_local_courses": "courses",
    "seed_local_project_review": "courses",
    "seed_local_questions": "courses",
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
    "courses": 53,
    "data": 5,
}
EXPECTED_COURSES_MIGRATIONS = (
    "0001_initial",
    "0001_squashed_0029",
    "0002_alter_enrollment_student",
    "0003_replace_commas_with_linebreaks_in_possible_answers",
    "0004_update_correct_answer_indexes",
    "0005_update_answers_with_indexes",
    "0006_course_first_homework_scored",
    "0007_enrollment_position_on_leaderboard",
    "0008_remove_answer_student",
    "0009_rename_comments_peerreview_problems_comments_and_more",
    "0010_remove_reviewcriteria_max_score",
    "0011_alter_enrollment_position_on_leaderboard",
    "0012_project_points_for_peer_review_and_more",
    "0013_remove_homework_is_scored_homework_state_and_more",
    "0014_alter_projectsubmission_github_link_and_more",
    "0015_enrollment_certificate_url",
    "0016_enrollment_about_me_enrollment_github_url_and_more",
    "0017_alter_projectsubmission_learning_in_public_links_and_more",
    "0018_course_finished",
    "0019_remove_homework_problems_comments_field_and_more",
    "0020_remove_project_points_to_pass_and_more",
    "0021_course_min_projects_to_pass",
    "0022_projectstatistics",
    "0023_course_visible",
    "0024_alter_question_question_type",
    "0025_add_wrapped_statistics",
    "0026_enrollment_disable_learning_in_public_and_more",
    "0027_homework_instructions_url_project_instructions_url_and_more",
    "0028_leaderboardcomplaint",
    "0029_enrollment_display_public_profile",
    "0030_remove_enrollment_profile_fields",
    "0031_merge_instruction_urls_and_profile_fields",
    "0032_course_end_date_course_registration_url_and_more",
    "0033_projectsubmission_faq_contribution_url_and_more",
    "0034_preserve_submission_timestamps",
    "0035_projectvote",
    "0036_projectsubmission_volunteer_review_only",
    "0037_registrationcampaign_courseregistration",
    "0038_alter_courseregistration_mailchimp_sync_status",
    "0039_remove_courseregistration_mailchimp_error_and_more",
    "0040_courseregistration_company_name",
    "0041_courseregistrationcountsourcerun_and_more",
    "0042_course_schema_bridge",
    "0043_curriculum_and_project_criteria",
    "0044_alter_module_link_alter_unit_link",
    "0045_alter_criteriaresponse_criteria_and_more",
    "0046_cohort_identifier_and_more",
    "0047_alter_cohort_identifier",
    "0048_coursecurriculumimportrun_cohort_source_checksum_and_more",
    "0049_question_source_option_ids",
    "0050_homework_instructions_markdown_unit_content_markdown_and_more",
    "0051_unitreadstate",
    "0052_merge_duplicate_course_families",
)
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

    def test_course_list_template_records_its_design_system_rebuild_against_the_cmp_source(self):
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
        retired_destinations = retired_adoption_destinations(
            REPO_ROOT, (row["destination_path"] for row in copied_rows)
        )
        patches = {
            row["destination_path"]: row
            for row in patch_rows
            if row["destination_path"] not in retired_destinations
        }

        self.assertEqual(len(copied_rows), 768)
        expected_patches = set()
        for row in copied_rows:
            destination_path = row["destination_path"]
            if destination_path in retired_destinations:
                continue
            destination = REPO_ROOT / destination_path
            if not destination.is_file():
                continue
            if (
                destination.stat().st_size != int(row["size_bytes"])
                or _digest(destination) != row["sha256"]
            ):
                expected_patches.add(destination_path)
        self.assertEqual(set(patches), expected_patches)
        self.assertTrue(all(row["rationale"].strip() for row in patches.values()))
        for row in copied_rows:
            destination_path = row["destination_path"]
            if destination_path in retired_destinations:
                continue
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

        self.assertEqual(len(routes), 115)
        for route in routes:
            with self.subTest(route=route.route, name=route.name):
                example_path = route.example_path()
                if route.surface == "Public courses" and route.name == "course_list":
                    example_path = reverse("course_list")
                if route.surface == "Public courses" and route.name == "course":
                    route_kwargs = {"course_slug": "example-course"}
                    if "<slug:cohort_" in route.route:
                        route_kwargs["cohort_year"] = "2026"
                        example_path = reverse("course", kwargs=route_kwargs)
                    else:
                        example_path = reverse("courses:course", kwargs=route_kwargs)
                if route.surface == "Public courses" and not example_path.startswith("/courses"):
                    # ``courses.urls`` is mounted at ``/courses/`` in the unified
                    # URLconf.  The generated inventory intentionally keeps the
                    # source URLconf's route shape, so add the deployment mount
                    # only for this resolver smoke test.
                    example_path = "/courses" + example_path
                match = resolve(example_path)
                self.assertEqual(match.url_name, route.name or None)
                callback_name = f"{match.func.__module__}.{match.func.__name__}"
                expected_callback = EXPECTED_UNIFIED_ROUTE_CALLBACK_OVERRIDES.get(
                    (route.surface, route.name), route.callback
                )
                if (
                    route.surface == "Public courses"
                    and route.name == "course"
                    and "<slug:cohort_" not in route.route
                ):
                    expected_callback = "courses.views.course_aliases.legacy_course_redirect"
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
        self.assertEqual(tuple(migration_names("courses")), EXPECTED_COURSES_MIGRATIONS)
        self.assertEqual(migration_names("courses")[0], "0001_initial")
        self.assertEqual(
            migration_names("courses")[-1],
            "0052_merge_duplicate_course_families",
        )
        self.assertEqual(migration_names("data")[0], "0001_initial")
        self.assertEqual(migration_names("data")[-1], "0005_datamailersendaudit")
