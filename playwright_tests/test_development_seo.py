from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import pytest
from django.conf import settings
from django.test import Client, override_settings
from playwright.sync_api import Page, expect

from core.preview import SENSITIVE_PREVIEW_QUERY_KEYS

pytestmark = [
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("_development_seo_fixture_settings"),
]

ROBOTS_VALUE = "noindex, nofollow"
VIEWPORTS = ({"width": 1280, "height": 720}, {"width": 390, "height": 844})


@pytest.fixture
def _development_seo_fixture_settings():
    with override_settings(
        ROOT_URLCONF="core.tests.seo_fixture_urls",
        APPEND_SLASH=False,
        ALLOWED_HOSTS=["localhost", "127.0.0.1", "web.dtcdev.click"],
        NOINDEX=True,
    ):
        yield


def private_directives(headers: dict[str, str]) -> set[str]:
    return {
        item.strip().lower() for item in headers.get("cache-control", "").split(",") if item.strip()
    }


def assert_private(headers: dict[str, str]) -> None:
    directives = private_directives(headers)
    assert {"private", "no-store"}.issubset(directives)
    assert "public" not in directives
    assert not any(item.startswith("s-maxage=") and item != "s-maxage=0" for item in directives)


def assert_no_analytics(page: Page, request_urls: list[str]) -> None:
    denied_hosts = ("googletagmanager.com", "google-analytics.com")
    observed_hosts = {urlparse(url).hostname or "" for url in request_urls}
    assert not any(
        host == denied or host.endswith(f".{denied}")
        for host in observed_hosts
        for denied in denied_hosts
    )
    html = page.content()
    assert "GTM-K365CGB9" not in html
    assert not any(host in html.casefold() for host in denied_hosts)
    names = {cookie["name"] for cookie in page.context.cookies()}
    names.update(page.evaluate("[...Object.keys(localStorage), ...Object.keys(sessionStorage)]"))
    assert not any(name in {"_ga", "_gid", "_gat"} or name.startswith("_gcl_") for name in names)
    assert not page.context.service_workers


def screenshot_path(name: str, viewport: dict[str, int]) -> Path:
    directory = Path(".tmp/screenshots/issue-36")
    directory.mkdir(parents=True, exist_ok=True)
    device = "desktop" if viewport["width"] > 600 else "mobile"
    return directory / f"{name}-{device}.png"


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_explicit_and_unmapped_canonical_browser_contract(
    page: Page,
    live_server,
    viewport: dict[str, int],
) -> None:
    page.set_viewport_size(viewport)
    request_urls: list[str] = []
    page.on("request", lambda request: request_urls.append(request.url))

    mapped = page.goto(f"{live_server.url}/Fixture/Exact.html?source=browser")
    assert mapped is not None
    assert mapped.status == 200
    assert mapped.headers["x-robots-tag"] == ROBOTS_VALUE
    expect(page.get_by_role("heading", name="SEO policy fixture")).to_be_visible()
    expect(page.locator('link[rel="canonical"]')).to_have_count(1)
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", "https://datatalks.club/Fixture/Exact.html"
    )
    page.screenshot(path=screenshot_path("explicit-canonical", viewport), full_page=True)

    unmapped = page.goto(f"{live_server.url}/Fixture/Unmapped.html")
    assert unmapped is not None
    assert unmapped.status == 200
    assert unmapped.headers["x-robots-tag"] == ROBOTS_VALUE
    expect(page.locator('link[rel="canonical"]')).to_have_count(0)
    expect(page.get_by_text("Visible server-rendered policy content.")).to_be_visible()
    page.screenshot(path=screenshot_path("unmapped", viewport), full_page=True)
    assert_no_analytics(page, request_urls)

    host_response = page.request.get(
        f"{live_server.url}/Fixture/Unmapped.html",
        headers={"Host": "web.dtcdev.click"},
    )
    assert host_response.status == 200
    assert host_response.headers["x-robots-tag"] == ROBOTS_VALUE


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_preview_uses_safe_login_and_staff_session_without_token(
    page: Page,
    live_server,
    django_user_model,
    viewport: dict[str, int],
) -> None:
    page.set_viewport_size(viewport)
    request_urls: list[str] = []
    page.on("request", lambda request: request_urls.append(request.url))
    preview_url = f"{live_server.url}/private/preview/"

    initial = page.request.get(f"{preview_url}?benign=1", max_redirects=0)
    assert initial.status == 302
    assert initial.headers["location"] == "/accounts/login/?next=%2Fprivate%2Fpreview%2F"
    assert initial.headers["x-robots-tag"] == ROBOTS_VALUE
    assert_private(initial.headers)

    login = page.goto(f"{preview_url}?benign=1")
    assert login is not None
    assert login.status == 200
    expect(page).to_have_url(f"{live_server.url}/accounts/login/?next=%2Fprivate%2Fpreview%2F")
    expect(page.get_by_role("heading", name="Sign In")).to_be_visible()
    expect(page.locator('link[rel="canonical"]')).to_have_count(0)
    page.screenshot(path=screenshot_path("preview-sign-in", viewport), full_page=True)

    staff = django_user_model.objects.create_user(
        username="browser-staff@example.test",
        email="browser-staff@example.test",
        is_staff=True,
        is_active=True,
    )
    session_client = Client()
    session_client.force_login(staff)
    session_cookie = session_client.cookies[settings.SESSION_COOKIE_NAME]
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": session_cookie.value,
                "url": live_server.url,
            }
        ]
    )
    preview = page.goto(f"{preview_url}?benign=1")
    assert preview is not None
    assert preview.status == 200
    assert preview.headers["x-robots-tag"] == ROBOTS_VALUE
    assert_private(preview.headers)
    expect(page.get_by_role("heading", name="Private staff preview")).to_be_visible()
    expect(page.locator('link[rel="canonical"]')).to_have_count(0)
    assert "token" not in page.url.casefold()
    page.screenshot(path=screenshot_path("staff-preview", viewport), full_page=True)
    assert_no_analytics(page, request_urls)


