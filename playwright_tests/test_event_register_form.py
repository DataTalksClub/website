"""The event detail registration form, drawn and used in a real browser.

Every outcome the POST handler redirects to is reached by submitting the form,
then checked for the shared form primitives' computed treatment and for the
accessibility gate, light and dark, at desktop and mobile widths.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

import pytest
from community_base.events.models import EventRegistration
from community_base.events.tokens import generate_registration_token
from django.utils import timezone
from playwright.sync_api import Page, ViewportSize, expect

from events.identity import canonical_detail_path, create_event_identity
from playwright_tests.accessibility_support import assert_accessible_page
from test_support.event_pictures import TEST_EVENT_DESCRIPTION, TEST_EVENT_PICTURE

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

SCREENSHOTS = Path(".tmp/screenshots/event-register-form")
# The card keeps Register disabled until the form can succeed (and a click would
# blur the field, re-running that gate), so the server's own checks are reached
# by submitting the form directly, past the browser's validation.
SUBMIT_PAST_BROWSER_CHECKS = "f => { f.noValidate = true; f.requestSubmit(); }"


def _event_path(*, title: str, days: int) -> str:
    event = create_event_identity(
        picture=TEST_EVENT_PICTURE,
        description=TEST_EVENT_DESCRIPTION,
        speakers=[("synthetic-speaker", "Synthetic Speaker")],
        title=title,
        starts_at=timezone.now() + timedelta(days=days),
        source_repository="synthetic/event-register-form",
        source_revision="r1",
        source_key=title,
        event_type="webinar",
        status="upcoming" if days > 0 else "completed",
    )
    return canonical_detail_path(event.content_id)


def _settle(page: Page, dark: bool, name: str) -> None:
    dark_on = page.evaluate("document.body.classList.contains('dark')")
    if dark_on != dark:
        page.locator("#dark-mode-toggle").first.click()
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.locator("#event-register-heading").scroll_into_view_if_needed()
    page.screenshot(path=SCREENSHOTS / f"{name}.png")


@pytest.mark.parametrize(
    ("viewport", "suffix"),
    [
        ({"width": 1440, "height": 900}, "desktop"),
        ({"width": 390, "height": 844}, "mobile"),
    ],
)
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_event_register_form_states_use_the_shared_form_primitives(
    page: Page,
    live_server,
    viewport: ViewportSize,
    suffix: str,
    dark: bool,
) -> None:
    page.set_viewport_size(viewport)
    theme = "dark" if dark else "light"
    upcoming = _event_path(title="Synthetic register form webinar", days=10)
    past = _event_path(title="Synthetic finished webinar", days=-10)

    page.goto(f"{live_server.url}{upcoming}")
    form = page.locator("form.event-register-form")
    email = form.locator("#id_reg_email")
    # An anonymous visitor chooses how to register: the email form opens from
    # "Register with email" (the other choice is logging in).
    page.get_by_role("button", name="Register with email").click()
    expect(form).to_be_visible()
    _settle(page, dark, f"empty-{suffix}-{theme}")
    # The shared `.field-input` treatment, not the browser default control.
    assert email.evaluate("e => getComputedStyle(e).borderTopWidth") == "1px"
    assert email.evaluate("e => e.getBoundingClientRect().height") >= 40
    assert email.evaluate("e => e.getBoundingClientRect().width") >= 200
    assert form.locator(".form-check-input").first.evaluate(
        "e => e.getBoundingClientRect().width"
    ) == pytest.approx(17.6, abs=1)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert_accessible_page(page, f"event register empty {suffix} {theme}")

    # Register stays disabled until the form can succeed: a valid-looking email
    # and the newsletter agreement.
    register = form.get_by_role("button", name="Register")
    expect(register).to_be_disabled()
    email.fill("gate@example.com")
    expect(register).to_be_disabled()
    form.locator("#id_reg_newsletter_consent").check()
    expect(register).to_be_enabled()
    form.locator("#id_reg_newsletter_consent").uncheck()
    expect(register).to_be_disabled()

    # The server's own email check: bypass the browser's so the redirect is reached.
    email.fill("not-an-email")
    form.evaluate(SUBMIT_PAST_BROWSER_CHECKS)
    page.wait_for_url("**registered=invalid_email")
    summary = page.locator(".a11y-error-summary")
    expect(summary).to_be_focused()
    expect(summary.get_by_role("link")).to_have_attribute("href", "#id_reg_email")
    expect(email).to_have_attribute("aria-invalid", "true")
    expect(page.locator("#id_reg_email-error")).to_contain_text("Please enter a valid email")
    _settle(page, dark, f"invalid-email-{suffix}-{theme}")
    assert_accessible_page(page, f"event register invalid {suffix} {theme}")

    # Registering means agreeing to the newsletter; the server enforces it too.
    email.fill("event-form@example.com")
    form.evaluate(SUBMIT_PAST_BROWSER_CHECKS)
    page.wait_for_url("**registered=newsletter_required")
    summary = page.locator(".a11y-error-summary")
    expect(summary).to_be_focused()
    expect(summary.get_by_role("link")).to_have_attribute("href", "#id_reg_newsletter_consent")
    expect(page.locator("#id_reg_newsletter_consent")).to_have_attribute("aria-invalid", "true")
    _settle(page, dark, f"newsletter-required-{suffix}-{theme}")
    assert_accessible_page(page, f"event register newsletter {suffix} {theme}")

    # A valid email with the agreement holds a pending seat: the registration is
    # not done until the emailed link is followed.
    email.fill("event-form@example.com")
    form.locator("#id_reg_newsletter_consent").check()
    form.get_by_role("button", name="Register").click()
    pending = page.locator(".event-register-pending")
    expect(pending).to_contain_text("One last step")
    expect(pending).to_contain_text("event-form@example.com")
    expect(pending).to_be_focused()
    expect(page.locator(".event-register-notice")).to_have_count(0)
    expect(form).to_have_count(0)
    _settle(page, dark, f"pending-{suffix}-{theme}")
    assert_accessible_page(page, f"event register pending {suffix} {theme}")

    # Following the emailed link (built here the way the email builds it)
    # confirms the seat and replaces the form with the registered notice.
    registration = EventRegistration.objects.get(normalized_email="event-form@example.com")
    assert registration.status == EventRegistration.Status.PENDING_VERIFICATION
    token = generate_registration_token(registration, action="verify", expiry_hours=24)
    page.goto(f"{live_server.url}/events/registration/verify?{urlencode({'token': token})}")
    notice = page.get_by_role("alert").filter(has_text="You're registered")
    expect(notice).to_be_visible()
    expect(notice).to_be_focused()
    expect(page.locator(".a11y-error-summary")).to_have_count(0)
    expect(form).to_have_count(0)
    _settle(page, dark, f"registered-{suffix}-{theme}")
    assert_accessible_page(page, f"event register ok {suffix} {theme}")

    page.goto(f"{live_server.url}{past}")
    expect(page.locator("#event-register-heading")).to_have_count(0)
    page.goto(f"{live_server.url}{past}?registered=closed")
    closed = page.get_by_role("alert").filter(has_text="Registration for this event is closed")
    expect(closed).to_be_visible()
    expect(closed).to_be_focused()
    expect(page.locator("form.event-register-form")).to_have_count(0)
    _settle(page, dark, f"closed-{suffix}-{theme}")
    assert_accessible_page(page, f"event register closed {suffix} {theme}")
