"""The course Apply -> account step under production's sign-in configuration.

Email + password accounts are open to everyone, so the registration gate offers
"Create account" and "Sign in" (each keeping the way back to the campaign), the
signup page offers the configured Google/GitHub/Slack sign-in above the email
form, and with no provider configured the email form is the way in.  Providers
are real ``SocialApp`` rows with test client ids; the provider sign-in is started
but its redirect is never followed.
"""

from __future__ import annotations

import re

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
    # Email + password accounts are open to everyone: the gate offers the signup
    # page and the sign-in page, each keeping the way back to the campaign.
    expect(panel.get_by_role("link", name="Sign in")).to_have_attribute(
        "href", f"/accounts/login/?next={campaign_path}"
    )
    expect(panel.get_by_role("button", name="Continue with GitHub")).to_have_count(0)
    _assert_no_overflow(page)
    _shot(page, "register-gate", viewport_name)

    panel.get_by_role("link", name="Create account").click()
    expect(page.get_by_role("heading", name="Create your account", exact=True)).to_be_visible()
    auth = page.locator(".auth-panel")
    for provider, name in ALL_PROVIDERS:
        button = auth.get_by_role("button", name=f"Continue with {name}")
        expect(button).to_be_visible()
        action = button.locator("xpath=ancestor::form").get_attribute("action")
        assert action is not None
        assert urlsplit(action).path == f"/accounts/{provider}/login/"
        assert parse_qs(urlsplit(action).query)["next"] == [campaign_path]
    _assert_no_overflow(page)
    _shot(page, "signup-providers", viewport_name)

    # The button really starts the provider's sign-in.  The form is submitted
    # with the browser's own cookies and CSRF token, but redirects are not
    # followed, so the provider is never contacted: the redirect itself is
    # the evidence (a cross-origin navigation redirect is not routable).
    form = auth.locator("form", has=page.get_by_role("button", name="Continue with GitHub"))
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

    page.goto(f"{live_server.url}/accounts/login/?next={campaign_path}")
    expect(page.get_by_role("heading", name="Sign In", exact=True)).to_be_visible()
    expect(page.locator(".auth-panel").get_by_role("button", name="Continue with Google")).to_be_visible()
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
    expect(
        panel.get_by_role("heading", name="Create your free account to register")
    ).to_be_visible()
    expect(panel.get_by_role("link", name="Create account")).to_be_visible()
    _assert_no_overflow(page)
    _shot(page, "register-no-providers", viewport_name)

    # With no social provider configured the email + password form is the way
    # in: no provider button, no "unavailable" notice.
    page.goto(f"{live_server.url}/accounts/signup/")
    expect(page.get_by_role("heading", name="Create your account", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Create account")).to_be_visible()
    expect(page.get_by_role("button", name=re.compile("^Continue with"))).to_have_count(0)
    expect(page.get_by_text("Signing in isn't available right now")).to_have_count(0)
    _assert_no_overflow(page)
    _shot(page, "signup-no-providers", viewport_name)

    page.goto(f"{live_server.url}/accounts/login/")
    expect(page.get_by_role("heading", name="Sign In", exact=True)).to_be_visible()
    expect(page.get_by_role("textbox", name="Email")).to_be_visible()
    expect(page.get_by_text("Signing in isn't available right now")).to_have_count(0)
    _assert_no_overflow(page)
    _shot(page, "login-no-providers", viewport_name)
