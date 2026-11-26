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
    "pill-button",
    "scroller-button",
    "signup-button",
    "site-navigation-toggle",
    "user-menu-toggle",
}
# A button drawn as a text link among other text links (the footer's Analytics
# preferences control, the compact wiki graph's "Random topic") behaves exactly
# like those links: no lift.
# The "Read more" / "Show all N" expanders on the podcast episode and wiki pages
# are `band-link` text links that happen to be buttons, so they read as links too.
TEXT_LINK_BUTTON_CLASSES = {
    "site-footer-legal-control",
    "nav-group-toggle",
    "home-graph-link",
    "guest-card-more",
    "episode-topics-more",
    "wiki-episodes-more",
}


class InteractiveSurfaceContractTests(SimpleTestCase):
    def template_sources(self):
        for root in TEMPLATE_ROOTS:
            for path in root.rglob("*.html"):
                yield path, path.read_text(encoding="utf-8")

    def test_visual_control_families_use_the_single_lift_primitive(self) -> None:
        missing: list[str] = []
        for path, source in self.template_sources():
            for match in re.finditer(r'class="([^"]+)"', source):
                classes = set(match.group(1).split())
                has_lift = bool(re.search(r"(?:^|\s)interactive-lift(?:$|\s|\{)", match.group(1)))
                if classes & CONTROL_CLASSES and not has_lift:
                    relative = path.relative_to(REPOSITORY_ROOT)
                    missing.append(f"{relative}: {match.group(1)}")
        self.assertEqual(missing, [])

    def test_public_buttons_use_the_lift_primitive(self) -> None:
        missing: list[str] = []
        for path, source in self.template_sources():
            for match in re.finditer(r"<button\b[^>]*>", source, flags=re.DOTALL):
                classes = re.search(r'class="([^"]+)"', match.group(0))
                if classes and set(classes.group(1).split()) & TEXT_LINK_BUTTON_CLASSES:
                    continue
                if "interactive-lift" not in match.group(0):
                    relative = path.relative_to(REPOSITORY_ROOT)
                    missing.append(str(relative))
        self.assertEqual(missing, [])

    def test_footer_analytics_preferences_is_a_plain_text_link_button(self) -> None:
        source = (REPOSITORY_ROOT / "templates/core/_site_footer.html").read_text(encoding="utf-8")
        button = re.search(r"<button\b[^>]*data-analytics-preferences-open[^>]*>", source, re.S)
        assert button is not None, "the footer's Analytics preferences button is missing"
        classes = re.search(r'class="([^"]+)"', button.group(0))
        assert classes is not None
        self.assertIn("site-footer-legal-control", classes.group(1).split())
        self.assertNotIn("interactive-lift", classes.group(1).split())

    def test_whole_card_destinations_use_lift_and_bottom_right_indicator(self) -> None:
        for path, source in self.template_sources():
            for match in re.finditer(r'class="([^"]+)"', source):
                classes = set(match.group(1).split())
                is_whole_card = bool(
                    classes
                    & {
                        "stretched-card-link",
                        "event-card",
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
        cue_rule = styles.split(".interactive-card::before {", 1)[1].split("}", 1)[0]
        self.assertIn("transition: opacity 0.14s ease;", cue_rule)
        self.assertNotIn("translate:", cue_rule)
        self.assertIn("@media (hover: hover) and (pointer: fine)", styles)
        self.assertIn(".interactive-lift:focus-visible", styles)
        self.assertIn(":has(\n        > a:focus-visible", styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)
        self.assertIn("translate: none !important;", styles)
        self.assertNotIn(":where(button", styles)

    def test_informational_cards_are_not_marked_as_interactive(self) -> None:
        homepage = (REPOSITORY_ROOT / "templates/core/home.html").read_text(encoding="utf-8")
        dashboard = (REPOSITORY_ROOT / "courses/templates/courses/dashboard.html").read_text(
            encoding="utf-8"
        )
        # The climb card itself is the shared stage-card component (issue: the
        # two pages' hand-copied step-number badges drifted apart), drawn once
        # in core/_stage_card.html rather than inline in home.html.
        stage_card = (REPOSITORY_ROOT / "templates/core/_stage_card.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('{% include "core/_stage_card.html"', homepage)
        # The funnel redesign dissolved dashboard-card into the overview
        # grid's plain cards; the grid stays unmarked and the retired
        # class stays retired.
        self.assertIn('class="dashboard-overview-grid"', dashboard)
        self.assertNotIn("dashboard-card", dashboard)
        self.assertIn('class="card stage-card"', stage_card)
        self.assertIn('class="card stage-card stage-card-rail"', stage_card)
        self.assertNotIn('class="card stage-card interactive-', stage_card)
        self.assertNotIn('class="card stage-card stage-card-rail interactive-', stage_card)

    def test_direct_link_card_inventory_uses_both_primitives(self) -> None:
        expected = {
            "templates/core/home.html": (
                "topic interactive-card interactive-lift",
                "sponsor-card interactive-card interactive-lift",
            ),
            "templates/public/_wiki_topic_card.html": (
                "wiki-card interactive-card interactive-lift",
            ),
            "courses/templates/courses/course_list.html": (
                "catalog-card interactive-card interactive-lift",
            ),
        }
        for relative, fragments in expected.items():
            source = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
            for fragment in fragments:
                with self.subTest(template=relative, fragment=fragment):
                    self.assertIn(fragment, source)
