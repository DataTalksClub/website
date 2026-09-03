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
    route_entries,
)
from scripts.verify_course_platform_adoption import (
    verify_cadmin_reference_allowlist,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ADOPTION_DIR = REPO_ROOT / "_docs/adoption/course-platform"
MANIFEST_PATH = ADOPTION_DIR / "copied-files.tsv"
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
EXPECTED_UNIFIED_ROUTE_CALLBACK_OVERRIDES: dict[tuple[str, str], str] = {}
PROTECTED_COURSE_TEMPLATE_PREFIX = "courses/templates/"


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

    def test_legacy_cadmin_references_match_the_reviewed_allowlist(self):
        verify_cadmin_reference_allowlist(REPO_ROOT)

    def test_every_adopted_route_resolves_through_the_unified_urlconf(self):
        routes = route_entries()

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

    def test_original_app_identities_are_preserved(self):
        self.assertEqual(set(SOURCE_APP_LABELS), set(EXPECTED_APP_MODULES))
        for app_label, app_module in EXPECTED_APP_MODULES.items():
            with self.subTest(app=app_label):
                self.assertEqual(apps.get_app_config(app_label).name, app_module)
