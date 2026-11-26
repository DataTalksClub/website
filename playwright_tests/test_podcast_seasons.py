import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, ViewportSize, expect

from content.podcast_content import ordered_podcasts, podcast_seasons
from playwright_tests.accessibility_support import assert_accessible_page
from playwright_tests.navigation_support import primary_navigation_link

SCREENSHOTS = Path(".tmp/screenshots/issue-132")
# The index opens on a plain "Podcast" page title; the season heading it
# navigates by (the one h2) names the season on show.
PODCAST_HEADING = "Podcast"


def _season_numbers() -> list[int]:
    """The published season numbers, newest first, as the hub lists them."""

    return [season.number for season in podcast_seasons()]


def _season_scenarios() -> tuple[tuple[int, str, int | None, int | None], ...]:
    """The latest, a middle and the oldest season, each with its neighbours.

    The hub names a season's newer and older neighbours by their position in
    this list, not by arithmetic, so both the three seasons and their
    neighbours are read from the catalogue rather than written out.
    """

    numbers = _season_numbers()
    assert len(numbers) >= 3, numbers
    chosen = sorted({0, len(numbers) // 2, len(numbers) - 1})
    assert len(chosen) == 3, numbers
    return tuple(
        (
            numbers[index],
            "/podcast" if index == 0 else f"/podcast/s{numbers[index]:02d}/",
            numbers[index - 1] if index else None,
            numbers[index + 1] if index + 1 < len(numbers) else None,
        )
        for index in chosen
    )


def _season_heading(page: Page, number: int):
    """The one season heading; its name also carries the season's span and count."""

    return page.locator("#season-heading").filter(has_text=re.compile(rf"^\s*Season {number}\b"))


def _screenshot(page: Page, name: str, *, full_page: bool = True) -> None:
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


def _assert_season_targets(page: Page) -> None:
    # The newest seasons are pills; older ones fold into a closed disclosure
    # whose links have no box until it opens, so only drawn targets are measured.
    targets = page.get_by_role("navigation", name="Podcast seasons", exact=True).locator(
        ".filter-pill"
    )
    sizes = targets.evaluate_all(
        """(nodes) => nodes.map((node) => {
          const rect = node.getBoundingClientRect();
          return {text: node.textContent.trim(), width: rect.width, height: rect.height};
        }).filter((item) => item.width > 0)"""
    )
    assert len(sizes) >= min(8, len(_season_numbers()))
    # A season pill is 44px tall and at least 2.5rem (40px) wide: a one-digit
    # season is the narrowest, and the row's spacing keeps it a clear target.
    assert all(item["width"] >= 40 and item["height"] >= 44 for item in sizes), sizes


@pytest.mark.core
def test_homepage_latest_episode_and_global_podcast_journey(page: Page, live_server) -> None:
    origin = live_server.url
    latest = ordered_podcasts()[0]

    response = page.goto(origin, wait_until="networkidle")
    assert response is not None and response.status == 200
    page.locator(f'a[href="{latest["public_path"]}"]').click()
    expect(page).to_have_url(f"{origin}{latest['public_path']}")
    expect(page.get_by_role("heading", name=latest["title"], exact=True)).to_be_visible()
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href",
        f"https://datatalks.club{latest['public_path']}",
    )

    primary_navigation_link(page, "Podcast").click()
    expect(page).to_have_url(f"{origin}/podcast")
    expect(_season_heading(page, _season_numbers()[0])).to_be_visible()
    expect(page.locator("[data-podcast-episode]").first).to_contain_text(latest["title"])


@pytest.mark.core
def test_podcast_card_is_a_whole_keyboard_destination_without_nested_interactives(
    page: Page,
    live_server,
) -> None:
    episode = ordered_podcasts()[0]
    origin = live_server.url

    page.set_viewport_size({"width": 390, "height": 844})
    response = page.goto(f"{origin}/podcast", wait_until="networkidle")
    assert response is not None and response.status == 200
    _settle_analytics_preferences(page)

    # The card is the timeline's soft `.event-item`.  Its rail already names the
    # season (heading) and the episode, so the card opens with its title: there
    # is no season/episode eyebrow line inside it.
    card = page.locator("[data-podcast-episode]").first
    expect(card).to_have_class(re.compile(r"\bevent-item\b"))
    expect(card.locator(".event-item-time")).to_have_count(0)
    expect(card.locator(".podcast-meta")).to_have_count(0)
    expect(card.locator(".status-pill")).to_have_count(0)
    expect(card.locator("h3 a")).to_have_attribute("href", episode["public_path"])

    card_style = card.evaluate(
        """(node) => {
          const style = getComputedStyle(node);
          return {
            transparent: style.backgroundColor === 'rgba(0, 0, 0, 0)',
            border: style.borderTopWidth,
            borderStyle: style.borderTopStyle,
            shadow: style.boxShadow,
          };
        }"""
    )
    assert card_style == {
        "transparent": False,
        "border": "1px",
        "borderStyle": "solid",
        "shadow": "none",
    }, card_style

    assert card.evaluate("node => node.querySelectorAll('a a, a button, button a').length") == 0

    # The guest profile remains a separate destination above the stretched link.
    guest_link = card.locator(".person-chip a").first
    expect(guest_link).to_have_count(1)
    guest_path = guest_link.get_attribute("href")
    assert guest_path
    guest_link.click()
    expect(page).to_have_url(f"{origin}{guest_path}")
    page.go_back(wait_until="networkidle")
    _settle_analytics_preferences(page)
    card = page.locator("[data-podcast-episode]").first

    # A physical point on the card still activates the title anchor's stretched
    # hit area.  The pseudo-element is intentionally the topmost pointer target.
    card.scroll_into_view_if_needed()
    box = card.bounding_box()
    assert box is not None
    page.mouse.click(box["x"] + box["width"] - 8, box["y"] + box["height"] / 2)
    expect(page).to_have_url(f"{origin}{episode['public_path']}")

    page.go_back(wait_until="networkidle")
    _settle_analytics_preferences(page)
    title_link = page.locator("[data-podcast-episode]").first.locator("h3 a")
    title_link.focus()
    expect(title_link).to_be_focused()
    page.keyboard.press("Enter")
    expect(page).to_have_url(f"{origin}{episode['public_path']}")


@pytest.mark.core
@pytest.mark.parametrize(
    ("viewport", "suffix"),
    [
        ({"width": 1440, "height": 900}, "desktop"),
        ({"width": 390, "height": 844}, "mobile"),
    ],
)
def test_latest_middle_oldest_light_dark_and_keyboard_contract(
    page: Page,
    live_server,
    viewport: ViewportSize,
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    origin = live_server.url
    failed_requests: list[str] = []
    console_errors: list[str] = []
    page.on("requestfailed", lambda request: failed_requests.append(request.url))
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )

    scenarios = _season_scenarios()
    latest_number = _season_numbers()[0]
    inventory = {season.number: season for season in podcast_seasons()}
    for season, path, newer, older in scenarios:
        response = page.goto(f"{origin}{path}", wait_until="networkidle")
        assert response is not None and response.status == 200
        _settle_analytics_preferences(page)
        expect(page.locator("main h1")).to_have_count(1)
        expect(page.locator("main h2")).to_have_count(1)
        expect(
            page.get_by_role("heading", level=1, name=PODCAST_HEADING, exact=True)
        ).to_be_visible()
        expect(_season_heading(page, season)).to_be_visible()
        expect(page.locator("main h3")).to_have_count(len(inventory[season].episodes))
        expect(page.locator("[data-podcast-season]")).to_have_count(1)
        expect(page.locator("[data-podcast-episode]")).to_have_count(
            len(inventory[season].episodes)
        )

        navigation = page.get_by_role("navigation", name="Podcast seasons", exact=True)
        # The open season's pill is marked current; an older one also marks the
        # fold's summary, which then names that season.
        current = navigation.locator('a[aria-current="page"]')
        expect(current).to_have_count(1)
        expect(current).to_have_attribute("href", path)
        expect(current).to_have_attribute("aria-label", f"Season {season}")
        for _number, other_path, _n, _o in scenarios:
            expect(navigation.locator(f'a[href="{other_path}"]')).to_have_count(1)
        expect(page.locator('link[rel="canonical"]')).to_have_attribute(
            "href",
            f"https://datatalks.club{path}",
        )
        expect(page.locator('meta[property="og:url"]')).to_have_attribute(
            "content",
            f"https://datatalks.club{path}",
        )

        more_seasons = page.get_by_role("navigation", name="More podcast seasons", exact=True)
        if newer is None:
            expect(more_seasons.locator('a[rel="prev"]')).to_have_count(0)
            expect(page.locator('link[rel="prev"]')).to_have_count(0)
        else:
            newer_path = "/podcast" if newer == latest_number else f"/podcast/s{newer:02d}/"
            expect(more_seasons.get_by_role("link", name="← Newer season")).to_have_attribute(
                "href",
                newer_path,
            )
            expect(page.locator('link[rel="prev"]')).to_have_attribute(
                "href",
                f"https://datatalks.club{newer_path}",
            )

        if older is None:
            expect(more_seasons.locator('a[rel="next"]')).to_have_count(0)
            expect(page.locator('link[rel="next"]')).to_have_count(0)
        else:
            older_path = f"/podcast/s{older:02d}/"
            expect(more_seasons.get_by_role("link", name="Older season →")).to_have_attribute(
                "href",
                older_path,
            )
            expect(page.locator('link[rel="next"]')).to_have_attribute(
                "href",
                f"https://datatalks.club{older_path}",
            )

        _assert_no_horizontal_overflow(page)
        _assert_season_targets(page)
        assert_accessible_page(page, f"podcast-season-{season}-{suffix}")
        _screenshot(page, f"podcast-season-{season}-{suffix}-light.png")

        page.locator("#dark-mode-toggle").click()
        expect(page.locator("body.dark-mode")).to_have_count(1)
        _assert_no_horizontal_overflow(page)
        assert_accessible_page(page, f"podcast-season-{season}-{suffix}-dark")
        _screenshot(page, f"podcast-season-{season}-{suffix}-dark.png")
        page.locator("#dark-mode-toggle").click()
        expect(page.locator("body.dark-mode")).to_have_count(0)

    if suffix == "mobile":
        focus_season, focus_path, _newer, focus_older = scenarios[1]
        assert focus_older is not None, scenarios
        page.goto(f"{origin}{focus_path}", wait_until="networkidle")
        older_link = page.get_by_role("link", name="Older season →")

        # Locator.focus() is a programmatic focus and does not establish the
        # keyboard modality that :focus-visible is intended to cover.  Start
        # from the document body and traverse with real Tab input so this
        # contract remains deterministic across browser/mobile profiles.
        page.evaluate(
            """
            () => {
              document.body.tabIndex = -1;
              document.body.focus();
            }
            """
        )
        for _step in range(80):
            page.keyboard.press("Tab")
            if older_link.evaluate("element => element === document.activeElement"):
                break
        else:
            raise AssertionError(
                f"keyboard traversal did not focus Older season (Season {focus_older})"
            )

        expect(older_link).to_be_focused()
        focus = older_link.evaluate(
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
        assert focus["style"] == "solid" and focus["width"] >= 3, focus
        # The design system's global focus ring is 3px solid at a 2px offset
        # (_docs/design/design-system.md); the ring still clears the control it marks.
        assert focus["offset"] >= 2, focus
        _screenshot(page, f"podcast-season-{focus_season}-mobile-focus.png")

    assert failed_requests == []
    assert console_errors == []


@pytest.mark.core
def test_season_controls_activate_direct_normalized_destinations(page: Page, live_server) -> None:
    origin = live_server.url
    # The hub's own season order decides which season is "older" than the
    # latest and which the oldest is, so walk the list rather than naming the
    # numbers of a retired corpus.
    numbers = _season_numbers()
    latest, second, middle, oldest = numbers[0], numbers[1], numbers[len(numbers) // 2], numbers[-1]
    assert len({latest, second, middle, oldest}) == 4, numbers
    page.goto(f"{origin}/podcast", wait_until="networkidle")

    def season_pill(number: int):
        # The newest seasons are pills; older ones sit in the "Older" fold.
        navigation = page.get_by_role("navigation", name="Podcast seasons", exact=True)
        pill = navigation.locator(f'a[aria-label="Season {number}"]')
        if not pill.is_visible():
            navigation.locator("summary").click()
        return pill

    page.get_by_role("link", name="Older season →").click()
    expect(page).to_have_url(f"{origin}/podcast/s{second:02d}/")
    expect(_season_heading(page, second)).to_be_visible()
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href",
        f"https://datatalks.club/podcast/s{second:02d}/",
    )

    page.get_by_role("link", name="← Newer season").click()
    expect(page).to_have_url(f"{origin}/podcast")
    expect(_season_heading(page, latest)).to_be_visible()

    season_pill(middle).click()
    expect(page).to_have_url(f"{origin}/podcast/s{middle:02d}/")
    expect(_season_heading(page, middle)).to_be_visible()

    season_pill(oldest).click()
    expect(page).to_have_url(f"{origin}/podcast/s{oldest:02d}/")
    expect(_season_heading(page, oldest)).to_be_visible()
    expect(page.get_by_role("link", name="Older season →")).to_have_count(0)


@pytest.mark.core
def test_no_javascript_320px_reduced_motion_and_200_percent_reflow(
    browser: Browser,
    live_server,
) -> None:
    context = browser.new_context(
        java_script_enabled=False,
        reduced_motion="reduce",
        viewport={"width": 320, "height": 800},
    )
    page = context.new_page()
    try:
        for season, path, _newer, _older in _season_scenarios():
            response = page.goto(f"{live_server.url}{path}", wait_until="domcontentloaded")
            assert response is not None and response.status == 200
            expect(_season_heading(page, season)).to_be_visible()
            expect(
                page.get_by_role("navigation", name="Podcast seasons", exact=True)
            ).to_be_visible()
            _assert_no_horizontal_overflow(page)
            _assert_season_targets(page)
            page.get_by_role(
                "navigation", name="Podcast seasons", exact=True
            ).scroll_into_view_if_needed()
            _screenshot(
                page,
                f"podcast-season-{season}-320-no-js-reduced-motion.png",
                full_page=False,
            )
    finally:
        context.close()

    zoom_context = browser.new_context(
        reduced_motion="reduce",
        viewport={"width": 640, "height": 900},
    )
    zoom_page = zoom_context.new_page()
    try:
        zoom_season, zoom_path, _newer, _older = _season_scenarios()[1]
        response = zoom_page.goto(
            f"{live_server.url}{zoom_path}",
            wait_until="domcontentloaded",
        )
        assert response is not None and response.status == 200
        _settle_analytics_preferences(zoom_page)
        zoom_page.evaluate("document.documentElement.style.zoom = '2'")
        expect(_season_heading(zoom_page, zoom_season)).to_be_visible()
        _assert_no_horizontal_overflow(zoom_page)
        _assert_season_targets(zoom_page)
        zoom_page.get_by_role(
            "navigation", name="Podcast seasons", exact=True
        ).scroll_into_view_if_needed()
        _screenshot(
            zoom_page,
            f"podcast-season-{zoom_season}-200-percent-zoom.png",
            full_page=False,
        )
    finally:
        zoom_context.close()


@pytest.mark.core
def test_alias_query_and_safe_denial_browser_matrix(page: Page, live_server) -> None:
    origin = live_server.url
    # A season the catalogue publishes and one it does not, both read from the
    # catalogue: the season lives in the path now, so an alias's query is just
    # carried along, and the denial matrix needs a well-formed season path with
    # no season behind it.
    numbers = _season_numbers()
    published_season = numbers[len(numbers) // 2]
    absent_season = max(numbers) + 1
    for alias in ("/podcast.html", "/podcast/"):
        redirected = page.request.get(f"{origin}{alias}?season={published_season}", max_redirects=0)
        assert redirected.status == 301
        assert redirected.headers["location"] == f"/podcast?season={published_season}"
        final = page.goto(f"{origin}{alias}?season={published_season}", wait_until="networkidle")
        assert final is not None and final.status == 200
        expect(page).to_have_url(f"{origin}/podcast?season={published_season}")
        # `?season=` selects nothing: the hub answers its newest season.
        expect(_season_heading(page, numbers[0])).to_be_visible()

        invalid_redirect = page.request.get(f"{origin}{alias}?page=2", max_redirects=0)
        assert invalid_redirect.status == 301
        assert invalid_redirect.headers["location"] == "/podcast?page=2"

    episode = ordered_podcasts()[0]
    final_path = episode["public_path"]
    detail_query = "utm_source=oncall%2Btest&x=a%2Fb&blank="
    final = page.goto(f"{origin}{final_path}?{detail_query}", wait_until="networkidle")
    assert final is not None and final.status == 200
    expect(page).to_have_url(f"{origin}{final_path}?{detail_query}")
    expect(page.get_by_role("heading", name=episode["title"], exact=True)).to_be_visible()
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href",
        f"https://datatalks.club{final_path}",
    )
    expect(page.locator('meta[property="og:url"]')).to_have_attribute(
        "content",
        f"https://datatalks.club{final_path}",
    )

    competing_path = f"/podcast/s{episode['season']:02d}/e{episode['episode']:02d}-competing-title"
    competing = page.request.get(f"{origin}{competing_path}", max_redirects=0)
    assert competing.status == 301
    assert competing.headers["location"] == final_path

    unknown = page.request.get(
        f"{origin}/podcast/s99/e99-competing-title",
        max_redirects=0,
    )
    assert unknown.status == 404
    assert "location" not in unknown.headers
    assert "canonical" not in unknown.text().casefold()

    # A query string selects nothing on the hub (`content.public_query`, issue #174
    # follow-up): the season lives in the path.  `/podcast?page=2` and even a
    # malformed `?season=` answer the clean newest-season page and declare the
    # clean canonical, which is what keeps a tagged URL from duplicating the
    # catalogue across a crawl.
    latest_html = page.request.get(f"{origin}/podcast").text()
    for query in ("page=2", "season=01", "season=1&season=2", f"season={published_season}"):
        ride_along = page.request.get(f"{origin}/podcast?{query}", max_redirects=0)
        assert ride_along.status == 200
        assert query not in ride_along.text()
        assert 'canonical" href="https://datatalks.club/podcast"' in ride_along.text()
        assert ride_along.text() == latest_html

    denials = (
        ("GET", f"/podcast/s{absent_season:02d}/", 404),
        ("POST", f"/podcast/s{published_season:02d}/", 405),
    )
    for method, path, status in denials:
        response = (
            page.request.get(f"{origin}{path}", max_redirects=0)
            if method == "GET"
            else page.request.post(f"{origin}{path}")
        )
        assert response.status == status
        assert "no-store" in response.headers.get("cache-control", "")
        assert "canonical" not in response.text().casefold()
        assert "data-podcast-episode" not in response.text()
        assert "traceback" not in response.text().casefold()
        assert response.headers.get("x-robots-tag") == "noindex, nofollow"
        if method == "POST":
            assert response.headers["allow"] == "GET, HEAD"

    browser_error = page.goto(f"{origin}/podcast/s{absent_season:02d}/")
    assert browser_error is not None and browser_error.status == 404
    expect(page.locator('link[rel="canonical"]')).to_have_count(0)
    expect(page.locator('link[rel="prev"]')).to_have_count(0)
    expect(page.locator('link[rel="next"]')).to_have_count(0)
    expect(page.locator("body")).not_to_contain_text("Traceback")
    _screenshot(page, "podcast-absent-season-404.png")


@pytest.mark.core
def test_genai_pilots_episode_answers_at_its_canonical_only(page: Page, live_server) -> None:
    origin = live_server.url
    canonical_path = "/podcast/s24/e04-from-genai-pilots-to-production"

    final = page.goto(f"{origin}{canonical_path}", wait_until="networkidle")
    assert final is not None and final.status == 200
    expect(page).to_have_url(f"{origin}{canonical_path}")
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href",
        f"https://datatalks.club{canonical_path}",
    )
    expect(page.locator('meta[property="og:url"]')).to_have_attribute(
        "content",
        f"https://datatalks.club{canonical_path}",
    )

    # No older URL shape is routed: the flat .html, flat and stable-id forms 404.
    for unavailable_path in (
        "/podcast/s24e04-from-genai-pilots-to-production.html",
        "/podcast/s24e04-from-genai-pilots-to-production",
        "/podcast/s24e04/from-genai-pilots-to-production",
        "/podcast/s24e04",
    ):
        for method in (page.request.get, page.request.head):
            unavailable = method(f"{origin}{unavailable_path}", max_redirects=0)
            assert unavailable.status == 404
            assert "location" not in unavailable.headers
            assert '<link rel="canonical"' not in unavailable.text().casefold()
