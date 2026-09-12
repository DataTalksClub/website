from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import Browser, Locator, Page, Response, Route, ViewportSize, expect

from events.models import EventQnaSession
from events.qna import security, services
from test_support.design_review_data import ensure_checked_event_identity_snapshot

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]

EVIDENCE = Path(".tmp/qna-focus-preservation")
VIEWPORTS: tuple[tuple[ViewportSize, str], ...] = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)

Q1 = "How do you choose a retry budget when upstream latency changes throughout the day?"
Q2 = "Which signals distinguish a slow recovery from a stalled recovery?"
Q3 = "Can the replay boundary be moved safely after a schema migration?"
# A page whose first poll hits a transient lock recovers on its next poll;
# give the first list assertion after a load one poll interval of slack.
FIRST_LOAD = 10_000
REMOVED_MESSAGE = "A question was removed from the list."
HOST_PASSCODE = "bounded-focus-42"


@pytest.fixture
def qna_room() -> SimpleNamespace:
    event = ensure_checked_event_identity_snapshot()
    EventQnaSession.objects.create(event=event, state=EventQnaSession.State.OPEN)
    asker, _token = security.new_participant()
    for text, author in ((Q1, "Mina Okafor"), (Q2, "Jon Bell")):
        services.submit_question(event.id, text=text, author_name=author, participant=asker)
    items, _counts, _etag, _session = services.list_questions(event.id)
    ids = [item["question_id"] for item in items]
    return SimpleNamespace(
        event_id=event.id,
        path=f"{services.event_qna_path(event)}/",
        first=ids[0],
        second=ids[1],
    )


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


def focused_action(page: Page) -> dict[str, str] | None:
    value: dict[str, str] | None = page.evaluate(
        """() => {
             const el = document.activeElement;
             if (!el || el === document.body) return null;
             const row = el.closest('[data-qna-key]');
             return row
               ? { key: row.getAttribute('data-qna-key'),
                   action: el.getAttribute('data-qna-action') }
               : null;
           }"""
    )
    return value


def focused_is(page: Page, key: str, action: str) -> bool:
    # Identity check: the focused element must be the very node the keyed
    # renderer kept, not a rebuilt lookalike.
    same: bool = page.evaluate(
        """([key, action]) => {
             const button = document.querySelector(
               `[data-qna-key="${key}"] [data-qna-action="${action}"]`);
             return document.activeElement === button;
           }""",
        [key, action],
    )
    return same


def action_button(page: Page, key: str, action: str) -> Locator:
    return page.locator(f'[data-qna-key="{key}"] [data-qna-action="{action}"]')


def row_note(page: Page, key: str) -> Locator:
    return page.locator(f'[data-qna-key="{key}"] .qna-item-note')


def goto_with_retry(page: Page, url: str) -> None:
    # Under load a first request can land in the tail of the previous test's
    # database activity and come back as a transient 500; retry briefly
    # instead of timing out on a missing control.
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


def join_host(
    live_server, browser: Browser, join_url: str, viewport: ViewportSize | None = None
) -> Page:
    page = open_room(browser, f"{live_server.url}{join_url}", viewport)
    page.locator("#passcode").fill(HOST_PASSCODE)
    with page.expect_navigation():
        page.get_by_role("button", name="Continue").click()
    return page


def click_action(page: Page, key: str, action: str) -> None:
    # Wait for the mutation to reach the server so an observer's next poll
    # is guaranteed to see it. A request can land on a transient database
    # lock and come back 500; the page reverts and reports next to the
    # action, so simply try again.
    button = action_button(page, key, action)
    outcome: Response | None = None
    for attempt in range(3):
        with page.expect_response(
            lambda candidate: (
                "/questions/" in candidate.url
                and candidate.request.method in {"POST", "PATCH", "DELETE"}
            )
        ) as captured:
            button.click()
        outcome = captured.value
        if outcome.ok:
            return
        page.wait_for_timeout(300 * (attempt + 1))
    assert outcome is not None and outcome.ok, "the action never reached the server"


