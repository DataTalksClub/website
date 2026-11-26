"""The site has one FAQ block, and every list of questions renders through it.

Owner rule (2026-11-26): "we have so many different ways of doing FAQ -- one
canonical one, documented in the design system."  The canonical block is
``templates/core/_faq.html``: open questions (no accordion), the question as a
bold heading with its answer right under it, the soft rule between items, two
columns only for four or more short items, and the ``FAQPage`` JSON-LD written
from the same items.  Its CSS lives once in ``templates/core/_design_system.html``.
See _docs/design/design-system.md, "FAQ".

These checks fail when a template writes its own FAQ again: its own question
markup or CSS, one of the retired FAQ class names, the disclosure fold used for
questions, or a ``FAQPage`` node built anywhere but the block's template tag.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.template import Context, Template
from django.test import SimpleTestCase

from core.templatetags.faq import faq_columns

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOTS = (
    REPOSITORY_ROOT / "templates",
    *sorted(path for path in REPOSITORY_ROOT.glob("*/templates") if path.is_dir()),
)

PARTIAL = "templates/core/_faq.html"
DESIGN_SYSTEM = "templates/core/_design_system.html"
TEMPLATE_TAG = "core/templatetags/faq.py"

#: The disclosure fold (`.faq-panel` + `.faq-fold`) folds one long block that is
#: not a list of questions.  These are its only users; a new one is a design
#: decision, and a list of questions never is one.
DISCLOSURE_USERS = {
    # An episode's transcript.
    "templates/public/podcast_detail.html",
    # A dashboard's per-homework question breakdown (statistics, not answers).
    "courses/templates/courses/dashboard.html",
}
DISCLOSURE_CLASSES = re.compile(r"\bfaq-(?:fold|panel)\b")

#: Class names of the FAQ implementations the canonical block replaced.
RETIRED_CLASSES = re.compile(
    r"(?<![\w-])\.?(?:faq-grid|faq-item|faq-note|family-faq-list|faq-permalink(?:-row)?"
    r"|faq-question-head|academy-faq)(?![\w-])"
)
COMPONENT_CLASS = re.compile(r"(?<![\w-])faq-list[\w-]*")
COMPONENT_SELECTOR = re.compile(r"\.faq-list[\w-]*[^{};]*\{")
#: A heading whose whole content is a question field: `<h3>{{ item.question }}</h3>`.
QUESTION_HEADING = re.compile(r"<h[1-6][^>]*>\s*\{\{\s*[\w.]+\.question\s*\}\}")


def _relative(path: Path) -> str:
    return str(path.relative_to(REPOSITORY_ROOT))


def _templates() -> list[Path]:
    paths: list[Path] = []
    for root in TEMPLATE_ROOTS:
        paths.extend(sorted(root.rglob("*.html")))
    return paths


def _python_sources() -> list[Path]:
    paths: list[Path] = []
    for top in sorted(REPOSITORY_ROOT.iterdir()):
        if top.is_dir() and not top.name.startswith(".") and top.name != "node_modules":
            paths.extend(sorted(top.rglob("*.py")))
    return paths


class FaqComponentContractTests(SimpleTestCase):
    def test_only_the_partial_draws_faq_markup(self) -> None:
        failures = []
        for path in _templates():
            name = _relative(path)
            if name in {PARTIAL, DESIGN_SYSTEM}:
                continue
            source = path.read_text(encoding="utf-8")
            for match in COMPONENT_CLASS.finditer(source):
                line = source.count("\n", 0, match.start()) + 1
                failures.append(f"{name}:{line} uses {match.group(0)}; include {PARTIAL}")
            for match in QUESTION_HEADING.finditer(source):
                line = source.count("\n", 0, match.start()) + 1
                failures.append(f"{name}:{line} draws its own question heading")
        self.assertEqual(failures, [])

    def test_only_the_design_system_styles_the_faq(self) -> None:
        failures = []
        for path in _templates():
            name = _relative(path)
            if name == DESIGN_SYSTEM:
                continue
            source = path.read_text(encoding="utf-8")
            if COMPONENT_SELECTOR.search(source):
                failures.append(f"{name} restyles .faq-list; change {DESIGN_SYSTEM}")
        self.assertEqual(failures, [])
        design_system = (REPOSITORY_ROOT / DESIGN_SYSTEM).read_text(encoding="utf-8")
        for selector in (".faq-list {", ".faq-list-item {", ".faq-list-two-column {"):
            self.assertIn(selector, design_system)

    def test_retired_faq_classes_are_gone(self) -> None:
        failures = []
        for path in _templates():
            source = path.read_text(encoding="utf-8")
            for match in RETIRED_CLASSES.finditer(source):
                line = source.count("\n", 0, match.start()) + 1
                failures.append(f"{_relative(path)}:{line} {match.group(0)}")
        self.assertEqual(failures, [])

    def test_the_disclosure_fold_is_not_used_for_questions(self) -> None:
        users = set()
        for path in _templates():
            name = _relative(path)
            if name == DESIGN_SYSTEM:
                continue
            if DISCLOSURE_CLASSES.search(path.read_text(encoding="utf-8")):
                users.add(name)
        self.assertEqual(users - DISCLOSURE_USERS, set())

    def test_only_the_template_tag_builds_faqpage_data(self) -> None:
        failures = []
        for path in _python_sources():
            name = _relative(path)
            if name == TEMPLATE_TAG or "/tests/" in f"/{name}":
                continue
            if '"FAQPage"' in path.read_text(encoding="utf-8"):
                failures.append(name)
        for path in _templates():
            if _relative(path) != PARTIAL and "FAQPage" in path.read_text(encoding="utf-8"):
                failures.append(_relative(path))
        self.assertEqual(failures, [])


class FaqBlockRenderTests(SimpleTestCase):
    def _render(self, template: str, **context) -> str:
        return Template(template).render(Context(context))

    def test_items_render_open_with_one_faqpage(self) -> None:
        items = [
            {
                "id": "faq-1",
                "question": "Is it free?",
                "answer_html": "<p>Yes, <a href='/x'>always</a>.</p>",
            },
            {"question": "Can I join late?", "answer": "Yes & welcome."},
        ]
        html = self._render('{% include "core/_faq.html" with items=items %}', items=items)
        self.assertNotIn("<details", html)
        self.assertIn('<div class="faq-list-item" id="faq-1">', html)
        self.assertIn('<h3 class="faq-list-question">Is it free?</h3>', html)
        self.assertIn("<p>Yes &amp; welcome.</p>", html)
        self.assertEqual(html.count('"@type": "FAQPage"'), 1)
        self.assertIn('"text": "Yes, always."', html)
        self.assertNotIn("faq-list-two-column", html)

    def test_structured_data_can_be_left_to_the_page(self) -> None:
        items = [{"question": "Q?", "answer": "A."}]
        html = self._render(
            '{% include "core/_faq.html" with items=items structured_data=False %}', items=items
        )
        self.assertNotIn("FAQPage", html)

    def test_closing_line_and_empty_list(self) -> None:
        items = [{"question": "Q?", "answer": "A."}]
        html = self._render(
            '{% include "core/_faq.html" with items=items closing="More?" '
            'closing_url="/slack" closing_label="Join Slack" %}',
            items=items,
        )
        self.assertIn('<p class="faq-list-closing">', html)
        self.assertIn('<a class="band-link" href="/slack">Join Slack</a>', html)
        self.assertEqual(
            self._render('{% include "core/_faq.html" with items=items %}', items=[]).strip(), ""
        )

    def test_two_columns_only_for_four_or_more_short_items(self) -> None:
        short = {"question": "Is it free?", "answer": "Yes."}
        long_answer = {"question": "Is it free?", "answer": "word " * 80}
        self.assertEqual(faq_columns([short] * 3), 1)
        self.assertEqual(faq_columns([short] * 4), 2)
        self.assertEqual(faq_columns([short] * 3 + [long_answer]), 1)
        self.assertEqual(faq_columns([]), 1)


class FaqBlockTagTests(SimpleTestCase):
    """Copy written in a template goes through `{% faq %}`, not into Python."""

    def test_block_tag_collects_items_and_renders_the_partial(self) -> None:
        html = Template(
            "{% load faq %}"
            '{% faq items=items closing="More?" closing_url="/slack" closing_label="Join" %}'
            '{% faqitem question="Is it free?" unless_asked="free" %}Yes, always.{% endfaqitem %}'
            '{% faqitem question="Can I join?" %}<p>Any <a href="/x">time</a>.</p>{% endfaqitem %}'
            "{% endfaq %}"
        ).render(Context({"items": [{"question": "Q from data?", "answer": "A."}]}))
        self.assertIn('<h3 class="faq-list-question">Q from data?</h3>', html)
        self.assertIn('<h3 class="faq-list-question">Is it free?</h3>', html)
        self.assertIn("<p>Yes, always.</p>", html)
        self.assertIn('<h3 class="faq-list-question">Can I join?</h3>', html)
        self.assertIn('<p>Any <a href="/x">time</a>.</p>', html)
        self.assertIn('<a class="band-link" href="/slack">Join</a>', html)
        self.assertEqual(html.count('"@type": "FAQPage"'), 1)

    def test_unless_asked_drops_a_repeated_subject(self) -> None:
        html = Template(
            "{% load faq %}{% faq items=items %}"
            '{% faqitem question="Is it really free?" unless_asked="free" %}Yes.{% endfaqitem %}'
            "{% endfaq %}"
        ).render(Context({"items": [{"question": "Is the course free?", "answer": "Yes."}]}))
        self.assertNotIn("Is it really free?", html)
        self.assertIn("Is the course free?", html)

    def test_a_faqitem_outside_the_block_is_an_error(self) -> None:
        from django.template import TemplateSyntaxError

        with self.assertRaises(TemplateSyntaxError):
            Template('{% load faq %}{% faqitem question="Q?" %}A{% endfaqitem %}').render(
                Context({})
            )
