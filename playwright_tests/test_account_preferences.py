"""The account owns a member's theme and sign-in methods; the masthead does not.

Two controls moved into account settings.  The theme pill stays in the masthead
for a signed-out visitor, who has nowhere else to keep the choice, and is gone
once there is an account to keep it on.  The separate "Login connections" page
is gone from the account menu, and its route leads into the settings section
that replaced it.

The theme has two stores — the account for a signed-in member, the browser for
everyone else — so these tests drive the boundary between them: whose value
wins after signing in, and whether the wrong theme is ever painted first.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from allauth.socialaccount.models import SocialAccount, SocialApp
from django.conf import settings
from django.contrib.sites.models import Site
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Browser, Page, expect

from accounts.models import User
from courses.models.learner_profile import LearnerProfile

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

VIEWPORTS = (
    ({"width": 1280, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)
SCREENSHOTS = Path(".tmp/screenshots/account-preferences")


@pytest.fixture(autouse=True)
def _available_email_preferences():
    """Datamailer is unconfigured under test, and the settings page fetches it.

    Without a stand-in the page logs a 503 to the console on every visit, which
    says nothing about the two controls these tests are about.  live_server runs
    in this process, so patching the view's own reference is enough.
    """

    preferences = {
        "email_submission_confirmations": True,
        "email_deadline_reminders": True,
        "email_course_updates": False,
    }
    with patch(
        "accounts.views.email_preferences.get_email_preferences_for_user",
        return_value=preferences,
    ):
        yield


def _member(*, suffix: str, dark_mode: bool = False) -> User:
    email = f"preferences-{suffix}@example.invalid"
    return User.objects.create_user(
        username=email,
        email=email,
        dark_mode=dark_mode,
    )


def _sign_in(page: Page, live_server, user: User) -> None:
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


def _screenshot(page: Page, name: str, suffix: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / f"{name}-{suffix}.png", full_page=True)


def _open_account_menu(page: Page) -> None:
    page.locator('summary[aria-label="Account menu"]').click()


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_a_signed_out_visitor_keeps_the_masthead_theme_control(
    page: Page,
    live_server,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    page.goto(live_server.url)

    toggle = page.locator("#dark-mode-toggle")
    expect(toggle).to_be_visible()
    expect(toggle).to_have_attribute("aria-pressed", "false")

    toggle.click()
    expect(page.locator("body")).to_have_attribute("data-dark-mode", "true")
    expect(toggle).to_have_attribute("aria-pressed", "true")
    _screenshot(page, "signed-out-dark", suffix)

    # The browser is the only store a visitor without an account has, so the
    # choice has to survive both a reload and a move to another page.
    page.reload()
    expect(page.locator("body")).to_have_attribute("data-dark-mode", "true")
    page.goto(f"{live_server.url}{reverse('slack')}")
    expect(page.locator("body")).to_have_attribute("data-dark-mode", "true")


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_a_member_changes_the_theme_in_settings_and_the_page_follows(
    page: Page,
    live_server,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    member = _member(suffix=f"theme-{viewport['width']}")
    _sign_in(page, live_server, member)

    page.goto(live_server.url)
    expect(page.locator("#dark-mode-toggle")).to_have_count(0)
    _screenshot(page, "signed-in-light-home", suffix)

    page.goto(f"{live_server.url}/accounts/settings/")
    checkbox = page.locator("#id_dark_mode")
    expect(checkbox).not_to_be_checked()
    checkbox.check()

    # No reload: the settings page repaints itself in the new theme.
    expect(page.locator("body")).to_have_attribute("data-dark-mode", "true")
    _screenshot(page, "settings-dark", suffix)
    assert LearnerProfile.objects.get(user=member).dark_mode is True

    # And the next page is served in it, from the account rather than a script.
    page.goto(live_server.url)
    expect(page.locator("body")).to_have_attribute("data-dark-mode", "true")
    expect(page.locator("#dark-mode-toggle")).to_have_count(0)
    _screenshot(page, "signed-in-dark-home", suffix)


def test_the_account_theme_wins_over_a_browser_choice_made_while_signed_out(
    page: Page,
    live_server,
) -> None:
    page.goto(live_server.url)
    page.locator("#dark-mode-toggle").click()
    expect(page.locator("body")).to_have_attribute("data-dark-mode", "true")

    member = _member(suffix="boundary")
    _sign_in(page, live_server, member)
    page.goto(live_server.url)

    # The stored browser value is still there and is deliberately ignored: the
    # pre-paint bootstrap only reads it when the body is not authenticated.
    assert page.evaluate("localStorage.getItem('darkMode')") == "true"
    expect(page.locator("body")).to_have_attribute("data-dark-mode", "false")
    expect(page.locator("body")).not_to_have_class("dark dark-mode")


def test_a_dark_member_never_sees_a_light_first_paint(
    page: Page,
    live_server,
) -> None:
    """The theme is in the served markup, not applied by a script after paint."""

    member = _member(suffix="flash", dark_mode=True)
    _sign_in(page, live_server, member)

    seen: list[str] = []
    page.add_init_script(
        """
        window.__themeAtFirstScript = null;
        document.addEventListener('readystatechange', () => {
          if (window.__themeAtFirstScript === null && document.body) {
            window.__themeAtFirstScript = document.body.className;
          }
        });
        """
    )
    response = page.goto(live_server.url)
    assert response is not None
    body = response.text()
    # The class is in the bytes the server sent, before any script could run.
    assert 'class="dark dark-mode"' in body
    seen.append(page.evaluate("document.body.className"))
    assert "dark-mode" in seen[0]


def _theme_switch(page: Page):
    """The Display preferences switch and the status region the script inserts
    directly after its row (settings_toggles.js statusRegionFor)."""
    return (
        page.locator("#id_dark_mode"),
        page.locator(".toggle-row:has(#id_dark_mode) + p.toggle-status"),
    )


def test_a_denied_storage_write_never_rolls_back_a_saved_theme_switch(
    browser: Browser,
    live_server,
) -> None:
    """UX-11: the browser copy of the theme is a convenience, not the save.

    With localStorage writes denied, the copy's exception used to fall into
    the network-failure path: the switch reverted beside an already-applied
    dark theme, announced by a "not saved" message for a theme the server
    had in fact stored.  The server's answer is the state; only the copy
    may fail, silently.
    """
    member = _member(suffix="storage-denied")
    context = browser.new_context()
    context.add_init_script(
        """
        const original = Storage.prototype.setItem;
        Storage.prototype.setItem = function (key, value) {
          if (key === 'darkMode') {
            throw new DOMException('Storage is denied.', 'SecurityError');
          }
          return original.call(this, key, value);
        };
        """
    )
    page = context.new_page()
    try:
        _sign_in(page, live_server, member)
        page.goto(f"{live_server.url}/accounts/settings/")

        checkbox, status = _theme_switch(page)
        expect(checkbox).not_to_be_checked()

        checkbox.check()
        expect(status).to_contain_text("Saved.")
        expect(status).not_to_contain_text("not saved")
        expect(checkbox).to_be_checked()
        expect(page.locator("body")).to_have_attribute("data-dark-mode", "true")
        member.refresh_from_db()
        assert member.dark_mode is True
        _screenshot(page, "theme-storage-denied-dark", "desktop")

        # The switch still works after the denied copy: a second save rounds
        # the theme back to light with the same honest "Saved.".
        checkbox.uncheck()
        expect(status).to_contain_text("Saved.")
        expect(status).not_to_contain_text("not saved")
        expect(checkbox).not_to_be_checked()
        expect(page.locator("body")).to_have_attribute("data-dark-mode", "false")
        member.refresh_from_db()
        assert member.dark_mode is False
    finally:
        context.close()


def test_storage_denied_at_load_leaves_the_account_theme_in_charge(
    browser: Browser,
    live_server,
) -> None:
    """A browser that refuses storage entirely still serves the account theme.

    The signed-in theme arrives in the markup from the account, so a denied
    store costs nothing at load, and switching still saves, repaints, and
    reports the save honestly.
    """
    member = _member(suffix="dark-storage-denied", dark_mode=True)
    context = browser.new_context()
    context.add_init_script(
        """
        Object.defineProperty(window, 'localStorage', {
          configurable: true,
          value: {
            getItem: () => { throw new DOMException('Storage is denied.', 'SecurityError'); },
            setItem: () => { throw new DOMException('Storage is denied.', 'SecurityError'); },
            removeItem: () => {},
            clear: () => {},
            key: () => null,
          },
        });
        """
    )
    page = context.new_page()
    try:
        _sign_in(page, live_server, member)
        page.goto(f"{live_server.url}/accounts/settings/")

        checkbox, status = _theme_switch(page)
        expect(checkbox).to_be_checked()
        expect(page.locator("body")).to_have_attribute("data-dark-mode", "true")

        checkbox.uncheck()
        expect(status).to_contain_text("Saved.")
        expect(status).not_to_contain_text("not saved")
        expect(checkbox).not_to_be_checked()
        expect(page.locator("body")).to_have_attribute("data-dark-mode", "false")
        member.refresh_from_db()
        assert member.dark_mode is False
    finally:
        context.close()


def test_a_failed_theme_save_still_reverts_and_says_so(page: Page, live_server) -> None:
    """The honest-failure contract (UX-02) holds on the theme row too: a save
    the server rejects reverts the switch, repaints nothing, and says so."""
    member = _member(suffix="theme-reject")
    _sign_in(page, live_server, member)
    page.goto(f"{live_server.url}/accounts/settings/")

    def refuse(route):
        route.fulfill(
            status=500,
            content_type="application/json",
            body=json.dumps({"error": "Save failed."}),
        )

    page.route("**/accounts/settings/toggle/", refuse)

    checkbox, status = _theme_switch(page)
    # On the page before acting: a transient bad load must read as one, not
    # as a failed toggle.
    expect(checkbox).to_be_visible()
    checkbox.check()

    expect(status).to_contain_text("Your change was not saved.")
    expect(checkbox).not_to_be_checked()
    expect(page.locator("body")).to_have_attribute("data-dark-mode", "false")
    expect(checkbox).to_be_enabled()
    expect(checkbox).to_be_focused()
    member.refresh_from_db()
    assert member.dark_mode is False


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_sign_in_methods_moved_out_of_the_account_menu_into_settings(
    page: Page,
    live_server,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    app = SocialApp.objects.create(
        provider="github",
        name="GitHub",
        client_id="client",
        secret="secret",
    )
    app.sites.add(Site.objects.get_current())
    member = _member(suffix=f"connections-{viewport['width']}")
    SocialAccount.objects.create(
        user=member,
        provider="github",
        uid="4242",
        extra_data={"login": "student"},
    )
    _sign_in(page, live_server, member)

    page.goto(live_server.url)
    _open_account_menu(page)
    menu = page.locator(".user-menu-panel")
    expect(menu.get_by_role("link", name="Login connections")).to_have_count(0)
    expect(menu.get_by_role("link", name="Account settings")).to_be_visible()
    _screenshot(page, "account-menu", suffix)

    menu.get_by_role("link", name="Account settings").click()
    section = page.locator("#sign-in-methods")
    expect(page.get_by_role("heading", name="Sign-in methods")).to_be_visible()
    expect(section.locator(".connection-name")).to_have_text("GitHub")
    expect(section.locator(".connection-address")).to_have_text("student")
    expect(section.get_by_role("button", name="Disconnect GitHub")).to_be_visible()
    expect(section.get_by_role("link", name="Connect GitHub")).to_be_visible()
    _screenshot(page, "sign-in-methods", suffix)

    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def test_the_old_connections_url_lands_on_the_settings_section(
    page: Page,
    live_server,
) -> None:
    member = _member(suffix="redirect")
    _sign_in(page, live_server, member)

    page.goto(f"{live_server.url}/accounts/3rdparty/")

    assert page.url.endswith("/accounts/settings/#sign-in-methods")
    expect(page.get_by_role("heading", name="Sign-in methods")).to_be_visible()
