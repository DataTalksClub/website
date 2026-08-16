from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path

from django.template.loader import get_template
from django.test import SimpleTestCase, TestCase

from content.public_data import public_projection

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ADOPTED_BASE = REPOSITORY_ROOT / "course_platform_templates/base.html"
ADOPTED_CSS = REPOSITORY_ROOT / "courses/static/courses.css"
TARGET_SHELL_CSS = REPOSITORY_ROOT / "core/static/core/site_shell.css"
COPIED_LEDGER = REPOSITORY_ROOT / "_docs/adoption/course-platform/copied-files.tsv"
PATCHED_LEDGER = REPOSITORY_ROOT / "_docs/adoption/course-platform/integration-patched-files.tsv"
PINNED_BASE_SHA256 = "f51666391e33aec905f43312215bfd82094bfb0088414594f40bcbdfc21560b8"
PINNED_CSS_SHA256 = "282ed7b15df2502a8d4c2e9cd45ef1f8e92771835243d3cbc590f78db9ed5f8f"
# The homepage left this contract with the design 5a rebuild (issue #179), and the events
# index (/events and /events/past) left it with the design 5a mockup 6c rebuild: both own
# their inline stylesheet and none of the adopted CMP shell.  The event detail page still
# uses it, so the assertions that protect that page stay exactly as they were.
SCOPED_TEMPLATES = (
    REPOSITORY_ROOT / "templates/public/_event_meta.html",
    REPOSITORY_ROOT / "templates/public/event_detail.html",
)
FEATURED_EVENT_TITLE = "AI Dev Tools Zoomcamp 2026 Course Launch"


