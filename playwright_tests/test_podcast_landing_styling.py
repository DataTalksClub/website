from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


@pytest.mark.core
@pytest.mark.parametrize(
    "viewport",
    [
        {"width": 1440, "height": 900},
        {"width": 390, "height": 844},
    ],
)
def test_podcast_landing_keeps_bordered_cards_and_plain_blue_episode_metadata(
    page: Page,
    live_server,
    viewport: dict[str, int],
) -> None:
    page.set_viewport_size(viewport)
    response = page.goto(f"{live_server.url}/podcast", wait_until="networkidle")
    assert response is not None and response.status == 200

    expect(page.locator(".podcast-hero .mono-label")).to_have_count(0)

    card = page.locator("[data-podcast-episode]").first.locator(".archive-card")
    eyebrow = card.locator(".archive-body > .mono-label")
    expect(eyebrow).to_have_text("Season 24 · Episode 6")
    expect(card.locator(".status-pill")).to_have_count(0)

    card_border = card.evaluate(
        """(node) => {
          const style = getComputedStyle(node);
          return {width: style.borderTopWidth, style: style.borderTopStyle};
        }"""
    )
    assert card_border == {"width": "2px", "style": "solid"}, card_border

    eyebrow_style = eyebrow.evaluate(
        """(node) => {
          const style = getComputedStyle(node);
          return {
            color: style.color,
            width: style.borderTopWidth,
            style: style.borderTopStyle,
          };
        }"""
    )
    assert eyebrow_style == {
        "color": "rgb(90, 98, 196)",
        "width": "0px",
        "style": "none",
    }, eyebrow_style
