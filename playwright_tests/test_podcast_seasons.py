from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, ViewportSize, expect

from content.public_data import ordered_podcasts, podcast_seasons
from playwright_tests.accessibility_support import assert_accessible_page

SCREENSHOTS = Path(".tmp/screenshots/issue-132")
# The design 5a rebuild (issue #179) gave the index the mockup's own headline; the
# season heading it navigates by is unchanged.
PODCAST_HEADING = "Conversations with people who ship data"


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
    targets = page.get_by_role("navigation", name="Podcast seasons").locator(".pagination-target")
    sizes = targets.evaluate_all(
        """(nodes) => nodes.map((node) => {
          const rect = node.getBoundingClientRect();
          return {text: node.textContent.trim(), width: rect.width, height: rect.height};
        })"""
    )
    assert len(sizes) >= 24
    assert all(item["width"] >= 44 and item["height"] >= 44 for item in sizes), sizes


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

    page.locator("#site-navigation-links").get_by_role(
        "link",
        name="Podcast",
        exact=True,
    ).click()
    expect(page).to_have_url(f"{origin}/podcast")
    expect(page.get_by_role("heading", name="Season 24", exact=True)).to_be_visible()
    expect(page.locator("[data-podcast-episode]").first).to_contain_text(latest["title"])


@pytest.mark.core
def test_podcast_card_is_a_whole_keyboard_destination_without_nested_interactives(
    page: Page,
    live_server,
) -> None:
    episode = ordered_podcasts()[0]
    expected_meta = f"Season {episode['season']} · Episode {episode['episode']}"
    origin = live_server.url

    page.set_viewport_size({"width": 390, "height": 844})
    response = page.goto(f"{origin}/podcast", wait_until="networkidle")
    assert response is not None and response.status == 200
    _settle_analytics_preferences(page)

    card = page.locator("[data-podcast-episode]").first.locator(".podcast-card")
    expect(card).to_have_count(1)
    meta = card.locator(".podcast-meta")
    expect(meta).to_have_count(1)
    assert meta.evaluate("node => node.textContent.trim()") == expected_meta
    expect(card.locator(".status-pill")).to_have_count(0)
    expect(card.locator(".archive-title a")).to_have_attribute(
        "href",
        episode["public_path"],
    )

    card_style = card.evaluate(
        """(node) => {
          const style = getComputedStyle(node);
          return {
            background: style.backgroundColor,
            border: style.borderTopWidth,
            shadow: style.boxShadow,
          };
        }"""
    )
    assert card_style == {
        "background": "rgba(0, 0, 0, 0)",
        "border": "2px",
        "shadow": "none",
    }, card_style
    meta_style = meta.evaluate(
        """(node) => {
          const style = getComputedStyle(node);
          return {
            background: style.backgroundColor,
            border: style.borderTopWidth,
            radius: style.borderTopLeftRadius,
          };
        }"""
    )
    assert meta_style == {
        "background": "rgba(0, 0, 0, 0)",
        "border": "0px",
        "radius": "0px",
    }, meta_style

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
    card = page.locator("[data-podcast-episode]").first.locator(".podcast-card")

    # A physical point on the card still activates the title anchor's stretched
    # hit area.  The pseudo-element is intentionally the topmost pointer target.
    box = card.bounding_box()
    assert box is not None
    page.mouse.click(box["x"] + box["width"] - 8, box["y"] + box["height"] / 2)
    expect(page).to_have_url(f"{origin}{episode['public_path']}")

    page.go_back(wait_until="networkidle")
    _settle_analytics_preferences(page)
    title_link = page.locator("[data-podcast-episode]").first.locator(".archive-title a")
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

    scenarios = (
        (24, "/podcast", None, 23),
        (12, "/podcast?season=12", 13, 11),
        (1, "/podcast?season=1", 2, None),
    )
    inventory = {season.number: season for season in podcast_seasons()}
    for season, path, newer, older in scenarios:
        response = page.goto(f"{origin}{path}", wait_until="networkidle")
        assert response is not None and response.status == 200
        _settle_analytics_preferences(page)
        expect(page.locator("main h1")).to_have_count(1)
        expect(page.locator("main h2")).to_have_count(1)
        expect(page.get_by_role("heading", name=PODCAST_HEADING, exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name=f"Season {season}", exact=True)).to_be_visible()
        expect(page.locator("main h3")).to_have_count(len(inventory[season].episodes))
        expect(page.locator("[data-podcast-season]")).to_have_count(1)
        expect(page.locator("[data-podcast-episode]")).to_have_count(
            len(inventory[season].episodes)
        )

        navigation = page.get_by_role("navigation", name="Podcast seasons")
        expect(navigation.locator('[aria-current="page"]')).to_have_count(1)
        expect(navigation.locator('[aria-current="page"]')).to_have_text(f"Season {season}")
        expect(navigation.get_by_role("link", name="Season 24", exact=True)).to_have_count(
            0 if season == 24 else 1
        )
        expect(navigation.get_by_role("link", name="Season 12", exact=True)).to_have_count(
            0 if season == 12 else 1
        )
        expect(navigation.get_by_role("link", name="Season 1", exact=True)).to_have_count(
            0 if season == 1 else 1
        )
        expect(page.locator('link[rel="canonical"]')).to_have_attribute(
            "href",
            f"https://datatalks.club{path}",
        )
        expect(page.locator('meta[property="og:url"]')).to_have_attribute(
            "content",
            f"https://datatalks.club{path}",
        )

        if newer is None:
            expect(navigation.get_by_role("link", name="Newer season", exact=False)).to_have_count(
                0
            )
            expect(page.locator('link[rel="prev"]')).to_have_count(0)
        else:
            newer_name = f"Newer season — Season {newer}"
            newer_path = "/podcast" if newer == 24 else f"/podcast?season={newer}"
            expect(navigation.get_by_role("link", name=newer_name, exact=True)).to_have_attribute(
                "href",
                newer_path,
            )
            expect(page.locator('link[rel="prev"]')).to_have_attribute(
                "href",
                f"https://datatalks.club{newer_path}",
            )

        if older is None:
            expect(navigation.get_by_role("link", name="Older season", exact=False)).to_have_count(
                0
            )
            expect(page.locator('link[rel="next"]')).to_have_count(0)
        else:
            older_name = f"Older season — Season {older}"
            older_path = f"/podcast?season={older}"
            expect(navigation.get_by_role("link", name=older_name, exact=True)).to_have_attribute(
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
        page.goto(f"{origin}/podcast?season=12", wait_until="networkidle")
        older_link = page.get_by_role("link", name="Older season — Season 11", exact=True)

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
            raise AssertionError("keyboard traversal did not focus Older season — Season 11")

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
        # Design 5a's global focus ring is 3px solid at a 2px offset
        # (_docs/design/design-5a.md); the ring still clears the control it marks.
        assert focus["offset"] >= 2, focus
        _screenshot(page, "podcast-season-12-mobile-focus.png")

    assert failed_requests == []
    assert console_errors == []


@pytest.mark.core
def test_season_controls_activate_direct_normalized_destinations(page: Page, live_server) -> None:
    origin = live_server.url
    page.goto(f"{origin}/podcast", wait_until="networkidle")

    page.get_by_role("link", name="Older season — Season 23", exact=True).click()
    expect(page).to_have_url(f"{origin}/podcast?season=23")
    expect(page.get_by_role("heading", name="Season 23", exact=True)).to_be_visible()
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href",
        "https://datatalks.club/podcast?season=23",
    )

    page.get_by_role("link", name="Newer season — Season 24", exact=True).click()
    expect(page).to_have_url(f"{origin}/podcast")
    expect(page.get_by_role("heading", name="Season 24", exact=True)).to_be_visible()

    page.get_by_role("link", name="Season 12", exact=True).click()
    expect(page).to_have_url(f"{origin}/podcast?season=12")
    expect(page.get_by_role("heading", name="Season 12", exact=True)).to_be_visible()

    page.get_by_role("link", name="Season 1", exact=True).click()
    expect(page).to_have_url(f"{origin}/podcast?season=1")
    expect(page.get_by_role("heading", name="Season 1", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Older season", exact=False)).to_have_count(0)


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
        for season, path in (
            (24, "/podcast"),
            (12, "/podcast?season=12"),
            (1, "/podcast?season=1"),
        ):
            response = page.goto(f"{live_server.url}{path}", wait_until="domcontentloaded")
            assert response is not None and response.status == 200
            expect(page.get_by_role("heading", name=f"Season {season}", exact=True)).to_be_visible()
            expect(page.get_by_role("navigation", name="Podcast seasons")).to_be_visible()
            _assert_no_horizontal_overflow(page)
            _assert_season_targets(page)
            page.get_by_role("navigation", name="Podcast seasons").scroll_into_view_if_needed()
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
        response = zoom_page.goto(
            f"{live_server.url}/podcast?season=12",
            wait_until="domcontentloaded",
        )
        assert response is not None and response.status == 200
        _settle_analytics_preferences(zoom_page)
        zoom_page.evaluate("document.documentElement.style.zoom = '2'")
        expect(zoom_page.get_by_role("heading", name="Season 12", exact=True)).to_be_visible()
        _assert_no_horizontal_overflow(zoom_page)
        _assert_season_targets(zoom_page)
        zoom_page.get_by_role("navigation", name="Podcast seasons").scroll_into_view_if_needed()
        _screenshot(
            zoom_page,
            "podcast-season-12-200-percent-zoom.png",
            full_page=False,
        )
    finally:
        zoom_context.close()


@pytest.mark.core
def test_alias_query_and_safe_denial_browser_matrix(page: Page, live_server) -> None:
    origin = live_server.url
    for alias in ("/podcast.html", "/podcast/"):
        redirected = page.request.get(f"{origin}{alias}?season=12", max_redirects=0)
        assert redirected.status == 301
        assert redirected.headers["location"] == "/podcast?season=12"
        final = page.goto(f"{origin}{alias}?season=12", wait_until="networkidle")
        assert final is not None and final.status == 200
        expect(page).to_have_url(f"{origin}/podcast?season=12")

        invalid_redirect = page.request.get(f"{origin}{alias}?page=2", max_redirects=0)
        assert invalid_redirect.status == 301
        assert invalid_redirect.headers["location"] == "/podcast?page=2"

    episode = ordered_podcasts()[0]
    final_path = episode["public_path"]
    detail_query = "utm_source=oncall%2Btest&x=a%2Fb&blank="
    for alias in (final_path.removesuffix(".html"), f"{final_path.removesuffix('.html')}/"):
        redirected = page.request.get(
            f"{origin}{alias}?{detail_query}",
            max_redirects=0,
        )
        assert redirected.status == 301
        assert redirected.headers["location"] == f"{final_path}?{detail_query}"
        head = page.request.head(f"{origin}{alias}?{detail_query}", max_redirects=0)
        assert head.status == 301
        assert head.headers["location"] == f"{final_path}?{detail_query}"

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

    competing_path = f"/podcast/s{episode['season']:02d}e{episode['episode']:02d}/competing-title"
    competing = page.request.get(f"{origin}{competing_path}", max_redirects=0)
    assert competing.status == 404
    assert "location" not in competing.headers
    assert "canonical" not in competing.text().casefold()

    # A parameter this catalogue does not select on rides along instead of being
    # refused (`content.public_query`, issue #174 follow-up): `page` is not a
    # podcast selector, so `/podcast?page=2` answers the clean latest-season page
    # and declares the clean canonical, which is what keeps a tagged URL from
    # duplicating the catalogue across a crawl.  The season grammar itself stays
    # strict: a malformed or doubled selector is still a bad request.
    ride_along = page.request.get(f"{origin}/podcast?page=2", max_redirects=0)
    assert ride_along.status == 200
    assert "page=2" not in ride_along.text()
    assert 'canonical" href="https://datatalks.club/podcast"' in ride_along.text()
    assert ride_along.text() == page.request.get(f"{origin}/podcast").text()

    denials = (
        ("GET", "/podcast?season=01", 400),
        ("GET", "/podcast?season=1&season=2", 400),
        ("GET", "/podcast?season=25", 404),
        ("POST", "/podcast?season=12", 405),
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

    browser_error = page.goto(f"{origin}/podcast?season=01")
    assert browser_error is not None and browser_error.status == 400
    expect(page.locator('link[rel="canonical"]')).to_have_count(0)
    expect(page.locator('link[rel="prev"]')).to_have_count(0)
    expect(page.locator('link[rel="next"]')).to_have_count(0)
    expect(page.locator("body")).not_to_contain_text("Traceback")
    _screenshot(page, "podcast-malformed-season-query-400.png")
