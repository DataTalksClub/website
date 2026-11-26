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
    # One reading column, no tabs: every section is on the page in order.
    expect(page.get_by_role("tab")).to_have_count(0)
    for heading in ("What we talked about", "Chapters"):
        expect(page.get_by_role("heading", level=2, name=heading)).to_be_visible()
    expect(page.locator("#transcript-heading")).to_be_visible()
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


def test_chapters_fold_past_eight_and_the_transcript_opens_as_one_fold(
    page: Page,
    live_server,
) -> None:
    episode = _episode(REPRESENTATIVE)
    _stub_video_provider(page)
    page.goto(f"{live_server.url}{episode['public_path']}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    rows = page.locator("#timestamps .chapter-row")
    total = rows.count()
    more = page.locator("[data-chapters-more]")
    if total > 10:
        # The script folds the list past the eighth chapter and offers the rest.
        expect(rows.nth(7)).to_be_visible()
        expect(rows.nth(8)).to_be_hidden()
        expect(more).to_have_text(f"Show all {total} chapters")
        more.click()
        expect(rows.nth(total - 1)).to_be_visible()
        expect(rows.nth(8).locator("a")).to_be_focused()
    else:
        expect(more).to_have_count(0)
        expect(rows.nth(total - 1)).to_be_visible()

    fold = page.locator("#transcript details")
    expect(fold).not_to_have_attribute("open", "")
    expect(page.locator("#transcript .transcript-body")).to_be_hidden()
    page.locator("#transcript summary").press("Enter")
    expect(fold).to_have_attribute("open", "")
    expect(page.locator("#transcript .transcript-body")).to_be_visible()
    expect(page.get_by_role("link", name="Download the transcript (.txt)")).to_be_visible()


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
        expect(page.locator("#overview-heading")).to_have_count(1)
        expect(page.locator("#timestamps-heading")).to_have_count(1)
        expect(page.locator("#transcript-heading")).to_have_count(1)
        expect(page.locator("#overview")).to_be_visible()
        # No JavaScript ran, so nothing is folded: every chapter shows.
        expect(page.locator("#timestamps")).to_be_visible()
        expect(page.locator("#timestamps .chapter-row").last).to_be_visible()
        expect(page.locator("[data-chapters-more]")).to_be_hidden()
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
