"""The accent left-border ban, as a check instead of a memory.

The design system draws no coloured single-side accent rail: no
``border-inline-start`` or ``border-left`` used to mark a block as a callout, a
quotation, a date or a reply.  Tone is the surface and the words; a block that
needs to stand apart takes a full border, a surface, or the system's dashed
rule.  That decision was enforced case by case for a long time and written down
nowhere, so the same rail kept coming back on a new page.

This test makes the rule global.  Every ``border-left`` / ``border-inline-start``
declaration in a template or stylesheet this site ships must either remove a
border (a zero or ``none`` value) or be a reviewed structural use listed in
``STRUCTURAL_LEFT_BORDERS`` below with the reason it is not an accent rail.

A divider between siblings in a row, a table cell rule, an input outline, a full
box border and a shape drawn out of borders are not accent rails, and each one
in the list below says which it is.  Adding a row is a design decision, not a
formality: if the new rule marks a single block rather than separating two, it
is the accent rail this test exists to stop.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from django.test import TestCase

ROOT = Path(__file__).resolve().parents[2]

# Files this site does not author: vendored assets, browser fixtures, and
# generated projections of source-authored content.
EXCLUDED_PREFIXES = (
    "content/faq_assets/",
    "content/public_projection/",
    "test_support/fixtures/",
)
EXCLUDED_PARTS = ("vendor", "node_modules")
SCANNED_SUFFIXES = (".css", ".html")

LEFT_BORDER = re.compile(
    r"border-(?:left|inline-start)(?:-color|-style|-width)?\s*:\s*(?P<value>[^;}]+)",
    re.IGNORECASE,
)
# A value that removes a border is how a rule is switched off, never how one is
# drawn, so it is always allowed.
REMOVAL = re.compile(r"^(?:0[a-z%]*|none)(?:\s+.*)?$", re.IGNORECASE)

# Tailwind's left-border utilities, which arrive inside copied or generated
# markup.  They draw nothing here — no page loads Tailwind — but a stylesheet
# that started defining them would reintroduce the rail through the back door.
TAILWIND_LEFT_BORDER = re.compile(r"\.border-l(?:-\d+)?\s*(?:,|\{)")

# Reviewed structural uses: (relative path, selector) -> why it is not an accent
# rail.  Every entry separates siblings or draws a shape; none of them marks a
# single block as special.
STRUCTURAL_LEFT_BORDERS: dict[tuple[str, str], str] = {
    (
        "templates/core/home.html",
        ".build-check::after",
    ): "Two borders rotated 45 degrees draw the check glyph itself; there is no block to mark.",
    (
        "templates/core/home.html",
        ".latest-grid > * + *",
    ): "The dashed rule between the two latest-columns, drawn only at >=40rem, is the divider "
    "the bands use; it separates siblings and disappears with the second column.",
    (
        "courses/templates/courses/course.html",
        ".course-specs .spec",
    ): "The dashed rule between spec cells inside one bordered table; the first cell and the "
    "narrow layouts reset it.",
}


def _scanned_files() -> tuple[Path, ...]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    files: list[Path] = []
    for item in listed.stdout.split(b"\0"):
        if not item:
            continue
        relative = item.decode("utf-8")
        if not relative.endswith(SCANNED_SUFFIXES):
            continue
        if relative.startswith(EXCLUDED_PREFIXES):
            continue
        parts = relative.split("/")
        if any(part in EXCLUDED_PARTS for part in parts):
            continue
        absolute = ROOT / relative
        if absolute.is_file():
            files.append(absolute)
    return tuple(files)


def _selector_for(text: str, position: int) -> str:
    """The selector of the rule the declaration at ``position`` sits in."""

    opening = text.rfind("{", 0, position)
    if opening == -1:
        return "<no rule>"
    start = max(
        text.rfind("}", 0, opening),
        text.rfind("{", 0, opening),
        text.rfind("*/", 0, opening),
        text.rfind(";", 0, opening),
    )
    selector = text[start + 1 : opening]
    if selector.startswith("/"):
        selector = selector[1:]
    return " ".join(selector.split())


class AccentLeftBorderBanTests(TestCase):
    """No page, partial or stylesheet draws a coloured single-side accent rail."""

    def test_every_left_border_is_a_removal_or_a_reviewed_structural_rule(self) -> None:
        findings: list[str] = []
        seen: set[tuple[str, str]] = set()
        for absolute in _scanned_files():
            relative = absolute.relative_to(ROOT).as_posix()
            text = absolute.read_text(encoding="utf-8")
            for match in LEFT_BORDER.finditer(text):
                if REMOVAL.match(match.group("value").strip()):
                    continue
                selector = _selector_for(text, match.start())
                key = (relative, selector)
                if key in STRUCTURAL_LEFT_BORDERS:
                    seen.add(key)
                    continue
                line = text.count("\n", 0, match.start()) + 1
                findings.append(
                    f"accent left border at {relative}:{line} in {selector!r}: "
                    f"{match.group().strip()}"
                )
        self.assertEqual(findings, [])
        self.assertEqual(
            sorted(set(STRUCTURAL_LEFT_BORDERS) - seen),
            [],
            "a reviewed structural left border no longer exists; delete its row",
        )

    def test_no_stylesheet_defines_a_tailwind_left_border_utility(self) -> None:
        offenders = [
            absolute.relative_to(ROOT).as_posix()
            for absolute in _scanned_files()
            if TAILWIND_LEFT_BORDER.search(absolute.read_text(encoding="utf-8"))
        ]
        self.assertEqual(offenders, [])