def test_preview_token_and_response_matrix_are_safe(page: Page, live_server) -> None:
    request_urls: list[str] = []
    page.on("request", lambda request: request_urls.append(request.url))
    canary = "browser-preview-canary-36"
    key = next(iter(sorted(SENSITIVE_PREVIEW_QUERY_KEYS)))
    rejected = page.goto(f"{live_server.url}/private/preview/?{key.upper()}={canary}")
    assert rejected is not None
    assert rejected.status == 400
    assert rejected.headers["x-robots-tag"] == ROBOTS_VALUE
    assert_private(rejected.headers)
    assert canary not in page.content()
    assert not any(canary in value for value in rejected.headers.values())
    assert "location" not in rejected.headers

    studio = page.request.get(f"{live_server.url}/studio/", max_redirects=0)
    assert studio.status == 302
    assert studio.headers["x-robots-tag"] == ROBOTS_VALUE
    assert_private(studio.headers)
    assert studio.headers["location"] == "/accounts/login/?next=%2Fstudio%2F"
    login = page.goto(f"{live_server.url}/studio/")
    assert login is not None
    assert login.status == 200
    expect(page.get_by_role("heading", name="Sign In")).to_be_visible()

    admin = page.request.get(f"{live_server.url}/api/v1/admin/health", max_redirects=0)
    assert admin.status == 401
    assert admin.headers["x-robots-tag"] == ROBOTS_VALUE
    assert_private(admin.headers)

    missing = page.goto(f"{live_server.url}/deliberate-missing")
    assert missing is not None
    assert missing.status == 404
    assert missing.headers["x-robots-tag"] == ROBOTS_VALUE
    expect(page.locator("body")).not_to_contain_text("Traceback")
    expect(page.locator('link[rel="canonical"]')).to_have_count(0)

    robots = page.request.get(f"{live_server.url}/robots.txt", max_redirects=0)
    assert robots.status == 200
    assert robots.headers["content-type"] == "text/plain; charset=utf-8"
    assert robots.headers["x-robots-tag"] == ROBOTS_VALUE
    assert robots.body() == b"User-agent: *\nDisallow: /\n"

    sitemap = page.request.get(f"{live_server.url}/sitemap.xml", max_redirects=0)
    assert sitemap.status == 200
    assert sitemap.headers["content-type"] == "application/xml; charset=utf-8"
    assert sitemap.headers["x-robots-tag"] == ROBOTS_VALUE
    sitemap_body = sitemap.body().decode()
    assert "<sitemapindex" in sitemap_body
    assert "https://datatalks.club/sitemaps/events.xml" in sitemap_body
    assert "<?xml-stylesheet" not in sitemap_body

    static = page.request.get(f"{live_server.url}/fixture/asset.css", max_redirects=0)
    assert static.status == 200
    assert static.headers["x-robots-tag"] == ROBOTS_VALUE
    assert static.headers["content-type"].startswith("text/css")
    assert_no_analytics(page, request_urls)
