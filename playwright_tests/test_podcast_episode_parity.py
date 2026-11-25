"""Browser contracts for the rebuilt podcast episode experience (issue #216)."""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Browser, Page, expect

from content import catalogue

pytestmark = [pytest.mark.core]

# The reference catalogue's fully-populated episode: a video, chaptered
# timestamps, show-note links and guests with their own profile links, which is
# what this module measures.  Read every identifier off the record below rather
# than restating one corpus's episode.
REPRESENTATIVE = "synthetic-episode-one"


def _episode(slug: str) -> dict[str, Any]:
    """The published episode a test names, which the catalogue must hold."""

    record = catalogue.podcast(slug)
    assert record is not None, slug
    return record


def _person(key: str) -> dict[str, Any]:
    """The profile record a guest credit resolves to."""

    return catalogue.people_by_slug()[key]


def _timestamp_href(episode: dict[str, Any]) -> str:
    """The first transcript timestamp's deep link into the episode's own video."""

    first = next(entry for entry in episode["transcript"] if "sec" in entry)
    return f"https://www.youtube.com/watch?v={episode['video']['id']}&t={first['sec']}"


def _settle_analytics_preferences(page: Page) -> None:
    dialog = page.get_by_role("dialog", name="Optional analytics")
    if dialog.is_visible():
        dialog.get_by_role("button", name="Keep analytics off").click()


def _stub_video_provider(page: Page) -> None:
    """Keep the browser suite offline while exercising the rendered iframe contract."""

    page.route(
        "https://www.youtube-nocookie.com/**",
        lambda route: route.fulfill(status=200, content_type="text/html", body=""),
    )


def _assert_no_horizontal_overflow(page: Page) -> None:
    dimensions = page.evaluate(
        """() => ({
          viewport: document.documentElement.clientWidth,
          content: document.documentElement.scrollWidth,
        })"""
    )
    assert dimensions["content"] <= dimensions["viewport"], dimensions


