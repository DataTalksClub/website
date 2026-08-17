from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, ViewportSize, expect

from content.docs_projection import docs_projection
from content.public_data import public_projection

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

SCREENSHOTS = Path(".tmp/screenshots/issue-105")
PODCAST_SCREENSHOTS = Path(".tmp/screenshots/issue-132")
EVENT_DESCRIPTION_SCREENSHOTS = Path(".tmp/screenshots/issue-131")
FEATURED_EVENT_TITLE = "AI Dev Tools Zoomcamp 2026 Course Launch"
FEATURED_SPEAKER_PATH = "/people/alexeygrigorev.html"
FEATURED_SPEAKER_NAME = "Alexey Grigorev"
HOME_HEADING = "Ship data pipelines and AI systems that run in production."
# The podcast index carries the design 5a headline from mockup 6d (issue #179).
PODCAST_HEADING = "Conversations with people who ship data"
# The events index is a design 5a page (issue #179, mockup 6c) and leads with the
# mockup's own headline; "Events" is now only the navigation label and the page title.
EVENTS_HEADING = "Something happening every week"


def _featured_event_path() -> str:
    """Resolve the DB-backed numeric event URL once test data exists."""

    return next(
        event["public_path"]
        for event in public_projection()["events"]
        if event["title"] == FEATURED_EVENT_TITLE
    )


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
    expect(page).to_have_title("DataTalks.Club — free courses for data and AI engineers")
    expect(page.get_by_role("heading", name=HOME_HEADING)).to_be_visible()
    expect(
        page.get_by_role("heading", name="The climb, as members describe it", exact=True)
    ).to_be_visible()
    expect(
        page.get_by_role("heading", name="Something to attend this week", exact=True)
    ).to_be_visible()
    expect(page.get_by_role("heading", name="Latest podcast episode", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Latest article", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="The wiki, as a graph", exact=True)).to_be_visible()
    featured_course = page.locator("[data-featured-course]")
    expect(featured_course).to_have_count(1)
    expect(featured_course.get_by_role("heading", name="AI Dev Tools Zoomcamp")).to_be_visible()
    expect(featured_course.get_by_text("Starts August 31, 2026")).to_be_visible()
    expect(featured_course.get_by_role("link", name="View the syllabus")).to_have_attribute(
        "href",
        "/courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026",
    )
    expect(page.get_by_role("link", name="all courses")).to_have_attribute(
        "href",
        "/courses",
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    expect(page.get_by_role("link", name="People", exact=True)).to_have_count(0)
    _shot(page, f"home-{suffix}.png", full_page=True)

    for label, path, heading in (
        ("Events", "/events", EVENTS_HEADING),
        ("Courses", "/courses", "Learn data skills. For free. Together."),
        ("Blog", "/blog", "Latest Articles"),
        ("Podcast", "/podcast", PODCAST_HEADING),
        ("Wiki", "/wiki", "DataTalks.Club Podcast Wiki"),
        ("Books", "/books", "Book of the Week"),
    ):
        page.goto(origin)
        if viewport["width"] < 1024:
            page.get_by_role("button", name="Menu").click()
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
            # The three ways into the wiki stay one stacked list at every width.
            # The design 5a rebuild (issue #179) draws them as the system's
            # divided rows, which are a grid rather than a flex column, so the
            # contract is the stacking itself: three rows, each below the last.
            exploration = page.locator('nav[aria-label="Wiki exploration"] a')
            expect(exploration).to_have_count(3)
            offsets = [
                exploration.nth(index).evaluate("node => node.getBoundingClientRect().top")
                for index in range(3)
            ]
            assert offsets == sorted(offsets), offsets
            assert len(set(offsets)) == 3, offsets


@pytest.mark.core
@pytest.mark.parametrize(
    "viewport",
    [{"width": 1280, "height": 800}, {"width": 390, "height": 844}],
)
def test_docs_and_faq_root_trailing_slash_browser_contract(
    page: Page,
    live_server,
    viewport: ViewportSize,
) -> None:
    page.set_viewport_size(viewport)
    origin = live_server.url
    query = "utm_source=oncall%2Btest&x=a%2Fb&blank="

    for final_path, alias_path, heading in (
        ("/docs/", "/docs", "DataTalks.Club Zoomcamps Notes and Resources"),
        ("/faq/", "/faq", "Frequently Asked Questions"),
    ):
        alias = page.request.get(f"{origin}{alias_path}?{query}", max_redirects=0)
        assert alias.status == 301
        assert alias.headers["location"] == f"{final_path}?{query}"
        head = page.request.head(f"{origin}{alias_path}?{query}", max_redirects=0)
        assert head.status == 301
        assert head.headers["location"] == f"{final_path}?{query}"

        response = page.goto(f"{origin}{final_path}?{query}", wait_until="networkidle")
        assert response is not None and response.status == 200
        assert "location" not in response.headers
        expect(page).to_have_url(f"{origin}{final_path}?{query}")
        expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
        expect(page.locator('link[rel="canonical"]')).to_have_attribute(
            "href", f"https://datatalks.club{final_path}"
        )
        expect(page.locator('meta[property="og:url"]')).to_have_attribute(
            "content", f"https://datatalks.club{final_path}"
        )
        expect(page.locator(f'a[href="{final_path}"]')).to_have_count(1)
        expect(page.locator(f'a[href="{alias_path}"]')).to_have_count(0)

        redirected = page.goto(f"{origin}{alias_path}?{query}", wait_until="networkidle")
        assert redirected is not None and redirected.status == 200
        expect(page).to_have_url(f"{origin}{final_path}?{query}")

    docs_detail = page.goto(
        f"{origin}/docs/courses/ai-dev-tools-zoomcamp/getting-started/",
        wait_until="networkidle",
    )
    assert docs_detail is not None and docs_detail.status == 200
    assert page.locator('a[href="/docs/"]').count() >= 1
    docs_asset_path = docs_projection()["assets"][0]["public_path"]
    docs_asset = page.request.get(f"{origin}{docs_asset_path}")
    assert docs_asset.status == 200

    faq_detail = page.goto(f"{origin}/faq/ai-dev-tools-zoomcamp.html", wait_until="networkidle")
    assert faq_detail is not None and faq_detail.status == 200
    assert page.locator('a[href="/faq/"]').count() >= 1
    faq_courses = page.request.get(f"{origin}/faq/json/courses.json")
    assert faq_courses.status == 200
    faq_course = page.request.get(f"{origin}/faq/json/ai-dev-tools-zoomcamp.json")
    assert faq_course.status == 200


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
    featured_event_path = _featured_event_path()
    page.goto(f"{origin}/events")
    page.get_by_role("link", name=FEATURED_EVENT_TITLE, exact=True).click()
    expect(page).to_have_url(f"{origin}{featured_event_path}")
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href",
        f"https://datatalks.club{featured_event_path}",
    )
    expect(page.locator('section[aria-label="Event description"]')).to_have_count(1)
    expect(
        page.get_by_text("The new cohort of AI Dev Tools Zoomcamp 2026 starts", exact=False)
    ).to_be_visible()
    expect(page.get_by_role("heading", name="Event links", exact=True)).to_have_count(0)
    expect(page.locator('a[href*="luma.com"], a[href*="lu.ma"]')).to_have_count(0)
    expect(page.locator(f'a[href="{featured_event_path}/register"]')).to_have_count(0)
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
        expect(page.get_by_role("heading", name=PODCAST_HEADING, exact=True)).to_be_visible()
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
    projection = public_projection()
    podcast_target = next(
        item["public_path"]
        for item in projection["podcasts"]
        if item["slug"] == "practical-llm-engineering-and-rag"
    )
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
        "/podcast/practical-llm-engineering-and-rag": podcast_target,
        "/podcast/practical-llm-engineering-and-rag/": podcast_target,
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
        if source in {"/podcast.html", "/podcast/"}:
            query = "season=12"
        elif source in {"/events.html", "/events/"}:
            query = "filter=past"
        else:
            query = "source=browser"
        response = page.request.get(
            f"{origin}{source}?{query}",
            max_redirects=0,
        )
        expected_target = (
            "/events/past"
            if source in {"/events.html", "/events/"} and query == "filter=past"
            else target
        )
        expected_query = "" if expected_target == "/events/past" else query
        assert response.status == 301
        expected_location = (
            f"{expected_target}?{expected_query}" if expected_query else expected_target
        )
        assert response.headers["location"] == expected_location
        navigation = page.goto(f"{origin}{source}?{query}")
        assert navigation is not None and navigation.status == 200
        expected_url = (
            f"{origin}{expected_target}?{expected_query}"
            if expected_query
            else f"{origin}{expected_target}"
        )
        expect(page).to_have_url(expected_url)
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
            ("/", HOME_HEADING),
            ("/events", EVENTS_HEADING),
            (_featured_event_path(), FEATURED_EVENT_TITLE),
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
    podcast_path = next(
        record["public_path"]
        for record in public_projection()["podcasts"]
        if record["slug"] == "practical-llm-engineering-and-rag"
    )
    for path in (
        "/blog/sponsor-datatalks-club.html",
        podcast_path,
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
    menu = page.get_by_role("button", name="Menu")
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
