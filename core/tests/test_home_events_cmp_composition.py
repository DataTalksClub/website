from __future__ import annotations

import csv
import hashlib
import re
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.template.loader import get_template
from django.test import TestCase

from events.queries import published_event_records

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ADOPTED_BASE = REPOSITORY_ROOT / "course_platform_templates/base.html"
ADOPTED_CSS = REPOSITORY_ROOT / "courses/static/courses.css"
COPIED_LEDGER = REPOSITORY_ROOT / "_docs/adoption/course-platform/copied-files.tsv"
PATCHED_LEDGER = REPOSITORY_ROOT / "_docs/adoption/course-platform/integration-patched-files.tsv"
PINNED_BASE_SHA256 = "f51666391e33aec905f43312215bfd82094bfb0088414594f40bcbdfc21560b8"
PINNED_CSS_SHA256 = "282ed7b15df2502a8d4c2e9cd45ef1f8e92771835243d3cbc590f78db9ed5f8f"
# The homepage left this contract with the design system rebuild (issue #179), the events
# index (/events and /events/past) left it with the design system mockup 6c rebuild, and the
# event detail page followed them: each owns its inline stylesheet and none of the adopted
# CMP shell.  What remains scoped here is the event meta partial, which the detail page no
# longer includes and which is now unreferenced.
SCOPED_TEMPLATES = (REPOSITORY_ROOT / "templates/public/_event_meta.html",)
FEATURED_EVENT_TITLE = "AI Dev Tools Zoomcamp 2026 Course Launch"


def _ledger_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream, delimiter="\t"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class HomeEventsCmpRepositoryContractTests(TestCase):
    def test_logical_base_fails_closed_to_the_adopted_cmp_origin(self) -> None:
        template = get_template("base.html")
        origin = getattr(template, "origin", None)

        if origin is None:
            self.fail("logical base template has no inspectable origin")
        self.assertEqual(Path(origin.name).resolve(), ADOPTED_BASE.resolve())
        self.assertFalse((REPOSITORY_ROOT / "templates/base.html").exists())
        self.assertFalse((REPOSITORY_ROOT / "core/static/core/site.css").exists())

    def test_scoped_templates_use_cmp_primitives_without_a_parallel_style_system(self) -> None:
        failures: list[str] = []
        for path in SCOPED_TEMPLATES:
            source = path.read_text(encoding="utf-8")
            relative = path.relative_to(REPOSITORY_ROOT)
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

    def test_event_detail_left_the_cmp_surface_for_the_design_system(self) -> None:
        """The last of the three surfaces this contract guarded (issue #179).

        Two tests lived here: one asserted the adopted shell's `max-w-3xl` column on this
        page, the other asserted the `home-events-cmp-surface` body class that scoped the
        mobile target rule in `core/static/core/site_shell.css`.  The page now carries the
        design system, which guarantees the same 2.75rem target floor for every control,
        so both are kept in the language the page is written in rather than left
        describing markup it no longer has.  That scoped CSS rule is now unreachable and
        can go with the rest of the adopted shell.
        """

        detail = (REPOSITORY_ROOT / "templates/public/event_detail.html").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("home-events-cmp-surface", detail)
        self.assertNotIn("max-w-3xl", detail)
        self.assertNotIn("primer-button", detail)
        self.assertIn('{% include "core/_design_system.html" %}', detail)
        self.assertIn('{% include "core/_site_shell_head.html" %}', detail)
        self.assertIn('{% include "core/_site_shell_foot.html" %}', detail)


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
                "Learn the fundamentals. Build real projects. Share your work.",
            )
            self.assertContains(response, "Create your free account")
            self.assertContains(response, "Events")
            self.assertContains(response, "AI Dev Tools Zoomcamp")
            self.assertContains(response, "all courses →")
            self.assertNotContains(response, "/static/core/site.css")

    def test_events_catalog_and_detail_preserve_public_content_and_seo(self) -> None:
        featured_event_path = next(
            event["public_path"]
            for event in published_event_records()
            if event["title"] == FEATURED_EVENT_TITLE
        )
        stable_now = datetime(2026, 8, 12, tzinfo=ZoneInfo("Europe/Berlin"))
        with patch("content.public_data.timezone.now", return_value=stable_now):
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
