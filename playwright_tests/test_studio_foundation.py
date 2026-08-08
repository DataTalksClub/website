from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client, override_settings
from playwright.sync_api import Page, expect

from accounts.studio_sessions import SESSION_REFERENCE_KEY, revoke_staff_session
from accounts.studio_test_support import make_studio_user
from core.models import AuditEvent

pytestmark = [
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("_studio_fixture_settings"),
]

VIEWPORTS = ({"width": 1280, "height": 720}, {"width": 390, "height": 844})


@pytest.fixture
def _studio_fixture_settings():
    with override_settings(
        NOINDEX=False,
        STUDIO_AUDIT_REDACTION_CANARIES=("browser-seeded-canary-86",),
    ):
        yield


def authenticated_cookie(page: Page, live_server, user) -> tuple[Client, uuid.UUID]:
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


def screenshot_path(name: str, viewport: dict[str, int]) -> Path:
    directory = Path(".tmp/screenshots/issue-86")
    directory.mkdir(parents=True, exist_ok=True)
    device = "desktop" if viewport["width"] > 600 else "mobile"
    return directory / f"{name}-{device}.png"


def assert_private_response(response) -> None:
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    cache = {part.strip().lower() for part in response.headers["cache-control"].split(",")}
    assert {"private", "no-store"}.issubset(cache)


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_capability_filtered_navigation_and_empty_state(
    page: Page,
    live_server,
    viewport: dict[str, int],
) -> None:
    page.set_viewport_size(viewport)
    content_operator = make_studio_user(
        username=f"content-{viewport['width']}",
        roles=("content_operator",),
    )
    authenticated_cookie(page, live_server, content_operator)
    content_home = page.goto(f"{live_server.url}/studio/")
    assert content_home is not None
    assert content_home.status == 200
    assert_private_response(content_home)
    expect(page.get_by_role("heading", name="Studio", exact=True)).to_be_visible()
    expect(page.get_by_text("You do not have any Studio sections assigned.")).to_be_visible()
    expect(page.get_by_role("link", name="Audit")).to_have_count(0)
    page.screenshot(path=screenshot_path("empty-sections", viewport), full_page=True)

    page.context.clear_cookies()
    auditor = make_studio_user(
        username=f"auditor-{viewport['width']}",
        roles=("content_operator", "auditor"),
    )
    authenticated_cookie(page, live_server, auditor)
    auditor_home = page.goto(f"{live_server.url}/studio/")
    assert auditor_home is not None
    assert auditor_home.status == 200
    expect(page.get_by_role("link", name="Audit")).to_be_visible()
    expect(page.get_by_text("You do not have any Studio sections assigned.")).to_have_count(0)
    page.get_by_role("link", name="Audit").focus()
    assert page.get_by_role("link", name="Audit").evaluate(
        "element => getComputedStyle(element).outlineStyle !== 'none'"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=screenshot_path("composed-navigation", viewport), full_page=True)


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_audit_filter_detail_redaction_and_layout(
    page: Page,
    live_server,
    viewport: dict[str, int],
) -> None:
    page.set_viewport_size(viewport)
    auditor = make_studio_user(
        username=f"audit-browser-{viewport['width']}",
        roles=("auditor",),
    )
    authenticated_cookie(page, live_server, auditor)
    event = AuditEvent.objects.create(
        actor_ref="person@example.test",
        action="browser.audit.fixture",
        target_type="fixture.browser",
        target_label="https://admin.example.test/manage/token",
        outcome=AuditEvent.Outcome.DENIED,
        request_id="browser-request-86",
        correlation_id="browser-correlation-86",
        changes={"authorization": "Bearer browser-token-86"},
        metadata={"summary": "visible browser fixture", "note": "browser-seeded-canary-86"},
    )

    audit = page.goto(f"{live_server.url}/studio/audit/")
    assert audit is not None
    assert audit.status == 200
    assert_private_response(audit)
    expect(page.get_by_role("heading", name="Audit events")).to_be_visible()
    expect(page.get_by_role("main")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.get_by_label("Action").fill("browser.audit.fixture")
    page.get_by_role("button", name="Apply filters").click()
    expect(page.get_by_role("link", name="browser.audit.fixture")).to_be_visible()
    page.get_by_role("link", name="browser.audit.fixture").focus()
    assert page.get_by_role("link", name="browser.audit.fixture").evaluate(
        "element => getComputedStyle(element).outlineStyle !== 'none'"
    )
    page.screenshot(path=screenshot_path("audit-list", viewport), full_page=True)

    page.get_by_role("link", name="browser.audit.fixture").click()
    expect(page.get_by_role("heading", name="Audit event", exact=True)).to_be_visible()
    expect(page.get_by_text("Immutable", exact=False)).to_be_visible()
    expect(page.get_by_text("visible browser fixture", exact=False)).to_be_visible()
    html = page.content()
    for protected in (
        "person@example.test",
        "https://admin.example.test/manage/token",
        "browser-token-86",
        "browser-seeded-canary-86",
    ):
        assert protected not in html
    assert str(event.id) not in page.url or page.url.endswith(f"/{event.id}/")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.screenshot(path=screenshot_path("audit-detail", viewport), full_page=True)


def test_revocation_prevents_back_cache_and_reload(page: Page, live_server) -> None:
    auditor = make_studio_user(username="revoked-browser", roles=("auditor",))
    _client, session_id = authenticated_cookie(page, live_server, auditor)
    loaded = page.goto(f"{live_server.url}/studio/audit/")
    assert loaded is not None and loaded.status == 200
    expect(page.get_by_role("heading", name="Audit events")).to_be_visible()

    page.goto(f"{live_server.url}/")
    revoke_staff_session(session_id, user=auditor)
    back = page.go_back()
    assert back is not None
    assert back.status == 403
    assert_private_response(back)
    expect(page.get_by_text("Studio access denied", exact=True)).to_be_visible()

    reloaded = page.reload()
    assert reloaded is not None
    assert reloaded.status == 403
    assert_private_response(reloaded)
    expect(page.get_by_text("Studio access denied", exact=True)).to_be_visible()
