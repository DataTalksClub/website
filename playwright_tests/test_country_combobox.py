"""The country combobox's reported state and its keyboard contract agree.

The shared registration widget once hid its panel without clearing
``aria-expanded``, left its options in the Tab order with a mouse-only
activation handler, and had no defined ArrowUp-when-closed or no-results
announcement (audit UX-03).  These tests drive the real registration
final-step page -- the member without a profile country sees the widget
there -- and pin the contract at every transition: actual panel visibility
equals ``aria-expanded``, every ``aria-activedescendant`` points at an
existing option, options are not Tab stops, Enter commits, Tab closes and
moves on, Escape closes without discarding the typed value, and the empty
state is announced politely.  The widget is shared, so the same assertions
run at desktop and mobile viewport sizes.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Page, expect

from accounts.models import CustomUser
from courses.models import Cohort, RegistrationCampaign

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

VIEWPORTS = (
    ({"width": 1280, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)


def _campaign() -> RegistrationCampaign:
    course = Cohort.objects.create(
        slug="combobox-zoomcamp",
        title="Combobox Zoomcamp",
        description="Registration page for the combobox tests.",
    )
    return RegistrationCampaign.objects.create(
        slug="combobox",
        title="Combobox Zoomcamp",
        edition_label="2026 cohort",
        current_course=course,
        marketing_markdown="## Learn things\n\nBuild useful apps.",
    )


def _register_path() -> str:
    return reverse("registration_campaign", kwargs={"campaign_slug": "combobox"})


def _member_without_country() -> CustomUser:
    # The final step asks for the profile fields the account lacks; a member
    # with no country is exactly who sees the combobox there.
    email = "combobox-member@example.invalid"
    return CustomUser.objects.create_user(username=email, email=email)


def _sign_in(page: Page, live_server, user: CustomUser) -> None:
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


def _input(page: Page):
    return page.locator("[data-country-combobox-input]")


def _panel(page: Page):
    return page.locator("[data-country-combobox-panel]")


def _active_option(page: Page):
    descendant = _input(page).get_attribute("aria-activedescendant")
    assert descendant, "an active option must be named while the popup is open"
    option = page.locator(f"#{descendant}")
    expect(option).to_have_count(1)
    return option


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_open_type_select_and_reopen_keep_state_and_visibility_agreeing(
    page: Page, live_server, viewport: dict[str, int], suffix: str
) -> None:
    page.set_viewport_size(viewport)
    _campaign()
    _sign_in(page, live_server, _member_without_country())
    page.goto(f"{live_server.url}{_register_path()}")

    field = _input(page)
    panel = _panel(page)
    field.focus()
    expect(panel).to_be_visible()
    expect(field).to_have_attribute("aria-expanded", "true")
    _active_option(page)

    # Typing filters; the active descendant keeps pointing at a real option.
    field.fill("Ger")
    expect(panel).to_be_visible()
    expect(field).to_have_attribute("aria-expanded", "true")
    active = _active_option(page)
    expect(active).to_contain_text("Germany")

    # Enter commits the active option and closes the popup.
    page.keyboard.press("Enter")
    expect(panel).to_be_hidden()
    expect(field).to_have_attribute("aria-expanded", "false")
    expect(field).to_have_value("Germany")
    expect(field).to_be_focused()
    assert field.get_attribute("aria-activedescendant") is None

    # Reopening after a selection follows the input-focused combobox pattern:
    # the input never lost focus, so ArrowDown is the defined reopen path
    # (a repeated focus() would fire no focus event at all).
    page.keyboard.press("ArrowDown")
    expect(panel).to_be_visible()
    expect(field).to_have_attribute("aria-expanded", "true")
    _active_option(page)


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_escape_closes_without_discarding_the_typed_value(
    page: Page, live_server, viewport: dict[str, int], suffix: str
) -> None:
    page.set_viewport_size(viewport)
    _campaign()
    _sign_in(page, live_server, _member_without_country())
    page.goto(f"{live_server.url}{_register_path()}")

    field = _input(page)
    panel = _panel(page)
    field.focus()
    expect(panel).to_be_visible()
    field.fill("Spa")

    page.keyboard.press("Escape")

    expect(panel).to_be_hidden()
    expect(field).to_have_attribute("aria-expanded", "false")
    # The typed query survives Escape: closing is not clearing.
    expect(field).to_have_value("Spa")
    assert field.get_attribute("aria-activedescendant") is None


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_options_are_not_tab_stops_and_tab_closes_the_popup(
    page: Page, live_server, viewport: dict[str, int], suffix: str
) -> None:
    page.set_viewport_size(viewport)
    _campaign()
    _sign_in(page, live_server, _member_without_country())
    page.goto(f"{live_server.url}{_register_path()}")

    field = _input(page)
    panel = _panel(page)
    field.focus()
    expect(panel).to_be_visible()

    # An option button must never land in the Tab order.
    for option in page.locator(".country-combobox-option").all():
        assert option.get_attribute("tabindex") == "-1"

    page.keyboard.press("Tab")

    expect(panel).to_be_hidden()
    expect(field).to_have_attribute("aria-expanded", "false")
    # Tab moved focus onward to the next form control, as it must.
    expect(field).not_to_be_focused()


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_pointer_and_touch_equivalent_click_select_and_keep_input_focus(
    page: Page, live_server, viewport: dict[str, int], suffix: str
) -> None:
    page.set_viewport_size(viewport)
    _campaign()
    _sign_in(page, live_server, _member_without_country())
    page.goto(f"{live_server.url}{_register_path()}")

    field = _input(page)
    panel = _panel(page)
    field.focus()
    expect(panel).to_be_visible()

    option = page.locator(".country-combobox-option").first
    country = option.inner_text()
    option.click()

    expect(field).to_have_value(country)
    expect(panel).to_be_hidden()
    expect(field).to_have_attribute("aria-expanded", "false")
    # The input kept focus throughout: mousedown was prevented, and the
    # click handler made activation work for touch/AT click synthesis too.
    expect(field).to_be_focused()


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_arrow_up_when_closed_opens_on_the_last_option(
    page: Page, live_server, viewport: dict[str, int], suffix: str
) -> None:
    page.set_viewport_size(viewport)
    _campaign()
    _sign_in(page, live_server, _member_without_country())
    page.goto(f"{live_server.url}{_register_path()}")

    field = _input(page)
    panel = _panel(page)
    # A closed popup after Escape, not merely an unfocused field.
    field.focus()
    expect(panel).to_be_visible()
    page.keyboard.press("Escape")
    expect(panel).to_be_hidden()

    page.keyboard.press("ArrowUp")

    expect(panel).to_be_visible()
    expect(field).to_have_attribute("aria-expanded", "true")
    # The defined closed behavior: open with the last option active.
    active = _active_option(page)
    expect(active).to_have_attribute("aria-selected", "true")
    last = page.locator(".country-combobox-option").last
    expect(active).to_have_text(last.inner_text())


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_no_results_are_announced_politely_with_no_dangling_reference(
    page: Page, live_server, viewport: dict[str, int], suffix: str
) -> None:
    page.set_viewport_size(viewport)
    _campaign()
    _sign_in(page, live_server, _member_without_country())
    page.goto(f"{live_server.url}{_register_path()}")

    field = _input(page)
    panel = _panel(page)
    field.focus()
    expect(panel).to_be_visible()
    field.fill("zzzz-no-such-country")

    expect(panel).to_be_visible()
    expect(field).to_have_attribute("aria-expanded", "true")
    expect(page.locator(".country-combobox-empty")).to_contain_text("No matching countries")
    # The empty state is announced politely, and no active-descendant
    # reference points at a removed option.  The announcement region is the
    # one the widget inserts inside its own field wrapper (the page also has
    # an unrelated analytics live region).
    assert field.get_attribute("aria-activedescendant") is None
    announcement = page.locator(".country-combobox-field p[aria-live='polite']")
    expect(announcement).to_have_text("No matching countries")

    # Recovery: narrowing to a real prefix reopens the option list cleanly.
    field.fill("Ger")
    expect(page.locator(".country-combobox-option").first).to_contain_text("Germany")
    _active_option(page)


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_enter_on_an_open_popup_selects_instead_of_submitting_the_form(
    page: Page, live_server, viewport: dict[str, int], suffix: str
) -> None:
    page.set_viewport_size(viewport)
    _campaign()
    _sign_in(page, live_server, _member_without_country())
    page.goto(f"{live_server.url}{_register_path()}")

    field = _input(page)
    panel = _panel(page)
    field.focus()
    field.fill("Ger")
    expect(panel).to_be_visible()

    page.keyboard.press("Enter")

    # The popup consumed Enter: a selection happened, the form did not
    # submit (form validation with the popup open is a later, deliberate
    # act by the member).
    expect(field).to_have_value("Germany")
    expect(panel).to_be_hidden()
    expect(page).to_have_url(f"{live_server.url}{_register_path()}")
