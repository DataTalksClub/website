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


def _stub_video_provider(page: Page) -> None:
    """Keep the browser suite offline while exercising the rendered iframe contract."""

    page.route(
        "https://www.youtube-nocookie.com/**",
        lambda route: route.fulfill(status=200, content_type="text/html", body=""),
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


def _assert_platform_buttons(page: Page, container: str) -> None:
    platforms = {
        "apple": "Apple Podcasts",
        "spotify": "Spotify",
        "youtube": "YouTube",
    }
    for provider, label in platforms.items():
        button = page.locator(f'{container} a[data-podcast-platform="{provider}"]')
        expect(button).to_have_count(1)
        expect(button).to_have_attribute("target", "_blank")
        expect(button).to_have_attribute("rel", "noopener noreferrer")
        expect(button).to_have_accessible_name(f"{label} (opens in a new tab)")
        expect(button).to_contain_text(label)
        expect(button.locator(".sr-only")).to_have_text(" (opens in a new tab)")
        icon = button.locator(f'svg[data-podcast-platform-icon="{provider}"]')
        expect(icon).to_have_count(1)
        expect(icon).to_have_attribute("aria-hidden", "true")
        expect(icon).to_have_attribute("focusable", "false")


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
    _stub_video_provider(page)
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
        if path == "/podcast":
            expect(page.get_by_role("group", name="Podcast platforms")).to_be_visible()
        _assert_platform_buttons(
            page, ".podcast-platforms" if path == "/podcast" else ".listen-row"
        )
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
    _stub_video_provider(page)
    page.goto(f"{live_server.url}{episode['public_path']}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    # A video episode can also carry the separate Spotify audio embed. Target
    # the preferred YouTube player by provider instead of counting both media
    # frames as the same control.
    player = page.locator('.episode-video[data-video-provider="youtube"]')
    expect(player).to_have_count(1)
    expect(player).to_have_attribute("data-video-id", episode["video"]["id"])
    iframe = page.locator("#podcast-video-player")
    expect(iframe).to_have_attribute(
        "title",
        f"Watch {episode['title']} on YouTube",
    )
    platform_link = page.locator(f'.listen-row a[href="{episode["links"]["youtube"]}"]')
    expect(platform_link).to_have_count(1)
    platform_link.focus()
    focus = platform_link.evaluate(
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
    # A player-present episode renders no redundant artwork of its own: the
    # player already fills that visual slot (issue #234).  Related-episode
    # artwork elsewhere on the page still carries useful alternative text, and
    # the shared person-chip portrait is deliberately decorative because the
    # adjacent name is the accessible credit.
    artwork_alt = f"Artwork for {episode['title']}"
    image_data = page.locator("main img").evaluate_all(
        "(nodes) => nodes.map((n) => ({alt: n.alt, className: n.className}))"
    )
    alts = [image["alt"] for image in image_data]
    assert alts.count(artwork_alt) == 0, alts
    assert all(
        image["alt"] or image["className"] == "person-chip-portrait" for image in image_data
    ), image_data
    # Transcript is not the default tab (Show Notes is); activate it to reach
    # its heading, the same way a keyboard or pointer user would.
    page.get_by_role("tab", name="Transcript").click()
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
    _stub_video_provider(page)
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
