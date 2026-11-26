"""Numbered steps are one component, as a check instead of a memory.

Every "how it works", process and numbered-step block on the site is the list
``templates/core/_steps.html`` draws, styled once in the "numbered steps"
section of ``templates/core/_design_system.html`` and called through
``{% steps %}``/``{% step %}`` (``core/templatetags/steps.py``).  Before it the
site had seven hand-made versions -- heavy ink rules over a green mono "01",
drawn discs on cards, "01" rows behind a rule, numbered lines in a card -- and
each page that needed a sequence invented another
(``_docs/design/design-system.md#numbered-steps``).

This test fails when a template:

* writes the component's markup itself instead of rendering the partial;
* defines CSS for the component's classes anywhere but the design system;
* brings back one of the retired implementations, a zero-padded "01" index,
  or a hand-numbered badge outside the reviewed non-step lists below.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.template import Context, Template
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]

TEMPLATE_GLOBS = ("templates/**/*.html", "*/templates/**/*.html")

PARTIAL = "templates/core/_steps.html"
DESIGN_SYSTEM = "templates/core/_design_system.html"

# The component's own class names: only the partial writes them as markup and
# only the design system styles them.
COMPONENT_MARKUP = re.compile(
    r'class="(?:[^"]*\s)?steps(?:-frame|-item|-badge|-title|-body|-count-\d+)?(?:[\s"])'
)
COMPONENT_CSS = re.compile(r"(?m)^[^{}\n]*(?<![\w-])\.steps(?![\w])(?:-[a-z0-9-]+)?[^{}\n]*\{")

# The implementations the component replaced.  None may come back.
RETIRED_CLASSES = (
    "step-grid",
    "step-no",
    "how-it-works",
    "wiki-steps",
    "wiki-step",
    "slack-steps",
    "slack-step",
    "slack-step-head",
    "academy-steps",
    "academy-step",
    "academy-numbered",
    "registration-features",
    "registration-feature-row",
    "registration-feature-index",
)
# As a class in markup or a selector in CSS; ids such as `how-it-works-heading`
# are anchors, not implementations, and stay.
RETIRED = re.compile(
    r"(?:\.|class=\"(?:[^\"]*\s)?)(?:"
    + "|".join(re.escape(name) for name in RETIRED_CLASSES)
    + r")(?![\w-])"
)

# A zero-padded ordinal ("01") is the mono index the component retired.
ZERO_PADDED = re.compile(r'stringformat:"02d"|>\s*0[1-9]\s*<')

# A hand-drawn number badge: a decorative digit (literal or loop counter).
NUMBER_BADGE = re.compile(
    r'aria-hidden="true">\s*(?:\d{1,2}|\{\{\s*forloop\.counter[^}]*\}\})\s*</span>'
)

# Numbered lists that are not steps, each with the reason.  A sequence of steps
# is never one of these: it renders the partial.
NUMBERED_NON_STEPS = {
    "courses/templates/homework/_submission_form.html": (
        "question numbers on a form, answered state on each; a navigator, not a sequence"
    ),
    "courses/templates/courses/module.html": "a module's lesson list (syllabus)",
    "courses/templates/courses/_shared_module_rail.html": "the lesson rail's read indicators",
}

# `.step-number` is the drawn disc of the illustrated stage card and the
# leaderboard podium, not a list badge.
STEP_NUMBER_OWNERS = {
    "templates/core/_stage_card.html",
    "courses/templates/courses/wrapped.html",
    DESIGN_SYSTEM,
}


def _templates():
    seen = set()
    for pattern in TEMPLATE_GLOBS:
        for path in ROOT.glob(pattern):
            rel = path.relative_to(ROOT).as_posix()
            if rel in seen or any(part.startswith(".") for part in path.relative_to(ROOT).parts):
                continue
            seen.add(rel)
            yield rel, path.read_text(encoding="utf-8")


def _lines(text: str, pattern: re.Pattern[str]) -> list[int]:
    return [text.count("\n", 0, match.start()) + 1 for match in pattern.finditer(text)]


class NumberedStepsContractTests(SimpleTestCase):
    def test_only_the_partial_writes_the_steps_markup(self):
        found = [
            f"{rel}:{line}"
            for rel, text in _templates()
            if rel != PARTIAL
            for line in _lines(text, COMPONENT_MARKUP)
        ]
        self.assertEqual(found, [], "render core/_steps.html via {% steps %} instead")

    def test_only_the_design_system_styles_the_steps(self):
        found = [
            f"{rel}:{line}"
            for rel, text in _templates()
            if rel != DESIGN_SYSTEM
            for line in _lines(text, COMPONENT_CSS)
        ]
        self.assertEqual(found, [], "the steps are styled once, in _design_system.html")

    def test_retired_step_implementations_stay_gone(self):
        found = [
            f"{rel}:{line}"
            for rel, text in _templates()
            for pattern in (RETIRED, ZERO_PADDED)
            for line in _lines(text, pattern)
        ]
        self.assertEqual(found, [])

    def test_no_hand_numbered_badges_outside_reviewed_lists(self):
        found = [
            f"{rel}:{line}"
            for rel, text in _templates()
            if rel != PARTIAL and rel not in NUMBERED_NON_STEPS
            for line in _lines(text, NUMBER_BADGE)
        ]
        self.assertEqual(found, [], "a numbered sequence renders core/_steps.html")

    def test_step_number_disc_stays_with_its_owners(self):
        found = [
            f"{rel}:{line}"
            for rel, text in _templates()
            if rel not in STEP_NUMBER_OWNERS
            for line in _lines(text, re.compile(r'class="[^"]*\bstep-number\b'))
        ]
        self.assertEqual(found, [])

    def test_the_tag_renders_a_real_ordered_list(self):
        html = Template(
            "{% load steps %}{% steps heading_level=4 %}"
            '{% step title="One" %}First <code>x</code>{% endstep %}'
            "{% for t in items %}{% step title=t %}{{ t }}{% endstep %}{% endfor %}"
            "{% step %}Untitled{% endstep %}{% endsteps %}"
        ).render(Context({"items": ["Two & <b>"]}))
        self.assertIn('<ol class="steps steps-count-3" role="list">', html)
        self.assertEqual(html.count('<li class="steps-item">'), 3)
        self.assertIn('<span class="steps-badge" aria-hidden="true">3</span>', html)
        self.assertIn('<h4 class="steps-title">One</h4>', html)
        self.assertIn('<p class="steps-body">First <code>x</code></p>', html)
        self.assertIn('<h4 class="steps-title">Two &amp; &lt;b&gt;</h4>', html)
        self.assertIn('<p class="steps-body">Two &amp; &lt;b&gt;</p>', html)
        self.assertEqual(html.count("steps-title"), 2)

    def test_the_scanners_catch_a_page_local_copy(self):
        self.assertTrue(COMPONENT_MARKUP.search('<li class="steps-item">'))
        self.assertTrue(COMPONENT_CSS.search("      .steps-badge {"))
        self.assertTrue(RETIRED.search('<div class="step-grid">'))
        self.assertTrue(RETIRED.search("      .slack-step h3 {"))
        self.assertFalse(RETIRED.search('<h2 id="slack-steps-heading">'))
        self.assertFalse(RETIRED.search('<h3 class="academy-steps-heading">'))
        self.assertTrue(ZERO_PADDED.search('<span class="x">01</span>'))
        self.assertTrue(NUMBER_BADGE.search('<span class="n" aria-hidden="true">2</span>'))
        self.assertFalse(COMPONENT_MARKUP.search('<section class="zoomcamp-steps">'))
        self.assertTrue(COMPONENT_CSS.search("      .zoomcamp-steps .steps-frame {"))
        self.assertFalse(COMPONENT_CSS.search("      .zoomcamp-steps > * {"))
