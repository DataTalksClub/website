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
CODE_BLOCK_SCRIPT = REPOSITORY_ROOT / "core/static/core/code_blocks.js"
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
    "--content-width",
    "--drawn-circle",
    "--drawn-circle-b",
    "--drawn-circle-c",
    "--font-mono",
    "--font-sans",
    "--form-measure",
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


def _palette_values(source: str, selector: str) -> dict[str, str]:
    """Return the declared value of every custom property in one palette block."""

    start = source.index(f"{selector} {{")
    end = source.index("\n      }", start)
    return {
        name: value.strip()
        for name, value in re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", source[start:end])
    }


def _channels(colour: str) -> tuple[float, float, float]:
    colour = colour.strip()
    if colour.startswith("#"):
        digits = colour[1:]
        if len(digits) == 3:
            digits = "".join(character * 2 for character in digits)
        return (
            float(int(digits[0:2], 16)),
            float(int(digits[2:4], 16)),
            float(int(digits[4:6], 16)),
        )
    numbers = re.findall(r"[\d.]+", colour)
    return (float(numbers[0]), float(numbers[1]), float(numbers[2]))


def _relative_luminance(colour: str) -> float:
    weights = (0.2126, 0.7152, 0.0722)
    total = 0.0
    for weight, raw in zip(weights, _channels(colour), strict=True):
        scaled = raw / 255
        linear = scaled / 12.92 if scaled <= 0.04045 else ((scaled + 0.055) / 1.055) ** 2.4
        total += weight * linear
    return total


def _contrast_ratio(foreground: str, background: str) -> float:
    first = _relative_luminance(foreground)
    second = _relative_luminance(background)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


