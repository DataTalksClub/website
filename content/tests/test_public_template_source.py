from __future__ import annotations

import re
from pathlib import Path

from django.test import SimpleTestCase

from core.accessibility_registry import template_readability_issues

from content.public_data import EVENT_TYPE_ICONS
from core.templatetags.public import public_text

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_TEMPLATE_PATHS = (
    REPOSITORY_ROOT / "templates/404.html",
    REPOSITORY_ROOT / "accounts/templates/accounts/login.html",
    # `templates/core/base.html` was read here until issue #179 finished porting the
    # site to design 5a: it was the public shell every page extended.  Design 5a pages
    # are complete documents that include the two shell partials below instead, so the
    # old base has been deleted and the markup it used to own is read from those.
    REPOSITORY_ROOT / "templates/core/home.html",
    # The design 5a shell now lives in two partials that every rebuilt page includes
    # (issue #179), so the markup those pages used to carry answers here instead.
    REPOSITORY_ROOT / "templates/core/_site_shell_head.html",
    REPOSITORY_ROOT / "templates/core/_site_shell_foot.html",
    # The courses index left the adopted shell with the design 5a rebuild (issue #179)
    # and is now a public page in its own right.
    REPOSITORY_ROOT / "courses/templates/courses/course_list.html",
    # The course page joined the design 5a system with issue #179 and now carries its
    # own stylesheet and markup, so it answers to the same readability contract.
    REPOSITORY_ROOT / "courses/templates/courses/course.html",
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
# The rule itself lives in core.accessibility_registry, which is what the site's
# own readability contract runs.  This module used to carry a second copy of it;
# two implementations of one contract drift, and this one did — it kept failing
# `<pre><code>`, which the shared rule exempts because everything between those
# tags is rendered verbatim.
readability_violations = template_readability_issues


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

    # `test_collection_surfaces_use_divided_rows_instead_of_column_grids` lived here
    # and asserted the adopted shell's `divide-y` on the collection surfaces.  Every
    # surface it named has since been rebuilt on design 5a (issue #179) — the
    # homepage, the events and podcast indexes, then the wiki hub, and now the
    # blog/books hub — and each draws its dashed division from the shared
    # `.row-list`/`.list-row` primitives instead.  The contract it protected is the
    # composition, not the class name, so it is kept in the language each surface is
    # written in: `test_collection_hub_rows_stay_a_divided_list` below for the
    # collection hub, and `content/tests/test_wiki_design.py` for the wiki surfaces.
    # The test itself is gone rather than left iterating over an empty tuple, which
    # would assert nothing while still reporting a pass.

    def test_collection_hub_rows_stay_a_divided_list(self) -> None:
        """The blog and books archives are one divided list, never a card grid.

        The hub joined design 5a with issue #179, so the dashed division now
        comes from the shared `.row-list`/`.list-row` primitives instead of the
        adopted shell's `divide-y`.  The composition contract is unchanged: one
        record per divided row, in one column.
        """

        source = (REPOSITORY_ROOT / "templates/public/collection_hub.html").read_text(
            encoding="utf-8"
        )
        self.assertNotRegex(source, r"(?:sm|md|lg):grid-cols-")
        self.assertNotIn("card-grid", source)
        self.assertIn('class="row-list collection-rows"', source)
        # The row itself is the shared archive row, so the composition contract
        # is now that the hub draws one of those per record.
        self.assertIn('include "public/_archive_row.html"', source)
        self.assertIn('row_class="record-row"', source)

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
