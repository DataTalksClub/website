from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from playwright.sync_api import Page, expect

from accounts.studio_sessions import SESSION_REFERENCE_KEY
from accounts.studio_test_support import make_studio_user

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]

VIEWPORTS = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)
SCREENSHOTS = Path(".tmp/screenshots/issue-187")


def authenticated_cookie(page: Page, live_server, user) -> Client:
    client = Client()
    client.force_login(user)
    assert uuid.UUID(client.session[SESSION_REFERENCE_KEY])
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )
    return client


def screenshot(page: Page, name: str, suffix: str, *, full_page: bool = True) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / f"{name}-{suffix}.png", full_page=full_page)


def assert_private(response) -> None:
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    cache = {part.strip().lower() for part in response.headers["cache-control"].split(",")}
    assert {"private", "no-store"}.issubset(cache)


def assert_no_horizontal_overflow(page: Page) -> None:
    metrics = page.evaluate(
        """() => ({
            viewport: window.innerWidth,
            documentWidth: document.documentElement.scrollWidth,
        })"""
    )
    assert metrics["documentWidth"] <= metrics["viewport"] + 3, metrics


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_operator_updates_public_navigation_without_restart(
    page: Page,
    live_server,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    admin = make_studio_user(
        username=f"navigation-browser-admin-{suffix}",
        roles=("site_admin",),
    )
    authenticated_cookie(page, live_server, admin)

    response = page.goto(f"{live_server.url}/studio/navigation")
    assert response is not None and response.status == 200
    assert_private(response)
    expect(page.get_by_role("heading", name="Site navigation", exact=True)).to_be_visible()
    expect(page.get_by_text("Current source: Code default.", exact=False)).to_be_visible()
    expect(page.get_by_text("Revision 0", exact=False)).to_be_visible()
    label = page.locator("#entry-0-label")
    expect(label).to_have_value("Events")
    label.focus()
    assert label.evaluate(
        "element => { const style = getComputedStyle(element); "
        "return style.outlineStyle !== 'none' || style.boxShadow !== 'none'; }"
    )
    assert_no_horizontal_overflow(page)
    screenshot(page, "navigation-default", suffix)

    label.fill("Gatherings")
    page.locator("#entry-1-visible").uncheck()
    page.locator("#entry-9-key").fill("home")
    page.locator("#entry-9-label").fill("Home")
    page.locator("#entry-9-target").select_option("home")
    page.locator("#entry-9-position").fill("10")
    page.locator("#entry-9-visible").check()
    page.get_by_role("button", name="Save site navigation").click()
    expect(page).to_have_url(f"{live_server.url}/studio/navigation?saved=1")
    expect(page.get_by_role("status")).to_contain_text("Site navigation saved.")

    home = page.goto(f"{live_server.url}/")
    assert home is not None and home.status == 200
    if suffix == "mobile":
        toggle = page.get_by_role("button", name="Menu")
        expect(toggle).to_be_visible()
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true")
    nav_links = page.locator("#site-navigation-links")
    expect(nav_links.get_by_role("link", name="Gatherings")).to_be_visible()
    expect(nav_links.get_by_role("link", name="Home")).to_be_visible()
    expect(nav_links.get_by_role("link", name="Courses")).to_have_count(0)
    assert_no_horizontal_overflow(page)
    screenshot(page, "public-navigation", suffix, full_page=False)

    events = page.goto(f"{live_server.url}/events/")
    assert events is not None and events.status == 200
    if suffix == "mobile":
        page.get_by_role("button", name="Menu").click()
    expect(
        page.locator("#site-navigation-links").get_by_role("link", name="Gatherings")
    ).to_have_attribute("aria-current", "page")

    page.context.clear_cookies()
    auditor = make_studio_user(
        username=f"navigation-browser-auditor-{suffix}",
        roles=("auditor",),
    )
    authenticated_cookie(page, live_server, auditor)
    page.goto(f"{live_server.url}/studio/navigation")
    expect(page.get_by_role("heading", name="Site navigation", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Save site navigation")).to_have_count(0)
    expect(page.locator("#entry-0-label")).to_have_attribute("readonly", "")
    page.evaluate("document.documentElement.style.fontSize = '200%'")
    assert_no_horizontal_overflow(page)
    screenshot(page, "navigation-read-only-zoom", suffix)


def test_stale_browser_save_is_atomic_and_retryable(page: Page, live_server) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    admin = make_studio_user(
        username="navigation-browser-stale",
        roles=("site_admin",),
    )
    authenticated_cookie(page, live_server, admin)
    stale_page = page.context.new_page()
    stale_page.set_viewport_size({"width": 390, "height": 844})

    page.goto(f"{live_server.url}/studio/navigation")
    stale_page.goto(f"{live_server.url}/studio/navigation")

    page.locator("#entry-0-label").fill("First browser save")
    page.get_by_role("button", name="Save site navigation").click()
    expect(page).to_have_url(f"{live_server.url}/studio/navigation?saved=1")

    stale_page.locator("#entry-0-label").fill("Second proposed browser save")
    with stale_page.expect_response(
        lambda response: (
            response.url.endswith("/studio/navigation") and response.request.method == "POST"
        )
    ) as stale_response:
        stale_page.get_by_role("button", name="Save site navigation").click()
    assert stale_response.value.status == 409
    summary = stale_page.get_by_role("alert")
    expect(summary).to_contain_text("changed in another session")
    expect(stale_page.locator("#entry-0-label")).to_have_value("Second proposed browser save")
    assert summary.evaluate("element => document.activeElement === element")
    current_revision = stale_page.locator('input[name="expected_revision"]').get_attribute("value")
    assert current_revision == "1"
    assert_no_horizontal_overflow(stale_page)
    screenshot(stale_page, "navigation-stale", "mobile")

    stale_page.get_by_role("button", name="Save site navigation").click()
    expect(stale_page).to_have_url(f"{live_server.url}/studio/navigation?saved=1")