def submit_question_from(page: Page, text: str) -> None:
    form_field = page.locator("#qna-text")
    form_field.fill(text)
    outcome: Response | None = None
    for attempt in range(3):
        with page.expect_response(
            lambda candidate: (
                candidate.request.method == "POST"
                and candidate.url.rstrip("/").endswith("questions")
            )
        ) as captured:
            page.get_by_role("button", name="Submit question").click()
        outcome = captured.value
        if outcome.ok:
            return
        page.wait_for_timeout(300 * (attempt + 1))
    assert outcome is not None and outcome.ok, "the question never reached the server"


@pytest.mark.parametrize(("viewport", "label"), VIEWPORTS)
def test_participant_focus_survives_poll_updates(
    browser: Browser, live_server, qna_room: SimpleNamespace, viewport: ViewportSize, label: str
) -> None:
    page = open_room(browser, f"{live_server.url}{qna_room.path}", viewport)
    context = page.context
    helpers: list[Page] = []
    try:
        expect(page.locator(".qna-item")).to_have_count(2, timeout=FIRST_LOAD)

        first = action_button(page, qna_room.first, "vote")
        first.focus()
        before = {"key": qna_room.first, "action": "vote"}
        assert focused_action(page) == before

        # A different question arrives from another participant.
        other = open_room(browser, f"{live_server.url}{qna_room.path}")
        helpers.append(other)
        submit_question_from(other, Q3)
        expect(other.locator(".qna-item")).to_have_count(3, timeout=FIRST_LOAD)
        poll_now(page)
        expect(page.locator(".qna-item")).to_have_count(3)
        assert focused_action(page) == before
        assert focused_is(page, qna_room.first, "vote")

        # The focused question's score changes underneath it.
        click_action(other, qna_room.first, "vote")
        expect(action_button(other, qna_room.first, "vote")).to_have_attribute(
            "aria-pressed", "true"
        )
        poll_now(page)
        expect(first).to_contain_text("(2)")
        assert focused_action(page) == before
        assert focused_is(page, qna_room.first, "vote")

        # Sorting reorders the rows; the focused action keeps its place.
        page.locator("#qna-sort").select_option("recent")
        expect(page.locator(".qna-item").first).to_contain_text(Q3)
        assert focused_action(page) == before
        assert focused_is(page, qna_room.first, "vote")
    finally:
        for helper in helpers:
            stop_polling(helper)
            helper.context.close()
        stop_polling(page)
        context.close()


@pytest.mark.parametrize(("viewport", "label"), VIEWPORTS)
def test_participant_vote_reports_failure_next_to_the_action(
    browser: Browser, live_server, qna_room: SimpleNamespace, viewport: ViewportSize, label: str
) -> None:
    page = open_room(browser, f"{live_server.url}{qna_room.path}", viewport)
    context = page.context
    try:
        expect(page.locator(".qna-item")).to_have_count(2, timeout=FIRST_LOAD)
        first = action_button(page, qna_room.first, "vote")
        first.focus()
        on_action = {"key": qna_room.first, "action": "vote"}

        def reject(route: Route) -> None:
            page.wait_for_timeout(150)
            route.abort("connectionrefused")

        page.route("**/questions/*/vote/", reject)
        page.keyboard.press("Enter")
        expect(first).to_have_attribute("aria-pressed", "false")
        expect(first).to_contain_text("(1)")
        expect(row_note(page, qna_room.first)).not_to_have_text("")
        assert focused_action(page) == on_action
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=EVIDENCE / f"vote-rejected-{label}.png", full_page=True)

        page.unroute("**/questions/*/vote/")
        click_action(page, qna_room.first, "vote")
        page.wait_for_timeout(200)
        expect(first).to_have_attribute("aria-pressed", "true")
        expect(first).to_contain_text("(2)")
        expect(row_note(page, qna_room.first)).to_have_text("")
        assert focused_action(page) == on_action
        assert focused_is(page, qna_room.first, "vote")
    finally:
        stop_polling(page)
        context.close()


