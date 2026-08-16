from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from content import public_views
from content.public_data import EventGroups, event_groups
from events.identity import load_identity_manifest

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

CMP_SOURCE_COMMIT = "98a235283904b4ef9ad29e196298540756cf1bcc"
CMP_BASE_SHA256 = "f51666391e33aec905f43312215bfd82094bfb0088414594f40bcbdfc21560b8"
CMP_CSS_SHA256 = "282ed7b15df2502a8d4c2e9cd45ef1f8e92771835243d3cbc590f78db9ed5f8f"
LIGHT_SURFACE_BACKGROUND = "rgb(255, 255, 255)"
DARK_SURFACE_BACKGROUND = "rgb(13, 17, 23)"
# The homepage and the events index carry the design-5a palette (issue #179, mockups 5a
# and 6c); the event detail page still renders on the adopted CMP shell.
DESIGN_5A_LIGHT_BACKGROUND = "rgb(253, 250, 243)"
DESIGN_5A_DARK_BACKGROUND = "rgb(19, 22, 42)"
DESIGN_5A_PATHS = frozenset({"/", "/unified/", "/events"})
HOME_HEADING = "Ship data pipelines and AI systems that actually run in production."
EVENTS_HEADING = "Something happening every week"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ADOPTED_BASE = REPOSITORY_ROOT / "course_platform_templates/base.html"
ADOPTED_CSS = REPOSITORY_ROOT / "courses/static/courses.css"
SCREENSHOTS = Path(".tmp/screenshots/issue-130-remediation/after")
FEATURED_EVENT_TITLE = "AI Dev Tools Zoomcamp 2026 Course Launch"


def _featured_event_path() -> str:
    return next(
        event.canonical_path
        for event in load_identity_manifest().events
        if event.title == FEATURED_EVENT_TITLE
    )


VIEWPORTS = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)
SURFACES = (
    ("/", "home", HOME_HEADING),
    ("/events", "events", EVENTS_HEADING),
    (_featured_event_path(), "event-detail", FEATURED_EVENT_TITLE),
)


def _light_background(path: str) -> str:
    return DESIGN_5A_LIGHT_BACKGROUND if path in DESIGN_5A_PATHS else LIGHT_SURFACE_BACKGROUND


def _dark_background(path: str) -> str:
    return DESIGN_5A_DARK_BACKGROUND if path in DESIGN_5A_PATHS else DARK_SURFACE_BACKGROUND


