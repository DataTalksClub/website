"""The spacing scale, as a check instead of a memory.

Every margin, padding, gap and inset in page and partial CSS uses a step of
the ``--space-*`` scale or one of its role tokens (``--space-head``,
``--space-section``, ...) defined in ``templates/core/_design_system.html``
(see ``_docs/design/design-system.md#spacing``).  Before the scale, ~50
distinct rem values were in use and the gap between a heading and its content
changed from one section to the next.

This test fails on any raw ``rem`` or ``px`` literal in one of those
declarations, except:

* hairline optical nudges of at most ``0.15rem`` / ``2px`` -- they align a
  glyph, they do not space a layout;
* the reviewed rows in ``ALLOWED`` below, each with its reason.

Sizes that are not spacing (``min-height``, ``width``, ``font-size``,
``scroll-margin``, positioned ``top``/``left`` offsets) are not scanned.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]

TEMPLATE_GLOBS = ("templates/**/*.html", "*/templates/**/*.html")

EXCLUDED_PREFIXES = (
    # TODO(academy redesign): remove once academy/ is migrated to the tokens.
    # The Academy pages are being redesigned by a separate agent right now.
    "academy/templates/",
    # The Django admin skin, outside the public design system.
    "templates/unfold/",
)
EXCLUDED_FILES = frozenset(
    {
        # HTML email: mail clients do not support CSS custom properties, and
        # the partial that defines the scale is not part of an email.
        "templates/community_base/mail/email.html",
        "core/templates/accessibility/email.html",
    }
)

DECLARATION = re.compile(
    r"(?<![\w-])(?P<prop>(?:margin|padding|inset)(?:-[a-z]+)*|gap|row-gap|column-gap)"
    r"\s*:\s*(?P<value>[^;{}\"]+)",
)
LITERAL = re.compile(r"(?<![\w.-])-?(?P<num>\d*\.?\d+)(?P<unit>rem|px)\b")

HAIRLINE = {"rem": 0.15, "px": 2.0}

# Reviewed exceptions: (relative path, declaration text) -> why.
ALLOWED: dict[tuple[str, str], str] = {
    (
        "templates/core/_design_system.html",
        "padding-bottom: var(--consent-banner-space, 0px)",
    ): "A zero fallback for a runtime-measured banner height, not a spacing step.",
    (
        "templates/core/_design_system.html",
        "margin-block-end: 0.28rem",
    ): "Optical: lifts the wordmark by half of it to centre it on the drawn DTC mark "
    "(the comment above .brand-name has the arithmetic); a glyph alignment, not a gap.",
}

COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(text: str) -> str:
    """Blank out CSS comments, keeping line numbers."""
    return COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)


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


def off_scale_declarations():
    found = []
    for rel, path in _template_files():
        text = _strip_comments(path.read_text(encoding="utf-8"))
        if not any(word in text for word in ("margin", "padding", "gap", "inset")):
            continue
        for match in DECLARATION.finditer(text):
            declaration = f"{match['prop']}: {match['value'].strip()}"
            if (rel, declaration) in ALLOWED:
                continue
            for literal in LITERAL.finditer(match["value"]):
                number = float(literal["num"])
                if number == 0 or number <= HAIRLINE[literal["unit"]]:
                    continue
                line = text.count("\n", 0, match.start()) + 1
                found.append(f"{rel}:{line}: {declaration}")
                break
    return found


class SpacingTokenTests(SimpleTestCase):
    def test_spacing_declarations_use_the_scale(self):
        offenders = off_scale_declarations()
        self.assertEqual(
            offenders,
            [],
            "Raw rem/px spacing found; use a --space-* step or role token "
            "(see _docs/design/design-system.md#spacing):\n" + "\n".join(offenders),
        )

    def test_scale_and_roles_are_defined_once_in_the_partial(self):
        partial = (ROOT / "templates/core/_design_system.html").read_text(encoding="utf-8")
        for step, value in enumerate(
            ("0.25rem", "0.5rem", "0.75rem", "1rem", "1.5rem", "2rem", "3rem", "4rem"),
            start=1,
        ):
            self.assertIn(f"--space-{step}: {value};", partial)
        for role in (
            "inline",
            "tight",
            "eyebrow",
            "stack",
            "head",
            "grid",
            "card",
            "section",
            "band",
        ):
            self.assertIn(f"--space-{role}:", partial)

    def test_structural_rhythm_uses_the_roles(self):
        partial = (ROOT / "templates/core/_design_system.html").read_text(encoding="utf-8")
        for selector, declaration in (
            (".band {", "padding-block: var(--space-band);"),
            (".band-head {", "margin-bottom: var(--space-head);"),
            (".band-head-secondary {", "margin-top: var(--space-section);"),
            (".card-grid {", "gap: var(--space-grid);"),
        ):
            start = partial.index("\n      " + selector)
            block = partial[start : partial.index("}", start)]
            self.assertIn(declaration, block, selector)

    def test_the_scanner_catches_a_raw_literal(self):
        sample = ".x { margin-top: 0.4rem; gap: var(--space-2); padding: 0.1rem 0; }"
        hits = [
            m["value"]
            for m in DECLARATION.finditer(sample)
            if any(
                float(lit["num"]) > HAIRLINE[lit["unit"]] for lit in LITERAL.finditer(m["value"])
            )
        ]
        self.assertEqual(hits, ["0.4rem"])
