from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, override_settings
from django.urls import reverse
from playwright.sync_api import Browser, Page, expect

from accounts.studio_sessions import SESSION_REFERENCE_KEY, revoke_staff_session
from accounts.studio_test_support import make_studio_user
from content import catalogue, event_content
from content.docs_projection import docs_pages
from content.faq_data import faq_course, faq_questions
from core.accessibility_registry import (
    BEHAVIOR_SCENARIOS,
    CRITICAL_STATES,
    NO_JAVASCRIPT_PUBLIC_STATE_IDS,
    PUBLIC_TEST,
)
from core.models import AuditEvent
from course_management.datamailer_templates.accessibility import (
    render_current_transactional_email,
)
from courses.models import Cohort, HomeworkState, ProjectState, RegistrationCampaign
from events.identity import canonical_detail_path
from events.models import (
    Event,
    HistoricalRegistrationAggregateRevision,
    HistoricalRegistrationAggregateSlot,
    HistoricalRegistrationPointerDisplacement,
    HistoricalRegistrationSourceRun,
    HistoricalRegistrationTotalState,
)
from events.queries import published_event_records
from management_auth.models import APIPrincipal
from management_auth.services import create_principal
from playwright_tests.accessibility_support import (
    assert_accessible_page,
    axe_issues,
    chromium_blink_tree_issues,
    control_state_issues,
    focus_issues,
    media_date_issues,
    motion_issues,
    preserved_value_issues,
    skip_link_issues,
    structure_issues,
    target_size_issues,
    text_spacing_issues,
)
from playwright_tests.test_historical_registration_totals import (
    seed_total,
    seed_validated_overlap,
)
from test_support.factories import FactoryContext, create_current_scenario
from test_support.runtime import DEFAULT_FROZEN_AT, current_worker_id

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("_accessibility_settings"),
]

VIEWPORTS = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)
SCREENSHOTS = Path(".tmp/screenshots/issue-65")
# The one value the invalid-registration state types, so the same string is
# submitted and then asserted to have survived the error.
INVALID_FORM_COMPANY = "Synthetic Valid Company"


@pytest.fixture
def _accessibility_settings(monkeypatch):
    monkeypatch.setattr(
        "core.views.event_groups",
        lambda: event_content.event_groups(DEFAULT_FROZEN_AT),
    )
    with override_settings(
        ROOT_URLCONF="playwright_tests.accessibility_fixture_urls",
        DEVELOPMENT_OWNER_LOGIN_ENABLED=True,
        NOINDEX=False,
    ):
        yield


@dataclass(frozen=True, slots=True)
class Surface:
    path: str
    actor: str = "anonymous"
    expected_status: int = 200


@dataclass(frozen=True, slots=True)
class AccessibilityEnvironment:
    surfaces: dict[str, Surface]
    users: dict[str, object]
    credential_target_id: str
    objects: dict[str, object]


@dataclass(frozen=True, slots=True)
class PublicRenderedState:
    """A rendered public state shared by the JS and no-JS scenario executors."""

    identifier: str
    surface: str
    marker: str


def _faq_anchor_sample() -> dict[str, str]:
    """The FAQ question the anchored surface is audited against.

    The audit needs one real question with a stable anchor, not a particular
    one, so it takes the first question of the reviewed course rather than
    naming a question that an upstream FAQ edit could retire.
    """

    course = faq_course("ai-dev-tools-zoomcamp")
    assert course is not None, "the reviewed FAQ course is absent"
    question = faq_questions(course)[0]
    return {
        "public_path": course["public_path"],
        "question_id": question["id"],
        "question": question["question"],
    }


def _public_rendered_states(
    environment: AccessibilityEnvironment,
) -> tuple[PublicRenderedState, ...]:
    """Build the public state/marker map from the frozen projections and fixture environment."""

    event = environment.objects["event"]
    assert isinstance(event, dict)
    article = catalogue.articles()[0]
    book = catalogue.books()[0]
    public_course = catalogue.courses()[0]
    wiki = catalogue.wiki_pages()[0]
    faq = _faq_anchor_sample()
    speaker = event["speakers"][0]
    docs = next(
        page
        for page in docs_pages()
        if page["public_path"] == "/docs/courses/ai-dev-tools-zoomcamp/getting-started/"
    )

    return (
        PublicRenderedState("public.home", "home", "Events"),
        PublicRenderedState("public.blog-hub", "blog", "Latest Articles"),
        PublicRenderedState("public.podcast-hub", "podcast", "Podcast"),
        PublicRenderedState("public.books-hub", "books", "Book of the Week"),
        # The design system events index (issue #179, mockup 6c) leads with the mockup's
        # headline; "Events" survives only as the navigation label and the page title.
        PublicRenderedState("public.events-hub", "events", "Something happening every week"),
        PublicRenderedState("public.courses-hub", "courses", "Learn data skills"),
        PublicRenderedState("public.wiki-hub", "wiki", "Podcast Wiki"),
        PublicRenderedState("public.docs-hub", "docs", "Documentation"),
        PublicRenderedState("public.faq-hub", "faq", "Frequently Asked Questions"),
        PublicRenderedState("public.slack", "slack", "Slack"),
        PublicRenderedState("public.article-detail", "article", article["title"]),
        PublicRenderedState("public.podcast-transcript-media", "podcast-detail", "Transcript"),
        PublicRenderedState("public.book-detail", "book", book["title"]),
        PublicRenderedState("public.event-aggregate-speaker", "event", "3 registered"),
        PublicRenderedState("public.person-detail", "person", speaker["name"]),
        PublicRenderedState("public.course-detail", "course", public_course["title"]),
        PublicRenderedState("public.wiki-detail", "wiki-detail", wiki["title"]),
        PublicRenderedState("public.docs-nested", "docs-detail", docs["title"]),
        PublicRenderedState("public.faq-anchor", "faq-anchor", faq["question"]),
        PublicRenderedState("public.wiki-results", "wiki-results", "results for"),
        PublicRenderedState("public.wiki-zero-results", "wiki-zero", "0 results"),
        PublicRenderedState("public.wiki-graph", "wiki-graph", "Podcast Graph"),
        PublicRenderedState("public.wiki-special-pages", "wiki-special", "Special pages"),
        PublicRenderedState("public.missing-media", "missing-media", "unavailable"),
        PublicRenderedState("public.empty-state", "wiki-zero", "0 results"),
        PublicRenderedState("public.application-404", "missing", "Page not found"),
    )


def _restore_audit_event_id_default() -> None:
    """Discard Django's cached callable after deterministic factories patch the field."""

    audit_id_field = AuditEvent._meta.get_field("id")
    audit_id_field.default = uuid.uuid4
    audit_id_field.__dict__.pop("_get_default", None)


def _cookie(page: Page, live_server, user: object | None) -> None:
    page.context.clear_cookies()
    if user is None:
        return
    client = Client()
    client.force_login(user)
    page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )


