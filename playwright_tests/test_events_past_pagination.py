"""The past-events archive on the shared public paginator (issues #177, #178).

The archive is the events family's only paginated surface: the upcoming hub
carries no control and accepts no page query.  These journeys hold the visitor's
half of that contract in a real browser — the shared control, the Europe/Berlin
date rail it pages through, the numeric/current-slug rows it leads to, and the
strict query policy around them — with JavaScript off where the issue's evidence
is taken, because nothing on these pages needs it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, ViewportSize, expect

from content.pagination import PUBLIC_PAGE_SIZE
from content.public_data import event_date_groups, event_groups
from events.models import Event
from playwright_tests.accessibility_support import assert_accessible_page

SCREENSHOTS = Path(".tmp/screenshots/issue-177")
EVENTS_HEADING = "Something happening every week"
# The control row the shared include draws, named by the label the archive passes.
ARCHIVE_NAV = "Past event pages"


def _screenshot(page: Page, name: Path | str, *, full_page: bool = False) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / name, full_page=full_page)


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
              return rect.left < -0.5 || rect.right > document.documentElement.clientWidth + 0.5;
            })
            .slice(0, 5)
            .map((node) => `${node.tagName.toLowerCase()}.${String(node.className)}`),
        })"""
    )
    assert overflow["content"] <= overflow["viewport"], overflow


def _assert_control_targets(page: Page) -> None:
    """Every control the archive offers is a target a thumb can land in."""

    targets = page.get_by_role("navigation", name=ARCHIVE_NAV).locator("a")
    sizes = targets.evaluate_all(
        """(nodes) => nodes.map((node) => {
          const rect = node.getBoundingClientRect();
          return {label: node.getAttribute("aria-label") || node.textContent.trim(),
                  width: rect.width, height: rect.height};
        })"""
    )
    assert len(sizes) >= 3, sizes
    assert all(item["width"] >= 44 and item["height"] >= 44 for item in sizes), sizes


def _expected_rail(start: int) -> list[str]:
    """The Europe/Berlin date keys page `start // 20 + 1` draws, one per row."""

    recent = event_groups().recent
    page_slice = list(recent[start : start + PUBLIC_PAGE_SIZE])
    groups = event_date_groups(page_slice, descending=True)
    return [group.key for group in groups for _ in group.events]


