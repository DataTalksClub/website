"""Design 5a parity for the blog article page (issue #179).

The article page carries its own inline stylesheet and is the first page in the
system built for reading, so this checks what only a browser can: the shared
palette actually paints, both themes hold, the reading column really is a
reading column, a body that mixes headings, lists and a wide code line does not
push the page sideways at 320px, and the heading anchors are reachable.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect

from content.public_data import public_projection

pytestmark = [pytest.mark.core]

SCREENSHOTS = Path(".tmp/screenshots/issue-179/article")
# The design 5a page ground.  The article opens warm — masthead, trail, title —
# and hands the page to the cool lavender reading band, which is also the last
# band, so `--page` follows it (`_docs/design/design-5a.md`, "the warm band marks
# where the page starts; it is not the page").  The dark theme keeps the
# partial's own `--page` ground.
LIGHT_BACKGROUND = "rgb(239, 241, 252)"
DARK_BACKGROUND = "rgb(19, 22, 42)"
VIEWPORTS = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)


def _shot(page: Page, name: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / name, full_page=True)


def _settle_analytics_preferences(page: Page) -> None:
    preferences = page.get_by_role("dialog", name="Optional analytics")
    if preferences.is_visible():
        preferences.get_by_role("button", name="Keep analytics off").click()
        expect(preferences).to_be_hidden()


def richest_article() -> dict[str, Any]:
    """The article whose body exercises the most heading levels, then the most blocks."""

    return max(
        public_projection()["articles"],
        key=lambda record: (
            len({block["level"] for block in record["blocks"] if block["kind"] == "heading"}),
            len(record["blocks"]),
        ),
    )


def _assert_no_horizontal_overflow(page: Page) -> None:
    overflow = page.evaluate(
        """() => ({
          viewport: document.documentElement.clientWidth,
          content: document.documentElement.scrollWidth,
          offenders: [...document.querySelectorAll('body *')]
            .filter((node) => {
              const rect = node.getBoundingClientRect();
              return rect.right > document.documentElement.clientWidth + 0.5;
            })
            .slice(0, 5)
            .map((node) => `${node.tagName.toLowerCase()}.${String(node.className)}`),
        })"""
    )
    assert overflow["content"] <= overflow["viewport"], overflow


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_the_article_page_renders_the_design_system_in_both_themes(
    page: Page,
    live_server,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    article = richest_article()
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )

    response = page.goto(f"{live_server.url}{article['public_path']}", wait_until="networkidle")
    assert response is not None and response.status == 200
    _settle_analytics_preferences(page)
    expect(page.locator('link[rel="stylesheet"]')).to_have_count(0)
    expect(page.locator("main h1")).to_have_count(1)
    expect(page.locator("body")).to_have_css("background-color", LIGHT_BACKGROUND)
    expect(page.locator("body")).not_to_contain_text("Traceback")
    expect(page.get_by_role("navigation", name="Breadcrumb")).to_be_visible()
    _assert_no_horizontal_overflow(page)
    _shot(page, f"article-{suffix}-light.png")

    page.locator("#dark-mode-toggle").click()
    expect(page.locator("body.dark-mode")).to_have_count(1)
    expect(page.locator("body")).to_have_css("background-color", DARK_BACKGROUND)
    _assert_no_horizontal_overflow(page)
    _shot(page, f"article-{suffix}-dark.png")
    page.locator("#dark-mode-toggle").click()
    expect(page.locator("body.dark-mode")).to_have_count(0)

    assert console_errors == []


def test_the_body_reads_at_a_measure_and_keeps_its_anchors(
    page: Page,
    live_server,
) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    article = richest_article()
    headings = [block for block in article["blocks"] if block["kind"] == "heading"]

    page.goto(f"{live_server.url}{article['public_path']}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    prose = page.locator(".prose")
    expect(prose).to_have_count(1)
    # A reading measure, not the full 76rem shell: wide enough for prose, never
    # the whole desktop window.
    width = prose.evaluate("(node) => node.getBoundingClientRect().width")
    assert 480 <= width <= 640, width
    # Every projected heading is on the page, at its own level, with its anchor.
    for block in headings:
        anchor = page.locator(f"#{block['id']}")
        expect(anchor).to_have_count(1)
        assert anchor.evaluate("(node) => node.tagName.toLowerCase()") == f"h{block['level']}"
    # The list items the body carries are real list items.
    assert page.locator(".prose ul li").count() >= 1
    marker = page.locator(".prose li").first.evaluate(
        "(node) => getComputedStyle(node, '::marker').color"
    )
    assert marker != "", marker


def test_a_code_sample_can_be_copied_with_accessible_feedback(
    browser: Browser,
    live_server,
) -> None:
    context = browser.new_context(
        permissions=["clipboard-read", "clipboard-write"],
        viewport={"width": 1440, "height": 900},
    )
    page = context.new_page()
    try:
        response = page.goto(
            f"{live_server.url}/blog/open-source-free-ai-agent-evaluation-tools.html",
            wait_until="networkidle",
        )
        assert response is not None and response.status == 200
        _settle_analytics_preferences(page)

        frame = page.locator(".code-block").first
        expect(frame).to_be_visible()
        code = frame.locator("pre code")
        expected = code.text_content() or ""
        button = frame.get_by_role("button", name="Copy code")
        expect(button).to_have_text("Copy")
        button.focus()
        button.press("Enter")

        expect(frame.get_by_role("status")).to_have_text("Code copied to clipboard.")
        expect(button).to_have_text("Copied")
        assert page.evaluate("navigator.clipboard.readText()") == expected
    finally:
        context.close()


def test_a_copy_failure_explains_the_manual_fallback(
    browser: Browser,
    live_server,
) -> None:
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    page.add_init_script(
        """
        Object.defineProperty(navigator, "clipboard", {
          configurable: true,
          value: {writeText: () => Promise.reject(new Error("blocked"))}
        });
        """
    )
    try:
        response = page.goto(
            f"{live_server.url}/blog/open-source-free-ai-agent-evaluation-tools.html",
            wait_until="networkidle",
        )
        assert response is not None and response.status == 200
        _settle_analytics_preferences(page)

        frame = page.locator(".code-block").first
        button = frame.get_by_role("button", name="Copy code")
        button.focus()
        button.press("Enter")

        expect(frame.get_by_role("status")).to_have_text(
            "Could not copy code. Select and copy it manually."
        )
        expect(button).to_have_text("Try again")
        expect(button).to_have_class(re.compile(r"\bis-error\b"))
    finally:
        context.close()


def test_a_code_sample_uses_language_tokens_in_both_themes(
    page: Page,
    live_server,
) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    response = page.goto(
        f"{live_server.url}/blog/open-source-free-ai-agent-evaluation-tools.html",
        wait_until="networkidle",
    )
    assert response is not None and response.status == 200
    _settle_analytics_preferences(page)

    code = page.locator(".prose pre code").first
    expect(code).to_have_attribute("data-code-language", "python")
    expect(code.locator(".code-token-keyword").first).to_be_visible()
    expect(code.locator(".code-token-string").first).to_be_visible()
    expect(code.locator(".code-token-function").first).to_be_visible()
    light_keyword = code.locator(".code-token-keyword").first.evaluate(
        "(node) => getComputedStyle(node).color"
    )

    page.locator("#dark-mode-toggle").click()
    expect(page.locator("body.dark-mode")).to_have_count(1)
    dark_keyword = code.locator(".code-token-keyword").first.evaluate(
        "(node) => getComputedStyle(node).color"
    )
    assert light_keyword != dark_keyword


def test_the_copy_control_is_invisible_until_hover_or_keyboard_focus(
    browser: Browser,
    live_server,
) -> None:
    """At rest a sample must look as though the control is not there.

    Hiding a control on hover is only acceptable when it stays reachable, so
    this checks the two failure modes that make the pattern unusable: a control
    dropped out of the tab order, and a control that never appears for a
    keyboard.  It also pins the 24px target-size floor, because quieter must
    not mean harder to hit.
    """

    context = browser.new_context(
        permissions=["clipboard-read", "clipboard-write"],
        viewport={"width": 1440, "height": 900},
    )
    page = context.new_page()
    try:
        response = page.goto(
            f"{live_server.url}/blog/open-source-free-ai-agent-evaluation-tools.html",
            wait_until="networkidle",
        )
        assert response is not None and response.status == 200
        _settle_analytics_preferences(page)

        frame = page.locator(".code-block").first
        frame.scroll_into_view_if_needed()
        page.mouse.move(1, 1)
        button = frame.get_by_role("button", name="Copy code")

        at_rest = button.evaluate(
            """(node) => {
              const style = getComputedStyle(node);
              const rect = node.getBoundingClientRect();
              return {
                opacity: style.opacity,
                display: style.display,
                visibility: style.visibility,
                width: rect.width,
                height: rect.height,
              };
            }"""
        )
        assert at_rest["opacity"] == "0", at_rest
        # Either of these would remove the control from the tab order and the
        # accessibility tree; opacity is deliberately the whole mechanism.
        assert at_rest["display"] != "none", at_rest
        assert at_rest["visibility"] != "hidden", at_rest
        # WCAG 2.2 target size.
        assert at_rest["width"] >= 24 and at_rest["height"] >= 24, at_rest

        # The block reserves no strip: the sample starts where its own padding
        # puts it, exactly as it does with the runtime switched off.
        padding = frame.locator("pre").evaluate("(node) => getComputedStyle(node).paddingTop")
        assert padding == "16px", padding

        box = frame.bounding_box()
        assert box is not None
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + 20)
        expect(button).to_have_css("opacity", "1")
        page.mouse.move(1, 1)
        expect(button).to_have_css("opacity", "0")

        # Real keyboard travel, not a programmatic focus call.
        for _ in range(200):
            page.keyboard.press("Tab")
            if page.evaluate("() => document.activeElement?.classList.contains('code-block-copy')"):
                break
        else:  # pragma: no cover - the control is in the tab order
            raise AssertionError("the copy control was never reached by keyboard")

        assert page.evaluate(
            "() => document.activeElement === document.querySelector('.code-block-copy')"
        )
        expect(button).to_have_css("opacity", "1")
        focused = page.evaluate(
            """() => {
              const node = document.activeElement;
              const style = getComputedStyle(node);
              const rect = node.getBoundingClientRect();
              const top = document.elementFromPoint(
                rect.left + rect.width / 2, rect.top + rect.height / 2
              );
              return {
                opacity: style.opacity,
                outlineWidth: parseFloat(style.outlineWidth),
                outlineStyle: style.outlineStyle,
                obscured: Boolean(top && top !== node && !node.contains(top)),
              };
            }"""
        )
        assert focused["opacity"] == "1", focused
        assert focused["outlineWidth"] >= 2 and focused["outlineStyle"] != "none", focused
        assert not focused["obscured"], focused

        # The answer to a press stays painted once the pointer and focus leave.
        page.keyboard.press("Enter")
        expect(button).to_have_text("Copied")
        page.evaluate("() => document.activeElement.blur()")
        page.mouse.move(1, 1)
        expect(button).to_have_css("opacity", "1")
    finally:
        context.close()


def test_a_touch_reader_always_sees_the_copy_control(
    browser: Browser,
    live_server,
) -> None:
    """A coarse pointer has no hover, so the control cannot hide behind one."""

    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        is_mobile=True,
        has_touch=True,
    )
    page = context.new_page()
    try:
        response = page.goto(
            f"{live_server.url}/blog/open-source-free-ai-agent-evaluation-tools.html",
            wait_until="networkidle",
        )
        assert response is not None and response.status == 200
        _settle_analytics_preferences(page)

        assert page.evaluate("() => matchMedia('(hover: none)').matches")
        frame = page.locator(".code-block").first
        frame.scroll_into_view_if_needed()
        button = frame.get_by_role("button", name="Copy code")
        expect(button).to_have_css("opacity", "1")

        # A control that cannot get out of the way gets room made for it.
        padding = frame.locator("pre").evaluate(
            "(node) => parseFloat(getComputedStyle(node).paddingTop)"
        )
        control = button.bounding_box()
        sample = frame.locator("pre code").bounding_box()
        assert control is not None and sample is not None
        assert padding > 16, padding
        assert control["y"] + control["height"] <= sample["y"], (control, sample)
    finally:
        context.close()


def test_a_code_sample_stays_plain_when_javascript_is_disabled(
    browser: Browser,
    live_server,
) -> None:
    context = browser.new_context(
        java_script_enabled=False,
        viewport={"width": 390, "height": 844},
    )
    page = context.new_page()
    try:
        response = page.goto(
            f"{live_server.url}/blog/open-source-free-ai-agent-evaluation-tools.html",
            wait_until="domcontentloaded",
        )
        assert response is not None and response.status == 200
        code = page.locator(".prose pre code").first
        expect(code).to_be_visible()
        expect(page.locator(".code-block-copy")).to_have_count(0)
        expect(page.locator(".code-token-keyword")).to_have_count(0)
        assert (code.text_content() or "").startswith("from arize.otel import register")
    finally:
        context.close()


def test_the_article_stays_usable_at_320px_without_javascript(
    browser: Browser,
    live_server,
) -> None:
    article = richest_article()
    context = browser.new_context(
        java_script_enabled=False,
        reduced_motion="reduce",
        viewport={"width": 320, "height": 800},
    )
    page = context.new_page()
    try:
        response = page.goto(
            f"{live_server.url}{article['public_path']}",
            wait_until="domcontentloaded",
        )
        assert response is not None and response.status == 200
        expect(page.locator("main h1")).to_have_count(1)
        expect(page.get_by_role("navigation", name="Primary navigation")).to_be_visible()
        expect(page.get_by_role("navigation", name="Breadcrumb")).to_be_visible()
        # The artwork is a social card, published in the head, never in the body.
        assert page.locator(".article-cover").count() == 0
        assert page.locator('meta[property="og:image"]').count() == 1
        _assert_no_horizontal_overflow(page)
        SCREENSHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=SCREENSHOTS / "article-320-no-js.png", full_page=True)
    finally:
        context.close()
