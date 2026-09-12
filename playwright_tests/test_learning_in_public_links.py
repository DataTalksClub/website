"""The "Add link" script builds rows a screen reader can follow.

The script once appended bare ``<input type="url">`` elements with no id or
label, moved no focus, and left the button clickable but inert once the cap
was reached (audit UX-04).  These tests drive the real homework page: each
click must append a row cloned from the server-rendered template -- indexed
id, sr-only label, shared hint -- and move focus to it; the click that
reaches the cap must disable the button and reveal the visible cap note; and
a client-side invalid URL must be announced through a stable error id wired
via aria-errormessage.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Page, expect

from accounts.models import CustomUser
from courses.models import Cohort, Homework, HomeworkState

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

CAP = 3


def _homework_setup() -> None:
    course = Cohort.objects.create(
        slug="liptest-cohort",
        title="Learning in Public Zoomcamp",
    )
    Homework.objects.create(
        course=course,
        title="LiP Homework",
        description="Homework with learning in public links.",
        due_date=timezone.now() + timezone.timedelta(days=7),
        state=HomeworkState.OPEN.value,
        slug="liptest-homework",
        learning_in_public_cap=CAP,
    )


def _homework_path() -> str:
    # Cohort.save() derives the family slug from the cohort slug and the
    # identifier from the year, so the live path is /courses/liptest-cohort/
    # cohorts/2026/homework/liptest-homework/.
    return reverse(
        "cohort_homework",
        kwargs={
            "course_slug": "liptest-cohort",
            "cohort_identifier": "2026",
            "homework_slug": "liptest-homework",
        },
    )


def _member() -> CustomUser:
    email = "liptest-member@example.invalid"
    return CustomUser.objects.create_user(username=email, email=email, password="liptest-pass")


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


def _open_homework_page(page: Page, live_server) -> None:
    _homework_setup()
    _sign_in(page, live_server, _member())
    page.goto(f"{live_server.url}{_homework_path()}")
    expect(page.locator("#learning-in-public-link-1")).to_have_count(1)


def test_add_appends_labelled_focused_rows_until_the_cap(page: Page, live_server) -> None:
    _open_homework_page(page, live_server)

    first_add = page.locator("#add-learning-public-link")
    expect(first_add).to_be_enabled()
    # Below the cap the note is served hidden and stays hidden.
    expect(page.locator("#learning-in-public-cap-note")).to_be_hidden()

    first_add.click()
    second_row = page.locator("#learning-in-public-link-2")
    expect(second_row).to_have_count(1)
    expect(second_row).to_be_focused()
    expect(second_row).to_have_attribute("aria-describedby", "learning-in-public-hint")
    label = page.locator("label[for='learning-in-public-link-2']")
    expect(label).to_contain_text("Learning in public link 2")

    first_add.click()
    third_row = page.locator("#learning-in-public-link-3")
    expect(third_row).to_have_count(1)
    expect(third_row).to_be_focused()

    # The click that reaches the cap disables the button and reveals the
    # note, so the control stops promising a row it will not add.
    expect(first_add).to_be_disabled()
    note = page.locator("#learning-in-public-cap-note")
    expect(note).to_be_visible()
    expect(note).to_contain_text("maximum of 3")


def test_invalid_url_error_is_wired_through_a_stable_error_id(page: Page, live_server) -> None:
    _open_homework_page(page, live_server)

    row = page.locator("#learning-in-public-link-1")
    row.fill("not-a-url")
    page.locator("#submit-button").click()

    # The client-side validation keeps the user on the page and names the
    # failing row through aria-invalid plus a stable aria-errormessage id.
    expect(row).to_have_class("field-input form-control is-invalid")
    expect(row).to_have_attribute("aria-invalid", "true")
    expect(row).to_have_attribute("aria-errormessage", "learning-in-public-link-1-error")
    feedback = page.locator("#learning-in-public-link-1-error")
    expect(feedback).to_be_visible()
    expect(feedback).to_contain_text("must start with http:// or https://")
    expect(page).to_have_url(f"{live_server.url}{_homework_path()}")
