"""#460 smoke: a learner enrolls through the form and is confirmed once.

The enrollment confirmation is an effect of the live enrollment action, so
the browser journey that enrolls a learner must still record exactly one
``enrollment-confirmation`` delivery, while the GET of the form records none.
"""

from __future__ import annotations

import pytest
from community_base.mail.models import EmailDelivery
from django.conf import settings
from django.test import Client
from django.urls import reverse
from playwright.sync_api import Page

from courses.models.cohort import Cohort, Enrollment

pytestmark = [pytest.mark.smoke, pytest.mark.django_db(transaction=True)]


def _learner():
    from accounts.models import User

    email = "enroll-confirm-learner@example.invalid"
    return User.objects.create_user(username=email, email=email)


def _sign_in(page: Page, live_server, user) -> None:
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


def test_enrolling_through_the_form_records_one_confirmation(
    page: Page, live_server, settings
) -> None:
    settings.PUBLIC_BASE_URL = live_server.url
    cohort = Cohort.objects.create(
        slug="enroll-confirm-smoke",
        title="Enrollment Confirmation Smoke",
        description="#460 browser smoke course",
    )
    learner = _learner()
    _sign_in(page, live_server, learner)
    enrollment_path = reverse(
        "cohort_enrollment",
        kwargs={"course_slug": cohort.slug, "cohort_identifier": cohort.identifier},
    )

    response = page.goto(f"{live_server.url}{enrollment_path}", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    assert Enrollment.objects.filter(student=learner).count() == 0
    assert EmailDelivery.objects.count() == 0

    page.locator("#id_display_name").fill("Smoke Learner")
    with page.expect_navigation() as navigation:
        page.get_by_role("button", name="Save changes").click()

    # The save redirects to the cohort page, which renders without an error.
    landed = navigation.value
    assert landed is not None and landed.status == 200
    assert page.url.rstrip("/").endswith(
        reverse(
            "cohort",
            kwargs={"course_slug": cohort.slug, "cohort_identifier": cohort.identifier},
        ).rstrip("/")
    )
    enrollment = Enrollment.objects.get(student=learner, course=cohort)
    deliveries = EmailDelivery.objects.filter(purpose="enrollment-confirmation")
    assert deliveries.count() == 1
    assert deliveries.get().idempotency_key == f"enrollment-confirmation:{enrollment.pk}"