# The CMP surfaces act through .primer-button; the design-5a pages act through .cta and
# the filter pills, and both kinds are held to the same 44px target floor.
SCOPED_ACTION_SELECTOR = (
    "#dark-mode-toggle, "
    "main a.primer-button, "
    "main a.cta, "
    "main a.filter-pill, "
    "main button, "
    'main input[type="button"], '
    'main input[type="submit"], '
    'main [role="button"]'
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_attribution_evidence() -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    evidence = {
        "adopted_base_sha256": _sha256(ADOPTED_BASE),
        "adopted_css_sha256": _sha256(ADOPTED_CSS),
        "cmp_base_source_sha256": CMP_BASE_SHA256,
        "cmp_css_source_sha256": CMP_CSS_SHA256,
        "cmp_source_commit": CMP_SOURCE_COMMIT,
        "reflow_evidence": {
            "css_viewport": {"width": 320, "height": 422},
            "device_scale_factor": 2,
            "physical_viewport": {"width": 640, "height": 844},
        },
        "routes": [surface[0] for surface in SURFACES],
        "viewports": [viewport for viewport, _suffix in VIEWPORTS],
    }
    (SCREENSHOTS / "cmp-composition-attribution.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _assert_no_horizontal_overflow(page: Page) -> None:
    overflow = page.evaluate(
        """() => ({
          innerWidth: window.innerWidth,
          scrollWidth: document.documentElement.scrollWidth,
          mainRight: document.querySelector('main').getBoundingClientRect().right,
        })"""
    )
    assert overflow["scrollWidth"] <= overflow["innerWidth"], overflow
    assert overflow["mainRight"] <= overflow["innerWidth"] + 1, overflow


def _assert_local_page_assets(page: Page, origin: str) -> None:
    assets = page.evaluate(
        """() => [...document.querySelectorAll('link[rel="stylesheet"], script[src]')]
          .map(node => node.href || node.src)"""
    )
    assert assets
    assert all(asset.startswith(f"{origin}/static/") for asset in assets), assets


def _assert_all_scoped_mobile_targets(page: Page, path: str) -> list[dict[str, object]]:
    targets = page.locator(SCOPED_ACTION_SELECTOR)
    records: list[dict[str, object]] = []
    for index in range(targets.count()):
        target = targets.nth(index)
        if not target.is_visible():
            continue
        bounds = target.bounding_box()
        assert bounds is not None
        identity = target.evaluate(
            """element => ({
              href: element.getAttribute('href') || '',
              label: element.getAttribute('aria-label') || element.textContent.trim(),
              tag: element.tagName.toLowerCase(),
            })"""
        )
        record = {
            "height": bounds["height"],
            "href": identity["href"],
            "label": identity["label"],
            "path": path,
            "tag": identity["tag"],
            "width": bounds["width"],
        }
        records.append(record)
        assert bounds["width"] >= 44, record
        assert bounds["height"] >= 44, record
    assert records, f"{path}: no visible scoped action targets were audited"
    return records


def _load_light(page: Page, url: str, path: str) -> None:
    page.goto(url)
    page.evaluate("localStorage.removeItem('darkMode')")
    response = page.goto(url, wait_until="networkidle")
    assert response is not None and response.status == 200
    expect(page.locator("body.dark-mode")).to_have_count(0)
    expect(page.locator("body")).to_have_css(
        "background-color",
        _light_background(path),
    )


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_home_events_and_detail_match_cmp_composition(
    page: Page,
    live_server,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    _write_attribution_evidence()
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.set_viewport_size(viewport)
    bad_responses: list[str] = []
    console_errors: list[str] = []
    mobile_target_records: list[dict[str, object]] = []
    page.on(
        "response",
        lambda response: (
            bad_responses.append(f"{response.status} {response.url}")
            if response.status >= 400
            else None
        ),
    )
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )

    for path, label, heading in SURFACES:
        _load_light(page, f"{live_server.url}{path}", path)
        expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
        expect(page.locator("body")).not_to_contain_text("Traceback")
        expect(page.locator("body")).not_to_contain_text("Page not found")
        _assert_local_page_assets(page, live_server.url)
        _assert_no_horizontal_overflow(page)

        if path == "/":
            expect(page.locator("main .hero")).to_have_count(1)
            expect(page.locator("[data-featured-course]")).to_have_css("display", "grid")
            expect(page.get_by_role("link", name="all courses")).to_have_attribute(
                "href", "/courses"
            )
        elif path == "/events":
            expect(page.locator("main .events-hero")).to_have_count(1)
            expect(page.locator("main .event-rows .list-row").first).to_have_css("display", "grid")
            expect(page.locator("main .event-rows .when").first).to_be_visible()
            expect(page.get_by_role("link", name="Past events", exact=True)).to_have_attribute(
                "href", "/events/past"
            )
            expect(page.get_by_role("link", name="our Google calendar")).to_have_attribute(
                "target", "_blank"
            )
        else:
            breadcrumb = page.get_by_role("navigation", name="Breadcrumb")
            expect(breadcrumb.get_by_role("link", name="Events", exact=True)).to_have_attribute(
                "href",
                "/events",
            )
            expect(page.locator('script[type="application/ld+json"]')).to_have_count(1)

        if suffix == "mobile":
            mobile_target_records.extend(_assert_all_scoped_mobile_targets(page, path))

        page.screenshot(path=SCREENSHOTS / f"{label}-{suffix}-light.png")
        if suffix == "mobile" and path == "/":
            page.locator("[data-featured-course]").screenshot(
                path=SCREENSHOTS / "home-mobile-course-cta-light.png"
            )
            page.evaluate("window.scrollTo(0, 0)")

        dark_mode = page.locator("#dark-mode-toggle")
        dark_mode.click()
        expect(page.locator("body.dark-mode")).to_have_count(1)
        expect(dark_mode).to_have_attribute("aria-pressed", "true")
        expect(page.locator("body")).to_have_css(
            "background-color",
            _dark_background(path),
        )
        _assert_no_horizontal_overflow(page)
        page.screenshot(path=SCREENSHOTS / f"{label}-{suffix}-dark.png")

    _load_light(page, f"{live_server.url}/unified/", "/unified/")
    expect(page.get_by_role("heading", name=HOME_HEADING, exact=True)).to_be_visible()
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", "https://datatalks.club/"
    )
    if suffix == "mobile":
        mobile_target_records.extend(_assert_all_scoped_mobile_targets(page, "/unified/"))
        (SCREENSHOTS / "mobile-target-audit.json").write_text(
            json.dumps(mobile_target_records, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    page.screenshot(path=SCREENSHOTS / f"unified-{suffix}-light.png")

    assert bad_responses == []
    assert console_errors == []


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_empty_optional_and_error_states_are_responsive(
    page: Page,
    live_server,
    monkeypatch,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.set_viewport_size(viewport)

    monkeypatch.setattr(public_views, "event_groups", lambda: EventGroups((), ()))
    empty = page.goto(f"{live_server.url}/events")
    assert empty is not None and empty.status == 200
    expect(page.get_by_text("No upcoming events.", exact=True)).to_be_visible()
    _assert_no_horizontal_overflow(page)
    page.screenshot(path=SCREENSHOTS / f"events-empty-{suffix}.png")

    event = next(
        item
        for item in (*event_groups().upcoming, *event_groups().recent)
        if item["public_path"] == _featured_event_path()
    )
    optional_event = {**event, "speakers": (), "links": (), "ends_at": ""}
    monkeypatch.setattr(
        public_views,
        "event_groups",
        lambda: EventGroups((optional_event,), ()),
    )
    monkeypatch.setattr(public_views, "public_registration_total", lambda _event: None)
    optional = page.goto(f"{live_server.url}{_featured_event_path()}")
    assert optional is not None and optional.status == 200
    expect(page.get_by_role("heading", name=FEATURED_EVENT_TITLE, exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Speakers", exact=True)).to_have_count(0)
    expect(page.get_by_role("heading", name="Event links", exact=True)).to_have_count(0)
    expect(page.get_by_text("registered", exact=False)).to_have_count(0)
    _assert_no_horizontal_overflow(page)
    page.screenshot(path=SCREENSHOTS / f"event-detail-optional-{suffix}.png")

    missing = page.goto(f"{live_server.url}/events/not-a-real-event")
    assert missing is not None and missing.status == 404
    expect(page.get_by_role("heading", name="Page not found", exact=True)).to_be_visible()
    expect(page.locator("body")).not_to_contain_text("Traceback")
    _assert_no_horizontal_overflow(page)
    page.screenshot(path=SCREENSHOTS / f"event-missing-{suffix}.png")


def test_keyboard_theme_reduced_motion_and_320px_reflow(
    browser: Browser,
    live_server,
) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(
        viewport={"width": 320, "height": 844},
        reduced_motion="reduce",
    )
    page = context.new_page()
    try:
        for path, label, heading in SURFACES:
            response = page.goto(f"{live_server.url}{path}")
            assert response is not None and response.status == 200
            expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
            _assert_no_horizontal_overflow(page)
            page.screenshot(path=SCREENSHOTS / f"{label}-320px.png")

        page.goto(live_server.url)
        page.keyboard.press("Tab")
        expect(page.locator(".skip-link")).to_be_focused()
        page.keyboard.press("Enter")
        expect(page.locator("#main-content")).to_be_focused()

        dark_mode = page.locator("#dark-mode-toggle")
        dark_mode.click()
        page.goto(f"{live_server.url}/events")
        expect(page.locator("body.dark-mode")).to_have_count(1)
        expect(page.locator("body")).to_have_css(
            "background-color",
            DESIGN_5A_DARK_BACKGROUND,
        )
    finally:
        context.close()


def test_200_percent_reflow_uses_two_device_pixels_per_css_pixel(
    browser: Browser,
    live_server,
) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(
        viewport={"width": 320, "height": 422},
        screen={"width": 640, "height": 844},
        device_scale_factor=2,
        reduced_motion="reduce",
    )
    page = context.new_page()
    try:
        response = page.goto(f"{live_server.url}/events", wait_until="networkidle")
        assert response is not None and response.status == 200
        expect(page.get_by_role("heading", name=EVENTS_HEADING, exact=True)).to_be_visible()

        metrics = page.evaluate(
            """() => ({
              devicePixelRatio: window.devicePixelRatio,
              innerHeight: window.innerHeight,
              innerWidth: window.innerWidth,
              visualHeight: window.visualViewport.height,
              visualScale: window.visualViewport.scale,
              visualWidth: window.visualViewport.width,
            })"""
        )
        assert metrics == {
            "devicePixelRatio": 2,
            "innerHeight": 422,
            "innerWidth": 320,
            "visualHeight": 422,
            "visualScale": 1,
            "visualWidth": 320,
        }
        _assert_no_horizontal_overflow(page)

        screenshot = page.screenshot(
            path=SCREENSHOTS / "events-200-percent-reflow.png",
            full_page=True,
            scale="device",
        )
        assert screenshot[:8] == b"\x89PNG\r\n\x1a\n"
        image_width, image_height = struct.unpack(">II", screenshot[16:24])
        assert image_width == 640
        assert image_height >= 844
    finally:
        context.close()


def test_home_events_and_detail_remain_useful_without_javascript(
    browser: Browser,
    live_server,
) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    context = browser.new_context(
        java_script_enabled=False,
        viewport={"width": 390, "height": 844},
        reduced_motion="reduce",
    )
    page = context.new_page()
    try:
        for path, label, heading in SURFACES:
            response = page.goto(f"{live_server.url}{path}")
            assert response is not None and response.status == 200
            expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible()
            expect(page.locator('link[rel="canonical"]')).to_have_count(1)
            _assert_no_horizontal_overflow(page)
            page.screenshot(path=SCREENSHOTS / f"{label}-mobile-no-js.png")
    finally:
        context.close()
