from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import pytest
from django.test import Client, override_settings
from playwright.sync_api import Page, Response, ViewportSize, expect

from content.sitemap_contract import EXPECTED_SITEMAP_LOCATIONS, validate_sitemap_index
from core.views import PRODUCTION_ROBOTS_BODY

pytestmark = [
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("_production_seo_fixture_settings"),
]

VIEWPORTS = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)
SCREENSHOTS = Path(".tmp/screenshots/issue-190")
UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE", "OPTIONS")
PRODUCTION_ROBOTS_BYTES = PRODUCTION_ROBOTS_BODY.encode()
FORBIDDEN_MARKERS = (b"/podwiki/", b"web.dtcdev.click")


@pytest.fixture
def _production_seo_fixture_settings():
    with override_settings(NOINDEX=False):
        yield


def cache_directives(headers: dict[str, str]) -> set[str]:
    return {
        item.strip().lower() for item in headers.get("cache-control", "").split(",") if item.strip()
    }


def assert_local_origin(origin: str) -> None:
    hostname = urlparse(origin).hostname
    assert hostname in {"127.0.0.1", "localhost"}


def assert_anonymous_production_robots(headers: dict[str, str], body: bytes | None) -> None:
    assert headers.get("content-type") == "text/plain; charset=utf-8"
    assert headers.get("cache-control") == "max-age=0, must-revalidate"
    assert headers.get("x-robots-tag") is None
    assert headers.get("location") is None
    if body is not None:
        assert body == PRODUCTION_ROBOTS_BYTES
        assert body.endswith(b"\n")
        assert body.count(b"\n") == PRODUCTION_ROBOTS_BODY.count("\n")
        assert not any(marker in body for marker in FORBIDDEN_MARKERS)


def assert_no_debug_or_redirect(
    page: Page, response: Response | None, *, origin: str, path: str
) -> None:
    assert response is not None
    assert response.status == 200
    assert response.url == f"{origin}{path}"
    assert page.url == f"{origin}{path}"
    assert response.headers.get("location") is None
    expect(page.locator("body")).not_to_contain_text("Traceback")
    expect(page.locator("body")).not_to_contain_text("DisallowedHost")


def screenshot_path(name: str, device: str) -> Path:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    return SCREENSHOTS / f"{name}-{device}.png"


def test_production_robots_and_sitemap_request_matrix(page: Page, live_server) -> None:
    origin = live_server.url
    assert_local_origin(origin)

    robots = page.request.get(f"{origin}/robots.txt", max_redirects=0)
    assert robots.status == 200
    assert_anonymous_production_robots(robots.headers, robots.body())

    head = page.request.head(f"{origin}/robots.txt", max_redirects=0)
    assert head.status == 200
    assert head.body() == b""
    assert_anonymous_production_robots(head.headers, body=None)

    sitemap = page.request.get(f"{origin}/sitemap.xml", max_redirects=0)
    assert sitemap.status == 200
    assert sitemap.headers.get("content-type") == "application/xml; charset=utf-8"
    assert sitemap.headers.get("x-robots-tag") is None
    assert sitemap.headers.get("location") is None
    assert validate_sitemap_index(sitemap.body()) == EXPECTED_SITEMAP_LOCATIONS
    assert len(EXPECTED_SITEMAP_LOCATIONS) == 10
    assert not any(marker in sitemap.body() for marker in FORBIDDEN_MARKERS)

    sitemap_head = page.request.head(f"{origin}/sitemap.xml", max_redirects=0)
    assert sitemap_head.status == 200
    assert sitemap_head.body() == b""
    assert sitemap_head.headers.get("content-type") == sitemap.headers.get("content-type")
    assert sitemap_head.headers.get("x-robots-tag") is None

    client = Client()
    for method in UNSAFE_METHODS:
        response = client.generic(method, "/robots.txt", data=b"opaque-input")
        assert response.status_code == 405
        assert response.headers["Allow"] == "GET, HEAD"
        directives = {
            item.strip().lower()
            for item in response.headers.get("Cache-Control", "").split(",")
            if item.strip()
        }
        assert "no-store" in directives
        assert "max-age=0" in directives
        assert "public" not in directives
        assert not any(item.startswith("s-maxage=") and item != "s-maxage=0" for item in directives)

    options = page.request.fetch(
        f"{origin}/robots.txt",
        method="OPTIONS",
        max_redirects=0,
    )
    assert options.status == 405
    assert options.headers.get("allow") == "GET, HEAD"
    assert "no-store" in cache_directives(options.headers)
    assert "max-age=0" in cache_directives(options.headers)
    assert "public" not in cache_directives(options.headers)


@pytest.mark.parametrize(("viewport", "device"), VIEWPORTS)
def test_production_robots_and_sitemap_browser_session(
    page: Page,
    live_server,
    viewport: ViewportSize,
    device: str,
) -> None:
    page.set_viewport_size(viewport)
    request_urls: list[str] = []
    page.on("request", lambda request: request_urls.append(request.url))
    origin = live_server.url
    assert_local_origin(origin)

    robots = page.goto(f"{origin}/robots.txt", wait_until="domcontentloaded")
    assert_no_debug_or_redirect(page, robots, origin=origin, path="/robots.txt")
    assert_anonymous_production_robots(robots.headers, robots.body())
    expect(page.locator("body")).to_contain_text("User-agent: *")
    expect(page.locator("body")).to_contain_text("Disallow: /admin/")
    expect(page.locator("body")).to_contain_text("Sitemap: https://datatalks.club/sitemap.xml")
    expect(page.locator("body")).to_contain_text(
        "Sitemap: https://datatalks.club/sitemaps/wiki.xml"
    )
    page.screenshot(path=screenshot_path("robots", device), full_page=True)

    sitemap = page.goto(f"{origin}/sitemap.xml", wait_until="domcontentloaded")
    assert_no_debug_or_redirect(page, sitemap, origin=origin, path="/sitemap.xml")
    assert sitemap.headers.get("content-type") == "application/xml; charset=utf-8"
    assert sitemap.headers.get("x-robots-tag") is None
    assert validate_sitemap_index(sitemap.body()) == EXPECTED_SITEMAP_LOCATIONS
    expect(page.locator("body")).not_to_contain_text("Traceback")
    page.screenshot(path=screenshot_path("sitemap", device), full_page=True)

    observed_hosts = {urlparse(url).hostname or "" for url in request_urls}
    assert observed_hosts <= {"127.0.0.1", "localhost", ""}
    assert not any("datatalks.club" in url for url in request_urls)
    assert not any("web.dtcdev.click" in url for url in request_urls)
