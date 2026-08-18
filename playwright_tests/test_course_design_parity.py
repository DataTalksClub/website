from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Page, expect

from accounts.models import CustomUser
from courses.models import (
    Course,
    CourseRegistration,
    CourseRegistrationCountRevision,
    CourseRegistrationCountSlot,
    CourseRegistrationCountSourceRun,
    RegistrationCampaign,
)
from courses.registration_count_importer import (
    ADAPTER_VERSION,
    COUNT_POLICY_VERSION,
    aggregate_checksum,
    source_reference_digest,
)
from playwright_tests.accessibility_support import target_size_issues
from playwright_tests.course_catalog_contract import assert_copied_course_catalog_link

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

CMP_SOURCE_COMMIT = "98a235283904b4ef9ad29e196298540756cf1bcc"
# The courses index left the byte-exact CMP copy with the design 5a rebuild (issue #179);
# its digest is now recorded in _docs/adoption/course-platform/integration-patched-files.tsv
# and asserted by core/tests/test_course_platform_adoption.py.
DESIGN_MOCKUP_SOURCE = "_docs/design/mockups/datatalks-pages.source.html"
REPO_ROOT = Path(__file__).resolve().parents[1]
COURSE_LIST_TEMPLATE = REPO_ROOT / "courses/templates/courses/course_list.html"
ACTIVE_HEADING = "Active now — you can still join"
OPEN_HEADING = "Open registration"
FINISHED_HEADING = "Finished courses"
SCREENSHOTS = Path(".tmp/screenshots/issue-128-owner-remediation")
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

REGISTRATION_CAMPAIGN = {
    "slug": "synthetic-cmp-registration",
    "title": "Machine Learning Zoomcamp",
    "edition_label": "2026 cohort",
    "marketing_markdown": "Learn machine learning engineering in a free online course.",
    # A deterministic 600x282 image exercises the same intrinsic dimensions as
    # the production CMP registration hero without introducing an external test
    # request or a binary fixture.
    "hero_image_url": (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
        "width='600' height='282'%3E%3Crect width='600' height='282' "
        "fill='slateblue'/%3E%3C/svg%3E"
    ),
}


@pytest.fixture
def cmp_course_catalog() -> dict[str, Course]:
    return {
        "active": Course.objects.create(**ACTIVE_COURSE),
        "registration": Course.objects.create(**REGISTRATION_COURSE),
        "archived": Course.objects.create(**ARCHIVED_COURSE),
    }


@pytest.fixture
def cmp_registration_campaign(settings) -> RegistrationCampaign:
    course = Course.objects.create(
        title="Machine Learning Zoomcamp 2026",
        slug="synthetic-cmp-registration-course-2026",
        description="A deterministic registration layout fixture.",
    )
    campaign = RegistrationCampaign.objects.create(
        current_course=course,
        **REGISTRATION_CAMPAIGN,
    )
    source_reference = "synthetic-cmp-browser-count"
    source_checksum = "a" * 64
    schema_checksum = "b" * 64
    cutoff = datetime(2026, 1, 1, tzinfo=UTC)
    native_start = cutoff + timedelta(days=1)
    source_created_at = cutoff - timedelta(days=1)
    settings.COURSE_REGISTRATION_COUNT_SOURCES = {
        source_reference: {
            "adapter": ADAPTER_VERSION,
            "path": "/not-read-by-public-query",
            "sha256": source_checksum,
            "byte_size": 1,
            "schema_version": "synthetic-browser-v1",
            "schema_contract_checksum": schema_checksum,
            "captured_at": cutoff.isoformat(),
            "source_frozen_at": cutoff.isoformat(),
            "coverage_cutoff_at": cutoff.isoformat(),
            "native_start_at": native_start.isoformat(),
        }
    }
    run = CourseRegistrationCountSourceRun.objects.create(
        adapter_version=ADAPTER_VERSION,
        schema_version="synthetic-browser-v1",
        count_policy_version=COUNT_POLICY_VERSION,
        whole_source_checksum=source_checksum,
        source_byte_size=1,
        schema_contract_checksum=schema_checksum,
        aggregate_manifest_checksum="c" * 64,
        source_reference_digest=source_reference_digest(source_reference),
        captured_at=cutoff,
        source_frozen_at=cutoff,
        campaign_total=1,
        row_total=1,
        state=CourseRegistrationCountSourceRun.State.ACTIVE,
    )
    revision = CourseRegistrationCountRevision.objects.create(
        source_run=run,
        campaign=campaign,
        cohort=course,
        campaign_slug_snapshot=campaign.slug,
        cohort_slug_snapshot=course.slug,
        baseline_count=1,
        source_min_created_at=source_created_at,
        source_max_created_at=source_created_at,
        coverage_cutoff_at=cutoff,
        proposed_native_start_at=native_start,
        aggregate_checksum=aggregate_checksum(
            campaign_slug=campaign.slug,
            cohort_slug=course.slug,
            count=1,
            minimum=source_created_at,
            maximum=source_created_at,
            cutoff=cutoff,
        ),
        state=CourseRegistrationCountRevision.State.ACTIVE,
    )
    CourseRegistrationCountSlot.objects.create(
        campaign=campaign,
        cohort=course,
        campaign_slug_snapshot=campaign.slug,
        cohort_slug_snapshot=course.slug,
        mode=CourseRegistrationCountSlot.Mode.BASELINE_PLUS_NATIVE,
        active_baseline_revision=revision,
        native_start_at=native_start,
    )
    return campaign


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


