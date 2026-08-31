import pytest
from playwright.sync_api import Page, ViewportSize, expect

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize(
    "viewport",
    [{"width": 1280, "height": 800}, {"width": 390, "height": 844}],
)
def test_wiki_detail_has_one_context_label_and_an_accessible_title(
    page: Page,
    live_server,
    viewport: ViewportSize,
) -> None:
    page.set_viewport_size(viewport)
    response = page.goto(f"{live_server.url}/wiki/a-a-testing", wait_until="networkidle")

    assert response is not None and response.status == 200
    expect(page.locator("main#main-content")).to_have_count(1)
    expect(page.locator(".masthead")).to_have_count(1)
    expect(page.locator("footer")).to_have_count(1)

    heading = page.get_by_role("heading", name="A/A Testing", exact=True)
    expect(heading).to_have_count(1)
    expect(heading).to_be_visible()
    expect(page.locator("main h1")).to_have_count(1)
    expect(page.locator("section.wiki-hero")).to_have_attribute(
        "aria-labelledby", "wiki-page-heading"
    )

    breadcrumb = page.get_by_role("navigation", name="Breadcrumb")
    expect(breadcrumb).to_have_count(1)
    expect(breadcrumb.get_by_role("link", name="Wiki", exact=True)).to_have_count(1)
    expect(breadcrumb.locator('li[aria-current="page"]')).to_have_text("A/A Testing")
    expect(page.locator(".wiki-hero .wiki-shell > p.mono-label")).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
