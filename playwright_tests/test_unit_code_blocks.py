"""A course lesson's fenced samples reach the browser coloured.

Course Markdown is rendered and then sanitized on every request, and the
language marker the fence writes -- `class="language-python"` on the `code`
element -- is the only thing the browser's code runtime can read out of a
sanitized body.  While the course allowlist dropped that class, every lesson
sample rendered as plain grey text with no error anywhere, so the failure was
invisible to a Django test that only asserted the sample survived.  This checks
the whole path in a browser: allowlist, runtime, and painted colour.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import Browser, Page, expect

from courses.models import Cohort, Course, CurriculumFormat, Homework, Module, Unit

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

# The first line of the python fence deliberately runs the full width of the
# reading column.  Samples wrap rather than scroll, so a long opening line is
# exactly what the floating copy control would paint over, and a short one would
# make the "covers no code" check below pass without proving anything.
LESSON_MARKDOWN = (
    "Set the client up:\n\n"
    "```python\n"
    "# Build the client once and reuse it for every request you make in this lesson.\n"
    'client = OpenAI(api_key="placeholder")\n'
    "```\n\n"
    "Then the payload it returns:\n\n"
    "```json\n"
    '{"doc_id": "abc123", "score": 0.91, "ok": true, "tags": ["rag", "search", "agents"]}\n'
    "```\n\n"
    "And a fence with no language:\n\n"
    "```\n"
    "make test\n"
    "```\n"
)

# Returns every code character whose centre falls inside the copy control's own
# box, per code block.  Reading it off the rendered ranges rather than the
# element boxes is what catches a control that sits on top of wrapped text.
COVERED_CHARACTERS = """
() => [...document.querySelectorAll('.code-block')].map((frame) => {
  const control = frame.querySelector('.code-block-copy').getBoundingClientRect();
  const walker = document.createTreeWalker(
    frame.querySelector('pre code'), NodeFilter.SHOW_TEXT
  );
  let hidden = '';
  while (walker.nextNode()) {
    const node = walker.currentNode;
    for (let index = 0; index < node.length; index += 1) {
      const range = document.createRange();
      range.setStart(node, index);
      range.setEnd(node, index + 1);
      const box = range.getBoundingClientRect();
      const x = box.left + box.width / 2;
      const y = box.top + box.height / 2;
      if (x > control.left && x < control.right && y > control.top && y < control.bottom) {
        hidden += node.data[index];
      }
    }
  }
  return hidden;
})
"""


@pytest.fixture
def lesson_with_code() -> tuple[Module, Unit]:
    course = Course.objects.create(slug="llm-zoomcamp-code", title="LLM Zoomcamp Code")
    cohort = Cohort.objects.create(
        course=course,
        slug="llm-zoomcamp-code-2026",
        identifier="2026",
        year=2026,
        title="LLM Zoomcamp Code 2026",
        description="A module-format cohort used by the lesson code-block checks.",
        curriculum_format=CurriculumFormat.MODULES,
    )
    homework = Homework.objects.create(
        course=cohort,
        slug="homework-01",
        title="Homework 1: Agentic RAG",
        due_date=timezone.now() + timedelta(days=7),
    )
    module = Module.objects.create(
        cohort=cohort,
        position=10,
        slug="01-agentic-rag",
        title="Agentic RAG",
        terminal_homework=homework,
    )
    unit = Unit.objects.create(
        module=module,
        position=10,
        slug="01-intro",
        title="Introduction",
        content_markdown=LESSON_MARKDOWN,
    )
    return module, unit


def _lesson_path(module: Module, unit: Unit) -> str:
    cohort = module.cohort
    return f"/courses/{cohort.course.slug}/{cohort.identifier}/modules/{module.slug}/{unit.slug}"


def _settle_analytics_preferences(page: Page) -> None:
    preferences = page.get_by_role("dialog", name="Optional analytics")
    if preferences.is_visible():
        preferences.get_by_role("button", name="Keep analytics off").click()
        expect(preferences).to_be_hidden()


def test_a_fenced_lesson_sample_is_coloured_by_its_language(
    page: Page,
    live_server,
    lesson_with_code: tuple[Module, Unit],
) -> None:
    module, unit = lesson_with_code
    cohort = module.cohort
    path = f"/courses/{cohort.course.slug}/{cohort.identifier}/modules/{module.slug}/{unit.slug}"
    page.set_viewport_size({"width": 1440, "height": 900})

    response = page.goto(f"{live_server.url}{path}", wait_until="networkidle")
    assert response is not None and response.status == 200
    _settle_analytics_preferences(page)

    samples = page.locator(".prose pre code")
    expect(samples).to_have_count(3)
    expect(samples.nth(0)).to_have_attribute("data-code-language", "python")
    expect(samples.nth(1)).to_have_attribute("data-code-language", "json")
    # A fence with no language stays plain rather than guessing: the frame
    # records the language it settled on, and the sample keeps no tokens.
    expect(page.locator(".prose pre").nth(2)).to_have_attribute("data-code-language", "plaintext")

    python_sample = samples.nth(0)
    expect(python_sample.locator(".code-token-comment").first).to_be_visible()
    expect(python_sample.locator(".code-token-string").first).to_be_visible()
    assert samples.nth(2).locator("[class^='code-token-']").count() == 0

    # Colour, not only markup: a token is painted differently from the body,
    # and the theme actually changes it.
    def token_colours() -> dict[str, str]:
        return python_sample.evaluate(
            """(node) => ({
              body: getComputedStyle(node).color,
              comment: getComputedStyle(node.querySelector('.code-token-comment')).color,
              string: getComputedStyle(node.querySelector('.code-token-string')).color,
            })"""
        )

    light = token_colours()
    assert light["comment"] != light["body"], light
    assert light["string"] != light["body"], light

    page.locator("#dark-mode-toggle").click()
    expect(page.locator("body.dark-mode")).to_have_count(1)
    dark = token_colours()
    assert dark["comment"] != dark["body"], dark
    assert dark["string"] != light["string"], (light, dark)


def test_the_revealed_copy_control_covers_no_code(
    page: Page,
    live_server,
    lesson_with_code: tuple[Module, Unit],
) -> None:
    """Revealing the control must not cost the reader any of the sample.

    Lesson samples wrap instead of scrolling, so a long opening line reaches the
    block's right edge and an overlaid control paints on top of it.  That is a
    reading failure and it is WCAG 2.1 SC 1.4.13: content revealed on hover may
    not obscure other content.  The margin the control floats over is therefore
    reserved by an empty float inside the sample, which shortens only the lines
    the control covers -- so the block must also not reflow when it appears.
    """

    module, unit = lesson_with_code
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{live_server.url}{_lesson_path(module, unit)}", wait_until="networkidle")
    _settle_analytics_preferences(page)
    page.mouse.move(1, 1)

    frame = page.locator(".code-block").first
    frame.scroll_into_view_if_needed()
    button = frame.get_by_role("button", name="Copy code")
    expect(button).to_have_css("opacity", "0")

    # The opening line is long enough for this check to mean something.
    assert page.evaluate(
        """() => {
          const pre = document.querySelector('.code-block pre');
          return getComputedStyle(pre).whiteSpace.startsWith('pre-wrap')
            && pre.querySelector('code').textContent.split('\\n')[0].length > 60;
        }"""
    )

    block_heights = """
    () => [...document.querySelectorAll('.code-block pre')]
      .map((node) => node.getBoundingClientRect().height)
    """
    at_rest = page.evaluate(block_heights)
    box = frame.bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 20)
    expect(button).to_have_css("opacity", "1")

    assert page.evaluate(COVERED_CHARACTERS) == ["", "", ""]
    revealed = page.evaluate(block_heights)
    assert revealed == at_rest, (at_rest, revealed)
    # No strip is reserved above the sample: the compact padding is what a
    # reader with JavaScript switched off sees.
    padding = frame.locator("pre").evaluate("(node) => getComputedStyle(node).paddingTop")
    assert padding == "16px", padding

    # The same has to hold for the state the control keeps after a press, which
    # carries the longest label the control ever shows.
    page.evaluate(
        """() => {
          const button = document.querySelector('.code-block-copy');
          button.textContent = 'Try again';
          button.classList.add('is-error');
        }"""
    )
    page.mouse.move(1, 1)
    expect(button).to_have_css("opacity", "1")
    assert page.evaluate(COVERED_CHARACTERS)[0] == ""

    # And for keyboard focus, which reveals the control with no pointer at all.
    page.reload(wait_until="networkidle")
    _settle_analytics_preferences(page)
    page.mouse.move(1, 1)
    for _ in range(300):
        page.keyboard.press("Tab")
        if page.evaluate(
            "() => !!document.activeElement"
            " && document.activeElement.classList.contains('code-block-copy')"
        ):
            break
    else:  # pragma: no cover - the control is in the tab order
        raise AssertionError("the copy control was never reached by keyboard")
    expect(page.locator(".code-block-copy").first).to_have_css("opacity", "1")
    assert page.evaluate(COVERED_CHARACTERS)[0] == ""


def test_a_touch_reader_loses_no_code_to_the_permanent_control(
    browser: Browser,
    live_server,
    lesson_with_code: tuple[Module, Unit],
) -> None:
    """A coarse pointer gets the strip instead of the gutter, and still no overlap."""

    module, unit = lesson_with_code
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        is_mobile=True,
        has_touch=True,
    )
    page = context.new_page()
    try:
        page.goto(f"{live_server.url}{_lesson_path(module, unit)}", wait_until="networkidle")
        _settle_analytics_preferences(page)
        assert page.evaluate("() => matchMedia('(hover: none)').matches")

        frame = page.locator(".code-block").first
        frame.scroll_into_view_if_needed()
        expect(frame.get_by_role("button", name="Copy code")).to_be_visible()
        padding = frame.locator("pre").evaluate(
            "(node) => parseFloat(getComputedStyle(node).paddingTop)"
        )
        assert padding > 16, padding
        assert page.evaluate(COVERED_CHARACTERS) == ["", "", ""]
        # The gutter is the overlay's companion, so it goes away with the overlay.
        assert (
            page.evaluate(
                "() => getComputedStyle(document.querySelector('.code-block-gutter')).display"
            )
            == "none"
        )
    finally:
        context.close()


def test_the_lesson_sanitizer_admits_only_the_language_class(
    page: Page,
    live_server,
    lesson_with_code: tuple[Module, Unit],
) -> None:
    """The allowlist is a security boundary: course bodies are upstream text."""

    module, unit = lesson_with_code
    unit.content_markdown = (
        '<code class="impersonation-banner" onclick="alert(1)">borrowed</code>\n\n'
        "```python\nimport os\n```\n\n"
        # The fence name reaches the browser from a public repository, so it is
        # untrusted text.  An inherited object name must stay the word it was
        # rather than resolving to something off `Object.prototype`.
        "```constructor\nimport os\n```\n"
    )
    unit.save(update_fields=["content_markdown"])
    cohort = module.cohort
    path = f"/courses/{cohort.course.slug}/{cohort.identifier}/modules/{module.slug}/{unit.slug}"

    response = page.goto(f"{live_server.url}{path}", wait_until="networkidle")
    assert response is not None and response.status == 200
    _settle_analytics_preferences(page)

    borrowed = page.evaluate(
        """() => [...document.querySelectorAll('.unit-content code[class]')]
             .map((node) => node.className)
             .filter((name) => !/^language-[a-z0-9]+( code-highlighted)?$/.test(name))"""
    )
    assert borrowed == [], borrowed
    handlers = page.evaluate("() => document.querySelectorAll('.unit-content [onclick]').length")
    assert handlers == 0, handlers
    expect(page.locator(".unit-content code", has_text="borrowed").first).to_be_visible()

    # The runtime reads its language maps by own property, so an inherited name
    # is an unknown language rather than whatever `Object.prototype` holds.
    languages = page.evaluate(
        """() => [...document.querySelectorAll('.unit-content pre')]
             .map((node) => node.getAttribute('data-code-language'))"""
    )
    assert languages == ["python", "constructor"], languages
