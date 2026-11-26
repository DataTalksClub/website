"""Browser contracts for the rebuilt podcast episode experience (issue #216)."""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Browser, Frame, Page, expect

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


def _first_seconds(episode: dict[str, Any]) -> int:
    first = next(entry for entry in episode["transcript"] if "sec" in entry)
    return int(first["sec"])


def _timestamp_href(episode: dict[str, Any]) -> str:
    """The first timestamp's link: this page, cued at that second (``?t=``)."""

    return f"?t={_first_seconds(episode)}"


def _embed(episode: dict[str, Any]) -> str:
    return f"https://www.youtube-nocookie.com/embed/{episode['video']['id']}?enablejsapi=1&rel=0"


# A stand-in for the YouTube embed that speaks the IFrame API's message
# protocol: it answers "listening", reports its time and state, and records
# every seek command, so the page's player wiring is exercised offline.
FAKE_EMBED = """<html><body><script>
var state = {t: 0, s: -1, seeks: []};
var cue = location.search.match(/start=(\\d+)/);
if (cue) { state.t = +cue[1]; }
function send(message) { parent.postMessage(JSON.stringify(message), '*'); }
function info() {
  send({event: 'infoDelivery', info: {currentTime: state.t, playerState: state.s, duration: 3600}});
}
var ticking = false;
window.addEventListener('message', function (event) {
  var data = JSON.parse(event.data);
  if (data.event === 'listening' && !ticking) {
    ticking = true;
    state.s = 1;
    send({event: 'onReady'});
    setInterval(function () { if (state.s === 1) { state.t += 0.5; } info(); }, 250);
  }
  if (data.event === 'command') {
    if (data.func === 'seekTo') { state.t = data.args[0]; state.seeks.push(data.args[0]); }
    if (data.func === 'playVideo') { state.s = 1; }
    if (data.func === 'pauseVideo') { state.s = 2; }
    send({event: 'onStateChange', info: state.s});
    info();
  }
});
</script></body></html>"""


def _embed_frame(page: Page) -> Frame:
    return next(frame for frame in page.frames if "youtube-nocookie" in frame.url)


def _wait_for_embed_seek(page: Page, seconds: int) -> None:
    """The seek command is a postMessage into the frame, delivered after the
    page has already marked the chapter; wait for the frame to record it."""

    _embed_frame(page).wait_for_function("(s) => state.seeks.includes(s)", arg=seconds)


def _settle_analytics_preferences(page: Page) -> None:
    dialog = page.get_by_role("dialog", name="Optional analytics")
    if dialog.is_visible():
        dialog.get_by_role("button", name="Keep analytics off").click()


