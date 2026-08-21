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
    expect(page.get_by_role("link", name="Show Notes")).to_have_attribute("href", "#show-notes")
    expect(page.get_by_role("link", name="Timestamps")).to_have_attribute("href", "#timestamps")
    expect(page.get_by_role("link", name="Transcript")).to_have_attribute("href", "#transcript")
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

    timestamp = page.locator("#timestamps .episode-timestamp").first
    expect(timestamp).to_have_attribute("href", "https://www.youtube.com/watch?v=PosCx_4fwt0&t=0")
    timestamp.focus()
    timestamp.press("Enter")
    expect(page.locator("#podcast-video-player")).to_have_attribute(
        "src",
        "https://www.youtube-nocookie.com/embed/PosCx_4fwt0?enablejsapi=1&rel=0&start=0",
    )
    expect(timestamp).to_be_focused()


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
