from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Browser, expect

from content.public_data import public_projection
from events.identity import load_identity_manifest
from events.models import EventQnaSession
from events.qna import security, services
from jobs.models import DurableJob
from test_support.design_review_data import ensure_checked_event_identity_snapshot
from test_support.reference_data import EVENT_IDENTITY_MANIFEST

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]

EVIDENCE = Path(".tmp/adversarial-design-review/iteration-0/qna")
VIEWPORTS = (
    ({"width": 1440, "height": 900}, "light", "desktop"),
    ({"width": 390, "height": 844}, "light", "mobile"),
    ({"width": 640, "height": 720}, "light", "zoom-200"),
    ({"width": 390, "height": 844}, "dark", "mobile-dark"),
)


@pytest.fixture
def qna_review_paths() -> tuple[str, str]:
    event = ensure_checked_event_identity_snapshot()
    EventQnaSession.objects.create(event=event, state=EventQnaSession.State.OPEN)
    for text, author in (
        (
            "How do you choose a retry budget when upstream latency changes throughout the day?",
            "Mina Okafor",
        ),
        ("Which signals distinguish a slow recovery from a stalled recovery?", "Jon Bell"),
        ("Can the replay boundary be moved safely after a schema migration?", ""),
    ):
        participant, _token = security.new_participant()
        services.submit_question(
            event.id,
            text=text,
            author_name=author,
            participant=participant,
        )
    invite = services.create_cohost(
        event.id,
        name="review-host",
        passcode="bounded-review-42",
        actor_ref="review:issue-237",
    )
    return f"{services.event_qna_path(event)}/", str(invite["join_url"])


@pytest.mark.parametrize(("viewport", "color_scheme", "label"), VIEWPORTS)
def test_qna_participant_cohost_and_error_shells(
    browser: Browser,
    live_server,
    qna_review_paths: tuple[str, str],
    viewport: dict[str, int],
    color_scheme: str,
    label: str,
) -> None:
    participant_path, cohost_path = qna_review_paths
    manifest = load_identity_manifest(EVENT_IDENTITY_MANIFEST)
    projection = public_projection()
    assert DurableJob.objects.count() == 0
    assert {(event["identity_id"], event["public_path"]) for event in projection["events"]} == {
        (str(item.id), item.canonical_path) for item in manifest.events
    }
    context = browser.new_context(viewport=viewport, color_scheme=color_scheme)
    page = context.new_page()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    try:
        participant = page.goto(
            f"{live_server.url}{participant_path}", wait_until="domcontentloaded"
        )
        assert participant is not None and participant.status == 200
        expect(page.get_by_role("heading", name="Event Q&A")).to_be_visible()
        expect(page.locator(".qna-item")).to_have_count(3)
        event_link = page.locator(".qna-kicker a")
        event_link_box = event_link.bounding_box()
        assert event_link_box is not None and event_link_box["height"] >= 44
        detail = page.request.get(f"{live_server.url}{event_link.get_attribute('href')}")
        assert detail.status == 200
        for target in page.locator("button, textarea, input, select").all():
            box = target.bounding_box()
            assert box is not None and box["height"] >= 44
        sort = page.locator("#qna-sort")
        page.keyboard.press("Tab")
        sort.focus()
        assert sort.evaluate("element => getComputedStyle(element).outlineWidth") == "3px"
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        page.screenshot(path=EVIDENCE / f"participant-{label}.png", full_page=True)

        gate = page.goto(f"{live_server.url}{cohost_path}", wait_until="domcontentloaded")
        assert gate is not None and gate.status == 200
        expect(page.get_by_role("heading", name="Enter co-host passcode")).to_be_visible()
        passcode = page.locator("#passcode")
        button = page.get_by_role("button", name="Continue")
        for target in (passcode, button):
            box = target.bounding_box()
            assert box is not None and box["height"] >= 44
        button_colors = button.evaluate(
            "element => { const style = getComputedStyle(element); "
            "return [style.color, style.backgroundColor]; }"
        )
        if color_scheme == "dark":
            assert button_colors == ["rgb(23, 21, 27)", "rgb(183, 173, 255)"]
        page.screenshot(path=EVIDENCE / f"cohost-{label}.png", full_page=True)

        passcode.fill("incorrect-review-code")
        with page.expect_navigation() as navigation:
            button.click()
        denied = navigation.value
        assert denied is not None and denied.status == 403
        expect(page.get_by_role("alert")).to_contain_text("do not match")
        page.evaluate("scrollTo(0, 0)")
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        page.screenshot(path=EVIDENCE / f"cohost-error-{label}.png", full_page=True)
    finally:
        context.close()
