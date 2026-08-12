from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, ViewportSize, expect

from content.public_data import public_projection

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

SCREENSHOTS = Path(".tmp/screenshots/issue-105")
PODCAST_SCREENSHOTS = Path(".tmp/screenshots/issue-132")
EVENT_DESCRIPTION_SCREENSHOTS = Path(".tmp/screenshots/issue-131")
FEATURED_EVENT_PATH = next(
    event["public_path"]
    for event in public_projection()["events"]
    if event["title"] == "AI Dev Tools Zoomcamp 2026 Course Launch"
)
FEATURED_EVENT_TITLE = "AI Dev Tools Zoomcamp 2026 Course Launch"
FEATURED_SPEAKER_PATH = "/people/alexeygrigorev.html"
FEATURED_SPEAKER_NAME = "Alexey Grigorev"


def _shot(page: Page, name: str, *, full_page: bool = False) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / name, full_page=full_page)


def _podcast_shot(page: Page, name: str) -> None:
    preferences = page.get_by_role("dialog", name="Optional analytics")
    if preferences.is_visible():
        preferences.get_by_role("button", name="Keep analytics off").click()
    PODCAST_SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=PODCAST_SCREENSHOTS / name, full_page=True)


def _event_description_shot(page: Page, name: str) -> None:
    EVENT_DESCRIPTION_SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=EVENT_DESCRIPTION_SCREENSHOTS / name, full_page=True)


