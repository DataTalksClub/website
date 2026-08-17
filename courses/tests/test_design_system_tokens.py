"""The learner surfaces draw every colour from a token both themes define.

This contract used to be read from `courses/static/courses.css`, the adopted
course-platform stylesheet.  Issue #179 rebuilt every learner page on design 5a:
the pages carry their stylesheet inline and load no external CSS at all, so the
old file had stopped describing anything the site serves and was acting as the
source of truth for a design system it is not part of.  The same concerns are
read here from `templates/core/_design_system.html`, the shared stylesheet
partial every design 5a page inlines, and from the pages that own the deadline
countdown: control borders, the focus ring, the deadline states `time_left.js`
writes, readonly controls, and the impersonation banner must each resolve
through a custom property that the light and dark palettes both define, never
through a literal colour.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

COURSES_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = COURSES_DIR.parent
DESIGN_SYSTEM = REPOSITORY_ROOT / "templates/core/_design_system.html"
# The pages that render the countdown `time_left.js` writes its status class into.
DEADLINE_PAGES = (
    REPOSITORY_ROOT / "courses/templates/courses/course.html",
    REPOSITORY_ROOT / "courses/templates/homework/homework.html",
    REPOSITORY_ROOT / "courses/templates/projects/_base.html",
)
# Custom properties the dark palette deliberately does not restate: some carry no
# colour at all, and the rest are single-value marks documented in the partial
# itself.  The three --drawn-circle values are border-radii, not colours: the
# lopsided circle from the 4a mockup is the same shape in both themes.
THEME_NEUTRAL_TOKENS = {
    "--clay",
    "--drawn-circle",
    "--drawn-circle-b",
    "--drawn-circle-c",
    "--font-mono",
    "--font-sans",
    "--gold",
    "--graph-edge",
    "--measure",
    "--shell",
}
LITERAL_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
DECLARED_TOKEN = re.compile(r"(--[a-z0-9-]+)\s*:")


def _palette(source: str, selector: str) -> set[str]:
    """Return the custom properties declared in one palette block."""

    start = source.index(f"{selector} {{")
    end = source.index("\n      }", start)
    return set(DECLARED_TOKEN.findall(source[start:end]))


def _rule_body(source: str, selector: str) -> str:
    """Return the declarations of the first rule whose selector list ends in `selector`."""

    start = source.index(f"{selector} {{")
    end = source.index("}", start)
    return source[source.index("{", start) + 1 : end]


class DesignSystemTokenTests(SimpleTestCase):
    def test_deadline_script_emits_only_semantic_status_classes(self):
        script = (COURSES_DIR / "static" / "time_left.js").read_text()

        for class_name in (
            "time-left--normal",
            "time-left--warning",
            "time-left--overdue",
        ):
            self.assertIn(class_name, script)

        self.assertNotRegex(script, r"text-slate-")
        self.assertNotRegex(script, r"text-\[#")
        self.assertNotRegex(script, r"dark:text-")
        self.assertNotRegex(script, r"#[0-9a-fA-F]{3,8}")

    def test_both_palettes_declare_every_themed_token(self):
        stylesheet = DESIGN_SYSTEM.read_text(encoding="utf-8")

        light = _palette(stylesheet, ":root")
        dark = _palette(stylesheet, "body.dark-mode")

        self.assertTrue(light)
        self.assertEqual(light - dark, THEME_NEUTRAL_TOKENS)
        self.assertEqual(dark - light, set())
        for token in ("--card", "--ink", "--line", "--muted", "--sand", "--focus", "--olive"):
            with self.subTest(token=token):
                self.assertIn(token, light)
                self.assertIn(token, dark)

    def test_course_controls_use_theme_safe_tokens(self):
        stylesheet = DESIGN_SYSTEM.read_text(encoding="utf-8")

        # Form controls: surface, border and text are tokens, so a control keeps a
        # readable edge on either palette instead of a single hard-coded border.
        control = _rule_body(stylesheet, ".field-input")
        self.assertIn("background: var(--card)", control)
        self.assertIn("border: 2px solid var(--line)", control)
        self.assertIn("color: var(--ink)", control)
        self.assertNotRegex(control, LITERAL_COLOR)

        # Readonly and disabled controls keep their own surface/text pair.
        readonly = _rule_body(stylesheet, ".field-input[disabled]")
        self.assertIn("background: var(--sand)", readonly)
        self.assertIn("color: var(--muted)", readonly)
        self.assertNotRegex(readonly, LITERAL_COLOR)

        # The keyboard focus ring is one token, restated by the dark palette.
        focus = _rule_body(stylesheet, ":focus-visible")
        self.assertIn("outline: 3px solid var(--focus)", focus)
        self.assertNotRegex(focus, LITERAL_COLOR)

        # The impersonation banner and its return control are token-drawn too.
        banner = _rule_body(stylesheet, ".impersonation-banner")
        self.assertIn("background: var(--lavender)", banner)
        self.assertIn("color: var(--ink)", banner)
        self.assertNotRegex(banner, LITERAL_COLOR)
        return_button = _rule_body(stylesheet, ".impersonation-return-button")
        self.assertIn("background: var(--card)", return_button)
        self.assertIn("border: 2px solid var(--line)", return_button)
        self.assertIn("color: var(--ink)", return_button)
        self.assertNotRegex(return_button, LITERAL_COLOR)

    def test_every_deadline_state_is_a_token_on_every_page_that_shows_one(self):
        for path in DEADLINE_PAGES:
            page = path.read_text(encoding="utf-8")
            for state in ("--normal", "--warning", "--overdue"):
                with self.subTest(page=path.name, state=state):
                    body = _rule_body(page, f".time-left{state}")
                    self.assertRegex(body, r"color: var\(--[a-z-]+\)")
                    self.assertNotRegex(body, LITERAL_COLOR)
