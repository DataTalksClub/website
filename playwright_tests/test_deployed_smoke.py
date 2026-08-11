from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, expect

from content.sitemap_contract import EXPECTED_SITEMAP_LOCATIONS, validate_sitemap_index
from deploy.contracts import validate_image_digest, validate_source_sha, validate_version
from deploy.smoke import DEVELOPMENT_ORIGIN, ROBOTS_VALUE

pytestmark = [pytest.mark.core, pytest.mark.remote_readonly]


@pytest.fixture
def deployed_config() -> tuple[str, str, str, str, Path]:
    origin = os.getenv("DTC_TEST_BASE_URL")
    version = os.getenv("DTC_EXPECTED_VERSION")
    source_sha = os.getenv("DTC_EXPECTED_SOURCE_SHA")
    image_digest = os.getenv("DTC_EXPECTED_IMAGE_DIGEST")
    screenshot_directory = os.getenv("DTC_SCREENSHOT_DIR")
    if (
        not origin
        and not version
        and not source_sha
        and not image_digest
        and not screenshot_directory
    ):
        pytest.skip("deployed read-only smoke is enabled only by explicit safe configuration")
    assert origin == DEVELOPMENT_ORIGIN
    assert source_sha is not None
    validate_source_sha(source_sha)
    assert version is not None
    validate_version(version, source_sha)
    assert image_digest is not None
    validate_image_digest(image_digest)
    assert screenshot_directory is not None
    path = Path(screenshot_directory)
    assert path.parts[:1] == (".tmp",)
    path.mkdir(parents=True, exist_ok=True)
    return origin, version, source_sha, image_digest, path


def assert_private_no_store(headers: dict[str, str]) -> None:
    directives = {item.strip().lower() for item in headers.get("cache-control", "").split(",")}
    assert {"private", "no-store"}.issubset(directives)
    assert "public" not in directives
    assert not any(item.startswith("s-maxage=") and item != "s-maxage=0" for item in directives)


def assert_no_analytics(page: Page, request_urls: list[str]) -> None:
    analytics_hosts = ("googletagmanager.com", "google-analytics.com")
    assert not any(
        any(
            host == urlparse(url).hostname or (urlparse(url).hostname or "").endswith(f".{host}")
            for host in analytics_hosts
        )
        for url in request_urls
    )
    cookie_names = {cookie["name"] for cookie in page.context.cookies()}
    assert not any(
        name in {"_ga", "_gid", "_gat"} or name.startswith("_gcl_") for name in cookie_names
    )
    storage = page.evaluate("[...Object.keys(localStorage), ...Object.keys(sessionStorage)]")
    assert not any(key in {"_ga", "_gid", "_gat"} or key.startswith("_gcl_") for key in storage)
    assert not page.context.service_workers


