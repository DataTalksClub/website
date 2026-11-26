"""The event page's sign-in dialog, used in a real browser.

"Log in to register" opens a modal <dialog> drawing the login page's own card
(social buttons, or, email and password, Sign in, Forgot password, Create an
account).  Esc and the close button dismiss it; a password sign-in inside it
swaps the registration card for the signed-in one without leaving the event.
Without JavaScript the trigger is a link to the login page back to the event.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.utils import timezone
from playwright.sync_api import Page, ViewportSize, expect

from events.identity import canonical_detail_path, create_event_identity
from playwright_tests.accessibility_support import (
    assert_accessible_page,
    axe_issues,
    control_state_issues,
)
from test_support.event_pictures import TEST_EVENT_DESCRIPTION, TEST_EVENT_PICTURE

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

SCREENSHOTS = Path(".tmp/screenshots/event-sign-in-dialog")
PASSWORD = "synthetic-dialog-password-42"


def _event_path() -> str:
    event = create_event_identity(
        picture=TEST_EVENT_PICTURE,
        description=TEST_EVENT_DESCRIPTION,
        speakers=[("synthetic-speaker", "Synthetic Speaker")],
        title="Synthetic sign-in dialog webinar",
        starts_at=timezone.now() + timedelta(days=10),
        source_repository="synthetic/event-sign-in-dialog",
        source_revision="r1",
        source_key="sign-in-dialog",
        event_type="webinar",
        status="upcoming",
    )
    return canonical_detail_path(event.content_id)


def _member(email: str):
    user = get_user_model().objects.create_user(username=email, email=email, password=PASSWORD)
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    return user


@pytest.mark.parametrize(
    ("viewport", "suffix"),
    [
        ({"width": 1366, "height": 900}, "desktop"),
        ({"width": 390, "height": 844}, "mobile"),
    ],
)
def test_log_in_to_register_opens_a_dialog_and_signs_in_on_the_event(
    page: Page,
    live_server,
    viewport: ViewportSize,
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    event_path = _event_path()
    email = f"dialog-{suffix}@example.com"
    _member(email)
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)

    page.goto(f"{live_server.url}{event_path}")
    trigger = page.get_by_role("link", name="Log in to register")
    # The no-JS path: the login page, coming back to this event.
    expect(trigger).to_have_attribute("href", f"/accounts/login/?next={quote(event_path, safe='')}")
    dialog = page.locator("dialog#event-sign-in")
    expect(dialog).not_to_be_visible()

    assert_accessible_page(page, f"event registration card {suffix}")
    trigger.click()
    expect(dialog).to_be_visible()
    expect(dialog).to_have_attribute("aria-labelledby", "event-sign-in-title")
    expect(dialog.get_by_role("heading", name="Log in to register")).to_be_visible()
    assert dialog.evaluate("d => d.matches(':modal')")
    assert dialog.evaluate("d => d.contains(document.activeElement)")
    if suffix == "mobile":
        box = dialog.bounding_box()
        assert box is not None and box["width"] >= 389 and box["height"] >= 843
    page.screenshot(path=SCREENSHOTS / f"signin-{suffix}.png")
    # The page behind a modal dialog is inert (so its skip link cannot take
    # focus); the dialog itself answers to axe and the control-state checks.
    assert (
        axe_issues(page, f"sign-in dialog {suffix}")
        + control_state_issues(page, f"sign-in dialog {suffix}")
        == []
    )

    # Esc closes it and gives focus back to the trigger.
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(trigger).to_be_focused()

    # "Create an account" shows the sign-up form in the same dialog.
    trigger.click()
    dialog.get_by_role("link", name="Create an account").click()
    expect(dialog.get_by_role("heading", name="Create your account")).to_be_visible()
    expect(dialog).to_have_attribute("aria-labelledby", "event-sign-up-title")
    expect(dialog.get_by_role("button", name="Create account")).to_be_visible()
    page.screenshot(path=SCREENSHOTS / f"signup-{suffix}.png")
    dialog.get_by_role("link", name="Sign in").click()
    expect(dialog.get_by_role("heading", name="Log in to register")).to_be_visible()

    # The close button closes it too.
    dialog.get_by_role("button", name="Close").click()
    expect(dialog).not_to_be_visible()

    # A wrong password keeps the dialog open with the login page's error.
    trigger.click()
    dialog.locator("#id_login").fill(email)
    dialog.locator("#id_password").fill("not-the-password")
    dialog.get_by_role("button", name="Sign in").click()
    expect(dialog.locator(".auth-dialog-error")).to_be_visible()
    expect(dialog).to_be_visible()

    # The right one signs in on the event: the card becomes the signed-in,
    # one-click registration and the dialog is gone.
    dialog.locator("#id_password").fill(PASSWORD)
    dialog.get_by_role("button", name="Sign in").click()
    expect(page.locator("dialog#event-sign-in")).to_have_count(0)
    one_click = page.locator(".event-register-one-click")
    expect(one_click).to_be_visible()
    expect(page.locator(".event-register-who-email")).to_have_text(email)
    assert urlsplit(page.url).path == event_path
    page.locator("#event-register-heading").scroll_into_view_if_needed()
    page.screenshot(path=SCREENSHOTS / f"signed-in-{suffix}.png")
