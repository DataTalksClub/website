from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_TEMPLATE_PATHS = (
    REPOSITORY_ROOT / "templates/404.html",
    REPOSITORY_ROOT / "templates/core/base.html",
    REPOSITORY_ROOT / "templates/core/home.html",
    REPOSITORY_ROOT / "templates/review/course_family.html",
    *(sorted((REPOSITORY_ROOT / "templates/public").glob("*.html"))),
)
DJANGO_STRUCTURAL_TAG = re.compile(
    r"{%\s*(?:block|endblock|if|elif|else|endif|for|empty|endfor|include)\b"
)
HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
HTML_OPENING_TAG = re.compile(r"<(?!/)[A-Za-z][^>]*>")


def readability_violations(source: str) -> list[str]:
    violations = []
    for line_number, line in enumerate(source.splitlines(), start=1):
        structural_tags = DJANGO_STRUCTURAL_TAG.findall(line)
        if len(structural_tags) > 1:
            violations.append(f"line {line_number}: multiple Django structural tags")
        if structural_tags and HTML_TAG.search(line):
            violations.append(f"line {line_number}: Django structural tag shares HTML markup")
        if len(HTML_OPENING_TAG.findall(line)) > 1:
            violations.append(f"line {line_number}: adjacent nested opening tags")
    return violations


class PublicTemplateSourceTests(SimpleTestCase):
    def test_public_template_structure_is_readable_and_line_broken(self) -> None:
        failures: list[str] = []
        for path in PUBLIC_TEMPLATE_PATHS:
            relative = path.relative_to(REPOSITORY_ROOT)
            failures.extend(
                f"{relative}:{violation}"
                for violation in readability_violations(path.read_text(encoding="utf-8"))
            )
        self.assertEqual(failures, [])

    def test_source_scan_rejects_compacted_future_template_markup(self) -> None:
        compacted_sources = (
            "{% block title %}Title{% endblock %}",
            "{% if value %}<p>Value</p>{% endif %}",
            '{% for value in values %}{% include "item.html" %}{% endfor %}',
            '<div>{% include "item.html" %}</div>',
            '<li><a href="/">Item</a></li>',
        )
        for source in compacted_sources:
            with self.subTest(source=source):
                self.assertTrue(readability_violations(source))

    def test_collection_surfaces_use_divided_rows_instead_of_column_grids(self) -> None:
        paths = (
            REPOSITORY_ROOT / "templates/core/home.html",
            REPOSITORY_ROOT / "templates/public/collection_hub.html",
            REPOSITORY_ROOT / "templates/public/course_hub.html",
            REPOSITORY_ROOT / "templates/public/events.html",
            REPOSITORY_ROOT / "templates/public/people_hub.html",
            REPOSITORY_ROOT / "templates/public/wiki_hub.html",
        )
        for path in paths:
            with self.subTest(path=path.relative_to(REPOSITORY_ROOT)):
                source = path.read_text(encoding="utf-8")
                self.assertNotRegex(source, r"(?:sm|md|lg):grid-cols-")
                self.assertIn("divide-y", source)

    def test_event_icons_keep_text_alternatives_and_hide_decoration(self) -> None:
        source = (REPOSITORY_ROOT / "templates/public/_event_meta.html").read_text(encoding="utf-8")
        self.assertEqual(source.count('aria-hidden="true"'), 3)
        self.assertIn('class="sr-only"', source)
