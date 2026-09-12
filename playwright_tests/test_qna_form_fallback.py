"""UX-08 acceptance: the ask form never leaks authored text into a URL.

With the Q&A scripts disabled, missing, or crashed, the form's native
POST is handled by the participant page view (same service, same rate
limits): redirect after success, safe re-render with the draft preserved
on failure, and no authored content in any URL. With the scripts running,
the JSON API path stays navigation-free.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import Browser, Page, ViewportSize, expect

from events.models import EventQnaSession
from events.qna import services
from test_support.design_review_data import ensure_checked_event_identity_snapshot

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]

EVIDENCE = Path(".tmp/qna-form-fallback")
VIEWPORTS: tuple[tuple[ViewportSize, str], ...] = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)
QUESTION = "What breaks first when the fallback form carries the question?"
NAME = "Progressive Enhancement"


@pytest.fixture
def qna_event() -> SimpleNamespace:
    event = ensure_checked_event_identity_snapshot()
    EventQnaSession.objects.create(event=event, state=EventQnaSession.State.OPEN)
    return SimpleNamespace(
        event_id=event.id,
        path=f"{services.event_qna_path(event)}/",
        url=lambda live_server: f"{live_server.url}{services.event_qna_path(event)}/",
    )


def submitted_count(event_id) -> int:
    items, _counts, _etag, _session = services.list_questions(event_id)
    return len(items)


def ask(page: Page, text: str, name: str = "") -> None:
    page.locator("#qna-text").fill(text)
    if name:
        page.locator("#qna-name").fill(name)
    with page.expect_navigation():
        page.get_by_role("button", name="Submit question").click()


def assert_clean_url(page: Page, live_server, qna_event: SimpleNamespace) -> None:
    # The authored text and name live in the POST body only — never in the
    # address bar, history, or logs that record URLs.
    assert page.url == qna_event.url(live_server), page.url
    assert "?" not in page.url


@pytest.mark.parametrize(("viewport", "label"), VIEWPORTS)
def test_no_javascript_native_post_round_trip(
    browser: Browser, live_server, qna_event: SimpleNamespace, viewport: ViewportSize, label: str
) -> None:
    context = browser.new_context(viewport=viewport, java_script_enabled=False)
    page = context.new_page()
    page.goto(qna_event.url(live_server), wait_until="domcontentloaded")
    ask(page, QUESTION, NAME)
    try:
        assert_clean_url(page, live_server, qna_event)
        assert submitted_count(qna_event.event_id) == 1
        page.screenshot(path=str(EVIDENCE / f"no-js-after-submit-{label}.png"), full_page=True)
    finally:
        context.close()


def test_script_404_falls_back_to_native_post(
    browser: Browser, live_server, qna_event: SimpleNamespace
) -> None:
    context = browser.new_context()
    context.route("**/qna/room.js", lambda route: route.abort())
    page = context.new_page()
    page.goto(qna_event.url(live_server), wait_until="domcontentloaded")
    ask(page, QUESTION, NAME)
    try:
        assert_clean_url(page, live_server, qna_event)
        assert submitted_count(qna_event.event_id) == 1
    finally:
        context.close()


def test_initialization_exception_falls_back_to_native_post(
    browser: Browser, live_server, qna_event: SimpleNamespace
) -> None:
    context = browser.new_context()
    context.route(
        "**/qna/room.js",
        lambda route: route.fulfill(
            body='throw new Error("synthetic UX-08 initialization crash");',
            content_type="application/javascript",
        ),
    )
    page = context.new_page()
    page.goto(qna_event.url(live_server), wait_until="domcontentloaded")
    ask(page, QUESTION, NAME)
    try:
        assert_clean_url(page, live_server, qna_event)
        assert submitted_count(qna_event.event_id) == 1
    finally:
        context.close()


def test_running_scripts_keep_the_json_path_navigation_free(
    browser: Browser, live_server, qna_event: SimpleNamespace
) -> None:
    page = browser.new_context().new_page()
    page.goto(qna_event.url(live_server), wait_until="domcontentloaded")
    try:
        page.locator("#qna-text").fill(QUESTION)
        with page.expect_response(
            lambda r: r.request.method == "POST" and "/api/questions" in r.url
        ):
            page.get_by_role("button", name="Submit question").click()
        # No navigation happened at all — the URL was never at risk.
        assert_clean_url(page, live_server, qna_event)
        expect(page.locator(".qna-item")).to_have_count(1)
        expect(page.locator(".qna-question-text")).to_have_text(QUESTION)
        assert submitted_count(qna_event.event_id) == 1
    finally:
        page.evaluate(
            "() => { const top = window.setTimeout(function () {}, 0);"
            " for (let i = 0; i <= top; i++) window.clearTimeout(i); }"
        )
        page.context.close()
