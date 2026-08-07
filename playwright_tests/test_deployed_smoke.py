from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page, expect

from deploy.contracts import validate_source_sha
from deploy.smoke import ROBOTS_VALUE, SANDBOX_ORIGIN

pytestmark = pytest.mark.core


@pytest.fixture
def deployed_config() -> tuple[str, str, Path]:
    origin = os.getenv("DTC_TEST_BASE_URL")
    source_sha = os.getenv("DTC_EXPECTED_APP_VERSION")
    screenshot_directory = os.getenv("DTC_SCREENSHOT_DIR")
    if not origin and not source_sha and not screenshot_directory:
        pytest.skip("deployed read-only smoke is enabled only by explicit safe configuration")
    assert origin == SANDBOX_ORIGIN
    assert source_sha is not None
    validate_source_sha(source_sha)
    assert screenshot_directory is not None
    path = Path(screenshot_directory)
    assert path.parts[:1] == (".tmp",)
    path.mkdir(parents=True, exist_ok=True)
    return origin, source_sha, path


def assert_private_no_store(headers: dict[str, str]) -> None:
    directives = {item.strip().lower() for item in headers.get("cache-control", "").split(",")}
    assert {"private", "no-store"}.issubset(directives)


@pytest.mark.parametrize(
    "viewport", [{"width": 1280, "height": 720}, {"width": 390, "height": 844}]
)
def test_deployed_public_and_studio_html_are_exact_and_read_only(
    page: Page,
    deployed_config: tuple[str, str, Path],
    viewport: dict[str, int],
) -> None:
    origin, _source_sha, screenshot_directory = deployed_config
    page.set_viewport_size(viewport)

    home = page.goto(origin, wait_until="networkidle")
    assert home is not None
    assert home.status == 200
    assert home.url == f"{origin}/"
    assert home.headers["x-robots-tag"] == ROBOTS_VALUE
    expect(
        page.get_by_role("heading", name="Learn data skills. For free. Together.")
    ).to_be_visible()
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", "https://datatalks.club/"
    )
    expect(page.locator("body")).not_to_contain_text("Traceback")
    expect(page.locator("body")).not_to_contain_text("Page not found")
    assert page.locator('link[rel="stylesheet"]').count() > 0
    dimensions = f"{viewport['width']}x{viewport['height']}"
    page.screenshot(path=screenshot_directory / f"home-{dimensions}.png", full_page=True)

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
    page.screenshot(path=screenshot_directory / f"studio-sign-in-{dimensions}.png", full_page=True)


def test_deployed_health_and_anonymous_admin_api_contracts(
    page: Page,
    deployed_config: tuple[str, str, Path],
) -> None:
    origin, source_sha, _screenshot_directory = deployed_config

    live = page.request.get(f"{origin}/health/live", max_redirects=0)
    assert live.status == 200
    assert live.headers["x-robots-tag"] == ROBOTS_VALUE
    assert live.json() == {"status": "ok", "version": source_sha}

    ready = page.request.get(f"{origin}/health/ready", max_redirects=0)
    assert ready.status == 200
    assert ready.headers["x-robots-tag"] == ROBOTS_VALUE
    ready_payload = ready.json()
    assert ready_payload["status"] == "ready"
    assert {
        name: ready_payload["checks"][name]["status"]
        for name in ("configuration", "database", "migrations")
    } == {"configuration": "ok", "database": "ok", "migrations": "ok"}

    admin = page.request.get(f"{origin}/api/v1/admin/health", max_redirects=0)
    assert admin.status == 401
    assert "location" not in admin.headers
    assert admin.headers["x-robots-tag"] == ROBOTS_VALUE
    assert_private_no_store(admin.headers)
    assert admin.json() == {
        "error": {
            "code": "authentication_required",
            "message": "Authentication required",
        }
    }
