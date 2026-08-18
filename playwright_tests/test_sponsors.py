from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from playwright.sync_api import Page, expect

from accounts.studio_sessions import SESSION_REFERENCE_KEY
from accounts.studio_test_support import make_studio_user

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

VIEWPORTS = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)
SCREENSHOTS = Path(".tmp/screenshots/issue-188")


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


def screenshot(page: Page, name: str, suffix: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / f"{name}-{suffix}.png", full_page=True)


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
    assert metrics["documentWidth"] <= metrics["viewport"] + 1, metrics


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_operator_manages_sponsors_and_public_strip(
    page: Page,
    live_server,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    admin = make_studio_user(
        username=f"sponsor-browser-admin-{suffix}",
        roles=("site_admin",),
    )
    authenticated_cookie(page, live_server, admin)

    response = page.goto(f"{live_server.url}/studio/sponsors/")
    assert response is not None and response.status == 200
    assert_private(response)
    expect(page.get_by_role("heading", name="Sponsors", exact=True)).to_be_visible()
    expect(page.get_by_text("There are no sponsors yet.")).to_be_visible()
    assert_no_horizontal_overflow(page)
    screenshot(page, "sponsors-empty", suffix)

    create = page.get_by_role("region", name="Create a sponsor")
    create.get_by_label("Key").fill(f"acme-{suffix}")
    create.get_by_label("Name").fill("Acme Analytics")
    create.get_by_label("HTTPS URL").fill("https://acme.example")
    create.get_by_label("Tagline").fill("Data for everyone")
    create.get_by_label("Lifecycle").select_option("active")
    create.get_by_label("Placement", exact=True).select_option("events_hub")
    create.get_by_label("Position").fill("1")
    create.get_by_label("Enable this placement").check()
    create.get_by_role("button", name="Create sponsor").click()
    expect(page.get_by_role("status")).to_contain_text("Sponsor saved.")
    expect(page.get_by_text("Revision 1", exact=True)).to_be_visible()
    assert_no_horizontal_overflow(page)
    screenshot(page, "sponsors-detail", suffix)

    public = page.goto(f"{live_server.url}/events")
    assert public is not None and public.status == 200
    section = page.get_by_role("heading", name="Supported by", exact=True)
    expect(section).to_be_visible()
    expect(page.get_by_role("link", name="Acme Analytics")).to_have_attribute(
        "rel",
        "sponsored noopener noreferrer",
    )
    assert_no_horizontal_overflow(page)
    screenshot(page, "events-supported-by", suffix)

    page.goto(f"{live_server.url}/studio/sponsors/")
    page.get_by_role("link", name=f"acme-{suffix}").click()
    page.get_by_label(f"Confirm archival of acme-{suffix}").check()
    page.get_by_role("button", name="Archive sponsor").click()
    expect(page.get_by_text("archived", exact=True)).to_be_visible()
    omitted = page.goto(f"{live_server.url}/events")
    assert omitted is not None and omitted.status == 200
    expect(page.get_by_role("heading", name="Supported by", exact=True)).to_have_count(0)