def _assert_design_breadcrumb_trail(page: Page) -> None:
    """The design 5a trail: two levels, a real target on the way back, text where you are.

    The adopted shell drew a compact 24px crumb row and made the current page a link too.
    Design 5a (issue #179) states the opposite contract: every ancestor is a link that keeps
    the system's 2.75rem minimum target height, and the current page is plain text on
    ``li[aria-current="page"]``.  The trail is allowed to wrap onto a second row on a phone —
    that is exactly why its links carry the target height — so this asserts the levels, the
    link/text split and the target height rather than a single-line geometry.
    """

    geometry = page.locator(".breadcrumbs li").evaluate_all(
        """nodes => nodes.map(node => {
          const link = node.querySelector('a');
          return {
            isLink: link !== null,
            linkHeight: link ? link.getBoundingClientRect().height : 0,
            current: node.getAttribute('aria-current'),
            text: node.textContent.trim(),
          };
        })"""
    )
    assert len(geometry) == 2, geometry
    ancestor, current = geometry
    assert ancestor["isLink"] and ancestor["current"] is None, geometry
    assert ancestor["linkHeight"] + 0.5 >= 44, geometry
    assert not current["isLink"] and current["current"] == "page", geometry
    assert current["text"], geometry


def _write_attribution_evidence() -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    evidence = {
        "cmp_source_commit": CMP_SOURCE_COMMIT,
        "course_list_sha256": hashlib.sha256(COURSE_LIST_TEMPLATE.read_bytes()).hexdigest(),
        "design_source": DESIGN_MOCKUP_SOURCE,
        "fixture": [ACTIVE_COURSE, REGISTRATION_COURSE, ARCHIVED_COURSE],
        "template": "courses/templates/courses/course_list.html",
        "viewports": [viewport for viewport, _suffix in VIEWPORTS],
    }
    (SCREENSHOTS / "cmp-parity-attribution.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _dark_mode_toggle(page: Page):
    """Return the page's theme control, whichever shell drew it.

    The adopted catalogue shell labels it "Toggle dark mode"; the design 5a course page
    (issue #179) labels it by the mode it switches to.  Both are a single button with a
    pressed state, which is what the assertions below actually depend on.
    """

    toggle = page.locator("button#dark-mode-toggle")
    expect(toggle).to_have_count(1)
    assert toggle.get_attribute("aria-label") or toggle.inner_text().strip()
    return toggle


def _capture_dark_mode(page: Page, path: Path) -> None:
    dark_mode = _dark_mode_toggle(page)
    dark_mode.click()
    expect(page.locator("body.dark-mode")).to_have_count(1)
    expect(dark_mode).to_have_attribute("aria-pressed", "true")
    _assert_no_horizontal_overflow(page)
    page.screenshot(path=path, full_page=True)


def _assert_filter_pills_are_operable(page: Page, origin: str) -> None:
    """The segmented control is links, so it works by keyboard and without JavaScript."""

    pills = page.locator(".filter-pills .filter-pill")
    expect(pills).to_have_count(4)
    expect(page.locator('.filter-pill[aria-current="page"]')).to_have_text("All courses")
    active_pill = page.get_by_role("link", name="Active", exact=True)
    active_pill.focus()
    expect(active_pill).to_be_focused()
    page.keyboard.press("Enter")
    expect(page).to_have_url(f"{origin}/courses?filter=active")
    expect(page.locator('.filter-pill[aria-current="page"]')).to_have_text("Active")
    expect(page.get_by_role("heading", name=ACTIVE_HEADING, exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name=OPEN_HEADING, exact=True)).to_have_count(0)
    expect(page.get_by_role("heading", name=FINISHED_HEADING, exact=True)).to_have_count(0)
    for pill in pills.all():
        box = pill.bounding_box()
        assert box is not None and box["height"] + 0.5 >= 44, box
    page.goto(f"{origin}/courses", wait_until="networkidle")


def _capture_design_dark_mode(page: Page, path: Path) -> None:
    """Design 5a pages carry their own compact "Dark"/"Light" masthead toggle."""

    dark_mode = page.locator("#dark-mode-toggle")
    dark_mode.click()
    expect(page.locator("body.dark-mode")).to_have_count(1)
    expect(dark_mode).to_have_attribute("aria-pressed", "true")
    _assert_no_horizontal_overflow(page)
    page.screenshot(path=path, full_page=True)


def _assert_registration_hero_is_contained(page: Page) -> dict[str, float]:
    """The campaign artwork stays inside its own column, whatever size it arrives at.

    The image is taken from the page's main landmark: the design 5a rebuild (issue #179)
    puts the hero artwork in the hero band and the register card in a band of its own, so
    the two no longer share a parent element.  What is asserted is unchanged — the loaded
    image is bounded by the column it sits in and the page does not scroll sideways.
    """

    geometry = page.locator("main").evaluate(
        """main => {
          const image = main.querySelector('img[alt=""]');
          const parent = image.parentElement;
          const imageRect = image.getBoundingClientRect();
          const parentRect = parent.getBoundingClientRect();
          return {
            imageHeight: imageRect.height,
            imageLeft: imageRect.left,
            imageNaturalHeight: image.naturalHeight,
            imageNaturalWidth: image.naturalWidth,
            imageRight: imageRect.right,
            imageWidth: imageRect.width,
            parentLeft: parentRect.left,
            parentRight: parentRect.right,
            scrollWidth: document.documentElement.scrollWidth,
            viewportWidth: window.innerWidth,
          };
        }"""
    )
    assert geometry["imageNaturalWidth"] == 600, geometry
    assert geometry["imageNaturalHeight"] == 282, geometry
    assert geometry["imageWidth"] > 0 and geometry["imageHeight"] > 0, geometry
    assert geometry["imageLeft"] >= geometry["parentLeft"] - 1, geometry
    assert geometry["imageRight"] <= geometry["parentRight"] + 1, geometry
    assert geometry["scrollWidth"] <= geometry["viewportWidth"], geometry
    return geometry


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_database_course_catalog_renders_the_design_system_index(
    page: Page,
    live_server,
    cmp_course_catalog: dict[str, Course],
    viewport: dict[str, int],
    suffix: str,
) -> None:
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
    expect(page.locator("main .courses-hero")).to_have_count(1)
    expect(page.locator("main #courses")).to_have_count(1)
    expect(page.locator("head style")).to_have_count(1)
    assert page.locator('link[rel="stylesheet"]').count() == 0
    # Three catalogue records, and the eyebrow counts them rather than asserting a number.
    expect(page.get_by_text("three courses · zero tuition", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name=ACTIVE_HEADING, exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name=OPEN_HEADING, exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name=FINISHED_HEADING, exact=True)).to_be_visible()
    expect(page.locator("#course-families-heading")).to_have_count(0)
    expect(page.get_by_text("No active cohort coursework right now.", exact=True)).to_have_count(0)
    course_links = {
        role: assert_copied_course_catalog_link(
            page,
            path=reverse("course", kwargs={"course_slug": course.slug}),
            title=course.title,
        )
        for role, course in cmp_course_catalog.items()
    }
    archived_link = course_links["archived"]
    # The finished-course row now names its own destination instead of wrapping a whole card.
    expect(page.get_by_role("link", name=ARCHIVED_COURSE["title"], exact=True)).to_have_count(1)
    expect(archived_link.locator("xpath=ancestor::article[@role='link']")).to_have_count(0)
    expect(
        archived_link.locator("xpath=ancestor::section[1]").get_by_role(
            "heading", name="2024", exact=True
        )
    ).to_be_visible()
    expect(page.get_by_text("registration open", exact=True)).to_be_visible()
    # The rebuilt page carries no click-anywhere card: every course is reached by a real link.
    assert page.locator("#courses article[role='link']").count() == 0
    assert page.locator("#courses article.card").count() == 2
    section_order = [text.strip() for text in page.locator("#courses h2").all_text_contents()]
    assert section_order == [ACTIVE_HEADING, OPEN_HEADING, FINISHED_HEADING]
    expect(
        page.locator("nav[aria-label='Primary navigation'] a[aria-current='page']")
    ).to_have_text("Courses")
    if suffix == "mobile":
        # One-line phone masthead (issue #179): "Log in" lives in the navigation
        # panel below 40rem, one tap behind the labelled Menu toggle.
        menu_toggle = page.get_by_role("button", name="Menu")
        menu_toggle.click()
        expect(page.get_by_role("link", name="Log in", exact=True)).to_be_visible()
        page.keyboard.press("Escape")
        expect(menu_toggle).to_have_attribute("aria-expanded", "false")
    else:
        expect(page.get_by_role("link", name="Log in", exact=True)).to_be_visible()
    expect(page.locator('link[rel="canonical"]')).to_have_attribute(
        "href", "https://datatalks.club/courses"
    )
    _assert_local_page_assets(page, live_server.url)
    _assert_no_horizontal_overflow(page)
    expect(page.locator("body")).not_to_contain_text("Traceback")
    expect(page.locator("body")).not_to_contain_text("Page not found")
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / f"course-catalog-cmp-{suffix}.png", full_page=True)

    _capture_design_dark_mode(page, SCREENSHOTS / f"course-catalog-cmp-dark-{suffix}.png")
    page.locator("#dark-mode-toggle").click()
    expect(page.locator("body.dark-mode")).to_have_count(0)
    page.reload(wait_until="networkidle")

    _assert_filter_pills_are_operable(page, live_server.url)

    page.keyboard.press("Tab")
    expect(page.locator(".skip-link")).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator("#main-content")).to_be_focused()
    detail_path = reverse(
        "course",
        kwargs={"course_slug": cmp_course_catalog["active"].slug},
    )
    # The active card now offers its title and its CTA as two real links to the
    # same course, so the keyboard pass takes the first (the title).
    active_link = page.locator(f'#courses a[href="{detail_path}"]').first
    active_link.focus()
    expect(active_link).to_be_focused()
    assert active_link.evaluate(
        "node => { const style = getComputedStyle(node); "
        "return style.outlineStyle !== 'none' || style.boxShadow !== 'none'; }"
    )
    page.keyboard.press("Enter")
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
def test_registration_breadcrumb_matches_cmp_without_losing_target_spacing(
    page: Page,
    live_server,
    cmp_registration_campaign: RegistrationCampaign,
    viewport: dict[str, int],
    suffix: str,
) -> None:
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
    registration_path = reverse(
        "registration_campaign",
        kwargs={"campaign_slug": cmp_registration_campaign.slug},
    )

    response = page.goto(f"{live_server.url}{registration_path}", wait_until="networkidle")

    assert response is not None and response.status == 200
    expect(page.get_by_role("heading", name=cmp_registration_campaign.title)).to_be_visible()
    breadcrumb = page.get_by_role("navigation", name="Breadcrumb")
    expect(breadcrumb.get_by_role("link", name="Courses", exact=True)).to_be_visible()
    # Design 5a leaves the page you are on as text, so the second level is read, not linked.
    expect(
        breadcrumb.get_by_role(
            "link",
            name=f"{cmp_registration_campaign.title} registration",
            exact=True,
        )
    ).to_have_count(0)
    current = page.locator(".breadcrumbs li[aria-current='page']")
    expect(current).to_have_count(1)
    expect(current).to_have_text(f"{cmp_registration_campaign.title} registration")
    expect(page.get_by_text("1 already registered for 2026 cohort", exact=True)).to_be_visible()
    _assert_design_breadcrumb_trail(page)
    assert target_size_issues(page, f"registration-{suffix}") == []
    _assert_local_page_assets(page, live_server.url)
    _assert_no_horizontal_overflow(page)
    assert bad_responses == []
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / f"registration-cmp-{suffix}.png", full_page=True)

    _capture_dark_mode(page, SCREENSHOTS / f"registration-cmp-dark-{suffix}.png")
    # The design 5a masthead labels the toggle by the mode it switches to, so the control
    # is taken by its stable id rather than by the copied shell's "Toggle dark mode" name.
    page.locator("#dark-mode-toggle").click()
    expect(page.locator("body.dark-mode")).to_have_count(0)

    cdp = page.context.new_cdp_session(page)
    cdp.send("Emulation.setPageScaleFactor", {"pageScaleFactor": 2})
    assert page.evaluate("visualViewport.scale") == 2
    _assert_design_breadcrumb_trail(page)
    _assert_no_horizontal_overflow(page)
    page.screenshot(
        path=SCREENSHOTS / f"registration-cmp-zoom-{suffix}.png",
        full_page=True,
    )
    cdp.send("Emulation.setPageScaleFactor", {"pageScaleFactor": 1})