def _rule_body(source: str, selector: str) -> str:
    """Return the declarations of the first rule whose selector list ends in `selector`."""

    match = re.search(
        rf"(?m)^[ \t]*{re.escape(selector)}(?:\s*,[^{{]*)?\s*\{{",
        source,
    )
    if match is None:
        raise ValueError(f"selector not found: {selector}")
    end = source.index("}", match.end())
    return source[match.end() : end]


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

    def test_code_blocks_use_shared_theme_tokens(self):
        stylesheet = DESIGN_SYSTEM.read_text(encoding="utf-8")

        inline_code = _rule_body(stylesheet, ".prose code")
        self.assertIn("background: var(--lavender-deep)", inline_code)
        self.assertIn("color: var(--ink)", inline_code)
        self.assertNotRegex(inline_code, LITERAL_COLOR)

        code_block = _rule_body(stylesheet, ".prose pre")
        for declaration in (
            "background: var(--card)",
            "border: 1.5px solid var(--line-soft)",
            "box-shadow: 0.2rem 0.2rem 0 var(--shadow-soft)",
            "color: var(--ink)",
        ):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, code_block)
        self.assertNotRegex(code_block, LITERAL_COLOR)

        copy_button = _rule_body(stylesheet, ".code-block-copy")
        for token in ("--card", "--line-soft", "--ink"):
            with self.subTest(token=token):
                self.assertIn(f"var({token})", copy_button)
        self.assertNotRegex(copy_button, LITERAL_COLOR)

        light = _palette(stylesheet, ":root")
        dark = _palette(stylesheet, "body.dark-mode")
        for token in (
            "--code-comment",
            "--code-keyword",
            "--code-string",
            "--code-number",
            "--code-function",
            "--code-property",
            "--code-type",
            "--code-constant",
            "--code-variable",
            "--code-operator",
            "--code-punctuation",
            "--code-tag",
            "--code-heading",
            "--code-success",
            "--code-error",
        ):
            with self.subTest(token=token):
                self.assertIn(token, light)
                self.assertIn(token, dark)

    def test_every_code_token_colour_clears_wcag_aa_on_the_block_ground(self):
        """A colour nobody can read is not a highlight.

        Both palettes paint tokens on `--card`, the code block's own ground, so
        each token is measured against that ground rather than against the page.
        """

        stylesheet = DESIGN_SYSTEM.read_text(encoding="utf-8")

        for theme, selector in (("light", ":root"), ("dark", "body.dark-mode")):
            values = _palette_values(stylesheet, selector)
            ground = values["--card"]
            for token, colour in sorted(values.items()):
                if not token.startswith("--code-"):
                    continue
                with self.subTest(theme=theme, token=token):
                    self.assertGreaterEqual(
                        round(_contrast_ratio(colour, ground), 2),
                        4.5,
                        f"{token} ({colour}) on --card ({ground}) in the {theme} theme",
                    )

    def test_the_copy_control_hides_at_rest_without_leaving_the_tab_order(self):
        """At rest a code sample looks exactly as it does with no JavaScript."""

        stylesheet = DESIGN_SYSTEM.read_text(encoding="utf-8")

        copy_button = _rule_body(stylesheet, ".code-block-copy")
        self.assertIn("opacity: 0;", copy_button)
        # Opacity is the whole mechanism: either of these would take the control
        # out of the tab order and out of the accessibility tree.
        self.assertNotIn("display: none", copy_button)
        self.assertNotIn("visibility: hidden", copy_button)
        # WCAG 2.2 target size (24px).  Quieter must not mean harder to hit.
        self.assertIn("min-height: 2rem;", copy_button)
        self.assertIn("min-width: 3rem;", copy_button)

        reveal_selector = ".prose .code-block:hover .code-block-copy"
        reveal = _rule_body(stylesheet, reveal_selector)
        self.assertIn("opacity: 1;", reveal)
        reveal_start = stylesheet.index(reveal_selector)
        selector_list = stylesheet[reveal_start : stylesheet.index(reveal, reveal_start)]
        for selector in (
            # `:focus-visible` alone is a browser heuristic; a focused control
            # must be painted however the focus arrived.
            ".prose .code-block:focus-within .code-block-copy",
            ".code-block-copy:focus-visible",
            # The answer to a press stays painted after the pointer leaves.
            ".code-block-copy.is-copied",
            ".code-block-copy.is-error",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, selector_list)

        # Samples wrap rather than scroll, so the margin the control floats over
        # has to be reserved or the revealed control paints on top of code
        # characters (WCAG 2.1 SC 1.4.13).  The reservation is an empty float at
        # the head of the sample, which shortens only the line boxes the control
        # covers: no strip above the first line, and no reflow on reveal.
        gutter = _rule_body(stylesheet, ".code-block-gutter")
        self.assertIn("float: right;", gutter)
        self.assertIn("width: 5rem;", gutter)
        self.assertIn("height: 2.4rem;", gutter)
        # It must never swallow a click meant for the sample underneath it.
        self.assertIn("pointer-events: none;", gutter)

        # A coarse pointer has no hover to enter, so the control is always
        # there — and that is the only place a code sample still reserves a
        # strip above its first line, because a permanent control has to sit
        # somewhere.  A fine pointer keeps the sample's compact padding, and the
        # inline gutter goes away with the overlay that needed it.
        coarse_start = stylesheet.index("@media (hover: none) {")
        coarse = stylesheet[coarse_start : stylesheet.index("\n      }", coarse_start)]
        self.assertIn("opacity: 1;", coarse)
        self.assertIn(".prose .code-block pre {", coarse)
        self.assertIn("padding-top: 2.9rem;", coarse)
        self.assertIn(".code-block-gutter {", coarse)
        self.assertEqual(stylesheet.count(".prose .code-block pre {"), 1)

    def test_code_highlighting_contract_covers_published_languages(self):
        stylesheet = DESIGN_SYSTEM.read_text(encoding="utf-8")
        script = CODE_BLOCK_SCRIPT.read_text(encoding="utf-8")

        # These are the language classes currently emitted by the checked
        # article projection, plus the common aliases used by course markdown.
        for language in (
            "bash",
            "docker",
            "python",
            "sql",
            "yaml",
            "json",
            "javascript",
            "markup",
        ):
            with self.subTest(language=language):
                self.assertIn(f"{language}:", script)

        for role in (
            "comment",
            "keyword",
            "string",
            "number",
            "function",
            "property",
            "type",
            "constant",
            "variable",
            "operator",
            "punctuation",
            "tag",
            "heading",
        ):
            with self.subTest(role=role):
                self.assertIn(f".prose pre code .code-token-{role} {{", stylesheet)

        self.assertIn('setAttribute("data-code-language", language)', script)
        self.assertIn("document.createTextNode(token.text)", script)
        self.assertNotIn("innerHTML", script)
        self.assertNotRegex(script, r"\beval\s*\(")
        self.assertNotIn("new Function", script)

    def test_code_copy_enhancement_is_accessible_and_csp_safe(self):
        script = CODE_BLOCK_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('querySelectorAll(".prose pre")', script)
        self.assertIn('setAttribute("aria-label", "Copy code")', script)
        self.assertIn('setAttribute("aria-live", "polite")', script)
        self.assertIn("navigator.clipboard.writeText", script)
        self.assertIn("Code copied to clipboard.", script)
        self.assertIn("Select and copy it manually.", script)
        self.assertNotRegex(script, r"\beval\s*\(")
        self.assertNotIn("new Function", script)
        self.assertNotRegex(script, r"set(?:Timeout|Interval)\s*\(\s*['\"]")

    def test_every_deadline_state_is_a_token_on_every_page_that_shows_one(self):
        for path in DEADLINE_PAGES:
            page = path.read_text(encoding="utf-8")
            for state in ("--normal", "--warning", "--overdue"):
                with self.subTest(page=path.name, state=state):
                    body = _rule_body(page, f".time-left{state}")
                    self.assertRegex(body, r"color: var\(--[a-z-]+\)")
                    self.assertNotRegex(body, LITERAL_COLOR)
