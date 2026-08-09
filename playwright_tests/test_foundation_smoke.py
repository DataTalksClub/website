import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, ViewportSize, expect

SCREENSHOTS = Path(".tmp/screenshots/issue-105")
FEATURED_EVENT_PATH = "/events/2026-08-31-ai-dev-tools-zoomcamp-2026-course-launch"
FEATURED_EVENT_TITLE = "AI Dev Tools Zoomcamp 2026 Course Launch"
FEATURED_SPEAKER_PATH = "/people/alexeygrigorev"
FEATURED_SPEAKER_NAME = "Alexey Grigorev"


def _shot(page: Page, name: str, *, full_page: bool = False) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / name, full_page=full_page)


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
    expect(page.get_by_text("Blog · 55")).to_be_visible()
    expect(page.get_by_text("Podcast · 205")).to_be_visible()
    expect(page.get_by_text("Books · 98")).to_be_visible()
    expect(page.get_by_text("People · 438")).to_be_visible()
    expect(page.get_by_text("Wiki · 282")).to_be_visible()
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
    _shot(page, f"home-{suffix}.png", full_page=True)

    for label, path, heading in (
        ("Events", "/events", "Events"),
        ("Courses", "/courses", "Courses"),
        ("Blog", "/blog", "Blog"),
        ("Podcast", "/podcast", "Podcast"),
        ("Wiki", "/wiki", "DataTalks.Club Wiki"),
        ("Books", "/books", "Books"),
        ("People", "/people", "People"),
    ):
        page.goto(origin)
        if viewport["width"] < 1024:
            page.get_by_role("button", name="Explore").click()
        page.locator("#site-navigation-links").get_by_role("link", name=label, exact=True).click()
        expect(page).to_have_url(f"{origin}{path}")
        expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        if label in {"Events", "Courses", "Blog", "Wiki", "People"}:
            if label == "Courses":
                expect(page.locator("[data-course-row]")).to_have_count(12)
                _shot(page, f"{label.casefold()}-hub-{suffix}.png", full_page=True)
            else:
                _shot(page, f"{label.casefold()}-hub-{suffix}.png")

        if label == "People":
            expect(page.get_by_text("Page 1 of 10", exact=True)).to_be_visible()
            page.get_by_role("link", name="Next", exact=True).click()
            expect(page).to_have_url(f"{origin}/people?page=2")
            expect(page.get_by_text("Page 2 of 10", exact=True)).to_be_visible()

    response = page.goto(f"{origin}/courses/ai-dev-tools-zoomcamp")
    assert response is not None and response.status == 200
    expect(page.get_by_role("heading", name="AI Dev Tools Zoomcamp", exact=True)).to_be_visible()
    course_cohort = page.locator("[data-featured-course]")
    expect(course_cohort).to_have_count(1)
    expect(course_cohort.get_by_text("2026 cohort", exact=True)).to_be_visible()
    expect(course_cohort.get_by_text("Starts August 31, 2026", exact=True)).to_be_visible()
    expect(course_cohort.get_by_role("link", name="View cohort")).to_have_attribute(
        "href",
        "/courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026",
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    _shot(page, f"ai-dev-tools-course-{suffix}.png", full_page=True)


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
    external = page.get_by_role("link", name=re.compile("Register on Luma"))
    expect(external).to_have_attribute("target", "_blank")
    expect(external).to_have_attribute("rel", "noopener noreferrer")
    expect(external).to_have_attribute("href", re.compile(r"^https://(?:luma\.com|lu\.ma)/"))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    _shot(page, f"event-detail-{suffix}.png", full_page=True)

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
def test_all_public_hub_aliases_redirect_once_with_query(page: Page, live_server) -> None:
    origin = live_server.url
    aliases = {
        "/articles.html": "/blog",
        "/blog/": "/blog",
        "/podcast.html": "/podcast",
        "/podcast/": "/podcast",
        "/books.html": "/books",
        "/books/": "/books",
        "/people.html": "/people",
        "/people/": "/people",
        "/events.html": "/events",
        "/events/": "/events",
        "/courses/": "/courses",
        "/wiki/": "/wiki",
        "/blog/guide-to-free-online-courses-at-datatalks-club.html": (
            "/blog/guide-to-free-online-courses-at-datatalks-club"
        ),
        "/blog/guide-to-free-online-courses-at-datatalks-club/": (
            "/blog/guide-to-free-online-courses-at-datatalks-club"
        ),
        "/podcast/practical-llm-engineering-and-rag.html": (
            "/podcast/practical-llm-engineering-and-rag"
        ),
        "/podcast/practical-llm-engineering-and-rag/": (
            "/podcast/practical-llm-engineering-and-rag"
        ),
        "/books/20251006-software-development-at-rocket-speed.html": (
            "/books/20251006-software-development-at-rocket-speed"
        ),
        "/books/20251006-software-development-at-rocket-speed/": (
            "/books/20251006-software-development-at-rocket-speed"
        ),
        "/people/alexeygrigorev.html": "/people/alexeygrigorev",
        "/people/alexeygrigorev/": "/people/alexeygrigorev",
    }
    for source, target in aliases.items():
        response = page.request.get(
            f"{origin}{source}?source=browser",
            max_redirects=0,
        )
        assert response.status == 301
        assert response.headers["location"] == f"{target}?source=browser"
        navigation = page.goto(f"{origin}{source}?source=browser")
        assert navigation is not None and navigation.status == 200
        expect(page).to_have_url(f"{origin}{target}?source=browser")
        assert navigation.request.redirected_from is not None
        assert navigation.request.redirected_from.redirected_from is None


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
            ("/people", "People"),
            ("/wiki/search", "Search"),
        ):
            response = page.goto(f"{live_server.url}{path}")
            assert response is not None and response.status == 200
            expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


@pytest.mark.core
def test_oldest_latest_details_and_media_fallback(page: Page, live_server) -> None:
    origin = live_server.url
    for path in (
        "/blog/sponsor-datatalks-club",
        "/podcast/practical-llm-engineering-and-rag",
        "/books/20251006-software-development-at-rocket-speed",
        "/wiki/a-a-testing",
        "/courses/de-zoomcamp-2026",
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
        if path.startswith("/courses/"):
            _shot(page, "course-detail-desktop.png")


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
    expect(page.get_by_role("heading", name="Knowledge graph")).to_be_visible()
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