@pytest.fixture
def accessibility_environment() -> AccessibilityEnvironment:
    namespace = f"accessibility-{current_worker_id()}"
    scenario = create_current_scenario(
        FactoryContext("issue-65-accessibility", namespace, DEFAULT_FROZEN_AT),
        bundle="adopted_courses",
        state="minimal_valid",
    ).by_factory()
    course = scenario["adopted_courses.course"].value
    campaign = scenario["adopted_courses.registration_campaign"].value
    learner = scenario["adopted_courses.enrollment"].value.student
    homework = scenario["adopted_courses.homework"].value
    project = scenario["adopted_courses.project"].value
    enrollment = scenario["adopted_courses.enrollment"].value
    review = scenario["adopted_courses.peer_review"].value
    reviewer = review.reviewer.student
    homework.state = HomeworkState.SCORED.value
    homework.save(update_fields=("state",))
    project.state = ProjectState.PEER_REVIEWING.value
    project.save(update_fields=("state",))

    empty_course = Cohort.objects.create(
        slug=f"synthetic-empty-{namespace}",
        title="Synthetic empty course",
        description="A deterministic empty learner state.",
    )
    error_campaign = RegistrationCampaign.objects.create(
        slug=f"synthetic-error-{namespace}",
        title="Synthetic validation campaign",
        edition_label="Synthetic edition",
        current_course=empty_course,
        is_active=True,
    )

    _restore_audit_event_id_default()

    site_admin = make_studio_user(
        username=f"accessibility-admin-{namespace}",
        roles=("site_admin",),
    )
    credential_user = make_studio_user(
        username=f"accessibility-credential-{namespace}",
        roles=("site_admin",),
    )
    denied_user = get_user_model().objects.create_user(
        username=f"accessibility-denied-{namespace}",
        email=f"denied-{namespace}@example.invalid",
    )
    access = Permission.objects.get(content_type__app_label="core", codename="access_studio")
    high_risk = Permission.objects.get(
        content_type__app_label="core",
        codename="execute_high_risk_fixture",
    )
    site_admin_permissions = tuple(
        Permission.objects.filter(group__user=site_admin).distinct().order_by("pk")
    )
    create_principal(
        kind=APIPrincipal.Kind.HUMAN,
        name="Synthetic accessibility administrator",
        identity_snapshot=f"human:site-admin:{namespace}",
        user=site_admin,
        permissions=site_admin_permissions,
    )
    credential_user.user_permissions.add(high_risk)
    create_principal(
        kind=APIPrincipal.Kind.HUMAN,
        name="Synthetic accessibility human",
        identity_snapshot=f"human:{namespace}",
        user=credential_user,
        permissions=(access, high_risk),
    )
    target = create_principal(
        kind=APIPrincipal.Kind.SERVICE,
        name="Synthetic accessibility service",
        identity_snapshot=f"service:{namespace}",
        permissions=(access,),
    )
    audit_id = uuid.uuid5(uuid.NAMESPACE_URL, f"https://web.dtcdev.click/{namespace}/audit")

    event = published_event_records()[0]
    database_event = Event.objects.filter(pk=event["identity_id"]).first()
    if database_event is not None:
        event = {**event, "public_path": canonical_detail_path(database_event.id)}
    person_path = event["speakers"][0]["public_path"]
    article = catalogue.articles()[0]
    podcast = next(record for record in catalogue.podcasts() if record.get("transcript"))
    book = catalogue.books()[0]
    public_course = catalogue.courses()[0]
    Cohort.objects.get_or_create(
        slug=public_course["slug"],
        defaults={
            "title": public_course["title"],
            "description": "A deterministic database-backed public course.",
            "visible": True,
        },
    )
    wiki = catalogue.wiki_pages()[0]
    faq = _faq_anchor_sample()
    course_route = {
        "course_slug": course.course.slug,
        "cohort_year": course.identifier,
    }
    empty_course_route = {
        "course_slug": empty_course.course.slug,
        "cohort_year": empty_course.identifier,
    }

    surfaces = {
        "home": Surface("/"),
        "blog": Surface("/blog"),
        "podcast": Surface("/podcast"),
        "books": Surface("/books"),
        "events": Surface("/events"),
        "courses": Surface("/courses"),
        "wiki": Surface("/wiki"),
        "docs": Surface("/docs/"),
        "faq": Surface("/faq/"),
        "slack": Surface("/slack"),
        "article": Surface(article["public_path"]),
        "podcast-detail": Surface(podcast["public_path"]),
        "book": Surface(book["public_path"]),
        "event": Surface(event["public_path"]),
        "person": Surface(person_path),
        "course": Surface(public_course["public_path"]),
        "wiki-detail": Surface(wiki["public_path"]),
        "docs-detail": Surface("/docs/courses/ai-dev-tools-zoomcamp/getting-started/"),
        "faq-anchor": Surface(f"{faq['public_path']}#{faq['question_id']}"),
        "wiki-results": Surface("/wiki?q=machine+learning"),
        "wiki-zero": Surface("/wiki?q=no-such-public-topic"),
        "wiki-graph": Surface("/wiki/graph"),
        "wiki-special": Surface("/wiki/special-pages"),
        "missing-media": Surface("/_accessibility/missing-media/"),
        "missing": Surface("/__accessibility_missing__", expected_status=404),
        "login": Surface("/accounts/login/"),
        "login-error": Surface("/accounts/login/"),
        "account-settings": Surface("/accounts/settings/", actor="learner"),
        # "/" branches on authentication (signed-in-home spec §3): the same URL
        # answers with the marketing page for "home" and with the member home
        # for a signed-in actor, so both branches are scanned.
        "member-home": Surface("/", actor="learner"),
        "account-welcome": Surface("/accounts/welcome/", actor="learner"),
        "identity-conflict": Surface(
            "/_accessibility/identity-conflict/",
            expected_status=409,
        ),
        "admin-login": Surface("/admin/login/?next=/admin/"),
        "studio-home": Surface("/studio/", actor="site-admin"),
        "credentials": Surface("/studio/access/api-credentials/", actor="site-admin"),
        "credential-copy": Surface(
            "/studio/_fixtures/credentials/",
            actor="credential",
        ),
        "audit-list": Surface("/studio/audit/", actor="site-admin"),
        "audit-detail": Surface(f"/studio/audit/{audit_id}/", actor="site-admin"),
        "historical-list": Surface(
            "/studio/events/historical-registration-totals/",
            actor="site-admin",
        ),
        "registration": Surface(
            reverse("registration_campaign", kwargs={"campaign_slug": campaign.slug})
        ),
        "registration-success": Surface(
            reverse("registration_campaign", kwargs={"campaign_slug": campaign.slug}),
            actor="learner",
        ),
        # Registration is account-owned (signed-in-home spec §8.3), so the form
        # that can be submitted invalidly is the signed-in "One final step"
        # card; the anonymous campaign carries the sign-in gate instead.
        "registration-error": Surface(
            reverse("registration_campaign", kwargs={"campaign_slug": error_campaign.slug}),
            actor="learner",
        ),
        "dashboard": Surface(
            reverse("dashboard", kwargs=course_route),
            actor="learner",
        ),
        "enrollment": Surface(
            reverse("enrollment", kwargs=course_route),
            actor="learner",
        ),
        "homework": Surface(
            reverse(
                "homework",
                kwargs={**course_route, "homework_slug": homework.slug},
            ),
            actor="learner",
        ),
        "project": Surface(
            reverse(
                "project",
                kwargs={**course_route, "project_slug": project.slug},
            ),
            actor="learner",
        ),
        "peer-review": Surface(
            reverse(
                "projects_eval",
                kwargs={**course_route, "project_slug": project.slug},
            ),
            actor="reviewer",
        ),
        "score": Surface(
            reverse(
                "leaderboard_score_breakdown",
                kwargs={**course_route, "enrollment_id": enrollment.id},
            ),
            actor="learner",
        ),
        "leaderboard": Surface(
            reverse("leaderboard", kwargs=course_route),
            actor="learner",
        ),
        "complaint": Surface(
            reverse(
                "leaderboard_complaint",
                kwargs={**course_route, "enrollment_id": enrollment.id},
            ),
            actor="reviewer",
        ),
        "course-empty": Surface(reverse("course", kwargs=empty_course_route)),
        "studio-courses": Surface(reverse("studio_courses_course_list"), actor="site-admin"),
        "studio-course-form": Surface(
            reverse("studio_courses_campaign_create"),
            actor="site-admin",
        ),
        "studio-course-table": Surface(
            reverse(
                "studio_courses_homework_submissions",
                kwargs={"course_slug": course.slug, "homework_slug": homework.slug},
            ),
            actor="site-admin",
        ),
    }
    return AccessibilityEnvironment(
        surfaces=surfaces,
        users={
            "learner": learner,
            "reviewer": reviewer,
            "site-admin": site_admin,
            "credential": credential_user,
            "denied": denied_user,
        },
        credential_target_id=str(target.id),
        objects={
            "audit_id": audit_id,
            "campaign": campaign,
            "course": course,
            "empty_course": empty_course,
            "enrollment": enrollment,
            "event": event,
            "homework": homework,
            "project": project,
            "registration": scenario["adopted_courses.course_registration"].value,
        },
    )


