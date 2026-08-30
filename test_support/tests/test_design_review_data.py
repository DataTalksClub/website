from __future__ import annotations

from typing import cast

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from courses.models import Cohort, CurriculumFormat, UnitReadState
from events.models import EventQnaSession
from test_support.design_review_data import seed_design_review_data

pytestmark = pytest.mark.django_db(transaction=True)


def test_issue_237_review_data_builds_distinct_course_contracts_and_routes() -> None:
    review_data = seed_design_review_data(execution_namespace="test-review-data")

    assert Cohort.objects.filter(curriculum_format=CurriculumFormat.LEGACY).count() == 3
    assert Cohort.objects.filter(curriculum_format=CurriculumFormat.MODULES).count() == 2
    assert UnitReadState.objects.count() > 0
    qna_session = EventQnaSession.objects.get(event__public_id=364)
    assert qna_session.provisioning_job_id is None
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
    responses = {}
    for surface in review_data.surfaces:
        client = Client()
        if surface.actor != "anonymous":
            client.force_login(users[surface.actor])
        response = client.get(surface.path, follow=False)
        assert response.status_code == surface.expected_status, surface
        responses[surface.key] = response

    busy_dashboard = responses["legacy-dashboard"]
    empty_dashboard = responses["legacy-dashboard-unenrolled"]
    assert busy_dashboard.context["total_enrollments"] > 0
    assert busy_dashboard.context["homework_stats"]
    assert empty_dashboard.context["total_enrollments"] == 0
    assert empty_dashboard.context["homework_stats"] == []
    assert busy_dashboard.content != empty_dashboard.content

    rich_unit = responses["native-unit-rich"]
    assert rich_unit.content.count(b"<h1") == 1
    assert b"Choosing boundaries for reliable replay and recovery" in rich_unit.content

    aggregate_wrapped = responses["wrapped-aggregate"]
    assert b"Data Reliability Zoomcamp 2026" in aggregate_wrapped.content
    assert b"9 participants" in aggregate_wrapped.content
    assert b"Avery Quartz" in aggregate_wrapped.content
    assert b"71" in aggregate_wrapped.content

    individual_wrapped = responses["wrapped-individual"]
    assert b"Data Reliability Zoomcamp 2026" in individual_wrapped.content
    assert b"34 points" in individual_wrapped.content
    assert b"#2" in individual_wrapped.content
    assert b"19.5" in individual_wrapped.content

    qna_participant = responses["event-qna-participant"]
    assert b"AI Dev Tools Zoomcamp 2026 Pre-Course Live Q&amp;A" in qna_participant.content
    assert b"Event Q&amp;A" in qna_participant.content
    assert b'id="qna-sort"' in qna_participant.content
    event_detail = Client().get(
        f"/events/{qna_session.event.public_id}/{qna_session.event.slug}", follow=False
    )
    assert event_detail.status_code == 200

    qna_cohost = responses["event-qna-cohost-gate"]
    assert b"Enter co-host passcode" in qna_cohost.content
    assert b'autocomplete="one-time-code"' in qna_cohost.content


def test_issue_237_review_manifest_contains_no_email_or_session_value() -> None:
    review_data = seed_design_review_data(execution_namespace="test-review-manifest")
    manifest = review_data.manifest()
    serialized = str(manifest)
    surfaces = cast(list[dict[str, object]], manifest["surfaces"])

    assert "@" not in serialized
    assert "session" not in serialized.casefold()
    assert all(surface["path"] != "/" for surface in surfaces)
    assert all(surface["path"] != "/events" for surface in surfaces)
