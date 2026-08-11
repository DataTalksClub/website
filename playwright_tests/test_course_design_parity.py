from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from django.urls import reverse
from playwright.sync_api import Page, expect

from courses.models import Course

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

CMP_SOURCE_COMMIT = "98a235283904b4ef9ad29e196298540756cf1bcc"
CMP_COURSE_LIST_SHA256 = "26e391ffdd2c90b89a668c41118f4a8e43efd2b5dde015097f893aee707984ef"
REPO_ROOT = Path(__file__).resolve().parents[1]
COURSE_LIST_TEMPLATE = REPO_ROOT / "courses/templates/courses/course_list.html"
SCREENSHOTS = Path(".tmp/screenshots/issue-128")
VIEWPORTS = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)

ACTIVE_COURSE = {
    "title": "Synthetic CMP Active Course",
    "slug": "synthetic-cmp-active-course",
    "description": "Build a deterministic project through practical assignments and peer review.",
    "start_date": "2026-08-03",
    "end_date": "2026-10-05",
}
REGISTRATION_COURSE = {
    "title": "Synthetic CMP Registration Course",
    "slug": "synthetic-cmp-registration-course",
    "description": "Prepare for a deterministic upcoming course.",
    "start_date": "2099-01-12",
    "end_date": "2099-03-16",
    "registration_url": "https://example.invalid/register",
}
ARCHIVED_COURSE = {
    "title": "Synthetic CMP Archive Course 2024",
    "slug": "synthetic-cmp-archive-course-2024",
    "description": "Review a deterministic completed course.",
    "finished": True,
}


@pytest.fixture
def cmp_course_catalog() -> dict[str, Course]:
    return {
        "active": Course.objects.create(**ACTIVE_COURSE),
        "registration": Course.objects.create(**REGISTRATION_COURSE),
        "archived": Course.objects.create(**ARCHIVED_COURSE),
    }


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


