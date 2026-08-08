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
        page.get_by_role(
            "heading",
            name=(
                "Global online community of data science professionals, ML engineers, "
                "and AI practitioners"
            ),
        )
    ).to_be_visible()
    expect(page.get_by_role("link", name="Courses").first).to_have_attribute("href", "/courses/")
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", "https://datatalks.club/"
    )
    expect(page.locator('script[src*="googletagmanager"]')).to_have_count(0)
    expect(page.get_by_text("Learn data skills. For free. Together.")).to_have_count(0)
    expect(page.locator("body")).not_to_contain_text("Traceback")

    course_response = page.goto(f"{live_server.url}/courses/")

    assert course_response is not None
    assert course_response.status == 200
    expect(
        page.get_by_role("heading", name="Learn data skills. For free. Together.")
    ).to_be_visible()
    expect(page.get_by_text("The place to talk about data")).to_have_count(0)
    expect(page.locator('link[rel="canonical"]')).to_have_count(0)


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
