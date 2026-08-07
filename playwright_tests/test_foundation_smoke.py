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
    expect(page.get_by_role("heading", name="Learn, share, and grow together.")).to_be_visible()
    expect(page.locator("body")).not_to_contain_text("Traceback")


@pytest.mark.core
def test_anonymous_staff_member_reaches_safe_sign_in_flow(page: Page, live_server) -> None:
    response = page.goto(f"{live_server.url}/studio/")

    assert response is not None
    assert response.status == 200
    expect(page).to_have_url(f"{live_server.url}/accounts/login/?next=%2Fstudio%2F")
    expect(page.get_by_role("link", name="DataTalks.Club")).to_be_visible()
    expect(page.get_by_role("heading", name="Sign in to Studio")).to_be_visible()
    expect(page.get_by_label("Email")).to_be_visible()