def _assert_archive_page_contract(page: Page, page_number: int) -> None:
    """Everything a rendered archive page promises, at any width."""

    recent = event_groups().recent
    start = (page_number - 1) * PUBLIC_PAGE_SIZE
    expected_rows = recent[start : start + PUBLIC_PAGE_SIZE]
    path = "/events/past" if page_number == 1 else f"/events/past?page={page_number}"

    expect(page.get_by_role("heading", name=EVENTS_HEADING, exact=True)).to_be_visible()
    # The archive is the Past view: its own tab is the current one, the upcoming
    # hub's tab is an ordinary link, and the rows say which view drew them.
    past_tab = page.get_by_role("link", name="Past events", exact=True)
    expect(past_tab).to_have_attribute("href", "/events/past")
    expect(past_tab).to_have_attribute("aria-current", "page")
    upcoming_tab = page.get_by_role("link", name="Upcoming events", exact=True)
    expect(upcoming_tab).to_have_attribute("href", "/events")
    expect(upcoming_tab).not_to_have_attribute("aria-current", "page")
    expect(page.locator(".event-rows")).to_have_attribute("data-event-view", "past")

    expect(page.get_by_role("navigation", name=ARCHIVE_NAV)).to_be_visible()
    navigation = page.get_by_role("navigation", name=ARCHIVE_NAV)
    expect(navigation.locator('[aria-current="page"]')).to_have_count(1)
    expect(navigation.locator('[aria-current="page"]')).to_have_text(str(page_number))
    # The walk back to the first page is the clean path, never a page-one query.
    expect(page.locator("body")).not_to_contain_text("?page=1")

    # Twenty numeric/current-slug rows, in the archive's deterministic order,
    # each filed under its Europe/Berlin calendar date.
    rows = page.locator(".event-rows .event-card h3 a")
    expect(rows).to_have_count(len(expected_rows))
    hrefs = rows.evaluate_all("nodes => nodes.map(node => node.getAttribute('href'))")
    assert hrefs == [event["public_path"] for event in expected_rows]
    assert all(re.fullmatch(r"/events/[1-9][0-9]*/[-a-z0-9]+", href) for href in hrefs), hrefs[:5]
    rail = page.locator(".event-rows time").evaluate_all(
        "nodes => nodes.map(node => node.getAttribute('datetime'))"
    )
    assert rail == _expected_rail(start), rail[:6]

    # The selected representation is the one the page names, everywhere it
    # names an address.
    expect(page).to_have_title(
        "Past events — DataTalks.Club"
        if page_number == 1
        else f"Past events — Page {page_number} — DataTalks.Club"
    )
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", f"https://datatalks.club{path}"
    )
    expect(page.locator('meta[property="og:url"]')).to_have_attribute(
        "content", f"https://datatalks.club{path}"
    )
    page_count = -(-len(recent) // PUBLIC_PAGE_SIZE)
    if page_number > 1:
        previous = "/events/past" if page_number == 2 else f"/events/past?page={page_number - 1}"
        expect(page.locator('link[rel="prev"]')).to_have_attribute(
            "href", f"https://datatalks.club{previous}"
        )
        expect(
            navigation.get_by_role("link", name=f"Previous page — page {page_number - 1}")
        ).to_have_attribute(  # noqa: E501
            "href", previous
        )
    else:
        expect(page.locator('link[rel="prev"]')).to_have_count(0)
        expect(navigation.get_by_role("link", name="Previous page", exact=False)).to_have_count(0)
    if page_number < page_count:
        expect(page.locator('link[rel="next"]')).to_have_attribute(
            "href", f"https://datatalks.club/events/past?page={page_number + 1}"
        )
        expect(
            navigation.get_by_role("link", name=f"Next page — page {page_number + 1}")
        ).to_have_attribute(  # noqa: E501
            "href", f"/events/past?page={page_number + 1}"
        )
    else:
        expect(page.locator('link[rel="next"]')).to_have_count(0)
        expect(navigation.get_by_role("link", name="Next page", exact=False)).to_have_count(0)

    _assert_no_horizontal_overflow(page)
    _assert_control_targets(page)


@pytest.mark.full
@pytest.mark.parametrize(
    ("viewport", "suffix"),
    [
        ({"width": 1440, "height": 900}, "desktop"),
        ({"width": 390, "height": 844}, "mobile"),
    ],
)
def test_archive_pages_hold_the_shared_contract_at_both_widths(
    page: Page,
    live_server,
    viewport: ViewportSize,
    suffix: str,
) -> None:
    origin = live_server.url
    page.set_viewport_size(viewport)
    failed_requests: list[str] = []
    console_errors: list[str] = []
    page.on("requestfailed", lambda request: failed_requests.append(request.url))
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )

    for page_number in (1, 2):
        path = "/events/past" if page_number == 1 else "/events/past?page=2"
        response = page.goto(f"{origin}{path}", wait_until="networkidle")
        assert response is not None and response.status == 200
        _settle_analytics_preferences(page)
        _assert_archive_page_contract(page, page_number)
        assert_accessible_page(page, f"events-past-page-{page_number}-{suffix}")
        _screenshot(page, f"events-past-page-{page_number}-{suffix}-light.png", full_page=True)

        page.locator("#dark-mode-toggle").click()
        expect(page.locator("body.dark-mode")).to_have_count(1)
        _assert_no_horizontal_overflow(page)
        assert_accessible_page(page, f"events-past-page-{page_number}-{suffix}-dark")
        _screenshot(page, f"events-past-page-{page_number}-{suffix}-dark.png", full_page=True)
        page.locator("#dark-mode-toggle").click()
        expect(page.locator("body.dark-mode")).to_have_count(0)

    # The upcoming hub is unchanged by the archive's controls: its own tab is
    # current, the archive is an ordinary link, and no page control exists.
    response = page.goto(f"{origin}/events", wait_until="networkidle")
    assert response is not None and response.status == 200
    _settle_analytics_preferences(page)
    upcoming_tab = page.get_by_role("link", name="Upcoming events", exact=True)
    expect(upcoming_tab).to_have_attribute("aria-current", "page")
    expect(page.get_by_role("link", name="Past events", exact=True)).not_to_have_attribute(
        "aria-current", "page"
    )
    expect(page.get_by_role("navigation", name=ARCHIVE_NAV)).to_have_count(0)
    expect(page.locator(".event-rows")).to_have_attribute("data-event-view", "upcoming")

    assert failed_requests == []
    assert console_errors == []


@pytest.mark.full
@pytest.mark.parametrize(
    ("viewport", "suffix"),
    [
        ({"width": 1440, "height": 900}, "desktop"),
        ({"width": 390, "height": 844}, "mobile"),
    ],
)
def test_the_required_evidence_pages_work_without_javascript(
    browser: Browser,
    live_server,
    viewport: ViewportSize,
    suffix: str,
) -> None:
    """The issue's two evidence captures: page two, no JavaScript, both widths.

    The journey there is the one a visitor takes — the shared controls
    themselves — so the capture also proves those controls are ordinary links.
    """

    context = browser.new_context(java_script_enabled=False, viewport=viewport)
    page = context.new_page()
    try:
        response = page.goto(f"{live_server.url}/events/past", wait_until="domcontentloaded")
        assert response is not None and response.status == 200

        page.get_by_role("link", name="Next page — page 2", exact=True).click()
        expect(page).to_have_url(f"{live_server.url}/events/past?page=2")
        _assert_archive_page_contract(page, 2)
        _assert_no_horizontal_overflow(page)
        # Full-page so the capture holds the tabs, the rail, the rows and the
        # control row in one image.
        _screenshot(page, Path(suffix) / "events-past-page-2.png", full_page=True)

        page.get_by_role("link", name="Go to page 1", exact=True).click()
        expect(page).to_have_url(f"{live_server.url}/events/past")
        _assert_archive_page_contract(page, 1)
    finally:
        context.close()


