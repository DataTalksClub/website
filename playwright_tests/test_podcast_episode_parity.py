"""Browser contracts for the rebuilt podcast episode experience (issue #216)."""

from __future__ import annotations

import pytest
from playwright.sync_api import Browser, Page, expect

from content.public_data import public_projection

pytestmark = [pytest.mark.core]

REPRESENTATIVE = "s24e06-how-to-build-ai-that-actually-ships-in-production"


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
    episode = public_projection()["podcasts_by_slug"][REPRESENTATIVE]
    _stub_video_provider(page)
    page.goto(f"{live_server.url}{episode['public_path']}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    expect(page.locator("#episode-heading")).to_have_text(episode["title"])
    expect(page.locator("#podcast-video-player")).to_have_attribute(
        "title", f"Watch {episode['title']} on YouTube"
    )
    # The reading-section anchors keep their `#show-notes`/`#timestamps`/`#transcript`
    # hrefs as a working no-JS fallback, but the tabs script enhances them into a
    # real WAI-ARIA tablist once it runs, so their accessible role is "tab".
    expect(page.get_by_role("tab", name="Show Notes")).to_have_attribute("href", "#show-notes")
    expect(page.get_by_role("tab", name="Timestamps")).to_have_attribute("href", "#timestamps")
    expect(page.get_by_role("tab", name="Transcript")).to_have_attribute("href", "#transcript")
    expect(page.locator("#show-notes a[target='_blank']")).to_have_count(2)
    expect(page.locator(".guest-bio-card img")).to_have_attribute(
        "alt", "Portrait of Aleksandr Kim"
    )
    expect(page.locator(".guest-bio-links a[target='_blank']")).to_have_count(2)
    expect(page.locator(".episode-timestamp").first).to_have_attribute(
        "href", "https://www.youtube.com/watch?v=PosCx_4fwt0&t=0"
    )
    _assert_no_horizontal_overflow(page)


def test_timestamp_enhancement_updates_player_and_keeps_native_target(
    page: Page,
    live_server,
) -> None:
    episode = public_projection()["podcasts_by_slug"][REPRESENTATIVE]
    _stub_video_provider(page)
    page.goto(f"{live_server.url}{episode['public_path']}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    # Timestamps is not the default tab (Show Notes is); activate it first so the
    # panel is actually visible and its links can receive real keyboard focus.
    page.get_by_role("tab", name="Timestamps").click()
    timestamp = page.locator("#timestamps .episode-timestamp").first
    expect(timestamp).to_have_attribute("href", "https://www.youtube.com/watch?v=PosCx_4fwt0&t=0")
    timestamp.focus()
    timestamp.press("Enter")
    expect(page.locator("#podcast-video-player")).to_have_attribute(
        "src",
        "https://www.youtube-nocookie.com/embed/PosCx_4fwt0?enablejsapi=1&rel=0&start=0",
    )
    expect(timestamp).to_be_focused()


def test_reading_sections_become_a_wai_aria_tablist_with_show_notes_first(
    page: Page,
    live_server,
) -> None:
    episode = public_projection()["podcasts_by_slug"][REPRESENTATIVE]
    _stub_video_provider(page)
    page.goto(f"{live_server.url}{episode['public_path']}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    tablist = page.get_by_role("tablist", name="Episode sections")
    expect(tablist).to_be_visible()

    show_notes_tab = page.get_by_role("tab", name="Show Notes")
    timestamps_tab = page.get_by_role("tab", name="Timestamps")
    transcript_tab = page.get_by_role("tab", name="Transcript")

    # Show Notes is the default panel: selected, in the tab order, and visible.
    expect(show_notes_tab).to_have_attribute("aria-selected", "true")
    expect(show_notes_tab).to_have_attribute("tabindex", "0")
    expect(timestamps_tab).to_have_attribute("aria-selected", "false")
    expect(timestamps_tab).to_have_attribute("tabindex", "-1")
    expect(transcript_tab).to_have_attribute("aria-selected", "false")
    expect(transcript_tab).to_have_attribute("tabindex", "-1")

    show_notes_panel = page.locator("#show-notes")
    timestamps_panel = page.locator("#timestamps")
    transcript_panel = page.locator("#transcript")
    expect(show_notes_panel).to_be_visible()
    expect(show_notes_panel).to_have_attribute("role", "tabpanel")
    expect(timestamps_panel).to_be_hidden()
    expect(transcript_panel).to_be_hidden()

    # Clicking another tab swaps the selected state and the visible panel.
    timestamps_tab.click()
    expect(timestamps_tab).to_have_attribute("aria-selected", "true")
    expect(show_notes_tab).to_have_attribute("aria-selected", "false")
    expect(timestamps_panel).to_be_visible()
    expect(show_notes_panel).to_be_hidden()
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
    episode = public_projection()["podcasts_by_slug"][REPRESENTATIVE]
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
        expect(page.locator("#show-notes-heading")).to_be_visible()
        expect(page.locator("#timestamps-heading")).to_be_visible()
        expect(page.locator("#transcript-heading")).to_be_visible()
        # No JavaScript ran, so the tabs script never rewrote the nav or hid any
        # panel: all three stay stacked and reachable through the plain anchors.
        expect(page.locator(".episode-tabs")).not_to_have_attribute("role", "tablist")
        expect(page.locator("#timestamps")).to_be_visible()
        expect(page.locator("#transcript")).to_be_visible()
        expect(page.locator("#timestamps .episode-timestamp").first).to_have_attribute(
            "href", "https://www.youtube.com/watch?v=PosCx_4fwt0&t=0"
        )
        expect(page.locator("#timestamps .episode-timestamp").first).not_to_have_attribute(
            "href", "#"
        )
        expect(page.locator("#podcast-video-player")).not_to_be_visible()
        expect(page.locator("[data-video-fallback]")).to_be_visible()
        _assert_no_horizontal_overflow(page)
    finally:
        context.close()