def test_episode_sections_player_and_guest_links_are_keyboard_reachable(
    page: Page,
    live_server,
) -> None:
    episode = _episode(REPRESENTATIVE)
    _stub_video_provider(page)
    page.goto(f"{live_server.url}{episode['public_path']}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    expect(page.locator("#episode-heading")).to_have_text(episode["title"])
    # The YouTube player is a click-to-play poster until the reader asks for it.
    facade = page.get_by_role("button", name=f"Play {episode['title']} on YouTube")
    expect(facade).to_be_visible()
    expect(page.locator("#podcast-video-player")).to_have_count(0)
    expect(page.locator("[data-video-fallback]").first).to_be_hidden()
    facade.press("Enter")
    expect(page.locator("#podcast-video-player")).to_have_attribute(
        "title", f"Watch {episode['title']} on YouTube"
    )
    expect(page.locator("#podcast-video-player")).to_be_focused()
    # The reading-section anchors keep their `#overview`/`#timestamps`/`#transcript`
    # hrefs as a working no-JS fallback, but the tabs script enhances them into a
    # real WAI-ARIA tablist once it runs, so their accessible role is "tab".
    expect(page.get_by_role("tab", name="Overview")).to_have_attribute("href", "#overview")
    expect(page.get_by_role("tab", name="Timestamps")).to_have_attribute("href", "#timestamps")
    expect(page.get_by_role("tab", name="Transcript")).to_have_attribute("href", "#transcript")
    expect(page.locator("#overview .episode-resource-list a[target='_blank']")).to_have_count(
        len(episode["resources"])
    )
    guests = episode["guest_profiles"]
    expect(page.locator(".guest-card-name a").first).to_have_text(guests[0]["name"])
    # Every guest card carries that guest's own profile links, as icon buttons.
    expect(page.locator(".guest-card-links a[target='_blank']")).to_have_count(
        sum(len(_person(guest["key"])["links"]) for guest in guests)
    )
    expect(page.locator(".chapter-time").first).to_have_attribute("href", _timestamp_href(episode))
    _assert_no_horizontal_overflow(page)


def test_timestamp_enhancement_updates_player_and_keeps_native_target(
    page: Page,
    live_server,
) -> None:
    episode = _episode(REPRESENTATIVE)
    _stub_video_provider(page)
    page.goto(f"{live_server.url}{episode['public_path']}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    # Timestamps is not the default tab (Overview is); activate it first so the
    # panel is actually visible and its links can receive real keyboard focus.
    page.get_by_role("tab", name="Timestamps").click()
    timestamp = page.locator("#timestamps .chapter-time").first
    expect(timestamp).to_have_attribute("href", _timestamp_href(episode))
    timestamp.focus()
    timestamp.press("Enter")
    video_id = episode["video"]["id"]
    expect(page.locator("#podcast-video-player")).to_have_attribute(
        "src",
        f"https://www.youtube-nocookie.com/embed/{video_id}?enablejsapi=1&rel=0&autoplay=1&start=0",
    )
    expect(timestamp).to_be_focused()


def test_reading_sections_become_a_wai_aria_tablist_with_overview_first(
    page: Page,
    live_server,
) -> None:
    episode = _episode(REPRESENTATIVE)
    _stub_video_provider(page)
    page.goto(f"{live_server.url}{episode['public_path']}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    tablist = page.get_by_role("tablist", name="Episode sections")
    expect(tablist).to_be_visible()

    overview_tab = page.get_by_role("tab", name="Overview")
    timestamps_tab = page.get_by_role("tab", name="Timestamps")
    transcript_tab = page.get_by_role("tab", name="Transcript")

    # Overview is the default panel: selected, in the tab order, and visible.
    expect(overview_tab).to_have_attribute("aria-selected", "true")
    expect(overview_tab).to_have_attribute("tabindex", "0")
    expect(timestamps_tab).to_have_attribute("aria-selected", "false")
    expect(timestamps_tab).to_have_attribute("tabindex", "-1")
    expect(transcript_tab).to_have_attribute("aria-selected", "false")
    expect(transcript_tab).to_have_attribute("tabindex", "-1")

    overview_panel = page.locator("#overview")
    timestamps_panel = page.locator("#timestamps")
    transcript_panel = page.locator("#transcript")
    expect(overview_panel).to_be_visible()
    expect(overview_panel).to_have_attribute("role", "tabpanel")
    expect(timestamps_panel).to_be_hidden()
    expect(transcript_panel).to_be_hidden()

    # Clicking another tab swaps the selected state and the visible panel.
    timestamps_tab.click()
    expect(timestamps_tab).to_have_attribute("aria-selected", "true")
    expect(overview_tab).to_have_attribute("aria-selected", "false")
    expect(timestamps_panel).to_be_visible()
    expect(overview_panel).to_be_hidden()
    expect(transcript_panel).to_be_hidden()

    # Arrow-key navigation moves focus and activates the next tab, per the
    # WAI-ARIA tabs pattern's automatic-activation model.
    timestamps_tab.focus()
    page.keyboard.press("ArrowRight")
    expect(transcript_tab).to_be_focused()
    expect(transcript_tab).to_have_attribute("aria-selected", "true")
    expect(transcript_panel).to_be_visible()
    expect(timestamps_panel).to_be_hidden()

    page.keyboard.press("ArrowLeft")
    expect(timestamps_tab).to_be_focused()
    expect(timestamps_tab).to_have_attribute("aria-selected", "true")
    expect(timestamps_panel).to_be_visible()


def test_timestamp_and_section_links_remain_native_without_javascript(
    browser: Browser,
    live_server,
) -> None:
    episode = _episode(REPRESENTATIVE)
    context = browser.new_context(
        java_script_enabled=False,
        viewport={"width": 320, "height": 800},
        reduced_motion="reduce",
    )
    page = context.new_page()
    _stub_video_provider(page)
    try:
        response = page.goto(
            f"{live_server.url}{episode['public_path']}",
            wait_until="domcontentloaded",
        )
        assert response is not None and response.status == 200
        # Panel headings are screen-reader only (the tab label says it); the
        # panels themselves are all there.
        expect(page.locator("#overview-heading")).to_have_count(1)
        expect(page.locator("#timestamps-heading")).to_have_count(1)
        expect(page.locator("#transcript-heading")).to_have_count(1)
        expect(page.locator("#overview")).to_be_visible()
        # No JavaScript ran, so the tabs script never rewrote the nav or hid any
        # panel: all three stay stacked and reachable through the plain anchors.
        expect(page.locator(".episode-tabs")).not_to_have_attribute("role", "tablist")
        expect(page.locator("#timestamps")).to_be_visible()
        expect(page.locator("#transcript")).to_be_visible()
        expect(page.locator("#timestamps .chapter-time").first).to_have_attribute(
            "href", _timestamp_href(episode)
        )
        expect(page.locator("#timestamps .chapter-time").first).not_to_have_attribute("href", "#")
        # Without JavaScript the poster is a plain link to the YouTube watch page;
        # the "unavailable" note is reserved for a player that actually failed.
        expect(page.locator("#podcast-video-player")).to_have_count(0)
        expect(page.locator("#podcast-video-facade")).to_have_attribute(
            "href", f"https://www.youtube.com/watch?v={episode['video']['id']}"
        )
        expect(page.locator("[data-video-fallback]").first).to_be_hidden()
        _assert_no_horizontal_overflow(page)
    finally:
        context.close()