def _ledger_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HomeEventsCmpRepositoryContractTests(SimpleTestCase):
    def test_logical_base_fails_closed_to_the_adopted_cmp_origin(self) -> None:
        template = get_template("base.html")
        origin = getattr(template, "origin", None)

        if origin is None:
            self.fail("logical base template has no inspectable origin")
        self.assertEqual(Path(origin.name).resolve(), ADOPTED_BASE.resolve())
        self.assertFalse((REPOSITORY_ROOT / "templates/base.html").exists())
        self.assertFalse((REPOSITORY_ROOT / "core/static/core/site.css").exists())

    def test_adopted_base_and_css_remain_bound_to_pinned_cmp_provenance(self) -> None:
        copied_by_destination = {
            row["destination_path"]: row for row in _ledger_rows(COPIED_LEDGER)
        }
        patched_by_destination = {
            row["destination_path"]: row for row in _ledger_rows(PATCHED_LEDGER)
        }

        self.assertEqual(
            copied_by_destination["course_platform_templates/base.html"]["sha256"],
            PINNED_BASE_SHA256,
        )
        self.assertEqual(
            copied_by_destination["courses/static/courses.css"]["sha256"],
            PINNED_CSS_SHA256,
        )
        self.assertEqual(
            _sha256(ADOPTED_BASE),
            patched_by_destination["course_platform_templates/base.html"]["sha256"],
        )
        self.assertEqual(
            _sha256(ADOPTED_CSS),
            patched_by_destination["courses/static/courses.css"]["sha256"],
        )

    def test_scoped_templates_use_cmp_primitives_without_a_parallel_style_system(self) -> None:
        failures: list[str] = []
        for path in SCOPED_TEMPLATES:
            source = path.read_text(encoding="utf-8")
            relative = path.relative_to(REPOSITORY_ROOT)
            if path.name == "event_detail.html":
                if not source.startswith('{% extends "core/base.html" %}'):
                    failures.append(f"{relative}: does not extend core/base.html")
            for forbidden in (
                "core/site.css",
                "<style",
                "style=",
                "sm:flex-row",
                "md:grid-cols-",
            ):
                if forbidden in source:
                    failures.append(f"{relative}: contains {forbidden}")
            if re.search(r"#[0-9a-fA-F]{3,8}\b", source):
                failures.append(f"{relative}: contains a literal color")

        self.assertEqual(failures, [])

    def test_catalog_surfaces_keep_cmp_hero_and_divided_row_composition(self) -> None:
        detail = (REPOSITORY_ROOT / "templates/public/event_detail.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('class="max-w-3xl"', detail)

    def test_mobile_target_fix_covers_every_scoped_cmp_button(self) -> None:
        detail = (REPOSITORY_ROOT / "templates/public/event_detail.html").read_text(
            encoding="utf-8"
        )
        shell_css = TARGET_SHELL_CSS.read_text(encoding="utf-8")

        self.assertIn("home-events-cmp-surface", detail)
        target_rule = re.search(
            r"body\.home-events-cmp-surface main \.primer-button\s*\{(?P<body>[^}]*)\}",
            shell_css,
        )
        if target_rule is None:
            self.fail("scoped mobile CMP button rule is missing")
        self.assertIn("min-height: 2.75rem", target_rule.group("body"))
        self.assertIn("min-width: 2.75rem", target_rule.group("body"))
        self.assertNotIn(
            "body.home-events-cmp-surface .home-hero-actions .primer-button",
            shell_css,
        )
        self.assertIn(
            "body.home-events-cmp-surface #dark-mode-toggle.user-menu-toggle",
            shell_css,
        )


class HomeEventsCmpRenderingTests(TestCase):
    @staticmethod
    def _main_markup(response) -> str:
        match = re.search(
            r'<main id="main-content"[^>]*>(.*?)</main>',
            response.content.decode(),
            re.DOTALL,
        )
        if match is None:
            raise AssertionError("rendered response has no shared main-content element")
        return match.group(1)

    def test_home_and_unified_are_nonredirecting_equivalent_cmp_surfaces(self) -> None:
        home = self.client.get("/", follow=False)
        unified = self.client.get("/unified/", follow=False)

        self.assertEqual(home.status_code, 200)
        self.assertEqual(unified.status_code, 200)
        self.assertNotIn("Location", unified.headers)
        self.assertEqual(self._main_markup(home), self._main_markup(unified))
        for response in (home, unified):
            self.assertContains(response, '<link rel="canonical" href="https://datatalks.club/">')
            self.assertContains(
                response,
                "Ship data pipelines and AI systems that actually run in production.",
            )
            self.assertContains(response, "Create your free account")
            self.assertContains(response, "Something to attend this week")
            self.assertContains(response, "AI Dev Tools Zoomcamp")
            self.assertContains(response, "all courses →")
            self.assertNotContains(response, "/static/core/site.css")

    def test_events_catalog_and_detail_preserve_public_content_and_seo(self) -> None:
        featured_event_path = next(
            event["public_path"]
            for event in public_projection()["events"]
            if event["title"] == FEATURED_EVENT_TITLE
        )
        catalog = self.client.get("/events")
        detail = self.client.get(featured_event_path)

        self.assertEqual(catalog.status_code, 200)
        self.assertContains(catalog, "Events")
        self.assertContains(catalog, "Upcoming events")
        self.assertContains(catalog, "Past events")
        self.assertContains(catalog, "our Google calendar")
        self.assertContains(catalog, f'href="{featured_event_path}"')
        self.assertNotContains(catalog, "/static/core/site.css")

        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "AI Dev Tools Zoomcamp 2026 Course Launch")
        self.assertContains(detail, "Alexey Grigorev")
        self.assertContains(detail, "The new cohort of AI Dev Tools Zoomcamp 2026")
        self.assertNotContains(detail, "Event links")
        self.assertNotContains(detail, "lu.ma")
        self.assertContains(
            detail,
            f'<link rel="canonical" href="https://datatalks.club{featured_event_path}">',
        )
        self.assertContains(detail, 'type="application/ld+json"')
        self.assertNotContains(detail, "/static/core/site.css")
