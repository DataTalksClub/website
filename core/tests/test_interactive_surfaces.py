"""Contract tests for explicit public control and whole-card interactions."""

from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DESIGN_SYSTEM = REPOSITORY_ROOT / "templates/core/_design_system.html"
TEMPLATE_ROOTS = (
    REPOSITORY_ROOT / "accounts/templates",
    REPOSITORY_ROOT / "courses/templates/courses",
    REPOSITORY_ROOT / "courses/templates/projects",
    REPOSITORY_ROOT / "templates/core",
    REPOSITORY_ROOT / "templates/public",
    REPOSITORY_ROOT / "templates/review",
)
CONTROL_CLASSES = {
    "cta",
    "filter-pill",
    "home-graph-tool",
    "pill-button",
    "scroller-button",
    "signup-button",
    "site-navigation-toggle",
    "user-menu-toggle",
}


class InteractiveSurfaceContractTests(SimpleTestCase):
    def template_sources(self):
        for root in TEMPLATE_ROOTS:
            for path in root.rglob("*.html"):
                # The media kit deliberately owns its landing-page interactions.
                if path.name == "mediakit.html":
                    continue
                yield path, path.read_text(encoding="utf-8")

    def test_visual_control_families_use_the_single_lift_primitive(self) -> None:
        missing: list[str] = []
        for path, source in self.template_sources():
            for match in re.finditer(r'class="([^"]+)"', source):
                classes = set(match.group(1).split())
                has_lift = bool(
                    re.search(r"(?:^|\s)interactive-lift(?:$|\s|\{)", match.group(1))
                )
                if classes & CONTROL_CLASSES and not has_lift:
                    relative = path.relative_to(REPOSITORY_ROOT)
                    missing.append(f"{relative}: {match.group(1)}")
        self.assertEqual(missing, [])

    def test_public_buttons_use_the_lift_primitive(self) -> None:
        missing: list[str] = []
        for path, source in self.template_sources():
            for match in re.finditer(r"<button\b[^>]*>", source, flags=re.DOTALL):
                if "interactive-lift" not in match.group(0):
                    relative = path.relative_to(REPOSITORY_ROOT)
                    missing.append(str(relative))
        self.assertEqual(missing, [])

    def test_whole_card_destinations_use_lift_and_bottom_right_indicator(self) -> None:
        for path, source in self.template_sources():
            for match in re.finditer(r'class="([^"]+)"', source):
                classes = set(match.group(1).split())
                is_whole_card = bool(
                    classes
                    & {
                        "stretched-card-link",
                        "event-card",
                        "related-episode-card",
                        "sponsor-card",
                    }
                )
                if not is_whole_card:
                    continue
                with self.subTest(template=path.name, classes=classes):
                    self.assertIn("interactive-card", classes)
                    self.assertIn("interactive-lift", classes)

    def test_the_primitive_is_pointer_keyboard_touch_and_motion_safe(self) -> None:
        styles = DESIGN_SYSTEM.read_text(encoding="utf-8")

        self.assertIn(".interactive-lift {", styles)
        self.assertIn(".interactive-card::before {", styles)
        self.assertIn('content: "→";', styles)
        self.assertIn("bottom: var(--interactive-card-bottom", styles)
        self.assertIn("right: var(--interactive-card-right", styles)
        self.assertIn("@media (hover: hover) and (pointer: fine)", styles)
        self.assertIn(".interactive-lift:focus-visible", styles)
        self.assertIn(":has(\n        > a:focus-visible", styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)
        self.assertIn("translate: none !important;", styles)
        self.assertNotIn(":where(button", styles)

    def test_informational_cards_are_not_marked_as_interactive(self) -> None:
        homepage = (REPOSITORY_ROOT / "templates/core/home.html").read_text(encoding="utf-8")
        dashboard = (
            REPOSITORY_ROOT / "courses/templates/courses/dashboard.html"
        ).read_text(encoding="utf-8")

        self.assertIn('class="card climb-card"', homepage)
        self.assertIn('class="card dashboard-card"', dashboard)
        self.assertNotIn('class="card climb-card interactive-', homepage)
        self.assertNotIn('class="card dashboard-card interactive-', dashboard)

    def test_direct_link_card_inventory_uses_both_primitives(self) -> None:
        expected = {
            "templates/core/home.html": (
                "topic interactive-card interactive-lift",
                "sponsor-card interactive-card interactive-lift",
            ),
            "templates/public/podcast_detail.html": (
                "previous-episode interactive-card interactive-lift",
                "next-episode interactive-card interactive-lift",
            ),
            "templates/public/wiki_hub.html": (
                "explore-row interactive-card interactive-lift",
            ),
            "courses/templates/courses/course_list.html": (
                "active-card interactive-card interactive-lift",
                "course-card interactive-card interactive-lift",
            ),
        }
        for relative, fragments in expected.items():
            source = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
            for fragment in fragments:
                with self.subTest(template=relative, fragment=fragment):
                    self.assertIn(fragment, source)
