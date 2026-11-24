"""The course Apply -> account step under production's sign-in configuration.

The owner-reported dead end: a course page's Apply led to "Create account",
which led to "Sign-up is closed", whose "Sign in" led to "Sign-in is
temporarily unavailable".  Production closes email signup on purpose, so the
account step must offer the configured Google/GitHub/Slack sign-in inline
(the first sign-in creates the account) and, with none configured, an honest
notice with the contact address.  Providers are real ``SocialApp`` rows with
test client ids; the provider sign-in is started but its redirect is never
followed.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from django.core.cache import cache
from django.urls import reverse
from playwright.sync_api import Page, expect

from accounts.tests.provider_fixtures import ALL_PROVIDERS, configure_site_providers
from courses.models import Course, RegistrationCampaign
from test_support.course_catalog import make_cohort

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]

SCREENSHOTS = Path(".tmp/screenshots/auth-journey")
VIEWPORTS = (
    ({"width": 1440, "height": 900}, "desktop"),
    ({"width": 390, "height": 844}, "mobile"),
)


@pytest.fixture
def production_sign_in(settings):
    settings.ENVIRONMENT = "production"
    settings.REGISTRATION_REQUIRES_ACCOUNT = True
    cache.delete("available_providers")
    yield
    cache.delete("available_providers")


def _course_with_campaign() -> tuple[Course, RegistrationCampaign]:
    family = Course.objects.create(
        slug="auth-journey-course",
        title="Production AI Agents",
        outcome="Ship an agent you can test and explain.",
    )
    cohort = make_cohort(family, 2026, project_count=0)
    campaign = RegistrationCampaign.objects.create(
        slug="auth-journey-course",
        title=family.title,
        current_course=cohort,
    )
    return family, campaign


def _dismiss_analytics(page: Page) -> None:
    choice = page.get_by_role("button", name="Keep analytics off")
    if choice.count() and choice.first.is_visible():
        choice.first.click()
        expect(choice.first).to_be_hidden()


def _assert_no_overflow(page: Page) -> None:
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def _shot(page: Page, name: str, viewport_name: str) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    # Dismissing the analytics banner returns focus to the skip link, which
    # would otherwise float over the page in a full-page capture; a fixed
    # element is also drawn at the scroll offset, so start from the top.
    page.evaluate("document.getElementById('main-content').focus(); scrollTo(0, 0)")
    page.screenshot(path=SCREENSHOTS / f"{name}-{viewport_name}.png", full_page=True)


@pytest.mark.usefixtures("production_sign_in")
@pytest.mark.parametrize(("viewport", "viewport_name"), VIEWPORTS)
def test_apply_reaches_provider_buttons_that_start_sign_in(
    page: Page, live_server, viewport, viewport_name: str
) -> None:
    configure_site_providers(*ALL_PROVIDERS)
    family, campaign = _course_with_campaign()
    campaign_path = reverse("registration_campaign", kwargs={"campaign_slug": campaign.slug})
    page.set_viewport_size(viewport)
    page.emulate_media(reduced_motion="reduce")

    response = page.goto(f"{live_server.url}{reverse('course_family', args=[family.slug])}")
    assert response is not None and response.status == 200
    _dismiss_analytics(page)
    page.locator(f'.family-card-actions a[href="{campaign_path}"]').first.click()
    expect(page).to_have_url(f"{live_server.url}{campaign_path}")

    panel = page.locator(".registration-panel")
    expect(
        panel.get_by_role("heading", name="Create your free account to register")
    ).to_be_visible()
    expect(panel).to_contain_text("Create your account with Google, GitHub or Slack.")
    for provider, name in ALL_PROVIDERS:
        button = panel.get_by_role("button", name=f"Continue with {name}")
        expect(button).to_be_visible()
        action = button.locator("xpath=ancestor::form").get_attribute("action")
        assert action is not None
        assert urlsplit(action).path == f"/accounts/{provider}/login/"
        assert parse_qs(urlsplit(action).query)["next"] == [campaign_path]
    expect(panel.get_by_role("link", name="Create account")).to_have_count(0)
    expect(panel.locator('a[href^="/accounts/signup/"]')).to_have_count(0)
    _assert_no_overflow(page)
    _shot(page, "register-providers", viewport_name)

    # The button really starts the provider's sign-in.  The form is submitted
    # with the browser's own cookies and CSRF token, but redirects are not
    # followed, so the provider is never contacted: the redirect itself is
    # the evidence (a cross-origin navigation redirect is not routable).
    form = panel.locator("form", has=page.get_by_role("button", name="Continue with GitHub"))
    token = form.locator('input[name="csrfmiddlewaretoken"]').get_attribute("value")
    action = form.get_attribute("action")
    assert token and action
    started = page.context.request.post(
        f"{live_server.url}{action}",
        form={"csrfmiddlewaretoken": token},
        headers={"Referer": page.url},
        max_redirects=0,
    )
    assert started.status == 302, started.status
    location = urlsplit(started.headers["location"])
    assert (location.hostname, location.path) == ("github.com", "/login/oauth/authorize")
    assert parse_qs(location.query)["client_id"] == ["test-github-client-id"]

    # The masthead's "Create free account" leads to the providers too, never a dead
    # "closed" page, and keeps the way back to the campaign.
    page.get_by_role("link", name="Create free account").click()
    expect(page.get_by_role("heading", name="Create your account", exact=True)).to_be_visible()
    expect(page.get_by_text("New accounts are created by signing in with")).to_be_visible()
    expect(page.get_by_role("heading", name="Choose how to sign in")).to_be_visible()
    google = page.get_by_role("button", name="Continue with Google")
    expect(google).to_be_visible()
    google_action = google.locator("xpath=ancestor::form").get_attribute("action")
    assert google_action is not None
    assert parse_qs(urlsplit(google_action).query)["next"] == [campaign_path]
    _assert_no_overflow(page)
    _shot(page, "signup-providers", viewport_name)

    page.goto(f"{live_server.url}/accounts/login/?next={campaign_path}")
    expect(page.get_by_text("Continuing with Google, GitHub or Slack creates")).to_be_visible()
    _shot(page, "login-providers", viewport_name)


@pytest.mark.usefixtures("production_sign_in")
@pytest.mark.parametrize(("viewport", "viewport_name"), VIEWPORTS)
def test_without_providers_every_step_is_honest_and_offers_contact(
    page: Page, live_server, viewport, viewport_name: str
) -> None:
    _family, campaign = _course_with_campaign()
    campaign_path = reverse("registration_campaign", kwargs={"campaign_slug": campaign.slug})
    page.set_viewport_size(viewport)
    page.emulate_media(reduced_motion="reduce")

    page.goto(f"{live_server.url}{campaign_path}")
    _dismiss_analytics(page)
    panel = page.locator(".registration-panel")
    expect(panel.get_by_role("heading", name="Registration needs an account")).to_be_visible()
    expect(panel.get_by_text("Signing in isn't available right now")).to_be_visible()
    expect(panel.get_by_role("link", name="alexey@datatalks.club")).to_have_attribute(
        "href", "mailto:alexey@datatalks.club"
    )
    expect(panel.locator('a[href^="/accounts/signup/"]')).to_have_count(0)
    _assert_no_overflow(page)
    _shot(page, "register-no-providers", viewport_name)

    for path, name in (("/accounts/signup/", "signup"), ("/accounts/login/", "login")):
        page.goto(f"{live_server.url}{path}")
        expect(page.get_by_text("Signing in isn't available right now")).to_be_visible()
        expect(page.get_by_text("temporarily unavailable")).to_have_count(0)
        expect(
            page.locator("main").get_by_role("link", name="alexey@datatalks.club")
        ).to_be_visible()
        _assert_no_overflow(page)
        _shot(page, f"{name}-no-providers", viewport_name)
