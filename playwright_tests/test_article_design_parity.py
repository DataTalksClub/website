"""Design 5a parity for the blog article page (issue #179).

The article page carries its own inline stylesheet and is the first page in the
system built for reading, so this checks what only a browser can: the shared
palette actually paints, both themes hold, the reading column really is a
reading column, a body that mixes headings, lists and a wide code line does not
push the page sideways at 320px, and the heading anchors are reachable.
"""

from __future__ import annotations

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
        # The cover never outgrows its column.
        cover = page.locator(".article-cover img")
        if cover.count():
            assert cover.evaluate("(node) => node.getBoundingClientRect().right") <= 320.5
        _assert_no_horizontal_overflow(page)
        SCREENSHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=SCREENSHOTS / "article-320-no-js.png", full_page=True)
    finally:
        context.close()