def test_registration_pages_emit_exact_production_canonicals(
    page: Page,
    live_server,
    cmp_registration_campaign: RegistrationCampaign,
) -> None:
    campaigns = [
        RegistrationCampaign.objects.create(
            slug="ml-zoomcamp",
            title="Machine Learning Zoomcamp",
            current_course=cmp_registration_campaign.current_course,
        ),
        RegistrationCampaign.objects.create(
            slug="ai-dev-tools",
            title="AI Dev Tools Zoomcamp",
            current_course=cmp_registration_campaign.current_course,
        ),
    ]

    for campaign in campaigns:
        registration_path = reverse(
            "registration_campaign",
            kwargs={"campaign_slug": campaign.slug},
        )
        response = page.goto(
            f"{live_server.url}{registration_path}?utm_source=canonical-test",
            wait_until="networkidle",
        )

        assert response is not None and response.status == 200
        assert response.headers["x-robots-tag"] == "noindex, nofollow"
        canonical = page.locator('link[rel="canonical"]')
        expect(canonical).to_have_count(1)
        expect(canonical).to_have_attribute(
            "href",
            f"https://datatalks.club/register/{campaign.slug}",
        )
        assert page.locator('link[rel="canonical"][href^="https://web.dtcdev.click/"]').count() == 0
        assert (
            page.locator(f'link[rel="canonical"][href$="/register/{campaign.slug}/"]').count() == 0
        )