def _write_attribution_evidence() -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    evidence = {
        "cmp_source_commit": CMP_SOURCE_COMMIT,
        "course_list_sha256": CMP_COURSE_LIST_SHA256,
        "fixture": [ACTIVE_COURSE, REGISTRATION_COURSE, ARCHIVED_COURSE],
        "template": "courses/templates/courses/course_list.html",
        "viewports": [viewport for viewport, _suffix in VIEWPORTS],
    }
    (SCREENSHOTS / "cmp-parity-attribution.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _capture_dark_mode(page: Page, path: Path) -> None:
    dark_mode = page.get_by_role("button", name="Toggle dark mode")
    dark_mode.click()
    expect(page.locator("body.dark-mode")).to_have_count(1)
    expect(dark_mode).to_have_attribute("aria-pressed", "true")
    _assert_no_horizontal_overflow(page)
    page.screenshot(path=path, full_page=True)


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_database_course_catalog_matches_pinned_cmp_composition(
    page: Page,
    live_server,
    cmp_course_catalog: dict[str, Course],
    viewport: dict[str, int],
    suffix: str,
) -> None:
    assert hashlib.sha256(COURSE_LIST_TEMPLATE.read_bytes()).hexdigest() == CMP_COURSE_LIST_SHA256
    _write_attribution_evidence()
    page.set_viewport_size(viewport)
    bad_responses: list[str] = []
    page.on(
        "response",
        lambda response: (
            bad_responses.append(f"{response.status} {response.url}")
            if response.status >= 400
            else None
        ),
    )

    catalog = page.goto(f"{live_server.url}/courses", wait_until="networkidle")
    assert catalog is not None and catalog.status == 200
    expect(
        page.get_by_role("heading", name="Learn data skills. For free. Together.")
    ).to_be_visible()
    expect(page.locator("main .home-hero")).to_have_count(1)
    expect(page.locator("main #courses")).to_have_count(1)
    expect(page.get_by_text("Start now", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Active courses", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Open registration", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Course archive", exact=True)).to_be_visible()
    expect(page.locator("#course-families-heading")).to_have_count(0)
    expect(page.get_by_text("No active cohort coursework right now.", exact=True)).to_have_count(0)
    expect(page.get_by_text(ACTIVE_COURSE["title"], exact=True)).to_be_visible()
    expect(page.get_by_text(REGISTRATION_COURSE["title"], exact=True)).to_be_visible()
    expect(page.get_by_text(ARCHIVED_COURSE["title"], exact=True)).to_be_visible()
    expect(page.get_by_text("Registration open", exact=True)).to_be_visible()
    assert page.locator("#courses article[role='link'][tabindex='0']").count() == 2
    section_order = page.locator("#courses h2").all_text_contents()
    assert section_order == ["Active courses", "Open registration", "Course archive"]
    expect(
        page.locator("nav[aria-label='Primary navigation'] a[aria-current='page']")
    ).to_have_text("Courses")
    expect(page.get_by_role("link", name="Login", exact=True)).to_be_visible()
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", "https://datatalks.club/courses"
    )
    _assert_local_page_assets(page, live_server.url)
    _assert_no_horizontal_overflow(page)
    expect(page.locator("body")).not_to_contain_text("Traceback")
    expect(page.locator("body")).not_to_contain_text("Page not found")
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / f"course-catalog-cmp-{suffix}.png", full_page=True)

    _capture_dark_mode(page, SCREENSHOTS / f"course-catalog-cmp-dark-{suffix}.png")
    page.get_by_role("button", name="Toggle dark mode").click()
    expect(page.locator("body.dark-mode")).to_have_count(0)
    page.reload(wait_until="networkidle")

    page.keyboard.press("Tab")
    expect(page.locator(".skip-link")).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator("#main-content")).to_be_focused()
    active_link = page.get_by_role("link", name=ACTIVE_COURSE["title"], exact=True)
    active_link.focus()
    expect(active_link).to_be_focused()
    assert active_link.evaluate(
        "node => { const style = getComputedStyle(node); "
        "return style.outlineStyle !== 'none' || style.boxShadow !== 'none'; }"
    )
    page.keyboard.press("Enter")
    detail_path = reverse(
        "course",
        kwargs={"course_slug": cmp_course_catalog["active"].slug},
    )
    expect(page).to_have_url(f"{live_server.url}{detail_path}")
    expect(page.get_by_role("heading", name=ACTIVE_COURSE["title"], exact=True)).to_be_visible()
    expect(
        page.get_by_text(
            "There are no homeworks or projects available for this course yet. Come back later.",
            exact=True,
        )
    ).to_be_visible()
    _assert_no_horizontal_overflow(page)
    page.screenshot(path=SCREENSHOTS / f"course-detail-cmp-{suffix}.png", full_page=True)

    _capture_dark_mode(page, SCREENSHOTS / f"course-detail-cmp-dark-{suffix}.png")

    cdp = page.context.new_cdp_session(page)
    cdp.send("Emulation.setPageScaleFactor", {"pageScaleFactor": 2})
    expect(page.locator("main h1")).to_be_visible()
    assert page.evaluate("visualViewport.scale") == 2
    _assert_no_horizontal_overflow(page)
    cdp.send("Emulation.setPageScaleFactor", {"pageScaleFactor": 1})
    assert bad_responses == []


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_no_database_course_catalog_uses_cmp_composition_with_real_projection(
    page: Page,
    live_server,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    assert not Course.objects.exists()
    page.set_viewport_size(viewport)

    catalog = page.goto(f"{live_server.url}/courses", wait_until="networkidle")

    assert catalog is not None and catalog.status == 200
    expect(page.locator("main .home-hero")).to_have_count(1)
    expect(page.locator("main #courses")).to_have_count(1)
    expect(page.get_by_text("Start now", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Active courses", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Course archive", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Open registration", exact=True)).to_have_count(0)
    expect(page.locator("[data-course-row]")).to_have_count(12)
    active_course = page.get_by_role(
        "link",
        name="Data Engineering Zoomcamp 2026",
        exact=True,
    )
    expect(active_course).to_have_attribute("href", "/courses/de-zoomcamp-2026")
    expect(active_course.locator("xpath=ancestor::article[@role='link']")).to_have_count(1)
    destinations = page.locator("[data-course-row]").evaluate_all(
        "nodes => nodes.map(node => node.href || node.querySelector('a').href)"
    )
    assert len(set(destinations)) == 12
    assert all(
        destination.startswith(f"{live_server.url}/courses/") for destination in destinations
    )
    expect(page.locator("#course-families-heading")).to_have_count(0)
    expect(page.get_by_text("No active cohort coursework right now.", exact=True)).to_have_count(0)
    _assert_local_page_assets(page, live_server.url)
    _assert_no_horizontal_overflow(page)
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / f"course-catalog-public-{suffix}.png", full_page=True)
    _capture_dark_mode(page, SCREENSHOTS / f"course-catalog-public-dark-{suffix}.png")


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_database_backed_empty_catalog_keeps_cmp_empty_composition(
    page: Page,
    live_server,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    Course.objects.create(
        title="Synthetic hidden course",
        slug="synthetic-hidden-course",
        description="A deterministic hidden course.",
        visible=False,
    )
    page.set_viewport_size(viewport)

    catalog = page.goto(f"{live_server.url}/courses", wait_until="networkidle")

    assert catalog is not None and catalog.status == 200
    expect(page.locator("main .home-hero")).to_have_count(1)
    expect(page.locator("main #courses")).to_have_count(1)
    expect(page.get_by_text("Start now", exact=True)).to_be_visible()
    expect(page.get_by_text("No active courses right now.", exact=True)).to_be_visible()
    expect(page.locator("[data-course-row]")).to_have_count(0)
    expect(page.get_by_text("Data Engineering Zoomcamp 2026", exact=True)).to_have_count(0)
    _assert_local_page_assets(page, live_server.url)
    _assert_no_horizontal_overflow(page)
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / f"course-catalog-empty-{suffix}.png", full_page=True)
    _capture_dark_mode(page, SCREENSHOTS / f"course-catalog-empty-dark-{suffix}.png")
