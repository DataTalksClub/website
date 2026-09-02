from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page

pytestmark = [pytest.mark.core]
SCREENSHOTS = Path(".tmp/screenshots/card-interactions")

# ``homepage_course_catalog`` is the shared conftest fixture: the featured panel and the
# catalogue cards read the database since issue #307, and three browser modules need the
# same rows, so they request one fixture rather than keeping a copy each.

# ``.interactive-lift`` moves a hovered or keyboard-focused card two pixels up and to the
# left.  Inside a horizontal strip that is also the scroll container, that movement is
# only safe if the strip keeps at least this much room between its clipping edge and the
# first card; otherwise the leading card's border is cropped off the page.
LIFT_PX = 2

# Every homepage strip is measured the same way: where the first card sits relative to
# the box that clips it, and whether the strip's snap geometry agrees with its padding.
_STRIP_GEOMETRY = """
(node) => {
  const style = getComputedStyle(node);
  const card = node.firstElementChild;
  const box = node.getBoundingClientRect();
  const cardBox = card.getBoundingClientRect();
  const round = (value) => Math.round(value * 100) / 100;
  // ``overflow`` clips at the padding box, and these strips draw no border, so the
  // border-box rectangle is the clipping rectangle.
  const clipLeft = box.x + parseFloat(style.borderLeftWidth);
  const scrollPadding = parseFloat(style.scrollPaddingLeft);
  return {
    cardX: round(cardBox.x),
    inset: round(cardBox.x - clipLeft),
    paddingLeft: round(parseFloat(style.paddingLeft)),
    scrollPaddingLeft: Number.isNaN(scrollPadding) ? null : round(scrollPadding),
    scrollLeft: round(node.scrollLeft),
    overflowX: style.overflowX,
    overflowY: style.overflowY,
    scrollable: node.scrollWidth > node.clientWidth,
    translate: getComputedStyle(card).translate,
  };
}
"""


def _strip(page, selector: str) -> dict[str, object]:
    return page.locator(selector).evaluate(_STRIP_GEOMETRY)


def _assert_strip_has_room_for_the_lift(strip: dict[str, object], expected_axis: int) -> None:
    """A homepage strip must clip nothing its cards are allowed to do.

    ``overflow-x: auto`` makes the strip a clipping box on *both* axes, so the
    resting card has to sit far enough inside it that the hover/focus lift stays
    drawn.  The strip pays for that with inline padding, and only keeps it if
    ``scroll-padding`` matches: ``scroll-snap-align: start`` aligns against the
    snapport, and a zero ``scroll-padding`` snaps the first card onto the padding
    edge, spending the whole gutter and putting the card back against the clip.
    """
    assert strip["overflowX"] == "auto"
    assert strip["scrollable"] is True
    assert strip["scrollLeft"] == 0, f"the strip opened part-scrolled: {strip}"

    # What a reader sees: the resting card must stand far enough inside the
    # clipping edge for the lift, and still on the page's vertical axis.
    assert strip["inset"] >= LIFT_PX, (
        f"the strip leaves no room for the {LIFT_PX}px lift, so a hovered or "
        f"focused card is cropped on its left edge: {strip}"
    )
    assert round(float(strip["cardX"])) == expected_axis, (
        f"the resting card left the page's vertical axis: {strip}"
    )

    # How that room survives: the gutter is only kept if the snapport matches it.
    # Device-pixel snapping moves the strip's own edge by a fraction of a pixel.
    assert strip["scrollPaddingLeft"] == strip["paddingLeft"], (
        "scroll-padding must match the strip's inline padding, or scroll snapping "
        f"spends the gutter that keeps the lift visible: {strip}"
    )
    assert strip["inset"] == pytest.approx(strip["paddingLeft"], abs=0.5), (
        f"the resting card is not at the strip's content edge: {strip}"
    )


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


def _assert_the_lift_is_drawn(
    page: Page,
    interaction: str,
    resting: dict[str, object],
    expected_axis: int,
) -> None:
    """The catalogue's first card must move, and stay whole while it does."""
    page.wait_for_timeout(220)
    lifted = _strip(page, ".catalog-scroller")
    assert lifted["translate"] == f"-{LIFT_PX}px -{LIFT_PX}px", (
        f"the card no longer lifts on {interaction}: {lifted}"
    )
    assert lifted["inset"] == pytest.approx(resting["inset"] - LIFT_PX, abs=0.5)
    assert lifted["inset"] > 0, (
        f"the card lifted on {interaction} is clipped on its left edge: {lifted}"
    )
    assert round(float(lifted["cardX"])) == expected_axis - LIFT_PX


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


def test_event_card_uses_the_same_bottom_right_cue(
    page: Page,
    live_server,
    stable_public_event_clock,
) -> None:
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
    homepage_course_catalog: None,
) -> None:
    axis_by_width = {1440: 132, 1280: 52, 1024: 20, 768: 20, 390: 16}
    for width, height in ((1440, 900), (1280, 900), (1024, 800), (768, 1000), (390, 844)):
        context = browser.new_context(viewport={"width": width, "height": height})
        page = context.new_page()
        response = page.goto(f"{live_server.url}/", wait_until="networkidle")
        assert response is not None and response.status == 200
        assert page.evaluate("document.documentElement.scrollWidth") == width

        card = page.locator(".course-card").first
        link = card.locator(".course-link")
        featured = page.locator("[data-featured-course]")
        control = page.locator(".catalog-scroller-controls .scroller-button").first
        expected_axis = axis_by_width[width]
        assert round(featured.bounding_box()["x"]) == expected_axis
        assert round(control.bounding_box()["x"]) == expected_axis
        assert link.inner_text() == "View course"

        # Both homepage strips are carousels built from the same primitive, so both
        # are held to the same geometry.
        catalog = _strip(page, ".catalog-scroller")
        _assert_strip_has_room_for_the_lift(catalog, expected_axis)
        _assert_strip_has_room_for_the_lift(_strip(page, ".stories-scroller"), expected_axis)

        # The lift is the interaction under test: it must still happen, and it must
        # still be drawn.  A card whose left edge crosses the strip's clipping edge
        # loses its border and the first sliver of every line inside it.
        card.hover()
        _assert_the_lift_is_drawn(page, "hover", catalog, expected_axis)
        link.focus()
        _assert_the_lift_is_drawn(page, "keyboard focus", catalog, expected_axis)

        scroller_box = page.locator(".catalog-scroller").bounding_box()
        content_right = scroller_box["x"] + scroller_box["width"] - catalog["paddingLeft"]
        cards = page.locator(".course-card")
        if width >= 1200:
            first_four = [cards.nth(index).bounding_box() for index in range(4)]
            assert len({round(box["width"], 1) for box in first_four}) == 1
            assert len({round(box["height"], 1) for box in first_four}) == 1
            assert round(first_four[-1]["x"] + first_four[-1]["width"]) == round(content_right)
            assert cards.nth(4).bounding_box()["x"] > content_right
        else:
            # Narrower shells show the next card peeking in from the right edge.
            boxes = [cards.nth(index).bounding_box() for index in range(cards.count())]
            peeking = [box for box in boxes if box["x"] < content_right < box["x"] + box["width"]]
            assert len(peeking) == 1

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
