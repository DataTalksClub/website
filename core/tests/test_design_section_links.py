"""A section's "see all" link sits under the section, on its left edge.

Owner rule (2026-11-24): the links that lead from a section to the rest of its
kind -- "see all events", "all episodes", "open the guide" -- are always on the
left, under the section's content, in the shared `.band-more` line.  They are
never in the heading row (`.band-head`, which used to float them to the row's
right end) and never right-aligned.  See _docs/design/design-system.md,
"Section links".
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

from django.test import SimpleTestCase

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOTS = (
    REPOSITORY_ROOT / "templates",
    *sorted(path for path in REPOSITORY_ROOT.glob("*/templates") if path.is_dir()),
)
# The Academy pages are being rebuilt in a separate change; their section links
# move to `.band-more` with that work, and this entry goes with it.
PENDING_REDESIGN = {"academy/templates/academy/home.html"}
VOID_ELEMENTS = {
    "area",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "wbr",
}


class _BandHeadLinks(HTMLParser):
    """Collects every `<a>` opened inside an element whose class has `band-head`."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.offenders: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        inside = any(is_head for _, is_head in self.stack)
        if tag == "a" and inside and "band-link" in classes:
            self.offenders.append(self.getpos()[0])
        if tag not in VOID_ELEMENTS:
            self.stack.append((tag, "band-head" in classes))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return


def _templates() -> list[Path]:
    paths: list[Path] = []
    for root in TEMPLATE_ROOTS:
        paths.extend(sorted(root.rglob("*.html")))
    return paths


class SectionLinkTests(SimpleTestCase):
    def test_no_band_head_holds_a_link(self) -> None:
        failures: list[str] = []
        for path in _templates():
            source = path.read_text(encoding="utf-8")
            if "band-head" not in source:
                continue
            if str(path.relative_to(REPOSITORY_ROOT)) in PENDING_REDESIGN:
                continue
            parser = _BandHeadLinks()
            parser.feed(source)
            failures.extend(
                f"{path.relative_to(REPOSITORY_ROOT)}:{line}" for line in parser.offenders
            )
        self.assertEqual(
            failures,
            [],
            "A section link belongs in `.band-more` under the section, not in its heading row.",
        )

    def test_band_more_is_defined_once_and_on_the_left(self) -> None:
        rule = re.compile(r"^[ \t]*([^{}\n]*\.band-more\b[^{}\n]*)\{([^}]*)\}", re.MULTILINE)
        definitions: list[str] = []
        for path in _templates():
            for match in rule.finditer(path.read_text(encoding="utf-8")):
                relative = str(path.relative_to(REPOSITORY_ROOT))
                self.assertNotRegex(
                    match.group(2),
                    r"text-align:\s*(right|end|center)|justify-(content|self):\s*(flex-)?end|margin-left:\s*auto",
                    f"{relative}: a section link is never right-aligned",
                )
                if match.group(1).strip() == ".band-more":
                    definitions.append(relative)
        self.assertEqual(definitions, ["templates/core/_design_system.html"])