def _visit_surface(
    page: Page,
    live_server,
    environment: AccessibilityEnvironment,
    name: str,
) -> None:
    surface = environment.surfaces[name]
    _cookie(page, live_server, environment.users.get(surface.actor))
    response = page.goto(f"{live_server.url}{surface.path}", wait_until="domcontentloaded")
    assert response is not None and response.status == surface.expected_status, name
    page.wait_for_load_state("load")

    if name == "login-error":
        page.get_by_label("Email", exact=True).fill("invalid@example.invalid")
        page.get_by_label("Password", exact=True).fill("synthetic-invalid-password")
        page.get_by_role("button", name="Sign in").click()
        expect(page.get_by_role("alert")).to_be_visible()
    elif name == "credential-copy":
        page.context.grant_permissions(
            ["clipboard-read", "clipboard-write"],
            origin=live_server.url,
        )
        page.get_by_label("Service principal").select_option(environment.credential_target_id)
        page.get_by_role("button", name="Create credential").click()
        expect(page.get_by_role("heading", name="Copy this credential now")).to_be_visible()
    elif name == "registration-error":
        # A valid value the member typed, submitted without the required
        # newsletter consent: the error summary appears and the typed value has
        # to survive it.  The email is not a form field on this card any more —
        # it is the account's, shown read-only (§8.2).
        form = page.locator("[data-registration-form]")
        form.locator('[name="company_name"]').fill(INVALID_FORM_COMPANY)
        form.evaluate("element => element.submit()")
        expect(page.locator("[data-focus-error-summary]")).to_be_visible()


def _capture_deterministic_screenshot(page: Page, path: Path) -> None:
    """Capture only when two identical-source renders produce identical PNG bytes."""

    page.evaluate(
        """async () => {
            await document.fonts.ready;
            // A below-the-fold `loading="lazy"` image never fires load or error
            // until it scrolls into view, and an <img> with no src never fires
            // either, so the wait below must not park on them: switching the
            // lazy ones to eager starts their request here (article prose
            // figures since 289d0ea), and src-less images are skipped.  The
            // listener-only wait wedged the whole full-profile visual-evidence
            // pass at 0% CPU — the issue #193 deadlock.
            for (const image of document.images) {
                if (image.getAttribute('loading') === 'lazy') image.loading = 'eager';
            }
            await Promise.all(
                Array.from(document.images, image => {
                    if (!image.currentSrc && !image.getAttribute('src')) return Promise.resolve();
                    if (image.complete) return image.decode().catch(() => {});
                    return new Promise(resolve => {
                        image.addEventListener('load', resolve, {once: true});
                        image.addEventListener('error', resolve, {once: true});
                    });
                })
            );
        }"""
    )
    screenshot_options = {
        "animations": "disabled",
        "caret": "hide",
        "full_page": True,
    }
    page.screenshot(**screenshot_options)
    first = page.screenshot(path=path, **screenshot_options)
    second = page.screenshot(**screenshot_options)
    assert hashlib.sha256(first).digest() == hashlib.sha256(second).digest(), path.name


