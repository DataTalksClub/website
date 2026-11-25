from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from content.podcast_content import ordered_podcasts


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

    # The hero carries no eyebrow label: it is the page title, a lede and the
    # follow-the-show line.
    expect(page.locator(".podcast-hero .mono-label")).to_have_count(0)
    expect(page.locator(".podcast-hero h1")).to_have_text("Podcast")

    # The hub leads with the newest episode, whichever that is in the catalogue.
    latest = ordered_podcasts()[0]
    card = page.locator("[data-podcast-episode]").first
    expect(card.locator("h3 a")).to_have_attribute("href", latest["public_path"])
    # The season is the section heading and the rail names the episode, so the
    # card opens with its title: no "Season N · Episode N" eyebrow inside it.
    expect(card.locator(".event-item-time, .mono-label, .podcast-meta")).to_have_count(0)
    expect(card.locator(".status-pill")).to_have_count(0)
    rail = page.locator(".event-day").first.locator(".event-day-date")
    expect(rail).to_contain_text(f"Episode {latest['episode']}")

    card_border = card.evaluate(
        """(node) => {
          const style = getComputedStyle(node);
          return {width: style.borderTopWidth, style: style.borderTopStyle};
        }"""
    )
    assert card_border == {"width": "1px", "style": "solid"}, card_border

    # The follow line names the listener platforms as plain links with a mark each.
    follow = page.locator(".podcast-follow")
    expect(follow).to_contain_text("Follow the show on")
    expect(follow.locator("a[data-podcast-platform]").first).to_have_attribute("target", "_blank")
    expect(follow.locator("svg.podcast-platform-icon").first).to_have_attribute(
        "aria-hidden", "true"
    )
