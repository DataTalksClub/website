"""The border system, as a check instead of a memory.

Every ``border``, ``border-*`` side, ``border-radius``, ``box-shadow`` and
``outline`` in page and partial CSS is one of the tokens defined on
``:root, body`` in ``templates/core/_design_system.html`` (see
``_docs/design/design-system.md#borders``): ``--border-soft``,
``--border-strong``, ``--border-field``, ``--border-quiet``, ``--rule``,
``--seam``, the five ``--radius-*`` steps, the ``--shadow-cta*`` /
``--shadow-popover`` shadows and ``--outline-focus``.  Before the system the
site mixed 1px/1.5px/2px/3px edges, ink and soft colours, a dozen radii and
hard shadows on cards as well as buttons.

This test fails on any raw length (``px``/``rem``/``em``) or raw colour in one
of those declarations, except:

* ``0`` / ``none`` -- switching a border off;
* percentage radii (``50%``, the ``--drawn-circle*`` shapes);
* ``border-color`` / ``border-style`` / ``border-width`` and the other
  longhands -- states (hover, invalid, active) recolour a token edge;
* the reviewed rows in ``ALLOWED`` below: shapes drawn out of borders (a tick, a
  chevron, a chart key), not frames.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]

TEMPLATE_GLOBS = ("templates/**/*.html", "*/templates/**/*.html")

EXCLUDED_PREFIXES = (
    # The Django admin skin, outside the public design system.
    "templates/unfold/",
)
EXCLUDED_FILES = frozenset(
    {
        # HTML email: mail clients do not support CSS custom properties, and
        # the partial that defines the tokens is not part of an email.
        "templates/community_base/mail/email.html",
        "core/templates/accessibility/email.html",
    }
)

DECLARATION = re.compile(
    r"(?<![\w-])(?P<prop>border(?:-(?:top|right|bottom|left|block|inline)(?:-(?:start|end))?)?"
    r"|border-radius|box-shadow|outline)\s*:\s*(?P<value>[^;{}\"]+)",
)
RAW_LENGTH = re.compile(r"(?<![\w.-])-?(?P<num>\d*\.?\d+)(?:px|rem|em)\b")
RAW_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")

# Reviewed exceptions: (relative path, selector, declaration) -> why.
ALLOWED: dict[tuple[str, str, str], str] = {
    (
        "templates/core/_design_system.html",
        ".faq-fold-marker, .row-fold-marker",
        "border-bottom: 2px solid var(--indigo)",
    ): "Two borders rotated 45 degrees draw the fold chevron.",
    (
        "templates/core/_design_system.html",
        ".faq-fold-marker, .row-fold-marker",
        "border-right: 2px solid var(--indigo)",
    ): "Two borders rotated 45 degrees draw the fold chevron.",
    (
        "templates/core/home.html",
        ".build-check::after",
        "border-bottom: 0.13rem solid var(--card)",
    ): "Two borders rotated 45 degrees draw the check glyph.",
    (
        "templates/core/home.html",
        ".build-check::after",
        "border-left: 0.13rem solid var(--card)",
    ): "Two borders rotated 45 degrees draw the check glyph.",
    (
        "templates/core/_design_system.html",
        ".masthead .theme-toggle .dot",
        "border: 2px solid currentColor",
    ): "The half-filled theme-toggle glyph is drawn as a ring in the text colour.",
    (
        "templates/core/_design_system.html",
        ".event-day-dot",
        "border: 2px solid var(--muted)",
    ): "The hollow day marker on the events timeline -- a glyph, not a frame.",
    (
        "templates/core/_design_system.html",
        ".event-timeline::before",
        "border-left: 2px dotted var(--line-soft)",
    ): "The dotted timeline rail behind the events days.",
    (
        "templates/core/_design_system.html",
        ".archive-row:not(.archive-row-undated)::before",
        "border-left: 2px dotted var(--line-soft)",
    ): "The dotted timeline rail behind dated archive rows.",
    (
        "templates/core/_design_system.html",
        ".graph-node",
        "border: 2px solid var(--indigo)",
    ): "A node of the knowledge-graph drawing; the legend swatch repeats it.",
    (
        "templates/core/_design_system.html",
        ".legend-swatch",
        "border: 1.5px solid var(--line)",
    ): "Legend key for the graph drawing.",
    (
        "templates/core/_design_system.html",
        ".person-chip-portrait",
        "border: 1px solid var(--line)",
    ): "Hairline ring on a portrait small enough that the 2px drawn edge would eat it.",
    (
        "templates/public/podcast_detail.html",
        ".episode-tab",
        "border-bottom: 3px solid transparent",
    ): "The active-tab indicator: transparent until the tab is selected.",
    (
        "courses/templates/courses/course_list.html",
        ".step",
        "border-top: 3px solid var(--line)",
    ): "The ink bar that heads each numbered step on /courses -- a drawn mark.",
    (
        "courses/templates/courses/dashboard.html",
        ".metric-help-button",
        "border: 1px solid currentColor",
    ): "The circled 'i' glyph of the metric help control.",
    (
        "courses/templates/courses/dashboard.html",
        ".range-mean",
        "border: 2px solid var(--ink)",
    ): "Mean marker drawn on the range chart.",
    (
        "courses/templates/courses/dashboard.html",
        ".k-mean",
        "border: 2px solid var(--ink)",
    ): "Mean marker in the range chart key.",
    (
        "courses/templates/projects/stats.html",
        ".distribution-marker-average",
        "border: 1px solid var(--line)",
    ): "Average marker drawn on the distribution chart.",
    (
        "courses/templates/projects/stats.html",
        ".key-middle",
        "border: 1px solid var(--line)",
    ): "Chart key swatch.",
    (
        "courses/templates/projects/stats.html",
        ".key-average",
        "border: 1px solid var(--line)",
    ): "Chart key swatch.",
    (
        "courses/templates/projects/site_gallery.html",
        ".gallery-select-shell::after",
        "border-bottom: 2px solid currentColor",
    ): "Two borders rotated 45 degrees draw the select chevron.",
    (
        "courses/templates/projects/site_gallery.html",
        ".gallery-select-shell::after",
        "border-right: 2px solid currentColor",
    ): "Two borders rotated 45 degrees draw the select chevron.",
    (
        "courses/templates/courses/dashboard.html",
        ".share > div + div",
        "border-inline-start: 2px solid var(--card)",
    ): "The gap between segments of one stacked share bar, cut in the card colour.",
    (
        "courses/templates/courses/dashboard.html",
        ".time-split .hw",
        "border-inline-start: 2px solid var(--card)",
    ): "The gap between the two halves of the time-split bar, cut in the card colour.",
}

COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
TEMPLATE_TAG = re.compile(r"\{[%{#].*?[%}#]\}", re.DOTALL)
TEMPLATE_COMMENT = re.compile(r"\{% comment %\}.*?\{% endcomment %\}", re.DOTALL)


def _blank(match: re.Match[str]) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))


def _template_files():
    seen = set()
    for pattern in TEMPLATE_GLOBS:
        for path in ROOT.glob(pattern):
            rel = path.relative_to(ROOT).as_posix()
            if rel in seen or rel.startswith(EXCLUDED_PREFIXES) or rel in EXCLUDED_FILES:
                continue
            if any(part.startswith(".") for part in path.relative_to(ROOT).parts):
                continue
            seen.add(rel)
            yield rel, path


def _selector_for(text: str, position: int) -> str:
    opening = text.rfind("{", 0, position)
    if opening == -1:
        return "<no rule>"
    start = max(text.rfind("}", 0, opening), text.rfind("{", 0, opening), text.rfind(";", 0, opening))
    return " ".join(text[start + 1 : opening].split())


def off_system_declarations():
    found = []
    for rel, path in _template_files():
        text = path.read_text(encoding="utf-8")
        if not any(word in text for word in ("border", "box-shadow", "outline")):
            continue
        text = TEMPLATE_TAG.sub(_blank, TEMPLATE_COMMENT.sub(_blank, COMMENT.sub(_blank, text)))
        for match in DECLARATION.finditer(text):
            value = " ".join(match["value"].split())
            declaration = f"{match['prop']}: {value}"
            raw = [n for n in RAW_LENGTH.finditer(value) if float(n["num"]) != 0]
            if not raw and not RAW_COLOUR.search(value):
                continue
            selector = _selector_for(text, match.start())
            if (rel, selector, declaration.removesuffix(" !important")) in ALLOWED:
                continue
            line = text.count("\n", 0, match.start()) + 1
            found.append(f"{rel}:{line}: {selector} {{ {declaration} }}")
    return found


class BorderTokenTests(SimpleTestCase):
    def test_border_radius_shadow_and_outline_use_the_tokens(self):
        offenders = off_system_declarations()
        self.assertEqual(
            offenders,
            [],
            "Raw border/radius/shadow/outline value found; use a border-system token "
            "(see _docs/design/design-system.md#borders):\n" + "\n".join(offenders),
        )

    def test_tokens_are_defined_once_in_the_partial(self):
        partial = (ROOT / "templates/core/_design_system.html").read_text(encoding="utf-8")
        for declaration in (
            "--border-soft: 1px solid var(--line-soft);",
            "--border-strong: 2px solid var(--line);",
            "--border-field: 1px solid var(--field-edge);",
            "--border-quiet: 1.5px dashed var(--line-soft);",
            "--rule: 1px solid var(--line-soft);",
            "--seam: 2px dashed var(--line-soft);",
            "--radius-sm: 0.4rem;",
            "--radius-control: 0.6rem;",
            "--radius-media: 0.75rem;",
            "--radius-card: 1rem;",
            "--radius-pill: 999px;",
            "--shadow-cta: 0.3rem 0.3rem 0 var(--shadow);",
            "--shadow-popover: 0 0.75rem 1.75rem var(--shadow-soft);",
            "--outline-focus: 3px solid var(--focus);",
        ):
            self.assertEqual(partial.count(declaration), 1, declaration)

    def test_cards_carry_no_shadow_and_buttons_keep_theirs(self):
        partial = (ROOT / "templates/core/_design_system.html").read_text(encoding="utf-8")
        for selector, present, absent in (
            (".card {", "border: var(--border-soft);", "box-shadow"),
            (".event-item {", "border: var(--border-soft);", "box-shadow"),
            (".cta {", "box-shadow: var(--shadow-cta);", None),
        ):
            start = partial.index("\n      " + selector)
            block = partial[start : partial.index("}", start)]
            self.assertIn(present, block, selector)
            if absent:
                self.assertNotIn(absent, block, selector)