def test_participant_focus_falls_back_after_a_moderated_removal(
    browser: Browser, live_server, qna_room: SimpleNamespace
) -> None:
    invite = services.create_cohost(
        qna_room.event_id,
        name="focus-host",
        passcode=HOST_PASSCODE,
        actor_ref="review:ux-06",
    )
    page = open_room(browser, f"{live_server.url}{qna_room.path}")
    context = page.context
    helpers: list[Page] = []
    try:
        expect(page.locator(".qna-item")).to_have_count(2, timeout=FIRST_LOAD)
        action_button(page, qna_room.first, "vote").focus()

        host = join_host(live_server, browser, str(invite["join_url"]))
        helpers.append(host)
        expect(host.locator(".qna-item")).to_have_count(2, timeout=FIRST_LOAD)
        click_action(host, qna_room.first, "delete")
        expect(host.locator(".qna-item")).to_have_count(1)
        poll_now(page)
        expect(page.locator(".qna-item")).to_have_count(1)
        assert focused_action(page) == {"key": qna_room.second, "action": "vote"}
        assert focused_is(page, qna_room.second, "vote")
        expect(page.locator("#qna-live")).to_have_text(REMOVED_MESSAGE)

        click_action(host, qna_room.second, "delete")
        expect(host.locator(".qna-item")).to_have_count(0)
        poll_now(page)
        expect(page.locator(".qna-item")).to_have_count(0)
        expect(page.locator("#qna-empty")).to_be_visible()
        assert page.evaluate("document.activeElement.id") == "questions-heading"
    finally:
        for helper in helpers:
            stop_polling(helper)
            helper.context.close()
        stop_polling(page)
        context.close()


@pytest.mark.parametrize(("viewport", "label"), VIEWPORTS)
def test_host_keyboard_moderation_keeps_focus(
    browser: Browser, live_server, qna_room: SimpleNamespace, viewport: ViewportSize, label: str
) -> None:
    invite = services.create_cohost(
        qna_room.event_id,
        name="focus-host",
        passcode=HOST_PASSCODE,
        actor_ref="review:ux-06",
    )
    page = join_host(live_server, browser, str(invite["join_url"]), viewport)
    context = page.context
    helpers: list[Page] = []
    try:
        expect(page.locator(".qna-item")).to_have_count(2, timeout=FIRST_LOAD)
        on_first_status = {"key": qna_room.first, "action": "status"}

        status_first = action_button(page, qna_room.first, "status")
        status_first.focus()
        page.keyboard.press("Enter")
        expect(status_first).to_have_text("Unanswer")
        assert focused_action(page) == on_first_status
        page.keyboard.press("Enter")
        expect(status_first).to_have_text("Answer")
        assert focused_action(page) == on_first_status

        # Another host pins the focused question; the row updates in place.
        host_b = join_host(live_server, browser, str(invite["join_url"]))
        helpers.append(host_b)
        expect(host_b.locator(".qna-item")).to_have_count(2, timeout=FIRST_LOAD)
        click_action(host_b, qna_room.first, "pin")
        expect(action_button(host_b, qna_room.first, "pin")).to_have_text("Unpin")
        poll_now(page)
        expect(page.locator(f'[data-qna-key="{qna_room.first}"].is-pinned')).to_be_visible()
        assert focused_action(page) == on_first_status

        # Own delete removes the row immediately; focus falls back to the
        # first action of the remaining question.
        delete_second = action_button(page, qna_room.second, "delete")
        delete_second.focus()
        page.keyboard.press("Enter")
        expect(page.locator(".qna-item")).to_have_count(1)
        assert focused_action(page) == on_first_status
        expect(page.locator("#qna-live")).to_have_text(REMOVED_MESSAGE)

        # Another host's deletion of the last question lands focus on the
        # documented heading fallback.
        click_action(host_b, qna_room.first, "delete")
        expect(host_b.locator(".qna-item")).to_have_count(0)
        poll_now(page)
        expect(page.locator(".qna-item")).to_have_count(0)
        assert page.evaluate("document.activeElement.id") == "moderation-heading"
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=EVIDENCE / f"host-empty-{label}.png", full_page=True)
    finally:
        for helper in helpers:
            stop_polling(helper)
            helper.context.close()
        stop_polling(page)
        context.close()
