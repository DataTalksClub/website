from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.test import override_settings
from playwright.sync_api import Page, expect

from accounts.development_owner import bootstrap_development_owner
from core.bootstrap import RuntimeEnvironment

pytestmark = [
    pytest.mark.full,
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("_development_owner_settings"),
]

VIEWPORTS = ({"width": 1440, "height": 900}, {"width": 390, "height": 844})
TOKEN_PATTERN = re.compile(r"dtca_v1_[A-Za-z0-9_-]{16}_[A-Za-z0-9_-]{43}")
OWNER_EMAIL = "browser-owner@example.test"
OWNER_PASSWORD = "browser-owner-password-107"


@pytest.fixture
def _development_owner_settings():
    with override_settings(
        RUNTIME_ENVIRONMENT=RuntimeEnvironment.TEST,
        DEVELOPMENT_OWNER_LOGIN_ENABLED=True,
    ):
        yield


def screenshot_path(name: str, viewport: dict[str, int]) -> Path:
    directory = Path(".tmp/screenshots/issue-107")
    directory.mkdir(parents=True, exist_ok=True)
    device = "desktop" if viewport["width"] > 600 else "mobile"
    return directory / f"{name}-{device}.png"


def assert_private(response) -> None:
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    cache = {part.strip().lower() for part in response.headers["cache-control"].split(",")}
    assert {"private", "no-store"}.issubset(cache)


def redact_one_time_token(page: Page, raw_token: str) -> None:
    page.locator("#one-time-token").evaluate(
        "element => { element.textContent = '[redacted one-time credential]'; }"
    )
    assert raw_token not in page.content()


