import pytest
from playwright.sync_api import Page, expect


@pytest.mark.core
@pytest.mark.parametrize(
    "viewport", [{"width": 1280, "height": 720}, {"width": 390, "height": 844}]
)
def test_visitor_sees_rendered_home_page(page: Page, live_server, viewport: dict[str, int]) -> None:
    page.set_viewport_size(viewport)
    response = page.goto(live_server.url)

    assert response is not None
    assert response.status == 200
    expect(page).to_have_title("Welcome to DataTalks.Club")
    expect(page.get_by_role("heading", name="The place to talk about data")).to_be_visible()
    expect(
        page.get_by_text("Global online community of data science professionals")
    ).to_be_visible()
    expect(page.get_by_role("heading", name="Events", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Courses and cohorts")).to_be_visible()
    expect(page.get_by_role("link", name="Courses").first).to_have_attribute("href", "/courses/")
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", "https://datatalks.club/"
    )
    expect(page.locator('script[src*="googletagmanager"]')).to_have_count(0)
    expect(page.locator("body")).not_to_contain_text("Traceback")

    course_response = page.goto(f"{live_server.url}/courses/")

    assert course_response is not None
    assert course_response.status == 200
    expect(
        page.get_by_role("heading", name="Learn data skills. For free. Together.")
    ).to_be_visible()
    expect(page.get_by_text("The place to talk about data")).to_have_count(0)
    expect(page.get_by_role("link", name="AI Dev Tools Zoomcamp")).to_be_visible()
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", "https://datatalks.club/courses/"
    )


@pytest.mark.core
@pytest.mark.parametrize(
    "viewport", [{"width": 1440, "height": 900}, {"width": 390, "height": 844}]
)
def test_review_skeleton_is_navigable_on_one_origin(
    page: Page,
    live_server,
    viewport: dict[str, int],
) -> None:
    page.set_viewport_size(viewport)
    origin = live_server.url
    page.goto(origin)

    for label, path, heading in (
        ("Events", "/events.html", "Events"),
        ("Courses", "/courses/", "Learn data skills. For free. Together."),
        ("Articles", "/articles.html", "Articles"),
        ("Podcast", "/podcast.html", "DataTalks.Club Podcast"),
        ("Wiki", "/podwiki/", "DataTalks.Club Podcast Wiki"),
        ("Books", "/books.html", "Books"),
        ("Docs", "/docs/", "Documentation"),
        ("FAQ", "/faq/", "Frequently Asked Questions"),
        ("Slack", "/slack.html", "Join our Slack"),
    ):
        page.goto(origin)
        if viewport["width"] < 1024:
            page.get_by_role("button", name="Explore").click()
        page.locator("#site-navigation-links").get_by_role("link", name=label).click()
        expect(page).to_have_url(f"{origin}{path}")
        expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.core
def test_content_relationships_course_preview_and_mobile_menu(page: Page, live_server) -> None:
    origin = live_server.url
    page.set_viewport_size({"width": 390, "height": 844})

    page.goto(f"{origin}/events.html")
    page.get_by_role("link", name="Aleksandr Kim").click()
    expect(page).to_have_url(f"{origin}/people/aleksandrkim.html")
    page.get_by_role(
        "link", name="How to Build AI That Actually Ships in Production podcast"
    ).click()
    expect(page).to_have_url(
        f"{origin}/podcast/s24e06-how-to-build-ai-that-actually-ships-in-production.html"
    )
    expect(page.get_by_role("heading", name="Transcript")).to_be_visible()

    page.goto(f"{origin}/courses/")
    page.get_by_role("link", name="AI Dev Tools Zoomcamp").click()
    expect(page).to_have_url(f"{origin}/courses/ai-dev-tools-zoomcamp/")
    page.get_by_role("link", name="AI Dev Tools Zoomcamp 2026").click()
    expect(page).to_have_url(f"{origin}/courses/ai-dev-tools-zoomcamp/cohorts/ai-dev-tools-2026/")
    page.get_by_role("link", name="Registration preview").click()
    expect(
        page.get_by_role("heading", name="Registration is not enabled in this review build")
    ).to_be_visible()
    expect(page.locator("form")).to_have_count(0)
    page.get_by_role("link", name="Back to the 2026 cohort").click()

    page.goto(origin)
    menu = page.get_by_role("button", name="Explore")
    menu.focus()
    page.keyboard.press("Enter")
    expect(menu).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("Escape")
    expect(menu).to_have_attribute("aria-expanded", "false")
    expect(menu).to_be_focused()

    dark_mode = page.get_by_role("button", name="Toggle dark mode")
    dark_mode.click()
    assert page.locator("body").evaluate("body => body.classList.contains('dark-mode')")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.core
def test_deep_links_and_podwiki_empty_search(page: Page, live_server) -> None:
    origin = live_server.url
    for path, heading in (
        ("/blog/ai-dev-tools-zoomcamp.html", "AI Dev Tools Zoomcamp 2026"),
        ("/books/20250922-how-software-fails.html", "How Software Fails"),
        (
            "/docs/courses/ai-dev-tools-zoomcamp/getting-started/",
            "Getting Started",
        ),
        ("/podwiki/wiki/ai-coding-tools/", "AI Coding Tools"),
    ):
        response = page.goto(f"{origin}{path}")
        assert response is not None and response.status == 200
        expect(page.get_by_role("heading", name=heading)).to_be_visible()

    page.goto(f"{origin}/faq/ai-dev-tools-zoomcamp.html#4487db3924")
    question = page.get_by_role("heading", name="How do I access the course modules and materials?")
    expect(question).to_be_in_viewport()

    page.goto(f"{origin}/podwiki/search/?q=no-such-review-topic")
    expect(
        page.get_by_role("heading", name="No matches for “no-such-review-topic”")
    ).to_be_visible()
    expect(page.get_by_role("link", name="browse the wiki topics")).to_have_attribute(
        "href", "/podwiki/"
    )


@pytest.mark.core
def test_anonymous_staff_member_reaches_safe_sign_in_flow(page: Page, live_server) -> None:
    response = page.goto(f"{live_server.url}/studio/")

    assert response is not None
    assert response.status == 200
    expect(page).to_have_url(f"{live_server.url}/accounts/login/?next=%2Fstudio%2F")
    expect(page.get_by_role("link", name="DataTalks.Club")).to_be_visible()
    expect(page.get_by_role("heading", name="Sign In")).to_be_visible()
    expect(page.get_by_text("Choose your preferred login method")).to_be_visible()
    expect(page.get_by_text("No login providers configured")).to_be_visible()
