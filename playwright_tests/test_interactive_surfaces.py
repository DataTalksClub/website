from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

pytestmark = [pytest.mark.core]
SCREENSHOTS = Path(".tmp/screenshots/card-interactions")


def _state(locator) -> dict[str, object]:
    return locator.evaluate(
        """(node) => {
          const style = getComputedStyle(node);
          const cue = getComputedStyle(node, '::before');
          const rect = node.getBoundingClientRect();
          return {
            x: rect.x,
            y: rect.y,
            translate: style.translate,
            cueContent: cue.content,
            cueOpacity: cue.opacity,
            cueBottom: cue.bottom,
            cueRight: cue.right,
          };
        }"""
    )


def test_podcast_cards_and_controls_share_hover_and_keyboard_affordances(
    browser: Browser,
    live_server,
) -> None:
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        has_touch=False,
        reduced_motion="no-preference",
    )
    page = context.new_page()
    response = page.goto(f"{live_server.url}/podcast", wait_until="networkidle")
    assert response is not None and response.status == 200

    card = page.locator("[data-podcast-episode] .archive-card").first
    title = card.locator(".archive-title a")
    platform = page.locator(".podcast-platforms .pill-button").first
    assert "interactive-card" in (card.get_attribute("class") or "")
    assert "interactive-lift" in (card.get_attribute("class") or "")

    initial = _state(card)
    assert initial["cueContent"] == '"→"'
    assert initial["cueOpacity"] == "0"
    assert float(str(initial["cueBottom"]).removesuffix("px")) > 0
    assert float(str(initial["cueRight"]).removesuffix("px")) > 0
    assert title.evaluate("node => getComputedStyle(node).textDecorationLine") == "none"

    card.hover()
    page.wait_for_timeout(180)
    hovered = _state(card)
    assert hovered["translate"] == "-2px -2px"
    assert hovered["cueOpacity"] == "1"
    assert title.evaluate("node => getComputedStyle(node).textDecorationLine") == "none"

    assert platform.evaluate("node => getComputedStyle(node).translate") == "none"
    platform.hover()
    page.wait_for_timeout(180)
    assert platform.evaluate("node => getComputedStyle(node).translate") == "-2px -2px"

    title.focus()
    page.wait_for_timeout(180)
    focused = _state(card)
    assert focused["cueOpacity"] == "1"
    assert focused["translate"] == "-2px -2px"

    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    card.screenshot(path=SCREENSHOTS / "podcast-card-desktop-hover-focus.png")
    context.close()


def test_event_card_uses_the_same_bottom_right_cue(page: Page, live_server) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    response = page.goto(f"{live_server.url}/events", wait_until="networkidle")
    assert response is not None and response.status == 200

    card = page.locator(".event-card").first
    title = card.locator("h3 a")
    card.hover()
    page.wait_for_timeout(180)

    state = _state(card)
    assert state["cueContent"] == '"→"'
    assert state["cueOpacity"] == "1"
    assert title.evaluate("node => getComputedStyle(node).textDecorationLine") == "none"
    card.screenshot(path=SCREENSHOTS / "event-card-desktop-hover.png")


def test_touch_and_reduced_motion_keep_layout_still_but_keyboard_cue_available(
    browser: Browser,
    live_server,
) -> None:
    reduced_context = browser.new_context(
        viewport={"width": 390, "height": 844},
        reduced_motion="reduce",
    )
    reduced_page = reduced_context.new_page()
    reduced_page.goto(f"{live_server.url}/podcast", wait_until="networkidle")
    card = reduced_page.locator("[data-podcast-episode] .archive-card").first
    title = card.locator(".archive-title a")

    card.hover()
    assert _state(card)["translate"] == "none"
    title.focus()
    focused = _state(card)
    assert focused["translate"] == "none"
    assert focused["cueOpacity"] == "1"
    card.screenshot(path=SCREENSHOTS / "podcast-card-mobile-reduced-motion-focus.png")
    reduced_context.close()

    touch_context = browser.new_context(
        viewport={"width": 390, "height": 844},
        has_touch=True,
        is_mobile=True,
    )
    touch_page = touch_context.new_page()
    touch_page.goto(f"{live_server.url}/podcast", wait_until="networkidle")
    assert touch_page.evaluate("matchMedia('(hover: hover) and (pointer: fine)').matches") is False
    touch_card = touch_page.locator("[data-podcast-episode] .archive-card").first
    assert _state(touch_card)["translate"] == "none"
    assert _state(touch_card)["cueOpacity"] == "0"
    touch_context.close()
