"""Real form submissions preserve CSRF without disclosing private links externally."""

from urllib.parse import urlsplit

import pytest
from allauth.account.models import EmailAddress
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from playwright.sync_api import expect

from slack_access import services
from slack_access.models import Application, Membership

pytestmark = [pytest.mark.core, pytest.mark.django_db(transaction=True)]


def _person(name, *, staff=False):
    user = get_user_model().objects.create_user(
        username=name, email=f"dev+{name}@dtcdev.click", is_staff=staff
    )
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    return user


def _sign_in(page, live_server, user):
    page.context.clear_cookies()
    client = Client()
    client.force_login(user)
    page.context.add_cookies(
        [
            {
                "name": django_settings.SESSION_COOKIE_NAME,
                "value": client.cookies[django_settings.SESSION_COOKIE_NAME].value,
                "url": live_server.url,
            }
        ]
    )


@pytest.mark.parametrize("width", [1440, 390])
def test_slack_forms_submit_with_browser_csrf_and_private_redirect(
    page, live_server, settings, width
):
    settings.SLACK_INVITE_URL = "https://join.slack.com/t/synthetic/shared_invite/test"
    applicant = _person(f"slack-browser-applicant-{width}")
    sponsor = _person(f"slack-browser-sponsor-{width}")
    staff = _person(f"slack-browser-reviewer-{width}", staff=True)
    staff.user_permissions.add(
        Permission.objects.get(
            content_type__app_label="slack_access", codename="review_applications"
        )
    )
    membership = Membership.objects.create(user=sponsor, slack_user_id=f"USYNTH{width}")
    page.set_viewport_size({"width": width, "height": 900})
    _sign_in(page, live_server, applicant)
    response = page.goto(live_server.url + reverse("slack-access:application"))
    assert response.headers["referrer-policy"] == "same-origin"

    # Invalid and valid HTML POSTs both go through the browser and Django CSRF.
    page.get_by_role("button", name="Request access", exact=True).click()
    expect(page.locator(".a11y-error-summary")).to_be_visible()
    page.get_by_label("Who are you?").fill("Synthetic browser applicant")
    page.get_by_label("Where do you work?").fill("Synthetic workplace")
    page.get_by_label("What do you do?").fill("Study data")
    page.get_by_role("button", name="Request access", exact=True).click()
    expect(page.get_by_text("Status: Awaiting approval", exact=True)).to_be_visible()
    application = Application.objects.get(user=applicant)

    _sign_in(page, live_server, sponsor)
    page.goto(live_server.url + reverse("slack-access:vouch", args=[application.vouch_key]))
    page.get_by_label("I know this person and accept this responsibility.").check()
    page.get_by_role("button", name="Confirm my vouch", exact=True).click()
    expect(page.get_by_text("Your vouch is recorded.", exact=False)).to_be_visible()
    application.refresh_from_db()
    assert application.sponsor_id == membership.pk

    services.review(staff, application.pk, approve=True)
    _sign_in(page, live_server, applicant)
    page.goto(live_server.url + reverse("slack-access:application"))
    page.get_by_role("button", name="Get my single-use invitation", exact=True).click()
    page.get_by_role("link", name="Continue to your single-use invitation", exact=True).click()
    application.refresh_from_db()
    assert application.redirect_consumed_at is None

    observed = []

    def redeem_without_provider_request(route):
        if route.request.method != "POST":
            route.continue_()
            return
        # Exercise the actual browser headers and server mutation, then intercept
        # the redirect response so no provider request or invitation is sent.
        assert route.request.headers.get("origin") == live_server.url
        result = route.fetch(max_redirects=0)
        assert result.status == 302
        assert result.headers["location"] == settings.SLACK_INVITE_URL
        assert result.headers["referrer-policy"] == "same-origin"
        observed.append(result.status)
        route.fulfill(status=200, content_type="text/plain", body="Controlled redirect intercepted")

    path = urlsplit(page.url).path
    page.route(live_server.url + path, redeem_without_provider_request)
    page.get_by_role("button", name="Use invitation and continue to Slack", exact=True).click()
    expect(page.get_by_text("Controlled redirect intercepted", exact=True)).to_be_visible()
    application.refresh_from_db()
    assert observed == [302]
    assert application.redirect_consumed_at is not None
    assert not application.redirect_digest
