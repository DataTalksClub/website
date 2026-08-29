from __future__ import annotations

import os
import subprocess
import sys

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, override_settings

from courses.models import (
    Answer,
    CourseRegistration,
    Enrollment,
    LeaderboardComplaint,
    Submission,
    Unit,
    UnitReadState,
)
from jobs.models import DurableJob
from test_support.design_review_data import seed_design_review_data
from test_support.design_review_identity import complaint_path

pytestmark = pytest.mark.django_db(transaction=True)

REVIEW_MIDDLEWARE = "review_import.middleware.LocalReviewNoNetworkMiddleware"


@override_settings(
    DEBUG=False,
    LOCAL_REVIEW_OUTBOUND_NETWORK_DISABLED=True,
    ISSUE_237_SYNTHETIC_INTERACTIONS_ENABLED=True,
    ISSUE_237_SYNTHETIC_COMPLAINT_PATH=complaint_path("test-review-interactions"),
    MIDDLEWARE=[*settings.MIDDLEWARE, REVIEW_MIDDLEWARE],
)
def test_issue_237_interactions_reach_only_bounded_validation_and_toggle_views() -> None:
    review_data = seed_design_review_data(execution_namespace="test-review-interactions")
    surfaces = {surface.key: surface for surface in review_data.surfaces}
    users = {
        persona.key: get_user_model().objects.get(username=persona.username)
        for persona in review_data.personas
    }

    unit_path = "/courses/streaming-systems-lab/autumn-2026/modules/module-03/unit-04/read"
    anonymous = Client()
    anonymous_toggle = anonymous.post(unit_path, {"is_read": "1"})
    assert anonymous_toggle.status_code == 302
    assert anonymous_toggle.headers["Location"].startswith("/accounts/login/")

    learner = Client()
    learner.force_login(users["active-learner"])
    unit = Unit.objects.get(
        module__cohort__course__slug="streaming-systems-lab",
        module__cohort__identifier="autumn-2026",
        module__slug="module-03",
        slug="unit-04",
    )
    assert not UnitReadState.objects.filter(user=users["active-learner"], unit=unit).exists()
    supported_toggle = learner.post(unit_path, {"is_read": "1"}, follow=True)
    assert supported_toggle.status_code == 200
    assert supported_toggle.redirect_chain == [
        ("/courses/streaming-systems-lab/autumn-2026/modules/module-03", 302)
    ]
    assert UnitReadState.objects.filter(user=users["active-learner"], unit=unit).exists()
    invalid_toggle = learner.post(unit_path, {"is_read": "issue-237-invalid"})
    assert invalid_toggle.status_code == 400

    durable_before = {
        "answers": Answer.objects.count(),
        "complaints": LeaderboardComplaint.objects.count(),
        "enrollments": Enrollment.objects.count(),
        "jobs": DurableJob.objects.count(),
        "registrations": CourseRegistration.objects.count(),
        "submissions": Submission.objects.count(),
    }

    registration = anonymous.post(surfaces["native-registration"].path, {})
    assert registration.status_code == 200
    assert b"data-focus-error-summary" in registration.content
    assert b"This field is required" in registration.content

    homework = learner.post(surfaces["legacy-homework-open"].path, {})
    assert homework.status_code == 200
    assert b'role="alert"' in homework.content
    assert b"time spent on homework" in homework.content

    reviewer = Client()
    reviewer.force_login(users["peer-reviewer"])
    assert surfaces["legacy-complaint"].path == complaint_path("test-review-interactions")
    complaint = reviewer.post(surfaces["legacy-complaint"].path, {})
    assert complaint.status_code == 200
    assert complaint.content.count(b"This field is required") >= 2

    second_enrollment = (
        Enrollment.objects.filter(course__slug="data-reliability-zoomcamp-2026")
        .exclude(pk=surfaces["legacy-complaint"].path.split("/")[-2])
        .order_by("pk")
        .first()
    )
    assert second_enrollment is not None
    second_complaint_path = (
        f"/courses/data-reliability-zoomcamp/2026/leaderboard/{second_enrollment.id}/report"
    )
    assert reviewer.post(second_complaint_path, {}).status_code == 403
    nonexistent_complaint_path = "/courses/data-reliability-zoomcamp/2026/leaderboard/1/report"
    assert reviewer.post(nonexistent_complaint_path, {}).status_code == 403

    durable_after = {
        "answers": Answer.objects.count(),
        "complaints": LeaderboardComplaint.objects.count(),
        "enrollments": Enrollment.objects.count(),
        "jobs": DurableJob.objects.count(),
        "registrations": CourseRegistration.objects.count(),
        "submissions": Submission.objects.count(),
    }
    assert durable_after == durable_before

    assert (
        anonymous.post(
            surfaces["native-registration"].path,
            {"email": "nonblank@example.invalid"},
        ).status_code
        == 403
    )
    assert (
        learner.post(
            surfaces["legacy-project-collecting"].path,
            {},
        ).status_code
        == 403
    )
    assert (
        reviewer.post(
            surfaces["legacy-complaint"].path,
            {"description": "nonblank"},
        ).status_code
        == 403
    )
    assert anonymous.post("/studio/courses/datamailer/", {}).status_code == 403
    assert anonymous.get("/accounts/github/login/").status_code == 403

    missing = anonymous.get("/__issue_237_missing__")
    assert missing.status_code == 404
    assert b"Page not found" in missing.content
    assert b"URL patterns" not in missing.content
    assert b"technical 404" not in missing.content.lower()


@override_settings(
    LOCAL_REVIEW_OUTBOUND_NETWORK_DISABLED=True,
    ISSUE_237_SYNTHETIC_INTERACTIONS_ENABLED=False,
    MIDDLEWARE=[*settings.MIDDLEWARE, REVIEW_MIDDLEWARE],
)
def test_ordinary_local_review_keeps_issue_237_paths_denied() -> None:
    response = Client().post(
        "/courses/streaming-systems-lab/autumn-2026/modules/module-03/unit-04/read",
        {"is_read": "1"},
    )
    assert response.status_code == 403


def test_design_review_settings_render_application_404_with_debug_disabled() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "DTC_ENVIRONMENT": "local",
            "DTC_SQLITE_PATH": ".tmp/design-review/settings-test-unused.sqlite3",
            "DJANGO_SETTINGS_MODULE": "website.settings.design_review",
        }
    )
    code = """
import django
django.setup()
from django.conf import settings
from django.test import Client
response = Client().get('/__issue_237_missing__')
assert settings.DEBUG is False
assert response.status_code == 404
assert b'Page not found' in response.content
assert b'URL patterns' not in response.content
print('application-404')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=settings.BASE_DIR,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "application-404"
