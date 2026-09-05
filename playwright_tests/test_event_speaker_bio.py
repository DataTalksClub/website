from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Page, ViewportSize, expect

from content.event_speakers import event_speaker_records
from content.public_data import public_projection
from events.queries import published_event_records

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

SCREENSHOTS = Path(".tmp/screenshots/event-speaker-bio")
FEATURED_EVENT_TITLE = "AI Dev Tools Zoomcamp 2026 Course Launch"
FEATURED_SPEAKER_NAME = "Alexey Grigorev"
FEATURED_EVENT_PATH = "/events/365/ai-dev-tools-zoomcamp-2026-course-launch"


def _featured_event() -> dict:
    """The published event row, with each credit joined to its profile biography.

    The page composes the biography the same way, from the person's own
    catalogue record: the event row carries the credit and nothing more.
    """

    event = next(
        event for event in published_event_records() if event["title"] == FEATURED_EVENT_TITLE
    )
    catalogue = public_projection()
    return {
        **event,
        "speakers": event_speaker_records(
            event["speakers"],
            people_by_slug=catalogue["people_by_slug"],
            people_by_path=catalogue["people_by_path"],
        ),
    }


def _screenshot(page: Page, name: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / name, full_page=True)


@pytest.mark.parametrize(
    ("viewport", "suffix"),
    [
        ({"width": 1440, "height": 900}, "desktop"),
        ({"width": 390, "height": 844}, "mobile"),
    ],
)
def test_featured_event_speaker_bio_is_rendered_at_both_viewports(
    page: Page,
    live_server,
    viewport: ViewportSize,
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    event = _featured_event()
    assert event["public_path"] == FEATURED_EVENT_PATH
    speaker = event["speakers"][0]
    bio_text = speaker["bio_blocks"][0]["text"]

    response = page.goto(f"{live_server.url}{FEATURED_EVENT_PATH}")
    assert response is not None and response.status == 200

    speakers = page.locator('section[aria-labelledby="event-speakers-heading"]')
    expect(speakers).to_be_visible()
    expect(speakers.get_by_role("heading", name="Speakers", exact=True)).to_be_visible()
    row = speakers.locator("li.speaker-row").filter(has_text=FEATURED_SPEAKER_NAME)
    expect(row).to_have_count(1)
    expect(row.get_by_role("link", name=FEATURED_SPEAKER_NAME, exact=True)).to_be_visible()
    expect(row.locator(".event-speaker-bio")).to_have_text(bio_text)
    expect(row.locator("script")).to_have_count(0)
    expect(page.locator('section[aria-label="Event description"]')).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    _screenshot(page, f"featured-{suffix}.png")
