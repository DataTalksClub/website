from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, Route, expect

from events.models import EventQnaSession
from events.qna import security, services
from test_support.design_review_data import ensure_checked_event_identity_snapshot

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]

EVIDENCE = Path(".tmp/qna-rejected-submission")
EXISTING_QUESTION = (
    "How do you choose a retry budget when upstream latency changes throughout the day?"
)
DRAFT = "Will this session be recorded for attendees in other time zones?"
VIEWPORTS = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)


@pytest.fixture
def qna_room_path() -> str:
    event = ensure_checked_event_identity_snapshot()
    EventQnaSession.objects.create(event=event, state=EventQnaSession.State.OPEN)
    participant, _token = security.new_participant()
    services.submit_question(
        event.id,
        text=EXISTING_QUESTION,
        author_name="Mina Okafor",
        participant=participant,
    )
    return f"{services.event_qna_path(event)}/"


def _reject_post(page: Page) -> list[str]:
    calls: list[str] = []

    def handler(route: Route) -> None:
        calls.append(route.request.method)
        page.wait_for_timeout(250)
        route.abort("connectionrefused")

    page.route("**/api/questions/", handler)
    return calls


@pytest.mark.parametrize(("viewport", "label"), VIEWPORTS)
def test_rejected_question_keeps_visible_list_and_draft(
    browser: Browser,
    live_server,
    qna_room_path: str,
    viewport: dict[str, int],
    label: str,
) -> None:
    context = browser.new_context(viewport=viewport)
    page = context.new_page()
    try:
        response = page.goto(
            f"{live_server.url}{qna_room_path}", wait_until="domcontentloaded"
        )
        assert response is not None and response.status == 200
        expect(page.locator(".qna-item")).to_have_count(1)
        expect(page.locator(".qna-item")).to_contain_text(EXISTING_QUESTION)

        calls = _reject_post(page)
        form = page.locator("#qna-question-form")
        form.locator("#qna-text").fill(DRAFT)
        submit = form.get_by_role("button", name="Submit question")
        submit.click()

        expect(submit).to_be_disabled()
        expect(page.locator("#qna-form-error")).to_have_text(re.compile(r"\S+"))

        expect(page.locator(".qna-item")).to_have_count(1)
        expect(page.locator(".qna-item")).to_contain_text(EXISTING_QUESTION)
        expect(page.locator(".qna-item")).not_to_contain_text(DRAFT)
        expect(form.locator("#qna-text")).to_have_value(DRAFT)
        expect(submit).to_be_enabled()
        assert calls == ["POST"]
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=EVIDENCE / f"rejected-{label}.png", full_page=True)

        # With the request gone, the next poll is an unchanged 304: the room
        # must already be showing the authoritative list, not depend on the
        # poll to repair itself.
        page.unroute("**/api/questions/")
        page.locator("#qna-sort").select_option("recent")
        expect(page.locator(".qna-item")).to_have_count(1)
        expect(page.locator(".qna-item")).to_contain_text(EXISTING_QUESTION)
        expect(page.locator(".qna-item")).not_to_contain_text(DRAFT)
    finally:
        context.close()


def test_accepted_question_replaces_draft_and_pending_card(
    browser: Browser, live_server, qna_room_path: str
) -> None:
    context = browser.new_context()
    page = context.new_page()
    try:
        response = page.goto(
            f"{live_server.url}{qna_room_path}", wait_until="domcontentloaded"
        )
        assert response is not None and response.status == 200
        expect(page.locator(".qna-item")).to_have_count(1)

        form = page.locator("#qna-question-form")
        form.locator("#qna-text").fill(DRAFT)
        submit = form.get_by_role("button", name="Submit question")
        submit.click()

        expect(page.locator(".qna-item")).to_have_count(2)
        expect(page.locator(".qna-item").last).to_contain_text(DRAFT)
        expect(page.locator(".qna-item.is-pending")).to_have_count(0)
        expect(form.locator("#qna-text")).to_have_value("")
        expect(page.locator("#qna-form-error")).to_have_text("")
        expect(submit).to_be_enabled()
    finally:
        context.close()
