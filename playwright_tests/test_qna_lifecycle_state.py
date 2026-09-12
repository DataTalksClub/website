"""UX-07 acceptance: the Q&A room's controls follow the session lifecycle.

A closed session starts read-only, a close that changes no question still
reaches an open room (the ETag covers the session state), a reopen
restores the controls, the configured default sort is honoured until the
participant picks their own, and connection trouble reports separately
from the lifecycle banner.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import Browser, Page, Response, ViewportSize, expect

from events.models import EventQnaSession
from events.qna import security, services
from test_support.design_review_data import ensure_checked_event_identity_snapshot

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]

EVIDENCE = Path(".tmp/qna-lifecycle-state")
VIEWPORTS: tuple[tuple[ViewportSize, str], ...] = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)
Q1 = "Does the cache stampede protection hold under a cold start?"
Q2 = "What measured the retry storm best in the incident review?"
FIRST_LOAD = 10_000


@pytest.fixture
def qna_event() -> SimpleNamespace:
    event = ensure_checked_event_identity_snapshot()
    return SimpleNamespace(
        event_id=event.id,
        path=f"{services.event_qna_path(event)}/",
    )


def make_session(event_id, **kwargs) -> None:
    EventQnaSession.objects.create(event_id=event_id, **kwargs)


def poll_now(page: Page) -> None:
    # The Q&A scripts poll on a timer; the visibilitychange listener runs an
    # immediate poll, which keeps these tests off real-time waits.
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")


def stop_polling(page: Page) -> None:
    # Kill the recurring poll timers before a page goes away: a straggler
    # poll holding the sqlite lock while the live server shuts down leaks
    # into the next test as spurious "database is locked" failures.
    page.evaluate(
        "() => { const top = window.setTimeout(function () {}, 0);"
        " for (let i = 0; i <= top; i++) window.clearTimeout(i); }"
    )
    page.wait_for_timeout(150)


def goto_with_retry(page: Page, url: str) -> None:
    response: Response | None = None
    for attempt in range(3):
        response = page.goto(url, wait_until="domcontentloaded")
        if response is not None and response.status == 200:
            return
        page.wait_for_timeout(300 * (attempt + 1))
    assert response is not None and response.status == 200, f"page never loaded cleanly: {url}"


def open_room(browser: Browser, url: str, viewport: ViewportSize | None = None) -> Page:
    if viewport is None:
        context = browser.new_context()
    else:
        context = browser.new_context(viewport=viewport)
    page = context.new_page()
    goto_with_retry(page, url)
    return page


@pytest.mark.parametrize(("viewport", "label"), VIEWPORTS)
def test_closed_room_starts_readonly(
    browser: Browser, live_server, qna_event: SimpleNamespace, viewport: ViewportSize, label: str
) -> None:
    make_session(qna_event.event_id, state=EventQnaSession.State.OPEN)
    participant, _token = security.new_participant()
    services.submit_question(
        qna_event.event_id, text=Q1, author_name="Ada Lovelace", participant=participant
    )
    services.transition_session(qna_event.event_id, EventQnaSession.State.CLOSED)
    page = open_room(browser, f"{live_server.url}{qna_event.path}", viewport)
    try:
        expect(page.locator("#qna-banner")).to_have_text("Closed")
        expect(page.locator("#qna-question-form")).to_be_hidden()
        expect(page.locator("#qna-ask-closed")).to_be_visible()
        expect(page.locator(".qna-item")).to_have_count(1, timeout=FIRST_LOAD)
        # Questions stay readable; the vote affordance is gone entirely.
        expect(page.locator(".qna-question-text")).to_have_text(Q1)
        expect(page.locator(".qna-vote")).to_have_count(0)
        page.screenshot(path=str(EVIDENCE / f"closed-room-{label}.png"), full_page=True)
    finally:
        stop_polling(page)
        page.context.close()


def test_close_reaches_an_open_room_without_question_changes(
    browser: Browser, live_server, qna_event: SimpleNamespace
) -> None:
    make_session(qna_event.event_id, state=EventQnaSession.State.OPEN)
    participant, _token = security.new_participant()
    services.submit_question(qna_event.event_id, text=Q1, participant=participant)
    page = open_room(browser, f"{live_server.url}{qna_event.path}")
    try:
        expect(page.locator(".qna-item")).to_have_count(1, timeout=FIRST_LOAD)
        expect(page.locator(".qna-vote")).to_have_count(1)

        # The host closes the session; no question changes afterwards, so
        # before the UX-07 ETag fix this poll answered 304 and the room
        # kept showing an open, votable session.
        services.transition_session(qna_event.event_id, EventQnaSession.State.CLOSED)
        poll_now(page)
        expect(page.locator("#qna-banner")).to_have_text("Closed")
        expect(page.locator("#qna-question-form")).to_be_hidden()
        expect(page.locator("#qna-ask-closed")).to_be_visible()
        expect(page.locator(".qna-vote")).to_have_count(0)
        expect(page.locator(".qna-item")).to_have_count(1)
        page.screenshot(path=str(EVIDENCE / "closed-mid-session.png"), full_page=True)

        # A reopen restores the controls from the same poll channel.
        services.transition_session(qna_event.event_id, EventQnaSession.State.OPEN)
        poll_now(page)
        expect(page.locator("#qna-banner")).to_have_text("Open")
        expect(page.locator("#qna-question-form")).to_be_visible()
        expect(page.locator("#qna-ask-closed")).to_be_hidden()
        expect(page.locator(".qna-vote")).to_have_count(1)
        page.screenshot(path=str(EVIDENCE / "reopened-room.png"), full_page=True)
    finally:
        stop_polling(page)
        page.context.close()


def test_configured_default_sort_holds_until_the_participant_chooses(
    browser: Browser, live_server, qna_event: SimpleNamespace
) -> None:
    make_session(
        qna_event.event_id,
        state=EventQnaSession.State.OPEN,
        default_sort=EventQnaSession.DefaultSort.RECENT,
    )
    participant, _token = security.new_participant()
    services.submit_question(qna_event.event_id, text=Q1, participant=participant)
    services.submit_question(qna_event.event_id, text=Q2, participant=participant)
    page = open_room(browser, f"{live_server.url}{qna_event.path}")
    try:
        expect(page.locator(".qna-item")).to_have_count(2, timeout=FIRST_LOAD)
        # Recent is the configured default: selected, used, newest first.
        expect(page.locator("#qna-sort")).to_have_value("recent")
        expect(page.locator(".qna-question-text").first).to_have_text(Q2)

        # An explicit user choice takes over the poll parameter and is not
        # reset by later polls; equal scores fall back to oldest first.
        with page.expect_response(lambda r: "sort=popular" in r.url) as _captured:
            page.locator("#qna-sort").select_option("popular")
        poll_now(page)
        expect(page.locator("#qna-sort")).to_have_value("popular")
        expect(page.locator(".qna-question-text").first).to_have_text(Q1)
        page.screenshot(path=str(EVIDENCE / "user-chosen-sort.png"), full_page=True)
    finally:
        stop_polling(page)
        page.context.close()


def test_connection_loss_reports_separately_and_recovers(
    browser: Browser, live_server, qna_event: SimpleNamespace
) -> None:
    make_session(qna_event.event_id, state=EventQnaSession.State.OPEN)
    participant, _token = security.new_participant()
    services.submit_question(qna_event.event_id, text=Q1, participant=participant)
    page = open_room(browser, f"{live_server.url}{qna_event.path}")
    try:
        expect(page.locator(".qna-item")).to_have_count(1, timeout=FIRST_LOAD)

        page.context.route("**/qna/api/questions/**", lambda route: route.abort())
        poll_now(page)
        expect(page.locator("#qna-connection")).to_be_visible()
        expect(page.locator("#qna-connection")).to_contain_text(re.compile(r"\S+"))
        # The lifecycle banner is untouched by network trouble.
        expect(page.locator("#qna-banner")).to_have_text("Open")
        page.screenshot(path=str(EVIDENCE / "connection-lost.png"), full_page=True)

        page.context.unroute("**/qna/api/questions/**")
        poll_now(page)
        expect(page.locator("#qna-connection")).to_have_text("Connection restored.")
        expect(page.locator("#qna-banner")).to_have_text("Open")
    finally:
        stop_polling(page)
        page.context.close()
