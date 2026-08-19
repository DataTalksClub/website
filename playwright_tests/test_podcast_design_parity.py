"""Design 5a parity for the podcast index and episode page (mockup 6d, issue #179).

The two surfaces carry their own inline stylesheet, so this checks what only a
browser can: the shared palette actually paints, both themes hold, nothing
overflows at the design's two viewports, and the play control on an episode is a
real keyboard-operable destination.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from content.public_data import ordered_podcasts, podcast_seasons

pytestmark = [pytest.mark.core]

SCREENSHOTS = Path(".tmp/screenshots/issue-179/podcast")
PODCAST_HEADING = "Conversations with people who ship data"
# The design 5a page ground: both the season index and an episode page open warm
# and end on the cool lavender content ground, so `--page` follows it
# (`_docs/design/design-5a.md`).  The dark theme keeps the partial's own `--page`
# ground.
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
def test_index_and_episode_render_the_design_system_in_both_themes(
    page: Page,
    live_server,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    origin = live_server.url
    episode = ordered_podcasts()[0]
    season = podcast_seasons()[0]
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )

    for path, label in (("/podcast", "index"), (episode["public_path"], "episode")):
        response = page.goto(f"{origin}{path}", wait_until="networkidle")
        assert response is not None and response.status == 200
        _settle_analytics_preferences(page)
        expect(page.locator('link[rel="stylesheet"]')).to_have_count(0)
        expect(page.locator("main h1")).to_have_count(1)
        expect(page.locator("body")).to_have_css("background-color", LIGHT_BACKGROUND)
        expect(page.locator("body")).not_to_contain_text("Traceback")
        _assert_no_horizontal_overflow(page)
        _shot(page, f"podcast-{label}-{suffix}-light.png")

        page.locator("#dark-mode-toggle").click()
        expect(page.locator("body.dark-mode")).to_have_count(1)
        expect(page.locator("body")).to_have_css("background-color", DARK_BACKGROUND)
        _assert_no_horizontal_overflow(page)
        _shot(page, f"podcast-{label}-{suffix}-dark.png")
        page.locator("#dark-mode-toggle").click()
        expect(page.locator("body.dark-mode")).to_have_count(0)

    page.goto(f"{origin}/podcast", wait_until="networkidle")
    expect(page.get_by_role("heading", name=PODCAST_HEADING, exact=True)).to_be_visible()
    expect(page.locator(".row-list .play-disc")).to_have_count(len(season.episodes))
    expect(page.locator(".episode-row").first.get_by_role("link").first).to_have_attribute(
        "href",
        season.episodes[0]["public_path"],
    )

    assert console_errors == []


def test_episode_play_control_is_a_labelled_keyboard_destination(
    page: Page,
    live_server,
) -> None:
    episode = ordered_podcasts()[0]
    page.goto(f"{live_server.url}{episode['public_path']}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    player = page.locator("a.player-frame")
    expect(player).to_have_count(1)
    expect(player).to_have_attribute("href", episode["links"]["youtube"])
    expect(player).to_have_attribute(
        "aria-label",
        f"Play {episode['title']} on YouTube",
    )
    player.focus()
    focus = player.evaluate(
        """(node) => {
          const style = getComputedStyle(node);
          return {
            focused: node === document.activeElement,
            style: style.outlineStyle,
            width: parseFloat(style.outlineWidth),
          };
        }"""
    )
    assert focus["focused"] is True, focus
    # The artwork carries the episode's identity, and the link carries its own name,
    # so neither borrows the other's meaning.  The guests' portrait chips are also
    # images in main, but their credit is the name printed beside them, so the
    # shared `_person_chip` partial keeps them decorative with empty alt text.
    artwork_alt = f"Artwork for {episode['title']}"
    alts = page.locator("main img").evaluate_all("(nodes) => nodes.map((n) => n.alt)")
    assert alts.count(artwork_alt) == 1, alts
    assert set(alts) <= {artwork_alt, ""}, alts
    expect(page.locator("#transcript-heading")).to_be_visible()


def test_index_and_episode_stay_usable_at_320px_without_javascript(
    browser: Browser,
    live_server,
) -> None:
    episode = ordered_podcasts()[0]
    context = browser.new_context(
        java_script_enabled=False,
        reduced_motion="reduce",
        viewport={"width": 320, "height": 800},
    )
    page = context.new_page()
    try:
        for path, label in (("/podcast", "index"), (episode["public_path"], "episode")):
            response = page.goto(f"{live_server.url}{path}", wait_until="domcontentloaded")
            assert response is not None and response.status == 200
            expect(page.locator("main h1")).to_have_count(1)
            expect(page.get_by_role("navigation", name="Primary navigation")).to_be_visible()
            _assert_no_horizontal_overflow(page)
            SCREENSHOTS.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=SCREENSHOTS / f"podcast-{label}-320-no-js.png")
    finally:
        context.close()