@pytest.mark.parametrize(
    "viewport", [{"width": 1280, "height": 720}, {"width": 390, "height": 844}]
)
def test_deployed_public_and_studio_html_are_exact_and_read_only(
    page: Page,
    deployed_config: tuple[str, str, str, str, Path],
    viewport: dict[str, int],
) -> None:
    origin, version, _source_sha, _image_digest, screenshot_directory = deployed_config
    page.set_viewport_size(viewport)
    request_urls: list[str] = []
    page.on("request", lambda request: request_urls.append(request.url))

    home = page.goto(origin, wait_until="networkidle")
    assert home is not None
    assert home.status == 200
    assert home.url == f"{origin}/"
    assert home.headers["x-robots-tag"] == ROBOTS_VALUE
    expect(page).to_have_title("Welcome to DataTalks.Club")
    expect(page.get_by_role("heading", name="The place to talk about data")).to_be_visible()
    expect(page.get_by_text("Learn data skills. For free. Together.")).to_have_count(0)
    expect(page.get_by_text(f"Version {version}", exact=False)).to_be_visible()
    expect(page.get_by_text("Learn data skills. For free. Together.")).to_have_count(0)
    expect(page.locator('link[rel="canonical"]')).to_have_count(1)
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", "https://datatalks.club/"
    )
    expect(page.locator("body")).not_to_contain_text("Traceback")
    expect(page.locator("body")).not_to_contain_text("Page not found")
    assert page.locator('link[rel="stylesheet"]').count() > 0
    dimensions = f"{viewport['width']}x{viewport['height']}"
    page.screenshot(path=screenshot_directory / f"home-{dimensions}.png", full_page=True)

    mapped = page.goto(f"{origin}/unified/", wait_until="networkidle")
    assert mapped is not None
    assert mapped.status == 200
    assert mapped.headers["x-robots-tag"] == ROBOTS_VALUE
    expect(page.locator('link[rel="canonical"]')).to_have_count(1)
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", "https://datatalks.club/"
    )
    expect(page.get_by_role("heading", name="The place to talk about data")).to_be_visible()

    courses = page.goto(f"{origin}/courses", wait_until="networkidle")
    assert courses is not None
    assert courses.status == 200
    assert courses.headers["x-robots-tag"] == ROBOTS_VALUE
    expect(
        page.get_by_role("heading", name="Learn data skills. For free. Together.", exact=True)
    ).to_be_visible()
    expect(page.locator("main .home-hero")).to_have_count(1)
    expect(page.locator("main #courses")).to_have_count(1)
    expect(page.get_by_text("Start now", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Active courses", exact=True)).to_be_visible()
    expect(page.locator("#course-families-heading")).to_have_count(0)
    expect(page.get_by_text("No active cohort coursework right now.", exact=True)).to_have_count(0)
    expect(page.get_by_text("The place to talk about data")).to_have_count(0)
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", "https://datatalks.club/courses"
    )
    expect(
        page.get_by_role("link", name="Data Engineering Zoomcamp 2026", exact=True)
    ).to_be_visible()
    expect(
        page.get_by_role("link", name="Data Engineering Zoomcamp 2026", exact=True).locator(
            "xpath=ancestor::article[@role='link']"
        )
    ).to_have_count(1)
    expect(page.get_by_text(f"Version {version}", exact=False)).to_be_visible()
    expect(page.locator("body")).not_to_contain_text("Traceback")
    expect(page.locator("body")).not_to_contain_text("Page not found")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    stylesheet_urls = page.locator('link[rel="stylesheet"]').evaluate_all(
        "nodes => nodes.map(node => node.href)"
    )
    assert stylesheet_urls
    assert all(url.startswith(f"{origin}/static/") for url in stylesheet_urls)
    page.screenshot(path=screenshot_directory / f"courses-{dimensions}.png", full_page=True)

    initial = page.request.get(f"{origin}/studio/", max_redirects=0)
    assert initial.status in {301, 302, 303, 307, 308}
    assert initial.headers["x-robots-tag"] == ROBOTS_VALUE
    assert_private_no_store(initial.headers)
    location = urlparse(initial.headers["location"])
    assert location.path == "/accounts/login/"
    assert location.query == "next=%2Fstudio%2F"

    login = page.goto(f"{origin}/studio/", wait_until="networkidle")
    assert login is not None
    assert login.status == 200
    assert page.url == f"{origin}/accounts/login/?next=%2Fstudio%2F"
    assert login.headers["x-robots-tag"] == ROBOTS_VALUE
    assert_private_no_store(login.headers)
    expect(page.get_by_role("heading", name="Sign In")).to_be_visible()
    expect(page.locator('link[rel="canonical"]')).to_have_count(0)
    page.screenshot(path=screenshot_directory / f"studio-sign-in-{dimensions}.png", full_page=True)

    missing = page.goto(
        f"{origin}/__dtc_deployed_smoke_missing__",
        wait_until="networkidle",
    )
    assert missing is not None
    assert missing.status == 404
    assert missing.headers["x-robots-tag"] == ROBOTS_VALUE
    expect(page.locator("body")).not_to_contain_text("Traceback")
    expect(page.locator("body")).not_to_contain_text("Technical 404")
    expect(page.locator("body")).not_to_contain_text("DEBUG=True")
    expect(page.locator('link[rel="canonical"]')).to_have_count(0)
    page.screenshot(path=screenshot_directory / f"not-found-{dimensions}.png", full_page=True)
    assert_no_analytics(page, request_urls)


def test_deployed_health_and_anonymous_admin_api_contracts(
    page: Page,
    deployed_config: tuple[str, str, str, str, Path],
) -> None:
    origin, version, source_sha, image_digest, _screenshot_directory = deployed_config
    expected_identity = {
        "version": version,
        "source_sha": source_sha,
        "image_digest": image_digest,
    }

    live = page.request.get(f"{origin}/health/live", max_redirects=0)
    assert live.status == 200
    assert live.headers["x-robots-tag"] == ROBOTS_VALUE
    assert live.json() == {"status": "ok", **expected_identity}

    ready = page.request.get(f"{origin}/health/ready", max_redirects=0)
    assert ready.status == 200
    assert ready.headers["x-robots-tag"] == ROBOTS_VALUE
    ready_payload = ready.json()
    assert ready_payload["status"] == "ready"
    assert {name: ready_payload[name] for name in expected_identity} == expected_identity
    assert {
        name: ready_payload["checks"][name]["status"]
        for name in ("configuration", "database", "migrations")
    } == {"configuration": "ok", "database": "ok", "migrations": "ok"}

    api_health = page.request.get(f"{origin}/api/health/", max_redirects=0)
    assert api_health.status == 200
    assert api_health.json() == {"status": "ok", **expected_identity}

    admin = page.request.get(f"{origin}/api/v1/admin/health", max_redirects=0)
    assert admin.status == 401
    assert "location" not in admin.headers
    assert admin.headers["x-robots-tag"] == ROBOTS_VALUE
    assert_private_no_store(admin.headers)
    assert admin.headers["www-authenticate"] == "Bearer"
    request_id = admin.headers["x-request-id"]
    assert request_id
    assert admin.json() == {
        "error": {
            "code": "authentication_required",
            "message": "Valid Bearer authentication is required.",
            "request_id": request_id,
        }
    }

    robots = page.request.get(f"{origin}/robots.txt", max_redirects=0)
    assert robots.status == 200
    assert robots.headers["x-robots-tag"] == ROBOTS_VALUE
    assert robots.headers["content-type"] == "text/plain; charset=utf-8"
    assert robots.body() == b"User-agent: *\nDisallow: /\n"

    sitemap = page.request.get(f"{origin}/sitemap.xml", max_redirects=0)
    assert sitemap.status == 200
    assert sitemap.headers["x-robots-tag"] == ROBOTS_VALUE
    assert sitemap.headers["content-type"] == "application/xml; charset=utf-8"
    assert validate_sitemap_index(sitemap.body()) == EXPECTED_SITEMAP_LOCATIONS

    home = page.goto(origin)
    assert home is not None
    static_href = page.locator('link[rel="stylesheet"][href^="/static/"]').first.get_attribute(
        "href"
    )
    assert static_href is not None
    static_response = page.request.get(f"{origin}{static_href}", max_redirects=0)
    assert static_response.status == 200
    assert static_response.headers["x-robots-tag"] == ROBOTS_VALUE