@pytest.mark.full
def test_no_javascript_320px_reflow_and_keyboard_focus(browser: Browser, live_server) -> None:
    """The archive's narrowest reader: reflow, targets and a visible focus ring."""

    context = browser.new_context(
        java_script_enabled=False,
        viewport={"width": 320, "height": 844},
    )
    page = context.new_page()
    try:
        response = page.goto(f"{live_server.url}/events/past?page=2", wait_until="domcontentloaded")
        assert response is not None and response.status == 200
        _assert_archive_page_contract(page, 2)
        _assert_no_horizontal_overflow(page)
        _assert_control_targets(page)

        navigation = page.get_by_role("navigation", name=ARCHIVE_NAV)
        navigation.scroll_into_view_if_needed()
        next_control = navigation.get_by_role("link", name="Next page — page 3", exact=True)
        # Locator.focus() is a programmatic focus and does not establish the
        # keyboard modality that :focus-visible is intended to cover.  Start
        # from the document body and traverse with real Tab input so this
        # contract remains deterministic across browser profiles.
        page.evaluate(
            """
            () => {
              document.body.tabIndex = -1;
              document.body.focus();
            }
            """
        )
        for _step in range(250):
            page.keyboard.press("Tab")
            if next_control.evaluate("element => element === document.activeElement"):
                break
        else:
            raise AssertionError("keyboard traversal did not focus Next page — page 3")

        expect(next_control).to_be_focused()
        focus = next_control.evaluate(
            """(node) => {
              const style = getComputedStyle(node);
              return {
                focusVisible: node.matches(':focus-visible'),
                style: style.outlineStyle,
                width: parseFloat(style.outlineWidth),
                offset: parseFloat(style.outlineOffset),
              };
            }"""
        )
        assert focus["focusVisible"] is True, focus
        # The design system's global focus ring is 3px solid at a 2px offset
        # (_docs/design/design-system.md); the ring still clears the control it marks.
        assert focus["style"] == "solid" and focus["width"] >= 3, focus
        assert focus["offset"] >= 2, focus
        _screenshot(page, "events-past-page-2-320-no-js.png")
    finally:
        context.close()


@pytest.mark.full
def test_alias_query_and_safe_denial_browser_matrix(page: Page, live_server) -> None:
    origin = live_server.url

    # The slash alias keeps the viewer's raw query in its one hop.
    slashed = page.request.get(f"{origin}/events/past/?utm_source=qa", max_redirects=0)
    assert slashed.status == 301
    assert slashed.headers["location"] == "/events/past?utm_source=qa"

    # The legacy filter spelling redirects into the archive's own page URLs.
    legacy = page.request.get(f"{origin}/events?filter=past&page=2", max_redirects=0)
    assert legacy.status == 301
    assert legacy.headers["location"] == "/events/past?page=2"
    navigation = page.goto(f"{origin}/events?filter=past&page=2", wait_until="domcontentloaded")
    assert navigation is not None and navigation.status == 200
    expect(page).to_have_url(f"{origin}/events/past?page=2")

    # A malformed selector and the upcoming hub's rejected page query both fail
    # closed; a valid number past the last page is a rendered miss.
    for path, status in (
        ("/events/past?page=0", 400),
        ("/events/past?page=1&page=2", 400),
        ("/events/past?page=999", 404),
        ("/events?page=2", 400),
    ):
        denied = page.request.get(f"{origin}{path}")
        assert denied.status == status, path
        if status == 404:
            assert "Page not found" in denied.text()
        assert "Traceback" not in denied.text()

    # GET and HEAD agree; an unsafe method is refused before catalogue work.
    head = page.request.fetch(f"{origin}/events/past?page=2", method="HEAD")
    assert head.status == 200
    assert head.headers["cache-control"] == "max-age=0, must-revalidate"
    post = page.request.post(f"{origin}/events/past?page=2")
    assert post.status == 405
    assert post.headers["allow"] == "GET, HEAD"
    assert post.headers["cache-control"] == "no-store, max-age=0"

    # A page-two row leads to its numeric/current-slug detail, self-canonical,
    # and #173's UUID and dated aliases still reach it in one hop.
    first_of_page_two = event_groups().recent[PUBLIC_PAGE_SIZE]
    canonical = str(first_of_page_two["public_path"])
    expect(page.get_by_role("link", name=first_of_page_two["title"], exact=True)).to_have_attribute(
        "href", canonical
    )
    page.get_by_role("link", name=first_of_page_two["title"], exact=True).click()
    expect(page).to_have_url(f"{origin}{canonical}")
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", f"https://datatalks.club{canonical}"
    )

    event = Event.objects.get(id=first_of_page_two["identity_id"])
    uuid_alias = page.request.get(f"{origin}/events/{event.id}", max_redirects=0)
    assert uuid_alias.status == 301
    assert uuid_alias.headers["location"] == canonical
    dated_alias = (
        event.aliases.filter(kind="legacy_date_path")
        .exclude(source_path__endswith="/")
        .get()
        .source_path
    )
    dated = page.request.get(f"{origin}{dated_alias}", max_redirects=0)
    assert dated.status == 301
    assert dated.headers["location"] == canonical