def assert_visible_login_controls(page: Page) -> None:
    controls = (
        page.get_by_label("Email", exact=True),
        page.get_by_label("Password", exact=True),
    )
    boxes = []
    for control in controls:
        expect(control).to_be_visible()
        expect(control).to_have_class(re.compile(r"(^|\s)form-control(\s|$)"))
        box = control.bounding_box()
        assert box is not None
        assert box["width"] >= 240
        assert box["height"] >= 40
        boxes.append(box)

        for dark_mode in (False, True):
            page.locator("body").evaluate(
                "(element, enabled) => element.classList.toggle('dark-mode', enabled)",
                dark_mode,
            )
            style = control.evaluate(
                """element => {
                    const style = getComputedStyle(element);
                    return {
                        backgroundColor: style.backgroundColor,
                        borderStyle: style.borderTopStyle,
                        borderWidth: style.borderTopWidth,
                    };
                }"""
            )
            assert style["backgroundColor"] not in {"transparent", "rgba(0, 0, 0, 0)"}
            assert style["borderStyle"] != "none"
            assert float(style["borderWidth"].removesuffix("px")) >= 1

        page.locator("body").evaluate("element => element.classList.remove('dark-mode')")
        control.focus()
        focused_style = control.evaluate(
            """element => {
                const style = getComputedStyle(element);
                return {
                    boxShadow: style.boxShadow,
                    outlineStyle: style.outlineStyle,
                };
            }"""
        )
        assert focused_style["boxShadow"] != "none" or focused_style["outlineStyle"] != "none"

    assert abs(boxes[0]["width"] - boxes[1]["width"]) <= 1
    assert abs(boxes[0]["height"] - boxes[1]["height"]) <= 1
    controls[-1].blur()


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_owner_login_distinct_surfaces_and_credential_lifecycle(
    page: Page,
    live_server,
    viewport: dict[str, int],
) -> None:
    page.set_viewport_size(viewport)
    bootstrap_development_owner(
        email=OWNER_EMAIL,
        password=OWNER_PASSWORD,
        reset_password=False,
        allow_test=True,
    )

    for source, target in (("/admin", "/admin/"), ("/studio", "/studio/")):
        redirected = page.request.get(
            f"{live_server.url}{source}?browser=safe",
            max_redirects=0,
        )
        assert redirected.status == 301
        assert redirected.headers["location"] == f"{target}?browser=safe"
        assert_private(redirected)
        unsafe = page.request.post(
            f"{live_server.url}{source}",
            data={"confirmed": True},
            max_redirects=0,
        )
        assert unsafe.status in {403, 405}
        assert "location" not in unsafe.headers
        assert_private(unsafe)

    anonymous_studio = page.request.get(
        f"{live_server.url}/studio/",
        max_redirects=0,
    )
    assert anonymous_studio.status == 302
    assert anonymous_studio.headers["location"] == "/accounts/login/?next=%2Fstudio%2F"

    admin_page_errors: list[str] = []
    page.on("pageerror", lambda error: admin_page_errors.append(str(error)))
    admin_login = page.goto(f"{live_server.url}/admin/login/?next=/admin/")
    assert admin_login is not None and admin_login.status == 200
    expect(page.locator("#login-form")).to_be_visible()
    expect(page.get_by_text("Available shortcuts", exact=True)).to_be_hidden()
    expect(page.get_by_text("Open command tool", exact=True)).to_be_hidden()
    expect(page.get_by_text("Toggle sidebar", exact=True)).to_be_hidden()

    login = page.goto(f"{live_server.url}/accounts/login/?next=%2Fstudio%2F")
    assert login is not None and login.status == 200
    assert_private(login)
    # Exact match: the design system sign-in page also carries an sr-visible
    # "Sign in with your DataTalks.Club account" panel heading, which a
    # substring lookup would resolve as a second heading.
    expect(page.get_by_role("heading", name="Sign In", exact=True)).to_be_visible()
    assert_visible_login_controls(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.screenshot(path=screenshot_path("development-login", viewport), full_page=True)

    page.get_by_label("Email", exact=True).fill("invalid-owner@example.test")
    page.get_by_label("Password", exact=True).fill("invalid-password")
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("Sign-in was not successful")
    expect(page.get_by_label("Password", exact=True)).to_have_value("")
    assert "invalid-password" not in page.content()
    assert_visible_login_controls(page)
    page.screenshot(path=screenshot_path("development-login-invalid", viewport), full_page=True)

    page.get_by_label("Email", exact=True).fill(OWNER_EMAIL)
    page.get_by_label("Password", exact=True).fill(OWNER_PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    expect(page).to_have_url(f"{live_server.url}/studio/")
    expect(page.get_by_role("heading", name="Studio", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="API credentials")).to_be_visible()

    admin = page.goto(f"{live_server.url}/admin/")
    assert admin is not None and admin.status == 200
    assert_private(admin)
    expect(page.get_by_text("DataTalks.Club Django admin", exact=True)).to_have_count(1)
    assert "Django administration" in page.title()
    expect(page.get_by_text("Private staff workspace", exact=True)).to_have_count(0)
    for broken_overlay_marker in (
        "Available shortcuts",
        "Open command tool",
        "Toggle sidebar",
    ):
        expect(page.get_by_text(broken_overlay_marker, exact=True)).to_have_count(0)
    page.keyboard.press("Shift+?")
    page.keyboard.press("Control+k")
    page.keyboard.press("Escape")
    expect(page.get_by_text("DataTalks.Club Django admin", exact=True)).to_be_visible()
    assert admin_page_errors == []

    credentials = page.goto(f"{live_server.url}/studio/access/api-credentials/")
    assert credentials is not None and credentials.status == 200
    assert_private(credentials)
    expect(page.get_by_role("heading", name="API credentials")).to_be_visible()
    expect(page.get_by_text("No service credentials have been issued.")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.screenshot(path=screenshot_path("credentials-empty", viewport), full_page=True)

    page.get_by_label("Allow management health reads only").check()
    page.get_by_label("I understand this secret is displayed once").check()
    with page.expect_response(
        lambda response: (
            response.url.endswith("/studio/access/api-credentials/")
            and response.request.method == "POST"
        )
    ) as create_response:
        page.get_by_role("button", name="Create credential").click()
    assert create_response.value.status == 201
    raw_token = page.locator("#one-time-token").text_content() or ""
    assert TOKEN_PATTERN.fullmatch(raw_token)
    expect(page.get_by_role("heading", name="Copy this credential now")).to_be_visible()

    health = page.request.get(
        f"{live_server.url}/api/v1/admin/health",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert health.status == 200
    denied_management = page.request.get(
        f"{live_server.url}/api/v1/admin/credentials",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert denied_management.status == 403

    redact_one_time_token(page, raw_token)
    page.screenshot(path=screenshot_path("credential-created-redacted", viewport), full_page=True)
    page.goto(f"{live_server.url}/studio/")
    page.go_back()
    assert raw_token not in page.content()

    # Design system (issue #179) dropped the old core/studio.css card classes from the
    # Studio credentials page; the record is still the article the view stamps with
    # its credential id, which is the stable hook for scoping a row's controls.
    active = page.locator("article[data-credential-id]").filter(has_text="active").first
    active.get_by_label("Confirm rotation").check()
    with page.expect_response(
        lambda response: response.url.endswith("/rotate/") and response.request.method == "POST"
    ) as rotate_response:
        active.get_by_role("button", name="Rotate credential").click()
    assert rotate_response.value.status == 201
    successor_token = page.locator("#one-time-token").text_content() or ""
    assert TOKEN_PATTERN.fullmatch(successor_token)
    assert successor_token != raw_token
    redact_one_time_token(page, successor_token)

    successor = page.locator("article[data-credential-id]").filter(has_text="active").first
    successor.get_by_label("Confirm immediate revocation").check()
    with page.expect_response(
        lambda response: response.url.endswith("/revoke/") and response.request.method == "POST"
    ) as revoke_response:
        successor.get_by_role("button", name="Revoke credential").click()
    assert revoke_response.value.status == 200
    expect(page.get_by_text("Credential revoked.", exact=False)).to_be_visible()
    assert raw_token not in page.content()
    assert successor_token not in page.content()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.screenshot(path=screenshot_path("credential-revoked", viewport), full_page=True)

    revoked_health = page.request.get(
        f"{live_server.url}/api/v1/admin/health",
        headers={"Authorization": f"Bearer {successor_token}"},
    )
    assert revoked_health.status == 401

    page.goto(f"{live_server.url}/studio/")
    page.get_by_role("button", name="Sign out").click()
    back = page.go_back()
    assert back is not None and back.status == 200
    assert_private(back)
    expect(page).to_have_url(f"{live_server.url}/accounts/login/?next=%2Fstudio%2F")
    expect(page.get_by_role("heading", name="Sign In", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Studio", exact=True)).to_have_count(0)