@pytest.mark.accessibility
@pytest.mark.core
@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_accessibility_core_smoke(
    page: Page,
    live_server,
    accessibility_environment: AccessibilityEnvironment,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    states = [state for state in CRITICAL_STATES if state.core_smoke and state.axe_surface]
    checked: set[str] = set()
    for state in states:
        surface = state.axe_surface
        assert surface is not None
        if surface in checked:
            continue
        _visit_surface(page, live_server, accessibility_environment, surface)
        assert_accessible_page(page, state.identifier)
        checked.add(surface)

    _visit_surface(page, live_server, accessibility_environment, "home")
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    _capture_deterministic_screenshot(
        page,
        SCREENSHOTS / f"shared-shell-{suffix}.png",
    )


@pytest.mark.accessibility
@pytest.mark.core
def test_homework_breadcrumb_target_spacing_ignores_closed_account_menu(
    page: Page,
    live_server,
    accessibility_environment: AccessibilityEnvironment,
) -> None:
    for width, height, suffix in ((390, 844, "mobile"), (1280, 900, "desktop")):
        page.set_viewport_size({"width": width, "height": height})
        _visit_surface(page, live_server, accessibility_environment, "homework")

        # The trail must be measured with the account menu shut: Chromium keeps a
        # stale rectangle for links inside a closed `details`, and counting those
        # would make the crumbs look crowded by controls nobody can reach.
        account_menu = page.locator("details.user-menu")
        expect(account_menu).not_to_have_attribute("open", "")
        hidden_courses_link = account_menu.locator("a.user-menu-item", has_text="Courses")
        expect(hidden_courses_link).to_have_count(0)

        geometry = page.locator(".breadcrumbs a[href]").evaluate_all(
            """nodes => nodes.map(node => {
              const rect = node.getBoundingClientRect();
              return {
                text: node.textContent.trim(),
                top: rect.top,
                left: rect.left,
                right: rect.right,
                bottom: rect.bottom,
                width: rect.width,
                height: rect.height,
              };
            })"""
        )
        # This fixture's cohort publishes no curriculum, so the trail is the
        # ancestors the shared submission document always names: courses, course
        # family, course edition.  A module-format cohort adds the module crumb;
        # that branch is covered in `courses/tests/test_homework_page_design.py`.
        # The homework itself is never a crumb — it is the h1 beneath the trail.
        assert len(geometry) == 3, geometry

        # WCAG 2.2 AA target size (2.5.8) is 24x24 CSS px, and the trail meets it
        # outright, so the spacing exception never has to be invoked for a crumb.
        # Issue #128's remediation had asserted the opposite arrangement — crumbs
        # under 24px relying on compensating space — and the design system rebuild
        # then over-corrected to a 44px row, which is the AAA (2.5.5) figure and
        # cost a whole row of the page.  The settled quiet trail draws a 2rem
        # crumb; measured on the homework route at both widths, the smallest is
        # "2026" at 29x32 CSS px (`templates/core/_design_system.html`,
        # `.breadcrumbs a`).  This asserts the AA floor, not a specific height,
        # so a later type-scale change is free as long as the floor holds.
        for crumb in geometry:
            assert crumb["width"] + 0.5 >= 24, (suffix, crumb, geometry)
            assert crumb["height"] + 0.5 >= 24, (suffix, crumb, geometry)

        # And no crumb sits on top of another: sufficient targets stay separate
        # targets, whether the trail is one row or wraps to two.
        for index, crumb in enumerate(geometry):
            for other in geometry[index + 1 :]:
                overlaps = (
                    crumb["left"] < other["right"]
                    and crumb["right"] > other["left"]
                    and crumb["top"] < other["bottom"]
                    and crumb["bottom"] > other["top"]
                )
                assert not overlaps, (suffix, crumb, other)

        assert target_size_issues(page, "learner.homework") == []

        screenshot_dir = Path(".tmp/screenshots/issue-128-breadcrumb-spacing-remediation")
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        _capture_deterministic_screenshot(page, screenshot_dir / f"homework-{suffix}.png")


@pytest.mark.accessibility
@pytest.mark.full
@pytest.mark.parametrize(("viewport", "suffix"), VIEWPORTS)
def test_accessibility_visual_evidence(
    page: Page,
    live_server,
    accessibility_environment: AccessibilityEnvironment,
    viewport: dict[str, int],
    suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    screenshot_surfaces = (
        ("shared-shell-focus", "home"),
        ("public-article", "article"),
        ("public-collection", "blog"),
        ("public-course", "course"),
        ("public-podcast", "podcast"),
        ("public-event", "event"),
        ("login", "login"),
        ("account-settings", "account-settings"),
        ("enrollment", "enrollment"),
        ("registration-error-focus", "registration-error"),
        ("studio-credentials", "credentials"),
        ("admin-login", "admin-login"),
    )
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    for name, surface in screenshot_surfaces:
        _visit_surface(page, live_server, accessibility_environment, surface)
        expect(page.locator("main h1")).to_be_visible()
        if surface == "home":
            explore = page.get_by_role("button", name="Menu")
            if explore.is_visible():
                explore.focus()
            else:
                page.locator("#site-navigation-links a").first.focus()
        _capture_deterministic_screenshot(
            page,
            SCREENSHOTS / f"{name}-{suffix}.png",
        )

    credential_surface = accessibility_environment.surfaces["credential-copy"]
    credential_user = accessibility_environment.users[credential_surface.actor]
    _cookie(page, live_server, credential_user)
    response = page.goto(
        f"{live_server.url}{credential_surface.path}",
        wait_until="domcontentloaded",
    )
    assert response is not None and response.status == 200
    expect(page.get_by_role("heading", name="Credential lifecycle")).to_be_visible()
    assert page.locator("[data-testid='one-time-token']").count() == 0
    _capture_deterministic_screenshot(
        page,
        SCREENSHOTS / f"credential-fixture-empty-{suffix}.png",
    )

    rendered_email = render_current_transactional_email("registration-confirmation")
    page.set_content(rendered_email.html, wait_until="domcontentloaded")
    assert axe_issues(page, "transactional-email.registration-confirmation") == []
    _capture_deterministic_screenshot(
        page,
        SCREENSHOTS / f"transactional-email-registration-html-images-disabled-{suffix}.png",
    )
    score_email = render_current_transactional_email("homework-score-notification")
    page.set_content(score_email.html, wait_until="domcontentloaded")
    assert axe_issues(page, "transactional-email.homework-score-notification") == []
    _capture_deterministic_screenshot(
        page,
        SCREENSHOTS / f"transactional-email-score-html-images-disabled-{suffix}.png",
    )
    page.set_content(
        "<!doctype html><html lang='en'><head><title>Registration confirmation plain email</title>"
        "<style>body{font:16px/1.6 monospace;margin:24px}"
        "pre{overflow-wrap:anywhere;white-space:pre-wrap}</style>"
        "</head><body><pre id='message'></pre></body></html>",
        wait_until="domcontentloaded",
    )
    page.locator("#message").evaluate(
        "(node, message) => { node.textContent = message; }",
        rendered_email.text,
    )
    _capture_deterministic_screenshot(
        page,
        SCREENSHOTS / f"transactional-email-registration-plain-{suffix}.png",
    )


class ScenarioRecorder:
    def __init__(
        self,
        page: Page,
        live_server,
        environment: AccessibilityEnvironment,
    ) -> None:
        self.page = page
        self.live_server = live_server
        self.environment = environment
        self.checked: set[str] = set()

    def scan(
        self,
        state: str,
        surface: str,
        *,
        text: str | None = None,
    ) -> None:
        _visit_surface(self.page, self.live_server, self.environment, surface)
        if text is not None:
            expect(
                self.page.locator("main, [role='main']").get_by_text(text, exact=False).first
            ).to_be_visible()
        self.scan_current(state)

    def scan_current(self, state: str) -> None:
        assert state not in self.checked, f"state exercised twice: {state}"
        # A form error page is focused by script as the document finishes
        # loading: the shared error summary (``data-focus-error-summary``,
        # ``tabindex="-1"``) on the public shell, and the inline
        # ``.focus()`` call on the studio shell.  The visible-marker waits
        # above resolve as soon as the markup is parsed, before those
        # end-of-body scripts have run, so walking tab order at that moment
        # lets the programmatic focus land mid-walk and be read as an
        # untracked control (issue #193's masked signature, reachable once
        # #177 fixed the events-hub ids that used to abort this test
        # earlier).  Every scan starts from a settled document instead —
        # the same load-event discipline as the goto in ``_visit_surface``
        # and the deterministic-screenshot settle helper.
        self.page.wait_for_load_state("load")
        assert_accessible_page(self.page, state, comprehensive=True)
        self.checked.add(state)

    def contract(self, state: str, path: str, status: int, *, location: str | None = None) -> None:
        response = self.page.request.get(
            f"{self.live_server.url}{path}",
            max_redirects=0,
        )
        assert response.status == status, state
        if location is not None:
            assert response.headers["location"] == location, state
        assert state not in self.checked, f"state exercised twice: {state}"
        self.checked.add(state)


def _public_scenario(recorder: ScenarioRecorder) -> set[str]:
    event = recorder.environment.objects["event"]
    assert isinstance(event, dict)
    seed_total(event, count=3, complete=True)
    for state in _public_rendered_states(recorder.environment):
        recorder.scan(state.identifier, state.surface, text=state.marker)

    for state, path in (
        ("public.people-removed", "/people"),
        ("public.people-slash-removed", "/people/"),
        ("public.people-html-removed", "/people.html"),
    ):
        recorder.contract(state, path, 404)
        response = recorder.page.goto(f"{recorder.live_server.url}{path}")
        assert response is not None and response.status == 404
        expect(recorder.page.get_by_role("heading", name="Page not found")).to_be_visible()

    redirect = recorder.page.request.get(
        f"{recorder.live_server.url}/articles.html?source=accessibility",
        max_redirects=0,
    )
    assert redirect.status == 301
    assert redirect.headers["location"] == "/blog?source=accessibility"
    destination = recorder.page.goto(
        f"{recorder.live_server.url}/blog?source=accessibility",
        wait_until="domcontentloaded",
    )
    assert destination is not None and destination.status == 200
    expect(recorder.page.get_by_role("heading", name="Latest Articles")).to_be_visible()
    recorder.scan_current("public.approved-redirect-destination")
    return recorder.checked


def _no_javascript_public_policy_issues(
    environment: AccessibilityEnvironment,
) -> list[str]:
    """Return bounded diagnostics for the explicit rendered no-JavaScript policy."""

    policy_ids = tuple(NO_JAVASCRIPT_PUBLIC_STATE_IDS)
    issues: list[str] = []
    if len(policy_ids) != 26:
        issues.append(f"policy must contain exactly 26 IDs, found {len(policy_ids)}")
    duplicate_policy_ids = sorted(
        identifier for identifier in set(policy_ids) if policy_ids.count(identifier) > 1
    )
    if duplicate_policy_ids:
        issues.append(f"policy IDs are duplicated: {duplicate_policy_ids}")

    states_by_id = {state.identifier: state for state in CRITICAL_STATES}
    for identifier in policy_ids:
        state = states_by_id.get(identifier)
        if state is None:
            issues.append(f"policy ID is absent from CRITICAL_STATES: {identifier}")
            continue
        if state.group != "public":
            issues.append(f"policy ID changed group: {identifier} group={state.group!r}")
        if state.axe_surface is None:
            issues.append(f"policy ID has no rendered surface: {identifier}")
        if state.route_contract:
            issues.append(f"route-contract ID cannot be rendered no-JS: {identifier}")
        if state.behavior_test != PUBLIC_TEST:
            issues.append(
                f"policy ID is not assigned to the public scenario: "
                f"{identifier} behavior={state.behavior_test!r}"
            )

    rendered_states = _public_rendered_states(environment)
    fixture_ids = tuple(state.identifier for state in rendered_states)
    duplicate_fixture_ids = sorted(
        identifier for identifier in set(fixture_ids) if fixture_ids.count(identifier) > 1
    )
    if duplicate_fixture_ids:
        issues.append(f"fixture state IDs are duplicated: {duplicate_fixture_ids}")
    policy_set = set(policy_ids)
    fixture_set = set(fixture_ids)
    for identifier in sorted(policy_set - fixture_set):
        issues.append(f"policy ID has no fixture route/marker: {identifier}")
    for identifier in sorted(fixture_set - policy_set):
        issues.append(f"fixture state is not classified by policy: {identifier}")

    for rendered in rendered_states:
        surface = environment.surfaces.get(rendered.surface)
        if surface is None:
            issues.append(
                f"fixture route is missing: state={rendered.identifier} surface={rendered.surface}"
            )
            continue
        if not surface.path:
            issues.append(
                f"fixture route is empty: state={rendered.identifier} surface={rendered.surface}"
            )
        expected_status = 404 if rendered.identifier == "public.application-404" else 200
        if surface.expected_status != expected_status:
            issues.append(
                f"fixture status disagrees with the public-read policy: "
                f"state={rendered.identifier} route={surface.path} "
                f"expected={expected_status} fixture={surface.expected_status}"
            )
        registry_state = states_by_id.get(rendered.identifier)
        if registry_state is not None and registry_state.axe_surface != rendered.surface:
            issues.append(
                f"fixture surface disagrees with registry: state={rendered.identifier} "
                f"registry={registry_state.axe_surface!r} fixture={rendered.surface!r}"
            )
        if not rendered.marker.strip():
            issues.append(f"fixture marker is empty: state={rendered.identifier}")
    return issues


def _assert_no_javascript_public_state(
    page: Page,
    live_server,
    environment: AccessibilityEnvironment,
    rendered: PublicRenderedState,
    *,
    viewport_name: str,
) -> None:
    surface = environment.surfaces[rendered.surface]
    diagnostic = f"state={rendered.identifier} route={surface.path} viewport={viewport_name}"
    try:
        _visit_surface(page, live_server, environment, rendered.surface)
        main = page.locator("main, [role='main']")
        expect(main).to_have_count(1)
        primary_heading = main.locator("h1, [role='heading'][aria-level='1']")
        expect(primary_heading).to_have_count(1)
        expect(primary_heading).to_be_visible()
        expect(page.locator(".site-navigation-links")).to_be_visible()
        expect(main.get_by_text(rendered.marker, exact=False).first).to_be_visible()

        geometry = page.evaluate(
            """() => ({
                viewport_width: window.innerWidth,
                document_width: document.documentElement.scrollWidth,
                body_width: document.body.scrollWidth,
            })"""
        )
        assert geometry["document_width"] <= geometry["viewport_width"] + 1, (
            "horizontal overflow exceeds 1px: "
            f"document={geometry['document_width']} body={geometry['body_width']} "
            f"viewport={geometry['viewport_width']}"
        )

        body_text = page.locator("body").inner_text().strip()
        assert body_text, "rendered body is blank"
        lowered = body_text.casefold()
        forbidden_markers = (
            "traceback (most recent call last)",
            "django debug toolbar",
            "exception type",
            "request information",
            "internal server error",
        )
        for marker in forbidden_markers:
            assert marker not in lowered, f"error/debug output contains {marker!r}"

    except Exception as exc:
        raise AssertionError(f"{diagnostic}: {exc}") from exc


@pytest.mark.accessibility
@pytest.mark.full
def test_javascript_off_public_policy_rejects_missing_fixture_route(
    accessibility_environment: AccessibilityEnvironment,
) -> None:
    """A test-only route removal cannot silently shrink the no-JS matrix."""

    missing_route_surfaces = {
        name: surface
        for name, surface in accessibility_environment.surfaces.items()
        if name != "article"
    }
    broken_environment = replace(
        accessibility_environment,
        surfaces=missing_route_surfaces,
    )
    issues = _no_javascript_public_policy_issues(broken_environment)
    assert any(
        "public.article-detail" in issue and "fixture route is missing" in issue for issue in issues
    ), issues


def _account_scenario(recorder: ScenarioRecorder) -> set[str]:
    recorder.scan("account.signed-out-login", "login", text="Sign In")
    recorder.scan("account.invalid-login", "login-error", text="Sign-in was not successful")
    with override_settings(DEVELOPMENT_OWNER_LOGIN_ENABLED=False):
        recorder.scan(
            "account.unavailable-login",
            "login",
            text="Sign-in is temporarily unavailable",
        )

    recorder.scan("account.settings", "account-settings", text="Account settings")
    menu = recorder.page.locator('summary[aria-label="Account menu"]')
    menu.click()
    expect(
        recorder.page.locator(".user-menu-panel").get_by_role("link", name="Courses", exact=True)
    ).to_have_count(0)
    recorder.scan_current("account.authenticated-navigation")

    # "/" branches on authentication rather than redirecting (§3), so the same
    # path that carries the marketing hero for "public.home" carries the member
    # home here, and onboarding is the page it sends an incomplete profile to.
    recorder.scan("account.member-home", "member-home", text="Getting started")
    recorder.scan("account.welcome", "account-welcome", text="About you")

    recorder.scan(
        "account.identity-conflict",
        "identity-conflict",
        text="could not safely link",
    )

    _cookie(
        recorder.page,
        recorder.live_server,
        recorder.environment.users["learner"],
    )
    recorder.page.goto(f"{recorder.live_server.url}/accounts/logout/")
    recorder.page.get_by_role("button", name="Sign Out", exact=False).click()
    recorder.page.goto(f"{recorder.live_server.url}/accounts/login/")
    # Exact match, same as the owner-credentials suite: the design system sign-in
    # page also carries an sr-visible "Sign in with your DataTalks.Club account"
    # panel heading, which a substring lookup resolves as a second heading.
    # Unreachable while the events-hub duplicate ids aborted this test earlier
    # (issue #193 class 1); #177's merged fix lets the walk reach it.
    expect(recorder.page.get_by_role("heading", name="Sign In", exact=True)).to_be_visible()
    recorder.scan_current("account.logout-return")

    staff_user = recorder.environment.users["site-admin"]
    client = Client()
    client.force_login(staff_user)
    session_id = uuid.UUID(client.session[SESSION_REFERENCE_KEY])
    recorder.page.context.clear_cookies()
    recorder.page.context.add_cookies(
        [
            {
                "name": settings.SESSION_COOKIE_NAME,
                "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                "url": recorder.live_server.url,
            }
        ]
    )
    loaded = recorder.page.goto(f"{recorder.live_server.url}/studio/")
    assert loaded is not None and loaded.status == 200
    revoke_staff_session(session_id, user=staff_user)
    revoked = recorder.page.reload()
    assert revoked is not None and revoked.status == 403
    assert "Studio access denied" in recorder.page.content()
    recorder.checked.add("account.revoked-session-return")

    _cookie(
        recorder.page,
        recorder.live_server,
        recorder.environment.users["denied"],
    )
    denied = recorder.page.goto(f"{recorder.live_server.url}/studio/audit/")
    assert denied is not None and denied.status == 403
    assert "Studio access denied" in recorder.page.content()
    recorder.checked.add("account.safe-denial")
    return recorder.checked


def _management_scenario(recorder: ScenarioRecorder) -> set[str]:
    # The audit list with nothing in it.  The reviewed reference data is imported
    # through the very services the studio audits, so a session opens on a list that
    # already holds their release events, and an audit record is append-only and
    # cannot be removed to get back to an unwritten database.  The state is reached
    # the way a reader reaches it instead: a filter that matches no event, which is
    # the case the page's own empty copy is written for.
    _visit_surface(recorder.page, recorder.live_server, recorder.environment, "audit-list")
    recorder.page.get_by_label("Action").fill("no.such.audited.action")
    recorder.page.get_by_role("button", name="Apply filters").click()
    expect(
        recorder.page.locator("main, [role='main']")
        .get_by_text("No audit events match these filters.", exact=False)
        .first
    ).to_be_visible()
    recorder.scan_current("studio.audit-empty")
    audit_id = recorder.environment.objects["audit_id"]
    assert isinstance(audit_id, uuid.UUID)
    AuditEvent.objects.create(
        id=audit_id,
        actor_ref="synthetic-accessibility-actor",
        action="accessibility.fixture",
        target_type="accessibility.fixture",
        target_label="synthetic fixture",
        outcome=AuditEvent.Outcome.SUCCEEDED,
        changes={},
        metadata={"summary": "Synthetic accessibility audit state"},
    )
    _visit_surface(recorder.page, recorder.live_server, recorder.environment, "audit-list")
    recorder.page.get_by_label("Action").fill("accessibility.fixture")
    recorder.page.get_by_role("button", name="Apply filters").click()
    expect(recorder.page.get_by_role("link", name="accessibility.fixture")).to_be_visible()
    recorder.scan_current("studio.audit-filter")
    recorder.scan("studio.audit-detail", "audit-detail", text="Audit event")

    recorder.scan("management.admin-entry", "admin-login", text="Log in")
    recorder.scan("management.studio-entry", "studio-home", text="Studio")
    recorder.scan("studio.home", "studio-home", text="Studio")

    recorder.scan("studio.credential-empty", "credentials", text="No service credentials")
    _visit_surface(
        recorder.page,
        recorder.live_server,
        recorder.environment,
        "credential-copy",
    )
    recorder.scan_current("studio.credential-copy")

    _cookie(
        recorder.page,
        recorder.live_server,
        recorder.environment.users["credential"],
    )
    recorder.page.goto(f"{recorder.live_server.url}/studio/_fixtures/credentials/away/")
    recorder.page.goto(f"{recorder.live_server.url}/studio/_fixtures/credentials/")
    expect(recorder.page.get_by_text("Status active", exact=False)).to_be_visible()
    expect(recorder.page.locator("[data-testid='one-time-token']")).to_have_count(0)
    recorder.scan_current("studio.credential-list")
    recorder.page.get_by_role("button", name="Rotate Browser fixture credential").click()
    expect(recorder.page.get_by_role("heading", name="Copy this credential now")).to_be_visible()
    recorder.scan_current("studio.credential-rotate")
    recorder.page.goto(f"{recorder.live_server.url}/studio/_fixtures/credentials/")
    successor = recorder.page.locator("article[data-credential-id]").filter(has_text="active").first
    successor.get_by_role("button", name="Revoke Browser fixture credential").click()
    expect(recorder.page.get_by_text("Credential revoked", exact=False).first).to_be_visible()
    recorder.scan_current("studio.credential-revoke")

    for state, path in (
        ("studio.credential-denied", "/studio/access/api-credentials/"),
        ("studio.audit-denied", "/studio/audit/"),
    ):
        _cookie(
            recorder.page,
            recorder.live_server,
            recorder.environment.users["denied"],
        )
        response = recorder.page.goto(f"{recorder.live_server.url}{path}")
        assert response is not None and response.status == 403
        assert "Studio access denied" in recorder.page.content()
        recorder.checked.add(state)
    return recorder.checked


def _historical_scenario(recorder: ScenarioRecorder) -> set[str]:
    HistoricalRegistrationPointerDisplacement.objects.all().delete()
    HistoricalRegistrationAggregateSlot.objects.all().delete()
    HistoricalRegistrationTotalState.objects.all().delete()
    HistoricalRegistrationAggregateRevision.objects.all().delete()
    HistoricalRegistrationSourceRun.objects.all().delete()
    recorder.scan("historical.empty", "historical-list", text="No source runs")
    event = recorder.environment.objects["event"]
    assert isinstance(event, dict)
    seed_total(event, count=3, complete=True)
    namespace = f"issue-65-historical-{recorder.page.viewport_size['width']}"
    historical = create_current_scenario(
        FactoryContext("issue-65-historical", namespace, DEFAULT_FROZEN_AT),
        bundle="historical_event_totals",
        state="minimal_valid",
    ).by_factory()
    _restore_audit_event_id_default()
    run = historical["historical_event_totals.historical_source_run"].value
    recorder.environment.surfaces["historical-detail"] = Surface(
        f"/studio/events/historical-registration-totals/{run.id}/",
        actor="site-admin",
    )
    recorder.scan("historical.list", "historical-list", text="Source runs")
    recorder.scan("historical.detail", "historical-detail", text="active")

    HistoricalRegistrationSourceRun.objects.filter(pk=run.pk).update(
        state=HistoricalRegistrationSourceRun.State.VALIDATED,
    )
    HistoricalRegistrationAggregateRevision.objects.filter(source_run=run).update(
        state=HistoricalRegistrationAggregateRevision.State.VALIDATED,
    )
    recorder.scan("historical.validation-success", "historical-detail", text="validated")
    HistoricalRegistrationSourceRun.objects.filter(pk=run.pk).update(
        state=HistoricalRegistrationSourceRun.State.QUARANTINED,
        reason_codes=["unsupported_schema"],
    )
    HistoricalRegistrationAggregateRevision.objects.filter(source_run=run).update(
        state=HistoricalRegistrationAggregateRevision.State.QUARANTINED,
    )
    recorder.scan(
        "historical.unsupported-quarantined",
        "historical-detail",
        text="quarantined",
    )

    preview_path = f"/studio/events/{event['identity_id']}/registration-total/"
    recorder.environment.surfaces["historical-preview"] = Surface(
        preview_path,
        actor="site-admin",
    )
    recorder.scan(
        "historical.activation-preview",
        "historical-preview",
        text="Registration total preview",
    )

    overlap_run = seed_validated_overlap(event, suffix=namespace)
    recorder.environment.surfaces["historical-overlap"] = Surface(
        f"/studio/events/historical-registration-totals/{overlap_run.id}/",
        actor="site-admin",
    )
    _visit_surface(recorder.page, recorder.live_server, recorder.environment, "historical-overlap")
    recorder.page.get_by_label("Confirm activate").check()
    with recorder.page.expect_response(
        lambda candidate: candidate.url.endswith(f"/{overlap_run.id}/activate/")
    ) as overlap_response:
        recorder.page.get_by_role("button", name="Activate").click()
    assert overlap_response.value.status == 409
    expect(recorder.page.get_by_role("alert")).to_be_visible()
    recorder.scan_current("historical.overlap-conflict")

    active_run = (
        HistoricalRegistrationSourceRun.objects.filter(
            aggregate_revisions__mapping__event_id=event["identity_id"],
            state=HistoricalRegistrationSourceRun.State.ACTIVE,
        )
        .distinct()
        .first()
    )
    assert active_run is not None
    recorder.environment.surfaces["historical-active"] = Surface(
        f"/studio/events/historical-registration-totals/{active_run.id}/",
        actor="site-admin",
    )
    _visit_surface(recorder.page, recorder.live_server, recorder.environment, "historical-active")
    recorder.page.get_by_label("Confirm rollback").check()
    recorder.page.get_by_role("button", name="Rollback").click()
    expect(recorder.page.get_by_text("rolled_back", exact=True).first).to_be_visible()
    recorder.scan_current("historical.rollback")

    _cookie(
        recorder.page,
        recorder.live_server,
        recorder.environment.users["denied"],
    )
    denied = recorder.page.goto(
        f"{recorder.live_server.url}/studio/events/historical-registration-totals/"
    )
    assert denied is not None and denied.status == 403
    assert "Studio access denied" in recorder.page.content()
    recorder.checked.add("historical.denied")
    return recorder.checked


def _learner_scenario(recorder: ScenarioRecorder) -> set[str]:
    simple_states = (
        (
            "learner.signed-out-registration",
            "registration",
            "Create your free account to register",
        ),
        ("learner.dashboard", "dashboard", None),
        ("learner.enrollment", "enrollment", "Edit Enrollment Details"),
        ("learner.homework", "homework", None),
        ("learner.project", "project", None),
        ("learner.peer-review", "peer-review", None),
        ("learner.score", "score", None),
        ("learner.leaderboard", "leaderboard", None),
        ("learner.complaint", "complaint", None),
        ("learner.empty", "course-empty", None),
        ("learner.success", "registration-success", "already registered"),
        ("learner.invalid-form", "registration-error", "Check the highlighted fields"),
    )
    for state, surface, marker in simple_states:
        recorder.scan(state, surface, text=marker)

    enrollment = recorder.environment.objects["enrollment"]
    enrollment.certificate_url = "https://example.invalid/synthetic-certificate.pdf"
    enrollment.save(update_fields=("certificate_url",))
    recorder.scan(
        "learner.certificate-graduation",
        "enrollment",
        text="Download",
    )

    campaign = recorder.environment.objects["campaign"]
    campaign.is_active = False
    campaign.save(update_fields=("is_active",))
    surface = recorder.environment.surfaces["registration"]
    _cookie(recorder.page, recorder.live_server, None)
    stale = recorder.page.goto(f"{recorder.live_server.url}{surface.path}")
    assert stale is not None and stale.status == 404
    expect(recorder.page.get_by_role("heading", name="Page not found")).to_be_visible()
    recorder.scan_current("learner.stale-denied")

    homework = recorder.environment.objects["homework"]
    homework.description = " ".join(["Long synthetic homework guidance"] * 120)
    homework.save(update_fields=("description",))
    recorder.scan("learner.long-content", "homework", text="Long synthetic homework guidance")
    return recorder.checked


def _studio_courses_scenario(recorder: ScenarioRecorder) -> set[str]:
    recorder.scan("studio-courses.list", "studio-courses", text="Courses")
    recorder.scan(
        "studio-courses.form",
        "studio-course-form",
        text="Create registration landing page",
    )
    recorder.scan("studio-courses.table", "studio-course-table", text="Submissions")

    _visit_surface(recorder.page, recorder.live_server, recorder.environment, "studio-course-form")
    course = recorder.environment.objects["course"]
    recorder.page.get_by_label("Title").fill("Accessible current campaign")
    recorder.page.get_by_label("URL slug").fill("accessible-current-campaign")
    recorder.page.get_by_label("Edition label").fill("2026 accessibility cohort")
    recorder.page.get_by_label("Current course edition").select_option(str(course.pk))
    recorder.page.get_by_role("button", name="Create landing page").click()
    expect(recorder.page.get_by_text("Registration landing page created.")).to_be_visible()
    recorder.scan_current("studio-courses.confirmation")

    _visit_surface(recorder.page, recorder.live_server, recorder.environment, "studio-course-form")
    recorder.page.locator("form").first.evaluate("form => form.submit()")
    expect(recorder.page.get_by_text("This field is required.").first).to_be_visible()
    recorder.scan_current("studio-courses.error")

    _cookie(
        recorder.page,
        recorder.live_server,
        recorder.environment.users["denied"],
    )
    denied = recorder.page.request.get(
        f"{recorder.live_server.url}/studio/courses",
        max_redirects=0,
    )
    assert denied.status == 302
    assert "/accounts/login/" in denied.headers["location"]
    recorder.checked.add("studio-courses.denied")

    _cookie(
        recorder.page,
        recorder.live_server,
        recorder.environment.users["site-admin"],
    )
    legacy = recorder.page.request.get(
        f"{recorder.live_server.url}/cadmin?source=accessibility",
        max_redirects=0,
    )
    assert legacy.status == 302
    assert legacy.headers["location"] == "/studio/courses?source=accessibility"
    recorder.checked.add("studio-courses.legacy-redirect")
    return recorder.checked


ScenarioExecutor = Callable[[ScenarioRecorder], set[str]]
STATE_SCENARIO_EXECUTORS: dict[str, ScenarioExecutor] = {
    "management-current-states": _management_scenario,
    "public-current-states": _public_scenario,
    "account-current-states": _account_scenario,
    "historical-current-states": _historical_scenario,
    "learner-current-states": _learner_scenario,
    "studio-courses-current-states": _studio_courses_scenario,
}


@pytest.mark.accessibility
@pytest.mark.full
@pytest.mark.parametrize(("viewport", "_suffix"), VIEWPORTS)
def test_complete_accessibility_registry(
    page: Page,
    live_server,
    accessibility_environment: AccessibilityEnvironment,
    viewport: dict[str, int],
    _suffix: str,
) -> None:
    page.set_viewport_size(viewport)
    expected_scenarios = {state.behavior_test for state in CRITICAL_STATES}
    assert expected_scenarios == BEHAVIOR_SCENARIOS
    assert STATE_SCENARIO_EXECUTORS.keys() == BEHAVIOR_SCENARIOS
    all_checked: set[str] = set()
    for scenario_key, executor in STATE_SCENARIO_EXECUTORS.items():
        recorder = ScenarioRecorder(page, live_server, accessibility_environment)
        checked = executor(recorder)
        expected = {
            state.identifier for state in CRITICAL_STATES if state.behavior_test == scenario_key
        }
        assert checked == expected, (
            scenario_key,
            sorted(expected - checked),
            sorted(checked - expected),
        )
        assert not all_checked.intersection(checked)
        all_checked.update(checked)
    assert all_checked == {state.identifier for state in CRITICAL_STATES}


@pytest.mark.accessibility
@pytest.mark.full
def test_named_chromium_blink_accessibility_tree_contracts(
    page: Page,
    live_server,
    accessibility_environment: AccessibilityEnvironment,
) -> None:
    """Record named browser-engine evidence while leaving the screen-reader gate pending."""

    cases = (
        (
            "public.home",
            "home",
            ("banner", "navigation", "main", "contentinfo", "heading"),
            ("Events",),
        ),
        (
            "public.podcast-transcript-media",
            "podcast-detail",
            ("main", "heading", "link"),
            ("Transcript",),
        ),
        (
            "public.event-aggregate-speaker",
            "event",
            ("main", "heading", "link"),
            ("CEST",),
        ),
        (
            "learner.invalid-form",
            "registration-error",
            ("main", "heading", "alert", "textbox", "button"),
            ("Check the highlighted fields",),
        ),
        (
            "studio.credential-copy",
            "credential-copy",
            ("main", "heading", "button", "status"),
            ("Copy this credential now", "Copy credential"),
        ),
        (
            "studio-courses.table",
            "studio-course-table",
            ("main", "heading", "table"),
            ("Submissions",),
        ),
    )
    records: list[dict[str, object]] = []
    for state, surface, roles, names in cases:
        _visit_surface(page, live_server, accessibility_environment, surface)
        evidence, issues = chromium_blink_tree_issues(
            page,
            state,
            required_roles=roles,
            required_name_fragments=names,
        )
        assert issues == []
        records.append({"state": state, **evidence})

    products = {str(record["browser"]) for record in records}
    assert len(products) == 1
    assert next(iter(products)).startswith(("Chrome/", "Chromium/", "HeadlessChrome/"))
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    (SCREENSHOTS / "named-browser-engine-evidence.json").write_text(
        json.dumps(
            {
                "fixture_clock": DEFAULT_FROZEN_AT.isoformat(),
                "human_screen_reader": "pending independent manual test",
                "records": records,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.accessibility
@pytest.mark.core
def test_keyboard_focus_status_and_form_error_contracts(
    page: Page,
    live_server,
    accessibility_environment: AccessibilityEnvironment,
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    _visit_surface(page, live_server, accessibility_environment, "home")
    page.keyboard.press("Tab")
    expect(page.locator(".skip-link")).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator("#main-content")).to_be_focused()

    menu = page.get_by_role("button", name="Menu")
    menu.focus()
    page.keyboard.press("Enter")
    expect(menu).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("Escape")
    expect(menu).to_have_attribute("aria-expanded", "false")
    expect(menu).to_be_focused()

    _visit_surface(page, live_server, accessibility_environment, "credential-copy")
    page.get_by_role("button", name="Copy credential").click()
    expect(page.locator("#copy-status")).to_contain_text("Credential copied")

    _visit_surface(page, live_server, accessibility_environment, "registration-error")
    assert (
        preserved_value_issues(
            page,
            "learner.invalid-form",
            {"company_name": INVALID_FORM_COMPANY},
        )
        == []
    )
    error_summary = page.locator("[data-focus-error-summary]")
    expect(error_summary).to_be_focused()
    assert error_summary.get_by_role("link").count() > 0


@pytest.mark.accessibility
@pytest.mark.full
def test_reflow_zoom_spacing_reduced_motion_and_forced_colors(
    page: Page,
    live_server,
    accessibility_environment: AccessibilityEnvironment,
) -> None:
    namespace = f"issue-65-reflow-{current_worker_id()}"
    historical = create_current_scenario(
        FactoryContext("issue-65-reflow", namespace, DEFAULT_FROZEN_AT),
        bundle="historical_event_totals",
        state="minimal_valid",
    ).by_factory()
    _restore_audit_event_id_default()
    run = historical["historical_event_totals.historical_source_run"].value
    accessibility_environment.surfaces["historical-detail"] = Surface(
        f"/studio/events/historical-registration-totals/{run.id}/",
        actor="site-admin",
    )
    cdp = page.context.new_cdp_session(page)
    for surface in (
        "article",
        "wiki-zero",
        "course-empty",
        "registration-success",
        "registration-error",
        "identity-conflict",
        "credential-copy",
        "homework",
        "historical-detail",
        "studio-course-table",
    ):
        page.set_viewport_size({"width": 320, "height": 800})
        _visit_surface(page, live_server, accessibility_environment, surface)
        assert structure_issues(page, f"{surface}.320px") == []
        cdp.send("Emulation.setPageScaleFactor", {"pageScaleFactor": 2})
        expect(page.locator("main h1")).to_be_visible()
        assert page.evaluate("visualViewport.scale") == 2
        assert structure_issues(page, f"{surface}.200-percent") == []
        cdp.send("Emulation.setPageScaleFactor", {"pageScaleFactor": 1})
        assert text_spacing_issues(page, surface) == []
        assert motion_issues(page, surface) == []
        page.emulate_media(forced_colors="active", reduced_motion="reduce")
        assert focus_issues(page, f"{surface}.forced-colors") == []
        page.emulate_media(forced_colors="none", reduced_motion="reduce")


FAILURE_FIXTURES = (
    ("missing-landmark-h1", "<button>Named</button>", "expected 1 mains"),
    (
        "broken-skip",
        '<header></header><a class="skip-link" href="#missing">Skip</a>'
        "<main><h1>Title</h1></main><footer></footer>",
        "skip target",
    ),
    (
        "stale-expanded",
        '<header></header><main><h1>Title</h1><button aria-expanded="stale">'
        "Menu</button></main><footer></footer>",
        "stale or invalid",
    ),
    (
        "unlinked-error",
        '<header></header><main><h1>Title</h1><input id="field" '
        'aria-invalid="true"></main><footer></footer>',
        "no linked field error",
    ),
    (
        "silent-status",
        '<header></header><main><h1>Title</h1><div aria-live="off">'
        "Saved</div></main><footer></footer>",
        "no polite announcement",
    ),
    (
        "undersized-target",
        "<header></header><main><h1>Title</h1><button "
        'style="width:10px;height:10px;padding:0">X</button><button '
        'style="width:10px;height:10px;padding:0">Y</button></main><footer></footer>',
        "undersized target",
    ),
    (
        "overflow",
        '<header></header><main><h1>Title</h1><div style="width:2000px">'
        "Wide</div></main><footer></footer>",
        "overflows horizontally",
    ),
    (
        "motion",
        "<style>button{transition:all 2s}</style><header></header>"
        "<main><h1>Title</h1><button>Move</button></main><footer></footer>",
        "non-essential motion",
    ),
    (
        "missing-alt",
        '<header></header><main><h1>Title</h1><img src="data:,fixture"></main><footer></footer>',
        "missing an alt",
    ),
    (
        "missing-transcript",
        "<header></header><main><h1>Title</h1><audio controls></audio></main><footer></footer>",
        "neither captions nor transcript",
    ),
    (
        "missing-timezone",
        "<header></header><main><h1>Title</h1><time "
        'datetime="2026-08-10T12:00:00Z">August 10 at noon</time>'
        "</main><footer></footer>",
        "no explicit timezone",
    ),
)


@pytest.mark.accessibility
@pytest.mark.full
@pytest.mark.parametrize(
    ("state", "body", "rule"),
    (
        (
            "axe-contrast",
            '<p style="color:#aaa;background:#fff">Low contrast fixture</p>',
            "color-contrast",
        ),
        ("axe-unnamed-control", "<button></button>", "button-name"),
    ),
)
def test_axe_and_unnamed_control_failures_are_actionable(
    page: Page,
    state: str,
    body: str,
    rule: str,
) -> None:
    page.set_content(
        "<!doctype html><html lang='en'><head><title>Fixture</title></head>"
        f"<body><header></header><main><h1>Fixture</h1>{body}</main>"
        "<footer></footer></body></html>"
    )
    issues = axe_issues(page, state)
    assert any(f"axe {rule}" in issue for issue in issues), issues
    assert len("; ".join(issues)) <= 2000


@pytest.mark.accessibility
@pytest.mark.full
@pytest.mark.parametrize(
    ("state", "body", "message"),
    (
        (
            "keyboard-order",
            '<button tabindex="2">Second</button><button tabindex="1">First</button>',
            "positive tabindex",
        ),
        (
            "keyboard-trap",
            "<button onkeydown=\"if(event.key==='Tab')event.preventDefault()\">Trapped</button>"
            "<button>Unreachable</button>",
            "keyboard focus is trapped",
        ),
    ),
)
def test_keyboard_order_and_trap_failures_are_actionable(
    page: Page,
    state: str,
    body: str,
    message: str,
) -> None:
    page.set_content(
        "<!doctype html><html lang='en'><head><title>Fixture</title></head>"
        f"<body><header></header><main><h1>Fixture</h1>{body}</main>"
        "<footer></footer></body></html>"
    )
    issues = focus_issues(page, state)
    assert any(message in issue for issue in issues), issues
    assert len("; ".join(issues)) <= 2000


@pytest.mark.accessibility
@pytest.mark.full
@pytest.mark.parametrize(("state", "body", "message"), FAILURE_FIXTURES)
def test_explicit_harness_failures_are_actionable(
    page: Page,
    state: str,
    body: str,
    message: str,
) -> None:
    page.set_content(
        "<!doctype html><html lang='en'><head><title>Fixture</title></head>"
        f"<body>{body}</body></html>"
    )
    checks = {
        "missing-landmark-h1": structure_issues,
        "broken-skip": skip_link_issues,
        "stale-expanded": control_state_issues,
        "unlinked-error": control_state_issues,
        "silent-status": control_state_issues,
        "undersized-target": target_size_issues,
        "overflow": structure_issues,
        "motion": motion_issues,
        "missing-alt": media_date_issues,
        "missing-transcript": media_date_issues,
        "missing-timezone": media_date_issues,
    }
    issues = checks[state](page, state)
    assert any(message in issue for issue in issues), issues


@pytest.mark.accessibility
@pytest.mark.full
def test_lost_value_and_invisible_obscured_focus_failures_are_actionable(page: Page) -> None:
    page.set_content(
        """
        <!doctype html><html lang="en"><head><title>Fixture</title>
        <style>button:focus { outline: 0; box-shadow: none; }</style></head><body>
        <header></header><main><h1>Fixture</h1>
        <form><input name="valid" value="lost"><button>Save</button></form>
        <div style="position:fixed;inset:0;z-index:10;background:white">Obstruction</div>
        </main><footer></footer></body></html>
        """
    )
    assert "was not preserved" in " ".join(
        preserved_value_issues(page, "lost-value", {"valid": "preserved"})
    )
    focus_failures = " ".join(focus_issues(page, "invisible-focus"))
    assert "focus is not visible" in focus_failures
    assert "focus target is obscured" in focus_failures


@pytest.mark.accessibility
@pytest.mark.full
def test_focus_scan_restores_scroll_before_geometry_checks(page: Page) -> None:
    controls = "".join(f"<button>Footer control {index}</button>" for index in range(42))
    page.set_viewport_size({"width": 390, "height": 844})
    page.set_content(
        """
        <!doctype html><html lang="en"><head><title>Fixture</title>
        <style>
          :focus { outline: 3px solid blue; }
          .skip-link { position: fixed; top: .5rem; transform: translateY(-160%); }
          .skip-link:focus { transform: translateY(0); }
          .learner-link { display: block; line-height: 19.25px; width: 120px; }
          .footer-controls { display: grid; gap: 4px; margin-top: 900px; }
          button { min-height: 24px; }
        </style></head><body>
        <a class="skip-link" href="#main-content">Skip to content</a>
        <main id="main-content" tabindex="-1">
          <h1>Fixture</h1>
          <a class="learner-link" href="#learner">Synthetic learner</a>
          <div class="footer-controls">
        """
        + controls
        + """
          </div>
        </main></body></html>
        """
    )

    assert focus_issues(page, "focus-scroll-reset") == []
    assert page.evaluate("window.scrollY") == 0
    assert target_size_issues(page, "focus-scroll-reset") == []


@pytest.mark.accessibility
@pytest.mark.full
def test_javascript_off_public_reads_remain_semantic(
    browser: Browser,
    live_server,
    accessibility_environment: AccessibilityEnvironment,
) -> None:
    policy_issues = _no_javascript_public_policy_issues(accessibility_environment)
    assert not policy_issues, "; ".join(policy_issues)
    event = accessibility_environment.objects["event"]
    assert isinstance(event, dict)
    seed_total(event, count=3, complete=True)
    rendered_states = _public_rendered_states(accessibility_environment)

    for viewport, viewport_name in VIEWPORTS:
        for rendered in rendered_states:
            # A new context per state prevents cookies, navigation state, or a prior document from
            # making this server-rendered contract pass accidentally.  The existing eight hub
            # states are deliberately included in this same policy matrix and remain covered by
            # the legacy mobile smoke above.
            context = browser.new_context(
                java_script_enabled=False,
                viewport=viewport,
                reduced_motion="reduce",
            )
            page = context.new_page()
            try:
                _assert_no_javascript_public_state(
                    page,
                    live_server,
                    accessibility_environment,
                    rendered,
                    viewport_name=viewport_name,
                )
            finally:
                context.close()
