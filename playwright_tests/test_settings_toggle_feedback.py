"""A failed immediate-toggle save says so, and says what still holds.

The leaderboard visibility switches saved asynchronously, but a rejected
request only restored the previous checkbox state — no message anywhere —
so a member whose session had expired believed a public record was hidden
when it was still published (audit UX-02).  These tests drive the real
script against the real enrollment page with the network stubbed, pinning
the acceptance matrix: visible and announced failure, the previous
visibility stated in the row's own words, an ended session named as such,
reconciliation to the server's answer on success, and exactly one
in-flight request per toggle.
"""

from __future__ import annotations

import json

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Page, expect

from courses.models.cohort import Cohort, Enrollment

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]


def _member(suffix: str):
    from accounts.models import CustomUser

    email = f"toggle-feedback-{suffix}@example.invalid"
    return CustomUser.objects.create_user(username=email, email=email)


def _enrollment_page(user, *, slug: str) -> tuple[Cohort, str]:
    cohort = Cohort.objects.create(
        slug=slug,
        title="Toggle feedback course",
    )
    Enrollment.objects.create(student=user, course=cohort)
    path = reverse(
        "cohort_enrollment",
        kwargs={"course_slug": cohort.slug, "cohort_identifier": cohort.identifier},
    )
    return cohort, path


def _toggle_path(cohort: Cohort) -> str:
    return reverse(
        "cohort_update_enrollment_toggle",
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


_LEADERBOARD_ROW = 'label:has(input[name="display_on_leaderboard"])'


def _leaderboard_switch(page: Page):
    return page.locator(f"{_LEADERBOARD_ROW} input")


def _status_for(page: Page):
    # The script inserts the status region directly after the row label.
    return page.locator(f"{_LEADERBOARD_ROW} + p.toggle-status")


def test_a_rejected_privacy_save_reverts_and_says_what_still_holds(page: Page, live_server) -> None:
    member = _member("reject")
    cohort, path = _enrollment_page(member, slug="toggle-reject")
    _sign_in(page, live_server, member)
    page.goto(f"{live_server.url}{path}")

    def refuse(route):
        route.fulfill(
            status=500,
            content_type="application/json",
            body=json.dumps({"error": "Save failed."}),
        )

    page.route(f"**{_toggle_path(cohort)}", refuse)

    switch = _leaderboard_switch(page)
    expect(switch).to_be_checked()
    switch.click()

    status = _status_for(page)
    expect(status).to_be_visible()
    expect(status).to_contain_text("Your change was not saved.")
    # The privacy row states which visibility still holds: the member asked
    # to hide the record, the save failed, so "shown" is the state that
    # remains — never silently assumed otherwise.
    expect(status).to_contain_text("Your record is still shown on the public leaderboard.")
    expect(switch).to_be_checked()
    expect(switch).to_be_enabled()
    expect(switch).to_be_focused()
    expect(status).to_have_attribute("role", "status")


def test_an_ended_session_is_named_as_the_reason(page: Page, live_server) -> None:
    member = _member("session")
    cohort, path = _enrollment_page(member, slug="toggle-session")
    _sign_in(page, live_server, member)
    page.goto(f"{live_server.url}{path}")

    def forbidden(route):
        route.fulfill(
            status=403,
            content_type="application/json",
            body=json.dumps({"error": "Authentication required."}),
        )

    page.route(f"**{_toggle_path(cohort)}", forbidden)

    switch = _leaderboard_switch(page)
    switch.click()

    status = _status_for(page)
    expect(status).to_contain_text("Your session has ended")
    expect(status).to_contain_text("Sign in again")
    expect(switch).to_be_checked()
    expect(switch).to_be_enabled()


def test_a_delayed_success_reconciles_to_the_server_answer(page: Page, live_server) -> None:
    member = _member("delayed")
    cohort, path = _enrollment_page(member, slug="toggle-delayed")
    _sign_in(page, live_server, member)
    page.goto(f"{live_server.url}{path}")

    def confirm_hidden(route):
        # The server is the state of record: it answers that the record is
        # hidden, whatever the browser's optimistic flip was.
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"field": "display_on_leaderboard", "value": False}),
        )

    page.route(f"**{_toggle_path(cohort)}", confirm_hidden)

    switch = _leaderboard_switch(page)
    expect(switch).to_be_checked()

    with page.expect_response(f"**{_toggle_path(cohort)}") as _:
        switch.click()

    status = _status_for(page)
    expect(status).to_contain_text("Saved.")
    # The checkbox follows the server's answer, not the request.
    expect(switch).not_to_be_checked()
    expect(switch).to_be_enabled()


def test_exactly_one_request_is_in_flight_while_saving(page: Page, live_server) -> None:
    member = _member("inflight")
    cohort, path = _enrollment_page(member, slug="toggle-inflight")
    _sign_in(page, live_server, member)
    page.goto(f"{live_server.url}{path}")

    # Gate the real persistence step in-process (the live server shares this
    # process): the save cannot finish until the test releases it, which
    # makes the mid-flight window deterministic instead of a sleep race.  The
    # view callable itself is captured by the URLconf at import, so the gate
    # goes on the persistence function the view calls as a module global.
    import threading
    from unittest.mock import patch

    from courses.views import course_enrollment

    release = threading.Event()
    calls: list[str] = []
    original = course_enrollment.update_enrollment_toggle_value

    def gated(update):
        calls.append(update.field)
        release.wait(timeout=10)
        return original(update)

    with patch.object(course_enrollment, "update_enrollment_toggle_value", gated):
        switch = _leaderboard_switch(page)
        switch.click()

        expect(switch).to_be_disabled()
        status = _status_for(page)
        expect(status).to_contain_text("Saving")

        # While the first save is in flight the control is disabled, so the
        # browser cannot fire a second change event and the script never
        # double-posts a privacy mutation.
        switch.click(force=True)
        page.wait_for_timeout(150)
        assert len(calls) == 1

        release.set()
        expect(status).to_contain_text("Saved.")
        expect(switch).to_be_enabled()
        expect(switch).not_to_be_checked()