@pytest.mark.parametrize("viewport_width", (320, 390, 414))
def test_registration_hero_fits_loaded_image_without_javascript(
    browser,
    live_server,
    cmp_registration_campaign: RegistrationCampaign,
    viewport_width: int,
) -> None:
    context = browser.new_context(
        java_script_enabled=False,
        viewport={"width": viewport_width, "height": 844},
        color_scheme="light",
        reduced_motion="reduce",
    )
    page = context.new_page()
    try:
        registration_path = reverse(
            "registration_campaign",
            kwargs={"campaign_slug": cmp_registration_campaign.slug},
        )
        response = page.goto(f"{live_server.url}{registration_path}", wait_until="networkidle")
        assert response is not None and response.status == 200
        expect(page.locator("html.no-js")).to_have_count(1)
        expect(page.locator(".registration-panel form[data-registration-form]")).to_have_count(1)
        geometry = _assert_registration_hero_is_contained(page)
        assert (
            abs(
                geometry["imageWidth"] / geometry["imageHeight"]
                - geometry["imageNaturalWidth"] / geometry["imageNaturalHeight"]
            )
            < 0.01
        ), geometry

        # Keep the overflow assertion fail-closed: an intentionally broken
        # presentation rule must be observable before the page is restored.
        mutated = page.locator("img[alt='']").evaluate(
            """image => {
              image.style.maxWidth = 'none';
              image.style.minWidth = '600px';
              image.style.width = '600px';
              image.style.position = 'relative';
              image.style.left = '600px';
              const rect = image.getBoundingClientRect();
              return {
                imageRight: rect.right,
                scrollWidth: document.documentElement.scrollWidth,
                viewportWidth: window.innerWidth,
              };
            }"""
        )
        assert (
            mutated["scrollWidth"] > mutated["viewportWidth"]
            or mutated["imageRight"] > mutated["viewportWidth"]
        ), mutated
        page.reload(wait_until="networkidle")
        _assert_registration_hero_is_contained(page)
        page.screenshot(
            path=Path(".tmp/screenshots/issue-134") / f"registration-no-js-{viewport_width}.png",
            full_page=True,
        )
    finally:
        context.close()


