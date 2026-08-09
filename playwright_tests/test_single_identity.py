from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

import pytest
from allauth.account.models import EmailAddress
from django.conf import settings
from django.test import Client, override_settings
from playwright.sync_api import Page, expect

from accounts.models import CustomUser
from accounts.studio_roles import synchronize_studio_roles

pytestmark = [
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("_identity_browser_settings"),
]

VIEWPORTS = ({"width": 1280, "height": 720}, {"width": 390, "height": 844})
BROWSER_TRANSITION_BYPASSES = (
    "/courses/../accounts/continue/",
    "/courses/%2e%2e/accounts/login/",
    "/courses/%2E%2e/accounts/logout/",
    "/courses/.%2E/accounts/github/login/callback/",
    "/courses/%252e%252e/accounts/continue/",
    "/courses\\..\\accounts\\logout\\",
    "/courses/%5c..%5caccounts%5clogin/",
    "/courses/%255C..%255caccounts%255ccontinue/",
    "/courses//../accounts//logout/",
    "../../accounts/login/",
)


@pytest.fixture
def _identity_browser_settings():
    with override_settings(
        ROOT_URLCONF="playwright_tests.identity_fixture_urls",
        NOINDEX=False,
    ):
        yield


def _screenshot_path(name: str, viewport: dict[str, int]) -> Path:
    directory = Path(".tmp/screenshots/issue-100")
    directory.mkdir(parents=True, exist_ok=True)
    device = "desktop" if viewport["width"] > 600 else "mobile"
    return directory / f"{name}-{device}.png"


def _active_user(*, suffix: str, is_staff: bool = False) -> CustomUser:
    email = f"synthetic-{suffix}@example.invalid"
    user = CustomUser.objects.create_user(
        username=f"synthetic-{suffix}",
        email=email,
        is_staff=is_staff,
        identity_state=CustomUser.IdentityState.ACTIVE,
    )
    EmailAddress.objects.create(
        user=user,
        email=email,
        verified=True,
        primary=True,
    )
    return user


def _add_authenticated_cookie(page: Page, live_server, user: CustomUser) -> None:
    client = Client()
    client.force_login(user)
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )


def _assert_private(response) -> None:
    cache_control = {
        part.strip().casefold() for part in response.headers["cache-control"].split(",")
    }
    assert {"private", "no-store"}.issubset(cache_control)


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_signed_out_login_and_explicit_reauthentication_are_one_safe_flow(
    page: Page,
    live_server,
    viewport: dict[str, int],
) -> None:
    page.set_viewport_size(viewport)
    home = page.goto(live_server.url)
    assert home is not None and home.status == 200
    login = page.get_by_role("link", name="Login")
    expect(login).to_have_attribute("href", "/accounts/login/?next=%2F")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.screenshot(
        path=_screenshot_path("signed-out-home", viewport),
        full_page=True,
    )

    login.click()
    expect(page).to_have_url(f"{live_server.url}/accounts/login/?next=%2F")
    expect(page.get_by_role("heading", name="Sign In")).to_be_visible()
    expect(page.get_by_text("Sign-in is temporarily unavailable")).to_be_visible()
    page.screenshot(
        path=_screenshot_path("returning-login", viewport),
        full_page=True,
    )

    with override_settings(ACCOUNT_CANONICAL_ORIGIN=live_server.url):
        continuity = page.goto(f"{live_server.url}/accounts/continue/?next=/courses/")
    assert continuity is not None and continuity.status == 200
    expect(page).to_have_url(f"{live_server.url}/accounts/login/?next=%2Fcourses%2F")
    for forbidden in ("token=", "code=", "credential=", "session="):
        assert forbidden not in page.url.casefold()

    with override_settings(ACCOUNT_CANONICAL_ORIGIN=live_server.url):
        safe_fallback = page.goto(f"{live_server.url}/accounts/continue/")
    assert safe_fallback is not None and safe_fallback.status == 200
    expect(page).to_have_url(f"{live_server.url}/accounts/login/?next=%2F")


def test_chromium_cannot_normalize_anonymous_next_into_an_auth_transition(
    page: Page,
    live_server,
) -> None:
    with override_settings(ACCOUNT_CANONICAL_ORIGIN=live_server.url):
        for candidate in BROWSER_TRANSITION_BYPASSES:
            query = urlencode({"next": candidate})
            response = page.goto(f"{live_server.url}/accounts/continue/?{query}")

            assert response is not None and response.status == 200
            expect(page).to_have_url(f"{live_server.url}/accounts/login/?next=%2F")
            expect(page.get_by_role("heading", name="Sign In")).to_be_visible()


