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
SCREENSHOTS = Path(".tmp/screenshots/issue-114")


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
            offenders: [...document.querySelectorAll('body *')]
                .map(element => ({
                    tag: element.tagName,
                    id: element.id,
                    className: typeof element.className === 'string' ? element.className : '',
                    right: element.getBoundingClientRect().right,
                    width: element.getBoundingClientRect().width,
                }))
                .filter(item => item.right > window.innerWidth + 1)
                .slice(0, 10),
        })"""
    )
    assert metrics["documentWidth"] <= metrics["viewport"] + 1, metrics


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_operator_updates_public_announcement_without_restart(
    page: Page,
    live_server,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    admin = make_studio_user(
        username=f"settings-browser-admin-{suffix}",
        roles=("site_admin",),
    )
    authenticated_cookie(page, live_server, admin)

    response = page.goto(f"{live_server.url}/studio/settings")
    assert response is not None and response.status == 200
    assert_private(response)
    expect(page.get_by_role("heading", name="Site settings", exact=True)).to_be_visible()
    expect(page.get_by_role("group", name="Site announcement")).to_be_visible()
    expect(page.get_by_label("Show site announcement")).to_be_visible()
    message = page.get_by_label("Announcement message")
    expect(message).to_have_attribute("maxlength", "500")
    expect(page.get_by_text("Type: Boolean. Default: Off.", exact=False)).to_be_visible()
    expect(page.get_by_text("Type: String. Default: empty.", exact=False)).to_be_visible()
    message.focus()
    assert message.evaluate(
        "element => { const style = getComputedStyle(element); "
        "return style.outlineStyle !== 'none' || style.boxShadow !== 'none'; }"
    )
    assert_no_horizontal_overflow(page)
    screenshot(page, "settings-default", suffix)

    page.get_by_label("Show site announcement").check()
    message.fill('Community office hours & "news"')
    page.get_by_role("button", name="Save site settings").click()
    expect(page).to_have_url(f"{live_server.url}/studio/settings?saved=1")
    expect(page.get_by_role("status")).to_have_text("Site settings saved.")

    for path in ("/", "/events"):
        public = page.goto(f"{live_server.url}{path}")
        assert public is not None and public.status == 200
        announcement = page.get_by_role("complementary", name="Site announcement")
        expect(announcement).to_have_count(1)
        expect(announcement).to_have_text('Community office hours & "news"')
        assert announcement.evaluate(
            "element => Boolean(element.compareDocumentPosition("
            "document.querySelector('main')) & Node.DOCUMENT_POSITION_FOLLOWING)"
        )
        assert_no_horizontal_overflow(page)
    page.evaluate("window.scrollTo(0, 0)")
    screenshot(page, "public-enabled", suffix, full_page=False)

    page.goto(f"{live_server.url}/studio/settings")
    page.get_by_label("Announcement message").fill("Updated community office hours")
    page.get_by_role("button", name="Save site settings").click()
    page.goto(live_server.url)
    expect(page.get_by_role("complementary", name="Site announcement")).to_have_text(
        "Updated community office hours"
    )

    page.goto(f"{live_server.url}/studio/settings")
    page.get_by_label("Show site announcement").uncheck()
    page.get_by_role("button", name="Save site settings").click()
    page.goto(live_server.url)
    expect(page.get_by_role("complementary", name="Site announcement")).to_have_count(0)

    page.context.clear_cookies()
    auditor = make_studio_user(
        username=f"settings-browser-auditor-{suffix}",
        roles=("auditor",),
    )
    authenticated_cookie(page, live_server, auditor)
    page.goto(f"{live_server.url}/studio/settings")
    expect(page.get_by_role("heading", name="Site settings", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Save site settings")).to_have_count(0)
    expect(page.get_by_label("Show site announcement")).to_be_disabled()
    expect(page.get_by_label("Announcement message")).to_have_attribute("readonly", "")
    page.evaluate("document.documentElement.style.fontSize = '200%'")
    assert_no_horizontal_overflow(page)
    label_box = page.get_by_text("Announcement message", exact=True).bounding_box()
    fieldset_box = page.get_by_role("group", name="Site announcement").bounding_box()
    assert label_box is not None and fieldset_box is not None
    assert label_box["x"] + label_box["width"] <= fieldset_box["x"] + fieldset_box["width"] + 1
    screenshot(page, "settings-read-only-zoom", suffix)


def test_stale_browser_save_is_atomic_and_retryable(page: Page, live_server) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    admin = make_studio_user(
        username="settings-browser-stale",
        roles=("site_admin",),
    )
    authenticated_cookie(page, live_server, admin)
    stale_page = page.context.new_page()
    stale_page.set_viewport_size({"width": 390, "height": 844})

    page.goto(f"{live_server.url}/studio/settings")
    stale_page.goto(f"{live_server.url}/studio/settings")

    page.get_by_label("Show site announcement").check()
    page.get_by_label("Announcement message").fill("First browser save")
    page.get_by_role("button", name="Save site settings").click()
    expect(page).to_have_url(f"{live_server.url}/studio/settings?saved=1")

    stale_page.get_by_label("Announcement message").fill("Second proposed browser save")
    with stale_page.expect_response(
        lambda response: (
            response.url.endswith("/studio/settings") and response.request.method == "POST"
        )
    ) as stale_response:
        stale_page.get_by_role("button", name="Save site settings").click()
    assert stale_response.value.status == 409
    summary = stale_page.get_by_role("alert")
    expect(summary).to_contain_text("changed in another session")
    expect(stale_page.get_by_label("Announcement message")).to_have_value(
        "Second proposed browser save"
    )
    assert summary.evaluate("element => document.activeElement === element")
    current_message_revision = stale_page.locator(
        'input[name="message_expected_revision"]'
    ).get_attribute("value")
    assert current_message_revision == "1"
    assert_no_horizontal_overflow(stale_page)
    screenshot(stale_page, "settings-stale", "mobile")

    stale_page.get_by_role("button", name="Save site settings").click()
    expect(stale_page).to_have_url(f"{live_server.url}/studio/settings?saved=1")