def test_registration_hero_fits_success_state_without_javascript(
    browser,
    live_server,
    cmp_registration_campaign: RegistrationCampaign,
) -> None:
    user = CustomUser.objects.create_user(
        username="cmp-registration-success",
        email="synthetic-success@example.invalid",
        password="test-password",
    )
    CourseRegistration.objects.create(
        campaign=cmp_registration_campaign,
        course=cmp_registration_campaign.current_course,
        user=user,
        email=user.email,
        name="Synthetic Student",
        country="Germany",
        region="Europe",
        role="data_engineer",
        accepted_newsletter=True,
    )
    client = Client()
    client.force_login(user)
    session_cookie = client.cookies["sessionid"]

    context = browser.new_context(
        java_script_enabled=False,
        viewport={"width": 390, "height": 844},
        color_scheme="light",
        reduced_motion="reduce",
    )
    page = context.new_page()
    try:
        page.context.add_cookies(
            [{"name": "sessionid", "value": session_cookie.value, "url": live_server.url}]
        )
        registration_path = reverse(
            "registration_campaign",
            kwargs={"campaign_slug": cmp_registration_campaign.slug},
        )
        response = page.goto(f"{live_server.url}{registration_path}", wait_until="networkidle")
        assert response is not None and response.status == 200
        expect(page.get_by_role("heading", name="You are already registered")).to_be_visible()
        expect(page.locator(".registration-panel form[data-registration-form]")).to_have_count(0)
        _assert_registration_hero_is_contained(page)
    finally:
        context.close()


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_no_database_course_catalog_uses_the_design_system_empty_state(
    page: Page,
    live_server,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    assert not Course.objects.exists()
    page.set_viewport_size(viewport)

    catalog = page.goto(f"{live_server.url}/courses", wait_until="networkidle")

    assert catalog is not None and catalog.status == 200
    expect(page.locator("main .courses-hero")).to_have_count(1)
    expect(page.locator("main #courses")).to_have_count(1)
    expect(page.get_by_text("zero courses · zero tuition", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name=ACTIVE_HEADING, exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name=FINISHED_HEADING, exact=True)).to_have_count(0)
    expect(page.get_by_role("heading", name=OPEN_HEADING, exact=True)).to_have_count(0)
    expect(page.get_by_text("No active courses right now.", exact=True)).to_be_visible()
    expect(page.get_by_text("Data Engineering Zoomcamp 2026", exact=True)).to_have_count(0)
    expect(page.locator("#course-families-heading")).to_have_count(0)
    expect(page.get_by_text("No active cohort coursework right now.", exact=True)).to_have_count(0)
    _assert_local_page_assets(page, live_server.url)
    _assert_no_horizontal_overflow(page)
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / f"course-catalog-empty-{suffix}.png", full_page=True)
    _capture_design_dark_mode(page, SCREENSHOTS / f"course-catalog-empty-dark-{suffix}.png")

    missing_detail = page.goto(f"{live_server.url}/courses/de-zoomcamp-2026")
    assert missing_detail is not None and missing_detail.status == 404
    expect(page.get_by_role("heading", name="Page not found")).to_be_visible()
    expect(page.locator('link[rel="canonical"]')).to_have_count(0)


@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_database_backed_empty_catalog_keeps_the_design_system_empty_composition(
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
    expect(page.locator("main .courses-hero")).to_have_count(1)
    expect(page.locator("main #courses")).to_have_count(1)
    expect(page.get_by_text("zero courses · zero tuition", exact=True)).to_be_visible()
    expect(page.get_by_text("No active courses right now.", exact=True)).to_be_visible()
    expect(page.locator("[data-course-row]")).to_have_count(0)
    expect(page.get_by_text("Data Engineering Zoomcamp 2026", exact=True)).to_have_count(0)
    _assert_local_page_assets(page, live_server.url)
    _assert_no_horizontal_overflow(page)
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOTS / f"course-catalog-hidden-{suffix}.png", full_page=True)
    _capture_design_dark_mode(page, SCREENSHOTS / f"course-catalog-hidden-dark-{suffix}.png")
