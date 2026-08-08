from __future__ import annotations

import pytest
from django.test import override_settings
from playwright.sync_api import Browser, Page, expect

pytestmark = [
    pytest.mark.core,
    pytest.mark.usefixtures("_compatibility_fixture_settings"),
]


@pytest.fixture
def _compatibility_fixture_settings():
    with override_settings(
        ROOT_URLCONF="compatibility.tests.fixture_urls",
        APPEND_SLASH=False,
    ):
        yield


@pytest.mark.parametrize(
    "viewport", [{"width": 1280, "height": 720}, {"width": 390, "height": 844}]
)
def test_fixture_is_server_rendered_with_exact_seo_asset_and_fragment(
    page: Page,
    live_server,
    viewport: dict[str, int],
) -> None:
    page.set_viewport_size(viewport)
    response = page.goto(f"{live_server.url}/fixture/#Caf%C3%A9")

    assert response is not None
    assert response.status == 200
    expect(page).to_have_url(f"{live_server.url}/fixture/#Caf%C3%A9")
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", "https://datatalks.club/fixture/"
    )
    expect(page.get_by_role("heading", name="Compatibility fixture")).to_be_visible()
    expect(page.get_by_text("Meaningful server-rendered body")).to_be_visible()
    expect(page.locator("#Café")).to_be_visible()
    image = page.get_by_alt_text("Fixture logo")
    expect(image).to_have_js_property("complete", True)
    assert image.evaluate("element => element.naturalWidth") > 0
    image_response = page.request.get(f"{live_server.url}/assets/logo.bin")
    assert image_response.status == 200
    assert image_response.headers["content-type"] == "image/png"


@pytest.mark.parametrize(
    "viewport", [{"width": 1280, "height": 720}, {"width": 390, "height": 844}]
)
def test_fixture_keeps_meaningful_content_when_javascript_is_disabled(
    browser: Browser,
    live_server,
    viewport: dict[str, int],
) -> None:
    context = browser.new_context(java_script_enabled=False, viewport=viewport)
    try:
        page = context.new_page()
        response = page.goto(f"{live_server.url}/fixture/")

        assert response is not None
        assert response.status == 200
        expect(page.get_by_role("heading", name="Compatibility fixture")).to_be_visible()
        expect(page.get_by_text("Meaningful server-rendered body")).to_be_visible()
    finally:
        context.close()


def test_fixture_redirect_gone_and_unknown_are_direct(page: Page, live_server) -> None:
    redirect = page.request.get(f"{live_server.url}/legacy", max_redirects=0)
    assert redirect.status == 301
    assert redirect.headers["location"] == "/fixture/"

    target = page.goto(f"{live_server.url}/legacy")
    assert target is not None
    assert target.status == 200
    expect(page).to_have_url(f"{live_server.url}/fixture/")

    gone = page.goto(f"{live_server.url}/gone")
    assert gone is not None
    assert gone.status == 410
    expect(page.locator("body")).to_contain_text("Gone")

    missing = page.goto(f"{live_server.url}/does-not-exist")
    assert missing is not None
    assert missing.status == 404
    expect(page).to_have_url(f"{live_server.url}/does-not-exist")
    expect(page.locator("body")).not_to_contain_text("Compatibility fixture")


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("/docs/Exact/", 200),
        ("/docs/exact/", 404),
        ("/docs/Exact", 404),
        ("/echo/Caf%C3%A9?x=1&x=&q=A+B&q=A%20B", 200),
        ("/echo/e%CC%81?encoded=%2f&other=%2F", 200),
    ],
)
def test_fixture_exercises_exact_case_unicode_query_and_slash_contracts(
    page: Page,
    live_server,
    path: str,
    status: int,
) -> None:
    response = page.goto(f"{live_server.url}{path}")

    assert response is not None
    assert response.status == status


def test_diagnostic_pages_expose_staging_canonical_and_js_only_shell(
    browser: Browser,
    page: Page,
    live_server,
) -> None:
    staging = page.goto(f"{live_server.url}/staging/")
    assert staging is not None
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", "https://web.dtcdev.click/staging/"
    )

    context = browser.new_context(java_script_enabled=False)
    try:
        no_js = context.new_page()
        response = no_js.goto(f"{live_server.url}/js-only/")
        assert response is not None
        assert response.status == 200
        expect(no_js.locator("main")).to_be_empty()
    finally:
        context.close()
