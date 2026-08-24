from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, override_settings
from playwright.sync_api import Page, expect

from accounts.studio_sessions import SESSION_REFERENCE_KEY
from management_auth.models import APIPrincipal
from management_auth.services import create_principal

pytestmark = [
    pytest.mark.full,
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("_credential_fixture_settings"),
]

VIEWPORTS = ({"width": 1280, "height": 720}, {"width": 390, "height": 844})


@pytest.fixture
def _credential_fixture_settings():
    with override_settings(
        ROOT_URLCONF="management_api.tests.fixture_urlconf",
        NOINDEX=False,
    ):
        yield


def _authenticated_cookie(page: Page, live_server, user) -> tuple[Client, uuid.UUID]:
    client = Client()
    client.force_login(user)
    session_id = uuid.UUID(client.session[SESSION_REFERENCE_KEY])
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )
    return client, session_id


def _screenshot_path(name: str, viewport: dict[str, int]) -> Path:
    directory = Path(".tmp/screenshots/issue-87")
    directory.mkdir(parents=True, exist_ok=True)
    device = "desktop" if viewport["width"] > 600 else "mobile"
    return directory / f"{name}-{device}.png"


def _assert_private(response) -> None:
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    cache = {part.strip().lower() for part in response.headers["cache-control"].split(",")}
    assert {"private", "no-store"}.issubset(cache)


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_one_time_credential_lifecycle_is_accessible_and_nonpersistent(
    page: Page,
    live_server,
    viewport: dict[str, int],
) -> None:
    page.set_viewport_size(viewport)
    access = Permission.objects.get(content_type__app_label="core", codename="access_studio")
    high_risk = Permission.objects.get(
        content_type__app_label="core",
        codename="execute_high_risk_fixture",
    )
    user = get_user_model().objects.create_user(
        username=f"credential-browser-{viewport['width']}",
        is_active=True,
        is_staff=True,
    )
    user.user_permissions.add(access, high_risk)
    create_principal(
        kind=APIPrincipal.Kind.HUMAN,
        name=f"credential browser {viewport['width']}",
        identity_snapshot=f"human:credential-browser-{viewport['width']}",
        user=user,
        permissions=(access, high_risk),
    )
    target = create_principal(
        kind=APIPrincipal.Kind.SERVICE,
        name=f"browser service {viewport['width']}",
        identity_snapshot=f"service:credential-browser-{viewport['width']}",
        permissions=(access,),
    )
    _authenticated_cookie(page, live_server, user)
    page.context.grant_permissions(
        ["clipboard-read", "clipboard-write"],
        origin=live_server.url,
    )

    fixture_url = f"{live_server.url}/studio/_fixtures/credentials/"
    loaded = page.goto(fixture_url)
    assert loaded is not None and loaded.status == 200
    _assert_private(loaded)
    expect(page.get_by_role("heading", name="Credential lifecycle")).to_be_visible()
    expect(page.get_by_label("Service principal")).to_have_value(str(target.id))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")

    page.get_by_role("button", name="Create credential").focus()
    assert page.get_by_role("button", name="Create credential").evaluate(
        "element => getComputedStyle(element).outlineStyle !== 'none'"
    )
    page.get_by_role("button", name="Create credential").click()
    expect(page.get_by_role("heading", name="Copy this credential now")).to_be_visible()
    token = page.get_by_test_id("one-time-token").inner_text()
    assert token.startswith("dtca_v1_")
    page.get_by_role("button", name="Copy credential").click()
    expect(page.get_by_text("Credential copied", exact=True)).to_be_visible()

    reloaded = page.reload()
    assert reloaded is not None and reloaded.status == 200
    _assert_private(reloaded)
    expect(page.get_by_test_id("one-time-token")).to_have_count(0)
    assert token not in page.content()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.screenshot(path=_screenshot_path("created-masked", viewport), full_page=True)

    page.get_by_role("button", name="Rotate Browser fixture credential").click()
    expect(page.get_by_role("heading", name="Copy this credential now")).to_be_visible()
    rotated_token = page.get_by_test_id("one-time-token").inner_text()
    assert rotated_token.startswith("dtca_v1_") and rotated_token != token
    page.reload()
    assert token not in page.content()
    assert rotated_token not in page.content()
    expect(page.get_by_text("fixture.credential.rotate — succeeded", exact=True)).to_be_visible()

    page.get_by_role("button", name="Revoke Browser fixture credential").click()
    expect(page.get_by_text("Credential revoked", exact=True)).to_be_visible()
    expect(page.get_by_text("Status revoked", exact=True)).to_be_visible()
    expect(page.get_by_text("fixture.credential.revoke — succeeded", exact=True)).to_be_visible()
    html = page.content()
    for protected in (token, rotated_token, "secret_digest", "Traceback", "@example"):
        assert protected not in html
    page.screenshot(path=_screenshot_path("revoked-operations", viewport), full_page=True)

    page.get_by_role("button", name="Create credential").click()
    expect(page.get_by_test_id("one-time-token")).to_be_visible()
    navigation_token = page.get_by_test_id("one-time-token").inner_text()
    page.get_by_role("link", name="Leave credential page").click()
    expect(page.get_by_role("heading", name="Credential page left")).to_be_visible()
    back = page.go_back()
    assert back is not None and back.status == 200
    expect(page.get_by_test_id("one-time-token")).to_have_count(0)
    assert navigation_token not in page.content()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.screenshot(path=_screenshot_path("back-secret-gone", viewport), full_page=True)