def test_chromium_keeps_authenticated_identity_through_canonical_next_matrix(
    page: Page,
    live_server,
) -> None:
    learner = _active_user(suffix="canonical-browser-next")
    _add_authenticated_cookie(page, live_server, learner)

    with override_settings(ACCOUNT_CANONICAL_ORIGIN=live_server.url):
        for candidate in BROWSER_TRANSITION_BYPASSES:
            query = urlencode({"next": candidate})
            response = page.goto(f"{live_server.url}/accounts/continue/?{query}")

            assert response is not None and response.status == 200
            expect(page).to_have_url(f"{live_server.url}/")
            expect(page.locator("body")).to_have_attribute(
                "data-account-id",
                str(learner.pk),
            )

        legitimate_query = urlencode(
            {"next": ("/courses/guides/../?tab=overview&view=full#catalog")}
        )
        legitimate = page.goto(f"{live_server.url}/accounts/continue/?{legitimate_query}")

    assert legitimate is not None and legitimate.status == 200
    expect(page).to_have_url(f"{live_server.url}/courses?tab=overview&view=full#catalog")
    expect(page.locator("body")).to_have_attribute(
        "data-account-id",
        str(learner.pk),
    )


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_returning_learner_keeps_one_account_across_site_and_courses(
    page: Page,
    live_server,
    viewport: dict[str, int],
) -> None:
    page.set_viewport_size(viewport)
    learner = _active_user(suffix=f"learner-{viewport['width']}")
    _add_authenticated_cookie(page, live_server, learner)

    home = page.goto(live_server.url)
    assert home is not None and home.status == 200
    _assert_private(home)
    expect(page.locator("body")).to_have_attribute("data-account-id", str(learner.pk))
    page.locator('summary[aria-label="Account menu"]').click()
    account_menu = page.locator(".user-menu-panel")
    expect(account_menu.get_by_role("link", name="Courses", exact=True)).to_be_visible()
    expect(account_menu.get_by_role("link", name="Account settings")).to_be_visible()
    expect(account_menu.get_by_role("link", name="Login connections")).to_be_visible()
    expect(account_menu.get_by_role("link", name="Studio", exact=True)).to_have_count(0)
    page.screenshot(
        path=_screenshot_path("learner-account-menu", viewport),
        full_page=True,
    )

    account_menu.get_by_role("link", name="Account settings").click()
    expect(page.get_by_role("heading", name="Account settings")).to_be_visible()
    expect(page.locator("body")).to_have_attribute("data-account-id", str(learner.pk))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.screenshot(
        path=_screenshot_path("learner-account-settings", viewport),
        full_page=True,
    )

    with override_settings(ACCOUNT_CANONICAL_ORIGIN=live_server.url):
        continuity = page.goto(f"{live_server.url}/accounts/continue/?next=/courses/")
    assert continuity is not None and continuity.status == 200
    expect(page).to_have_url(f"{live_server.url}/courses")
    expect(page.locator("body")).to_have_attribute("data-account-id", str(learner.pk))
    identity = page.request.get(f"{live_server.url}/api/v1/account/identity/")
    assert identity.status == 200
    assert identity.json()["account_id"] == learner.pk


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_staff_navigation_is_capability_filtered(
    page: Page,
    live_server,
    viewport: dict[str, int],
) -> None:
    page.set_viewport_size(viewport)
    unassigned = _active_user(
        suffix=f"unassigned-staff-{viewport['width']}",
        is_staff=True,
    )
    _add_authenticated_cookie(page, live_server, unassigned)
    page.goto(live_server.url)
    page.locator('summary[aria-label="Account menu"]').click()
    expect(page.get_by_role("link", name="Studio", exact=True)).to_have_count(0)
    expect(page.get_by_role("link", name="Course admin")).to_have_count(0)
    page.screenshot(
        path=_screenshot_path("staff-capability-denied", viewport),
        full_page=True,
    )

    page.context.clear_cookies()
    operator = _active_user(
        suffix=f"course-operator-{viewport['width']}",
        is_staff=True,
    )
    roles = {group.name: group for group in synchronize_studio_roles()}
    operator.groups.add(roles["course_operator"])
    _add_authenticated_cookie(page, live_server, operator)
    page.goto(live_server.url)
    page.locator('summary[aria-label="Account menu"]').click()
    expect(page.get_by_role("link", name="Studio", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Course admin")).to_be_visible()
    page.screenshot(
        path=_screenshot_path("staff-capability-allowed", viewport),
        full_page=True,
    )


@pytest.mark.parametrize("viewport", VIEWPORTS)
def test_duplicate_conflict_guidance_is_generic_and_accessible(
    page: Page,
    live_server,
    viewport: dict[str, int],
) -> None:
    page.set_viewport_size(viewport)
    response = page.goto(f"{live_server.url}/_identity-fixtures/link-conflict/")
    assert response is not None and response.status == 409
    expect(
        page.get_by_role("heading", name="We could not safely link this sign-in")
    ).to_be_visible()
    expect(page.get_by_text("No account was created or linked.")).to_be_visible()
    expect(page.get_by_role("link", name="Try another login method")).to_be_visible()
    assert "@example.invalid" not in page.content()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.screenshot(
        path=_screenshot_path("duplicate-conflict", viewport),
        full_page=True,
    )