@pytest.mark.core
@pytest.mark.parametrize(
    ("viewport", "suffix"),
    [({"width": 1280, "height": 800}, "desktop"), ({"width": 390, "height": 844}, "mobile")],
)
def test_public_home_and_hubs(
    page: Page,
    live_server,
    viewport: ViewportSize,
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    origin = live_server.url
    response = page.goto(origin)
    assert response is not None and response.status == 200
    expect(page).to_have_title("Welcome to DataTalks.Club")
    expect(page.get_by_role("heading", name="The place to talk about data")).to_be_visible()
    expect(page.get_by_text("Talk about data, machine learning, and engineering")).to_be_visible()
    expect(page.get_by_role("heading", name="Upcoming events", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Latest podcast episodes", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Book of the week", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Latest articles", exact=True)).to_be_visible()
    featured_course = page.locator("[data-featured-course]")
    expect(featured_course).to_have_count(1)
    expect(featured_course.get_by_text("AI Dev Tools Zoomcamp", exact=True)).to_be_visible()
    expect(featured_course.get_by_text("2026 cohort", exact=True)).to_be_visible()
    expect(featured_course.get_by_text("Starts August 31, 2026", exact=True)).to_be_visible()
    expect(featured_course.get_by_role("link", name="View cohort")).to_have_attribute(
        "href",
        "/courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026",
    )
    expect(page.get_by_role("link", name="Browse all courses")).to_have_attribute(
        "href",
        "/courses",
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    expect(
        page.locator("#site-navigation-links").get_by_role("link", name="People", exact=True)
    ).to_have_count(0)
    _shot(page, f"home-{suffix}.png", full_page=True)

    for label, path, heading in (
        ("Events", "/events", "Events"),
        ("Courses", "/courses", "Learn data skills. For free. Together."),
        ("Blog", "/blog", "Latest Articles"),
        ("Podcast", "/podcast", "Podcast"),
        ("Wiki", "/wiki", "DataTalks.Club Podcast Wiki"),
        ("Books", "/books", "Book of the Week"),
    ):
        page.goto(origin)
        if viewport["width"] < 1024:
            page.get_by_role("button", name="Explore").click()
        page.locator("#site-navigation-links").get_by_role("link", name=label, exact=True).click()
        expect(page).to_have_url(f"{origin}{path}")
        expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        if label == "Courses":
            expect(page.get_by_text("No active courses right now.", exact=True)).to_be_visible()
            _shot(page, f"{label.casefold()}-hub-{suffix}.png", full_page=True)
        else:
            _shot(page, f"{label.casefold()}-hub-{suffix}.png")

        if label == "Wiki":
            expect(page.locator('nav[aria-label="Wiki exploration"]')).to_have_css(
                "flex-direction",
                "column",
            )


@pytest.mark.core
@pytest.mark.parametrize(
    ("viewport", "suffix"),
    [({"width": 1280, "height": 800}, "desktop"), ({"width": 390, "height": 844}, "mobile")],
)
def test_internal_event_to_person_flow(
    page: Page,
    live_server,
    viewport: ViewportSize,
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    origin = live_server.url
    page.goto(f"{origin}/events")
    page.get_by_role("link", name=FEATURED_EVENT_TITLE, exact=True).click()
    expect(page).to_have_url(f"{origin}{FEATURED_EVENT_PATH}")
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href",
        f"https://datatalks.club{FEATURED_EVENT_PATH}",
    )
    expect(page.locator('section[aria-label="Event description"]')).to_have_count(1)
    expect(
        page.get_by_text("The new cohort of AI Dev Tools Zoomcamp 2026 starts", exact=False)
    ).to_be_visible()
    expect(page.get_by_role("heading", name="Event links", exact=True)).to_have_count(0)
    expect(page.locator('a[href*="luma.com"], a[href*="lu.ma"]')).to_have_count(0)
    expect(page.locator(f'a[href="{FEATURED_EVENT_PATH}/register"]')).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    _event_description_shot(page, f"described-no-external-links-{suffix}.png")

    page.get_by_role("link", name=FEATURED_SPEAKER_NAME, exact=True).click()
    expect(page).to_have_url(f"{origin}{FEATURED_SPEAKER_PATH}")
    expect(page.get_by_role("heading", name=FEATURED_SPEAKER_NAME, exact=True)).to_be_visible()
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href",
        f"https://datatalks.club{FEATURED_SPEAKER_PATH}",
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    _shot(page, f"person-detail-{suffix}.png", full_page=True)


@pytest.mark.core
@pytest.mark.parametrize(
    ("viewport", "suffix"),
    [({"width": 1280, "height": 800}, "desktop"), ({"width": 390, "height": 844}, "mobile")],
)
def test_podcast_latest_middle_and_oldest_seasons(
    page: Page,
    live_server,
    viewport: ViewportSize,
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    origin = live_server.url
    failed_requests: list[str] = []
    page.on("requestfailed", lambda request: failed_requests.append(request.url))

    scenarios = (
        (24, "/podcast"),
        (12, "/podcast?season=12"),
        (1, "/podcast?season=1"),
    )
    for season, path in scenarios:
        response = page.goto(f"{origin}{path}")
        assert response is not None and response.status == 200
        expect(page.get_by_role("heading", name="Podcast", exact=True)).to_be_visible()
        expect(page.locator("main h2")).to_have_count(1)
        expect(page.get_by_role("heading", name=f"Season {season}", exact=True)).to_be_visible()
        current_season = page.get_by_role("navigation", name="Podcast seasons").locator(
            '[aria-current="page"]'
        )
        expect(current_season).to_have_text(f"Season {season}")
        expect(current_season).to_have_attribute(
            "aria-label",
            f"Season {season}, current season",
        )
        expect(page.locator('link[rel="canonical"]')).to_have_attribute(
            "href",
            f"https://datatalks.club{path}",
        )
        expect(page.locator("[data-podcast-season]")).to_have_count(1)
        expect(page.get_by_role("link", name="Season 24", exact=True)).to_have_count(
            0 if season == 24 else 1
        )
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        _podcast_shot(page, f"podcast-season-{season}-{suffix}.png")

    assert failed_requests == []


@pytest.mark.core
def test_all_public_hub_aliases_redirect_once_with_query(page: Page, live_server) -> None:
    origin = live_server.url
    aliases = {
        "/articles.html": "/blog",
        "/blog/": "/blog",
        "/podcast.html": "/podcast",
        "/podcast/": "/podcast",
        "/books.html": "/books",
        "/books/": "/books",
        "/events.html": "/events",
        "/events/": "/events",
        "/courses/": "/courses",
        "/wiki/": "/wiki",
        "/blog/guide-to-free-online-courses-at-datatalks-club": (
            "/blog/guide-to-free-online-courses-at-datatalks-club.html"
        ),
        "/blog/guide-to-free-online-courses-at-datatalks-club/": (
            "/blog/guide-to-free-online-courses-at-datatalks-club.html"
        ),
        "/podcast/practical-llm-engineering-and-rag": (
            "/podcast/practical-llm-engineering-and-rag.html"
        ),
        "/podcast/practical-llm-engineering-and-rag/": (
            "/podcast/practical-llm-engineering-and-rag.html"
        ),
        "/books/20251006-software-development-at-rocket-speed": (
            "/books/20251006-software-development-at-rocket-speed.html"
        ),
        "/books/20251006-software-development-at-rocket-speed/": (
            "/books/20251006-software-development-at-rocket-speed.html"
        ),
        "/people/alexeygrigorev": "/people/alexeygrigorev.html",
        "/people/alexeygrigorev/": "/people/alexeygrigorev.html",
    }
    for source, target in aliases.items():
        query = "season=12" if source in {"/podcast.html", "/podcast/"} else "source=browser"
        response = page.request.get(
            f"{origin}{source}?{query}",
            max_redirects=0,
        )
        assert response.status == 301
        assert response.headers["location"] == f"{target}?{query}"
        navigation = page.goto(f"{origin}{source}?{query}")
        assert navigation is not None and navigation.status == 200
        expect(page).to_have_url(f"{origin}{target}?{query}")
        assert navigation.request.redirected_from is not None
        assert navigation.request.redirected_from.redirected_from is None

    for path in ("/people", "/people/", "/people.html"):
        response = page.request.get(f"{origin}{path}", max_redirects=0)
        assert response.status == 404
        assert "location" not in response.headers


@pytest.mark.core
def test_public_pages_remain_meaningful_without_javascript(
    browser: Browser,
    live_server,
) -> None:
    context = browser.new_context(
        java_script_enabled=False,
        viewport={"width": 390, "height": 844},
        reduced_motion="reduce",
    )
    page = context.new_page()
    try:
        for path, heading in (
            ("/", "The place to talk about data"),
            ("/events", "Events"),
            (FEATURED_EVENT_PATH, FEATURED_EVENT_TITLE),
            (FEATURED_SPEAKER_PATH, FEATURED_SPEAKER_NAME),
            ("/wiki/search", "Search"),
        ):
            response = page.goto(f"{live_server.url}{path}")
            assert response is not None and response.status == 200
            expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
            overflow = page.evaluate(
                """() => ({
                  innerWidth: window.innerWidth,
                  scrollWidth: document.documentElement.scrollWidth,
                  sources: [...document.querySelectorAll('body *')]
                    .filter((node) => {
                      const rect = node.getBoundingClientRect();
                      return rect.left < 0 || rect.right > window.innerWidth;
                    })
                    .slice(0, 5)
                    .map((node) => {
                      const rect = node.getBoundingClientRect();
                      return `${node.tagName.toLowerCase()}#${node.id}.${node.className}`
                        + ` [${Math.round(rect.left)},${Math.round(rect.right)}]`;
                    }),
                })"""
            )
            assert overflow["scrollWidth"] <= overflow["innerWidth"], overflow
    finally:
        context.close()


@pytest.mark.core
def test_oldest_latest_details_and_media_fallback(page: Page, live_server) -> None:
    origin = live_server.url
    for path in (
        "/blog/sponsor-datatalks-club.html",
        "/podcast/practical-llm-engineering-and-rag.html",
        "/books/20251006-software-development-at-rocket-speed.html",
        "/wiki/a-a-testing",
    ):
        response = page.goto(f"{origin}{path}")
        assert response is not None and response.status == 200
        expect(page.locator("h1")).to_be_visible()
        expect(page.locator('link[rel="canonical"]')).to_have_attribute(
            "href", f"https://datatalks.club{path}"
        )
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.evaluate("window.scrollTo(0, 0)")
        if path.startswith("/blog/"):
            _shot(page, "article-detail-desktop.png")
        if path.startswith("/wiki/"):
            _shot(page, "wiki-detail-desktop.png")


@pytest.mark.core
def test_wiki_search_graph_and_removed_mount(page: Page, live_server) -> None:
    origin = live_server.url
    page.goto(f"{origin}/wiki")
    page.get_by_label("Search the Wiki").fill("machine learning")
    page.get_by_role("button", name="Search").click()
    expect(page).to_have_url(f"{origin}/wiki?q=machine+learning")
    expect(page.get_by_text("results for “machine learning”")).to_be_visible()
    page.locator("main article a").first.click()
    expect(page.locator("h1")).to_be_visible()

    page.goto(f"{origin}/wiki/graph")
    expect(page.get_by_role("heading", name="Podcast Graph")).to_be_visible()
    expect(page.locator("main a").first).to_be_visible()

    response = page.goto(f"{origin}/podwiki")
    assert response is not None and response.status == 404
    expect(page.get_by_role("heading", name="Page not found")).to_be_visible()
    expect(page).to_have_url(f"{origin}/podwiki")
    _shot(page, "podwiki-404-desktop.png")


@pytest.mark.core
def test_mobile_keyboard_navigation_and_no_results(page: Page, live_server) -> None:
    origin = live_server.url
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(origin)
    menu = page.get_by_role("button", name="Explore")
    menu.focus()
    page.keyboard.press("Enter")
    expect(menu).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("Escape")
    expect(menu).to_have_attribute("aria-expanded", "false")
    expect(menu).to_be_focused()

    page.goto(f"{origin}/wiki?q=no-such-public-topic")
    expect(page.get_by_text("No matching Wiki pages were found.")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    _shot(page, "wiki-search-empty-mobile.png")


@pytest.mark.core
def test_anonymous_staff_member_reaches_safe_sign_in_flow(page: Page, live_server) -> None:
    response = page.goto(f"{live_server.url}/studio/")
    assert response is not None and response.status == 200
    expect(page).to_have_url(f"{live_server.url}/accounts/login/?next=%2Fstudio%2F")
    expect(page.get_by_role("heading", name="Sign In")).to_be_visible()
