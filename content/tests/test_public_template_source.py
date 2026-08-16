from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

from content.public_data import EVENT_TYPE_ICONS
from core.templatetags.public import public_text

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_TEMPLATE_PATHS = (
    REPOSITORY_ROOT / "templates/404.html",
    REPOSITORY_ROOT / "accounts/templates/accounts/login.html",
    REPOSITORY_ROOT / "templates/core/base.html",
    REPOSITORY_ROOT / "templates/core/home.html",
    # The courses index left the adopted shell with the design 5a rebuild (issue #179)
    # and is now a public page in its own right.
    REPOSITORY_ROOT / "courses/templates/courses/course_list.html",
    *(sorted((REPOSITORY_ROOT / "templates/public").glob("*.html"))),
    *(sorted((REPOSITORY_ROOT / "templates/review").glob("*.html"))),
)
PUBLIC_COPY_PYTHON_PATHS = (
    REPOSITORY_ROOT / "content/public_views.py",
    REPOSITORY_ROOT / "content/review_views.py",
)
FORBIDDEN_VISITOR_COPY = (
    "accepted source",
    "checked editorial source",
    "checked public collection",
    "checked public profiles",
    "checked records",
    "checked source",
    "current source snapshot",
    "public projection",
    "review build",
    "tracked catalogs",
    "tracked edition",
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
        # The homepage left the divided-row composition with the design 5a rebuild
        # (issue #179) and now owns its own card grid.  The events index and the
        # podcast index left it with the same rebuild, on mockups 6c and 6d: their
        # rows are design 5a `.row-list` rows, which draw the same dashed division
        # from the shared partial instead of the adopted shell's utility classes.
        paths = (
            REPOSITORY_ROOT / "templates/public/collection_hub.html",
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

    def test_event_types_use_the_original_site_semantics_with_distinct_icons(self) -> None:
        self.assertEqual(
            EVENT_TYPE_ICONS,
            {
                "conference": "fas fa-briefcase",
                "podcast": "fas fa-microphone-alt",
                "webinar": "fas fa-tv",
                "workshop": "fas fa-wrench",
            },
        )

    def test_public_copy_does_not_expose_projection_or_qa_language(self) -> None:
        failures: list[str] = []
        for path in (*PUBLIC_TEMPLATE_PATHS, *PUBLIC_COPY_PYTHON_PATHS):
            source = path.read_text(encoding="utf-8").casefold()
            for phrase in FORBIDDEN_VISITOR_COPY:
                if phrase in source:
                    failures.append(f"{path.relative_to(REPOSITORY_ROOT)}: {phrase}")
            if path.suffix == ".html" and "data-source-" in source:
                failures.append(f"{path.relative_to(REPOSITORY_ROOT)}: data-source- attribute")

        self.assertEqual(failures, [])

    def test_public_templates_have_no_source_provenance_partial_or_include(self) -> None:
        self.assertFalse((REPOSITORY_ROOT / "templates/public/_source.html").exists())
        self.assertFalse((REPOSITORY_ROOT / "templates/review/_source_link.html").exists())
        for path in PUBLIC_TEMPLATE_PATHS:
            source = path.read_text(encoding="utf-8").casefold()
            with self.subTest(path=path.relative_to(REPOSITORY_ROOT)):
                self.assertNotIn("view source on github", source)
                self.assertNotIn("this page is maintained on", source)
                self.assertNotIn('include "public/_source.html"', source)
                self.assertNotIn('include "review/_source_link.html"', source)

    def test_book_archive_text_escapes_markup_and_keeps_reviewed_links(self) -> None:
        rendered = str(
            public_text(
                "<script>alert(1)</script> See [the guide](https://example.com/guide)\n"
                "next line &gt; and &amp;"
            )
        )
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)
        self.assertIn('href="https://example.com/guide"', rendered)
        self.assertIn("<br>next line &gt; and &amp;", rendered)