def _stub_video_provider(page: Page) -> None:
    """Keep the browser suite offline while exercising the rendered iframe contract."""

    page.route(
        "https://www.youtube-nocookie.com/**",
        lambda route: route.fulfill(status=200, content_type="text/html", body=FAKE_EMBED),
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
    # The player is a plain frame with one "Play episode" button until the
    # reader asks for it; nothing loads from YouTube before that.
    facade = page.get_by_role("button", name=f"Play {episode['title']} on YouTube")
    expect(facade).to_be_visible()
    expect(page.locator("iframe")).to_have_count(0)
    expect(page.locator("[data-video-fallback]").first).to_be_hidden()
    facade.press("Enter")
    player = page.locator("#podcast-video-player")
    expect(player).to_have_attribute("title", f"Watch {episode['title']} on YouTube")
    expect(player).to_have_attribute("src", _embed(episode) + "&autoplay=1")
    expect(player).to_be_focused()
    # One reading column, no tabs: every section is on the page in order.
    expect(page.get_by_role("tab")).to_have_count(0)
    for heading in ("What we talked about", "Chapters"):
        expect(page.get_by_role("heading", level=2, name=heading)).to_be_visible()
    expect(page.locator("#transcript-heading")).to_be_visible()
    guests = episode["guest_profiles"]
    expect(page.locator(".episode-guests .person-chip-name").first).to_have_text(guests[0]["name"])
    # Every guest card carries that guest's own profile links, labelled.
    links = page.locator(".guest-card-links a[target='_blank']")
    expect(links).to_have_count(sum(len(_person(guest["key"])["links"]) for guest in guests))
    if links.count():
        assert links.first.inner_text().strip(), "a guest link names where it goes"
    expect(page.locator(".chapter-link").first).to_have_attribute("href", _timestamp_href(episode))
    _assert_no_horizontal_overflow(page)


def test_a_chapter_plays_in_place_and_the_docked_player_follows(
    page: Page,
    live_server,
) -> None:
    episode = _episode(REPRESENTATIVE)
    _stub_video_provider(page)
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{live_server.url}{episode['public_path']}", wait_until="networkidle")
    _settle_analytics_preferences(page)

    # The first chapter builds the player at its start.
    chapter = page.locator("#timestamps .chapter-link").first
    expect(chapter).to_have_attribute("href", _timestamp_href(episode))
    chapter.focus()
    chapter.press("Enter")
    player = page.locator("#podcast-video-player")
    cued = _embed(episode) + f"&autoplay=1&start={_first_seconds(episode)}"
    expect(player).to_have_attribute("src", cued)
    expect(chapter).to_be_focused()
    expect(page.locator(".chapter-row.is-current")).to_have_count(1)

    # Once the player reports in, a later chapter seeks it without a reload,
    # and that chapter is marked as the one playing.
    target = page.locator("#timestamps .chapter-row").nth(1)
    seconds = int(float(target.get_attribute("data-chapter-start") or "0"))
    page.wait_for_function(
        "() => document.querySelector('[data-mini-time]').textContent.includes('/')"
    )
    target.locator("a").click()
    page.wait_for_function(
        "(s) => { const row = document.querySelector('.chapter-row.is-current');"
        " return row && Number(row.dataset.chapterStart) === s; }",
        arg=seconds,
    )
    _wait_for_embed_seek(page, seconds)
    expect(player).to_have_attribute("src", cued)

    # Scrolled away from the video, the docked player appears with the
    # chapter and its controls; closing it puts it away.
    mini = page.locator("[data-mini-player]")
    expect(mini).to_be_hidden()
    page.locator("#transcript").scroll_into_view_if_needed()
    expect(mini).to_be_visible()
    expect(mini.locator("[data-mini-chapter]")).to_have_text(
        target.get_attribute("data-chapter-title") or ""
    )
    expect(mini.get_by_role("button", name="Pause")).to_be_visible()
    mini.get_by_role("button", name="Close the docked player").click()
    expect(mini).to_be_hidden()


def test_a_t_link_opens_the_page_with_the_player_cued_there(
    page: Page,
    live_server,
) -> None:
    episode = _episode(REPRESENTATIVE)
    _stub_video_provider(page)
    page.goto(f"{live_server.url}{episode['public_path']}?t=95", wait_until="networkidle")
    expect(page.locator("#podcast-video-player")).to_have_attribute(
        "src", _embed(episode) + "&autoplay=1&start=95"
    )


def test_the_transcript_opens_from_its_link_and_searches(
    page: Page,
    live_server,
) -> None:
    episode = _episode(REPRESENTATIVE)
    _stub_video_provider(page)
    page.goto(f"{live_server.url}{episode['public_path']}#transcript", wait_until="networkidle")
    _settle_analytics_preferences(page)

    expect(page.locator("#transcript details")).to_have_attribute("open", "")
    total = page.locator("[data-transcript-line]").count()
    word = next(
        word
        for entry in episode["transcript"]
        if entry.get("line")
        for word in entry["line"].split()
        if len(word) > 5 and word.isalpha()
    )
    search = page.get_by_role("searchbox", name="Search the transcript")
    search.fill(word)
    count = page.locator("[data-transcript-count]")
    expect(count).to_contain_text("match")
    expect(page.locator("[data-transcript-body] mark").first).to_be_visible()
    visible = page.locator("[data-transcript-line]:not([hidden])").count()
    assert 0 < visible <= total
    search.fill("zzzz-no-such-words")
    expect(count).to_have_text("0 matches")
    search.fill("")
    expect(page.locator("[data-transcript-line]:not([hidden])")).to_have_count(total)


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
        expect(page.locator("#timestamps .chapter-link").first).to_have_attribute(
            "href", _timestamp_href(episode)
        )
        # Without JavaScript the Play button is a plain link to the YouTube watch
        # page; the "unavailable" note is reserved for a player that failed.
        expect(page.locator("#podcast-video-player")).to_have_count(0)
        expect(page.locator("#podcast-video-facade")).to_have_attribute(
            "href", f"https://www.youtube.com/watch?v={episode['video']['id']}"
        )
        expect(page.locator("[data-video-fallback]").first).to_be_hidden()
        _assert_no_horizontal_overflow(page)
    finally:
        context.close()
