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
            cueTranslate: cue.translate,
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
    assert initial["cueTranslate"] == "none"
    assert float(str(initial["cueBottom"]).removesuffix("px")) > 0
    assert float(str(initial["cueRight"]).removesuffix("px")) > 0
    assert title.evaluate("node => getComputedStyle(node).textDecorationLine") == "none"

    card.hover()
    page.wait_for_timeout(180)
    hovered = _state(card)
    assert hovered["translate"] == "-2px -2px"
    assert hovered["cueOpacity"] == "1"
    assert hovered["cueTranslate"] == "none"
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
    assert state["cueTranslate"] == "none"
    assert title.evaluate("node => getComputedStyle(node).textDecorationLine") == "none"
    card.screenshot(path=SCREENSHOTS / "event-card-desktop-hover.png")


def test_homepage_cards_keep_the_viewport_aligned_and_use_the_static_cue(
    browser: Browser,
    live_server,
) -> None:
    for width, height in ((1440, 900), (1280, 900), (390, 844)):
        context = browser.new_context(viewport={"width": width, "height": height})
        page = context.new_page()
        response = page.goto(f"{live_server.url}/", wait_until="networkidle")
        assert response is not None and response.status == 200
        assert page.evaluate("document.documentElement.scrollWidth") == width

        card = page.locator(".course-card").first
        link = card.locator(".course-link")
        featured = page.locator("[data-featured-course]")
        control = page.locator(".catalog-scroller-controls .scroller-button").first
        expected_axis = {1440: 132, 1280: 52, 390: 16}[width]
        assert round(featured.bounding_box()["x"]) == expected_axis
        assert round(card.bounding_box()["x"]) == expected_axis
        assert round(control.bounding_box()["x"]) == expected_axis
        assert link.inner_text() == "View course"

        scroller_box = page.locator(".catalog-scroller").bounding_box()
        cards = page.locator(".course-card")
        if width >= 1200:
            first_four = [cards.nth(index).bounding_box() for index in range(4)]
            assert len({round(box["width"], 1) for box in first_four}) == 1
            assert len({round(box["height"], 1) for box in first_four}) == 1
            assert round(first_four[-1]["x"] + first_four[-1]["width"]) == round(
                scroller_box["x"] + scroller_box["width"]
            )
            assert cards.nth(4).bounding_box()["x"] > (scroller_box["x"] + scroller_box["width"])
        else:
            second_box = cards.nth(1).bounding_box()
            scroller_right = scroller_box["x"] + scroller_box["width"]
            assert second_box["x"] < scroller_right < second_box["x"] + second_box["width"]

        link.focus()
        page.wait_for_timeout(180)
        state = _state(card)
        assert state["cueOpacity"] == "1"
        assert state["cueTranslate"] == "none"
        context.close()


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
