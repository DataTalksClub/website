"""UX-10 browser acceptance: a failed enrollment save lands on the summary.

The enrollment page drew styled-invalid widgets with no summary to land on
and no relationship between an error and its field. It now draws the shared
contract: after a failed save the error summary takes focus, its link
reaches the failed field by keyboard, the typed value survives, and the
whole thing still works with JavaScript disabled and on a phone.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Browser, Page, expect

from courses.models.cohort import Cohort, Enrollment

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

EVIDENCE = Path(".tmp/enrollment-error-accessibility")
LONG_NAME = "x" * 256
OVERLONG_ERROR = "Ensure this value has at most 255 characters"


def _member(suffix: str):
    from accounts.models import User

    email = f"enroll-error-{suffix}@example.invalid"
    return User.objects.create_user(username=email, email=email)


@pytest.fixture
def error_course() -> Cohort:
    cohort = Cohort.objects.create(
        slug="enroll-error-contract",
        title="Enrollment Error Contract",
        description="UX-10 browser acceptance course",
    )
    Enrollment.objects.create(student=_member("student"), course=cohort)
    return cohort


def _enrollment_path(cohort: Cohort) -> str:
    return reverse(
        "cohort_enrollment",
        kwargs={"course_slug": cohort.slug, "cohort_identifier": cohort.identifier},
    )


def _sign_in(page: Page, live_server, user) -> None:
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


def goto_with_retry(page: Page, url: str) -> None:
    response = None
    for attempt in range(3):
        response = page.goto(url, wait_until="domcontentloaded")
        if response is not None and response.status == 200:
            return
        page.wait_for_timeout(300 * (attempt + 1))
    assert response is not None and response.status == 200, f"page never loaded: {url}"


def _submit_long_display_name(page: Page, live_server, cohort: Cohort, user) -> None:
    _sign_in(page, live_server, user)
    goto_with_retry(page, f"{live_server.url}{_enrollment_path(cohort)}")
    # The input carries maxlength=255, so no typed input can exceed it and a
    # plain fill would silently truncate into a valid save. Setting the value
    # programmatically stands in for a client that submits past the limit
    # (a stale tab, a scripted client); the failed save and its re-render are
    # the real server ones.
    page.locator("#id_display_name").evaluate("el => { el.value = 'x'.repeat(256); }")
    with page.expect_navigation():
        page.get_by_role("button", name="Save changes").click()


def test_a_failed_save_lands_on_the_summary_and_its_link_reaches_the_field(
    page: Page, live_server, error_course: Cohort
) -> None:
    member = error_course.enrollment_set.first().student
    _submit_long_display_name(page, live_server, error_course, member)

    summary = page.locator("[data-focus-error-summary]")
    expect(summary).to_be_visible()
    # The shared script moves focus to the summary: the member starts from
    # the explanation, not from the top of the page.
    expect(summary).to_be_focused()
    expect(summary).to_contain_text(OVERLONG_ERROR)

    field_error = page.locator("#id_display_name-error")
    expect(field_error).to_be_visible()
    expect(field_error).to_contain_text(OVERLONG_ERROR)
    name_input = page.locator("#id_display_name")
    expect(name_input).to_have_attribute("aria-invalid", "true")
    expect(name_input).to_have_attribute("aria-errormessage", "id_display_name-error")
    # What was typed is still in the field.
    assert name_input.input_value() == LONG_NAME
    page.screenshot(path=str(EVIDENCE / "failed-save-desktop.png"), full_page=True)

    # The summary's entry is a real control: keyboard users activate it and
    # land in the field it names.
    link = summary.get_by_role("link", name="Leaderboard name")
    link.focus()
    page.keyboard.press("Enter")
    expect(name_input).to_be_focused()

    # Fixing the value recovers: the save succeeds and the next visit of the
    # page carries the saved name and no error state.
    name_input.fill("Ada Lovelace")
    with page.expect_navigation():
        page.get_by_role("button", name="Save changes").click()
    assert _enrollment_path(error_course) not in page.url
    goto_with_retry(page, f"{live_server.url}{_enrollment_path(error_course)}")
    expect(page.locator("#id_display_name")).to_have_value("Ada Lovelace")
    expect(page.locator("[data-focus-error-summary]")).to_have_count(0)


def test_the_error_contract_holds_without_javascript(
    browser: Browser, live_server, error_course: Cohort
) -> None:
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        member = error_course.enrollment_set.first().student
        _submit_long_display_name(page, live_server, error_course, member)

        summary = page.locator("[data-focus-error-summary]")
        expect(summary).to_be_visible()
        expect(summary.get_by_role("link", name="Leaderboard name")).to_have_attribute(
            "href", "#id_display_name"
        )
        expect(page.locator("#id_display_name-error")).to_be_visible()
        expect(page.locator("#id_display_name")).to_have_attribute("aria-invalid", "true")
        assert page.locator("#id_display_name").input_value() == LONG_NAME
        page.screenshot(path=str(EVIDENCE / "failed-save-no-js.png"), full_page=True)
    finally:
        context.close()


def test_the_failed_form_stays_usable_on_a_phone(
    browser: Browser, live_server, error_course: Cohort
) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    try:
        member = error_course.enrollment_set.first().student
        _submit_long_display_name(page, live_server, error_course, member)

        summary = page.locator("[data-focus-error-summary]")
        expect(summary).to_be_visible()
        expect(page.locator("#id_display_name")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        page.screenshot(path=str(EVIDENCE / "failed-save-mobile.png"), full_page=True)
    finally:
        context.close()
