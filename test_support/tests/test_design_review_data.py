from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from courses.models import Cohort, CurriculumFormat, UnitReadState
from test_support.design_review_data import seed_design_review_data

pytestmark = pytest.mark.django_db(transaction=True)


def test_issue_237_review_data_builds_distinct_course_contracts_and_routes() -> None:
    review_data = seed_design_review_data(execution_namespace="test-review-data")

    assert Cohort.objects.filter(curriculum_format=CurriculumFormat.LEGACY).count() == 3
    assert Cohort.objects.filter(curriculum_format=CurriculumFormat.MODULES).count() == 2
    assert UnitReadState.objects.count() > 0
    assert {persona.key for persona in review_data.personas} == {
        "active-learner",
        "graduate",
        "observer",
        "peer-reviewer",
    }

    users = {
        persona.key: get_user_model().objects.get(username=persona.username)
        for persona in review_data.personas
    }
    for surface in review_data.surfaces:
        client = Client()
        if surface.actor != "anonymous":
            client.force_login(users[surface.actor])
        response = client.get(surface.path, follow=False)
        assert response.status_code == surface.expected_status, surface


def test_issue_237_review_manifest_contains_no_email_or_session_value() -> None:
    review_data = seed_design_review_data(execution_namespace="test-review-manifest")
    manifest = review_data.manifest()
    serialized = str(manifest)

    assert "@" not in serialized
    assert "session" not in serialized.casefold()
    assert all(surface["path"] != "/" for surface in manifest["surfaces"])
    assert all(surface["path"] != "/events" for surface in manifest["surfaces"])
