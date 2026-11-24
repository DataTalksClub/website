"""The Academy's forms, in a browser, against a mocked signup backend.

The pages are the synthetic Academy tree published through the real parsers;
the signup backend is a Playwright route, so nothing leaves the machine and no
email is sent.  Each scenario checks the exact request body the page sends --
the contract the existing backend reads -- and the visible state after it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import pytest
from playwright.sync_api import Page, Route, expect

pytestmark = [pytest.mark.full, pytest.mark.django_db(transaction=True)]

API = "https://augldvp3omxdxz3irqsmmxc25e0blpxx.lambda-url.eu-west-1.on.aws"
VIEWPORTS = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)
SCREENSHOTS = Path(".tmp/screenshots/academy/browser")
CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "content-type",
    "access-control-allow-methods": "GET, POST, OPTIONS",
}


@pytest.fixture
def academy(live_server):
    from academy.tests.content import publish

    publish()
    # The synthetic sessions run 21 to 23 November 2026; pin "now" before them
    # so every session is still offered (an ended one leaves the form).  The
    # live server answers in this process, so the patch reaches it.
    before = datetime(2026, 11, 1, tzinfo=UTC)
    with mock.patch("academy.presentation.timezone.now", return_value=before):
        yield live_server


def backend(page: Page, responses: dict[str, dict]) -> list[dict]:
    """Route the signup backend; return the recorded requests."""

    calls: list[dict] = []

    def handle(route: Route) -> None:
        request = route.request
        if request.method == "OPTIONS":
            route.fulfill(status=204, headers=CORS)
            return
        path = request.url.removeprefix(API).split("?")[0]
        calls.append(
            {
                "method": request.method,
                "path": path,
                "url": request.url,
                "body": json.loads(request.post_data) if request.post_data else None,
            }
        )
        route.fulfill(
            status=200,
            content_type="application/json",
            headers=CORS,
            body=json.dumps(responses.get(path, {"ok": True})),
        )

    page.route(f"{API}/**", handle)
    return calls


def no_overflow(page: Page) -> None:
    width = page.evaluate("document.documentElement.scrollWidth")
    assert width <= page.viewport_size["width"] + 1


def shot(page: Page, name: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / f"{name}.png", full_page=True)


ACADEMY_PAGES = (
    "/academy/",
    "/academy/agentic-engineering/",
    "/academy/ai-engineering/",
    "/academy/sessions/",
    "/academy/bildungsgutschein/",
    "/academy/faq/",
    "/academy/privacy/",
    "/academy/impressum/",
    "/academy/confirm/",
    "/academy/sessions/materials/?session=1",
)
#: German compounds and the funding line are the long words a narrow phone meets.
LONG_GERMAN_LINE = (
    "FÖRDERUNG PER BILDUNGSGUTSCHEIN (AZAV) IN KÜRZE, AUSSERDEM WEITERBILDUNGSBERATUNG."
)


@pytest.mark.parametrize("width", (320, 360, 390))
def test_no_academy_page_scrolls_sideways_on_a_narrow_phone(
    page: Page, live_server, width: int
) -> None:
    from academy.tests.content import PAGES, academy_tree, pages_copy, publish

    pages = pages_copy()
    pages["bildungsgutschein"][0]["hero"]["funding_line"] = LONG_GERMAN_LINE
    publish(academy_tree(pages=pages))
    assert PAGES["bildungsgutschein"][0]["hero"]["funding_line"] != LONG_GERMAN_LINE
    backend(page, {})
    page.set_viewport_size({"width": width, "height": 800})
    for path in ACADEMY_PAGES:
        response = page.goto(f"{live_server.url}{path}")
        assert response is not None and response.status == 200, path
        width_used = page.evaluate("document.documentElement.scrollWidth")
        assert width_used <= width, f"{path} is {width_used - width}px wider than {width}px"


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_waitlist_signup_and_quick_questions(
    page: Page, academy, viewport: dict[str, int], suffix: str
) -> None:
    page.set_viewport_size(viewport)
    calls = backend(page, {})
    page.goto(f"{academy.url}/academy/?utm_source=synthetic&utm_campaign=test")
    expect(page.get_by_role("heading", level=1)).to_have_text("Synthetic home headline")
    no_overflow(page)
    card = page.locator("#signup-hero")
    card.get_by_label("Synthetic program").select_option("ai-engineering")
    card.get_by_label("Synthetic name").fill("Synthetic Person")
    card.get_by_label("Synthetic email").fill("synthetic@example.invalid")
    card.get_by_role("button", name="Synthetic join").click()
    expect(card.get_by_text("Synthetic payment error.")).to_be_visible()
    assert calls == []
    card.get_by_label("Synthetic payment").select_option("Self-funded")
    card.get_by_role("button", name="Synthetic join").click()
    expect(card.get_by_role("heading", name="Synthetic confirm your email")).to_be_visible()
    assert len(calls) == 1
    body = calls[0]["body"]
    assert calls[0]["path"] == "/signup"
    assert list(body) == [
        "email",
        "name",
        "track",
        "payment_plan",
        "utm_source",
        "utm_campaign",
        "referrer",
        "page",
        "lang",
        "consent_version",
        "website",
    ]
    assert body["email"] == "synthetic@example.invalid"
    assert body["name"] == "Synthetic Person"
    assert body["track"] == "ai-engineering"
    assert body["payment_plan"] == "Self-funded"
    assert body["utm_source"] == "synthetic"
    assert body["page"] == "/"
    assert body["consent_version"] == "2026-11-13.v1"
    assert body["website"] == ""
    survey = card.get_by_role("link", name="Synthetic survey").get_attribute("href")
    assert survey == (
        "https://example.invalid/survey?usp=pp_url&entry.1984241375=A.%20AI%20Engineering"
        "&entry.1840392648=synthetic%40example.invalid"
    )
    chip = card.get_by_role("button", name="Option native")
    chip.click()
    expect(chip).to_have_attribute("aria-pressed", "true")
    assert calls[1]["body"] == {
        "email": "synthetic@example.invalid",
        "track": "ai-engineering",
        "answers": {"german_level": "native"},
    }
    # Campaign parameters follow the reader to the other Academy pages.
    link = page.get_by_role("link", name="Read about Agentic Engineering")
    assert "utm_source=synthetic" in (link.get_attribute("href") or "")
    no_overflow(page)
    shot(page, f"home-signed-up-{suffix}")


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_program_page_failure_then_success(
    page: Page, academy, viewport: dict[str, int], suffix: str
) -> None:
    page.set_viewport_size(viewport)
    calls = backend(page, {"/signup": {"ok": False}})
    page.goto(f"{academy.url}/academy/agentic-engineering/")
    no_overflow(page)
    # The page's one waitlist form, in the closing panel.
    card = page.locator("#signup-hero")
    card.get_by_label("Synthetic payment").select_option("Bildungsgutschein")
    card.get_by_label("Synthetic email").fill("not-an-address")
    card.get_by_role("button", name="Synthetic join").click()
    expect(card.get_by_text("Synthetic email error.")).to_be_visible()
    card.get_by_label("Synthetic email").fill("synthetic@example.invalid")
    card.get_by_role("button", name="Synthetic join").click()
    expect(card.get_by_role("alert").filter(has_text="Synthetic failure")).to_be_visible()
    assert calls[-1]["body"]["track"] == "agentic-engineering"
    assert calls[-1]["body"]["page"] == "/agentic-engineering/"
    page.unroute(f"{API}/**")
    backend(page, {})
    card.get_by_role("button", name="Synthetic join").click()
    expect(card.get_by_role("heading", name="Synthetic confirm your email")).to_be_visible()
    # The confirmation warning shows only now, after submitting.
    expect(card.locator("[data-success] [data-confirm-notice]")).to_be_visible()
    shot(page, f"program-signed-up-{suffix}")


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_session_registration(page: Page, academy, viewport: dict[str, int], suffix: str) -> None:
    page.set_viewport_size(viewport)
    calls = backend(page, {})
    page.goto(f"{academy.url}/academy/sessions/?utm_source=synthetic")
    no_overflow(page)
    form = page.locator("#register")
    form.get_by_label("Synthetic session email").fill("synthetic@example.invalid")
    form.get_by_label("Synthetic first name").fill("Synthetic")
    # No session is picked by default, and the form needs at least one.
    for number in (1, 2, 3):
        expect(form.get_by_label(f"Synthetic session {number}")).not_to_be_checked()
    form.get_by_role("button", name="Synthetic register").click()
    expect(form.get_by_role("alert").filter(has_text="Synthetic pick one.")).to_be_visible()
    assert calls == []
    form.get_by_label("Synthetic session 1").check()
    form.get_by_label("Synthetic session 3").check()
    form.get_by_label("Option ai", exact=True).check()
    form.get_by_label("Option job_hunting").check()
    form.get_by_label("Synthetic years").fill("7")
    form.get_by_label("Option outside").check()
    form.get_by_label("Option dont_know").check()
    form.get_by_label("Synthetic learn?").fill("Synthetic wish")
    form.get_by_label("Synthetic opt in").check()
    form.get_by_role("button", name="Synthetic register").click()
    expect(form.get_by_role("heading", name="Synthetic you are in")).to_be_visible()
    body = calls[0]["body"]
    assert list(body) == [
        "source",
        "form",
        "email",
        "name",
        "status",
        "academy_optin",
        "utm_source",
        "referrer",
        "page",
        "lang",
        "consent_version",
        "website",
        "sessions",
        "track_interest",
        "years_dev",
        "based_in",
        "bildungsgutschein",
        "free_text",
    ]
    assert body["source"] == "layoff_proof"
    assert body["form"] == "main"
    assert body["sessions"] == ["session-1", "session-3"]
    assert body["track_interest"] == "ai"
    assert body["status"] == "job_hunting"
    assert body["academy_optin"] == "yes"
    assert body["years_dev"] == "7"
    assert body["based_in"] == "outside"
    assert body["bildungsgutschein"] == "dont_know"
    assert body["free_text"] == "Synthetic wish"
    assert body["page"] == "/sessions/"
    expect(form.get_by_role("link", name="Synthetic session 1")).to_be_visible()
    expect(form.get_by_role("link", name="Synthetic session 2")).to_be_hidden()
    expect(form.get_by_role("link", name="Synthetic all sessions")).to_be_visible()
    shot(page, f"sessions-registered-{suffix}")


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_session_materials_from_the_qr_code(
    page: Page, academy, viewport: dict[str, int], suffix: str
) -> None:
    page.set_viewport_size(viewport)
    calls = backend(page, {})
    page.goto(f"{academy.url}/academy/sessions/materials/?session=2&utm_medium=qr")
    no_overflow(page)
    form = page.locator("#register")
    form.get_by_label("Synthetic session email").fill("synthetic@example.invalid")
    form.get_by_label("Option other").check()
    form.get_by_role("button", name="Synthetic send recording").click()
    expect(form.get_by_role("heading", name="Synthetic materials success.")).to_be_visible()
    body = calls[0]["body"]
    assert body["form"] == "qr"
    assert body["session"] == "session-2"
    assert body["status"] == "other"
    assert body["utm_medium"] == "qr"
    assert "sessions" not in body
    shot(page, f"materials-sent-{suffix}")


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_double_opt_in_confirmation(
    page: Page, academy, viewport: dict[str, int], suffix: str
) -> None:
    page.set_viewport_size(viewport)
    calls = backend(
        page,
        {"/confirm": {"ok": True, "track": "ai-engineering", "title": "Synthetic done"}},
    )
    response = page.goto(f"{academy.url}/academy/confirm/?token=synthetic-token")
    assert response is not None
    assert "no-store" in response.headers["cache-control"]
    heading = page.get_by_role("heading", level=1)
    expect(heading).to_have_text("Synthetic done")
    expect(page.get_by_text("Synthetic confirmed message.")).to_be_visible()
    back = page.get_by_role("link", name="Synthetic back")
    expect(back).to_have_attribute("href", "/academy/ai-engineering/")
    assert calls == [
        {
            "method": "GET",
            "path": "/confirm",
            "url": f"{API}/confirm?token=synthetic-token&format=json",
            "body": None,
        }
    ]
    no_overflow(page)
    shot(page, f"confirm-done-{suffix}")
    page.goto(f"{academy.url}/academy/confirm/")
    expect(page.get_by_role("heading", level=1)).to_have_text("Synthetic missing title")
    assert len(calls) == 1
