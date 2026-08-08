from __future__ import annotations

import html
import re
import uuid
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.db import DatabaseError, connection, transaction
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.studio_test_support import authenticated_studio_client, make_studio_user
from core.models import AppendOnlyViolation, AuditEvent


def make_event(
    *,
    actor=None,
    action: str = "tests.audit.read",
    outcome: str = AuditEvent.Outcome.SUCCEEDED,
    target_type: str = "fixture.record",
    request_id: str = "request-86",
    correlation_id: str = "correlation-86",
    actor_ref: str = "user:fixture",
    **overrides,
) -> AuditEvent:
    values = {
        "actor": actor,
        "actor_ref": actor_ref,
        "action": action,
        "outcome": outcome,
        "target_type": target_type,
        "request_id": request_id,
        "correlation_id": correlation_id,
        "changes": {},
        "metadata": {},
    }
    values.update(overrides)
    return AuditEvent.objects.create(
        **values,
    )


@override_settings(NOINDEX=False, STUDIO_AUDIT_REDACTION_CANARIES=("seeded-canary-86",))
class AuditBrowserTests(TestCase):
    def setUp(self) -> None:
        self.auditor = make_studio_user(username="audit-reader", roles=("auditor",))
        self.client = authenticated_studio_client(self.auditor)

    def assert_private(self, response, status: int) -> None:
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_bounded_list_exact_filters_and_pagination(self) -> None:
        for index in range(45):
            make_event(
                action="tests.audit.filtered" if index % 2 else "tests.audit.other",
                outcome=(AuditEvent.Outcome.DENIED if index % 3 else AuditEvent.Outcome.SUCCEEDED),
                target_type="fixture.match" if index % 2 else "fixture.other",
                request_id=f"request-{index}",
                correlation_id="correlation-match" if index % 2 else "correlation-other",
                target_label=f"Fixture {index}",
            )

        first = self.client.get(reverse("studio:audit-list"))
        self.assert_private(first, 200)
        self.assertEqual(len(first.context["events"]), 20)
        self.assertContains(first, "page 1 of 3")

        filtered = self.client.get(
            reverse("studio:audit-list"),
            {
                "action": "tests.audit.filtered",
                "outcome": "denied",
                "target_type": "fixture.match",
                "request_id": "request-1",
                "correlation_id": "correlation-match",
            },
        )
        self.assert_private(filtered, 200)
        self.assertEqual(len(filtered.context["events"]), 1)

        blank_controls = self.client.get(
            reverse("studio:audit-list"),
            {
                "action": "tests.audit.filtered",
                "outcome": "",
                "target_type": "",
                "request_id": "",
                "correlation_id": "",
            },
        )
        self.assert_private(blank_controls, 200)
        self.assertEqual(blank_controls.context["audit_page"].total_count, 22)

        filtered_page_one = self.client.get(
            reverse("studio:audit-list"),
            {"target_type": "fixture.match"},
        )
        next_match = re.search(
            r'href="([^"]+)">Next</a>',
            filtered_page_one.content.decode(),
        )
        self.assertIsNotNone(next_match)
        assert next_match is not None
        next_url = html.unescape(next_match.group(1))
        self.assertEqual(
            parse_qs(urlparse(next_url).query),
            {"target_type": ["fixture.match"], "page": ["2"]},
        )
        filtered_page_two = self.client.get(f"{reverse('studio:audit-list')}{next_url}")
        self.assertEqual(filtered_page_two.context["audit_page"].number, 2)
        self.assertTrue(filtered_page_two.context["events"])
        self.assertTrue(
            all(
                event[1]["target_type"] == "fixture.match"
                for event in filtered_page_two.context["events"]
            )
        )

    def test_invalid_filters_are_safe_400_without_reflection(self) -> None:
        canary = "invalid-filter-canary-86"
        for query in (
            f"?unknown={canary}",
            "?action=valid.action&action=other.action",
            f"?action={canary}%20unsafe",
            "?outcome=unknown",
            "?request_id=Bearer%20secret",
            "?page=0",
            "?page=100001",
        ):
            with self.subTest(query=query):
                response = self.client.get(f"{reverse('studio:audit-list')}{query}")
                self.assert_private(response, 400)
                self.assertNotContains(response, canary, status_code=400)
                self.assertNotContains(response, "Traceback", status_code=400)

    def test_permission_denial_happens_before_uuid_lookup(self) -> None:
        outsider = make_studio_user(username="content-only", roles=("content_operator",))
        client = authenticated_studio_client(outsider)
        existing = make_event()
        missing = uuid.uuid4()

        bodies: list[bytes] = []
        for event_id in (existing.id, missing):
            with CaptureQueriesContext(connection) as queries:
                response = client.get(reverse("studio:audit-detail", args=(event_id,)))
            self.assert_private(response, 403)
            self.assertFalse(
                any(
                    "core_auditevent" in query["sql"].casefold()
                    for query in queries.captured_queries
                )
            )
            bodies.append(response.content)
        self.assertEqual(bodies[0], bodies[1])

    def test_authorized_absent_and_sql_scoped_uuid_are_identical_404(self) -> None:
        excluded = make_event(target_type="private.fixture")
        responses = (
            self.client.get(reverse("studio:audit-detail", args=(uuid.uuid4(),))),
            self.client.get(reverse("studio:audit-detail", args=(excluded.id,))),
        )
        for response in responses:
            self.assert_private(response, 404)
            self.assertNotContains(response, "Traceback", status_code=404)
        self.assertEqual(responses[0].content, responses[1].content)

    def test_detail_is_allowlisted_and_re_redacted_after_direct_insert(self) -> None:
        event = make_event(
            actor_ref="person@example.test",
            action="tests.audit.unsafe",
            target_label="https://admin.example.test/manage/secret",
            changes={
                "authorization": "Bearer unsafe-token-86",
                "nested": {"email": "person@example.test", "note": "seeded-canary-86"},
            },
            metadata={
                "cookie": "sessionid=raw-cookie-86",
                "request_body": "private body",
                "safe": "visible operational summary",
            },
            source_ip_class="public",
            job_id="job-hidden-86",
            idempotency_key_hash="a" * 64,
        )

        response = self.client.get(reverse("studio:audit-detail", args=(event.id,)))
        self.assert_private(response, 200)
        self.assertContains(response, "visible operational summary")
        self.assertContains(response, "[REDACTED]")
        for protected in (
            "person@example.test",
            "https://admin.example.test/manage/secret",
            "unsafe-token-86",
            "raw-cookie-86",
            "private body",
            "seeded-canary-86",
            "job-hidden-86",
            "a" * 64,
        ):
            self.assertNotContains(response, protected)
        self.assertNotContains(response, "Edit")
        self.assertNotContains(response, "Delete")

    def test_actor_snapshot_remains_after_actor_deletion(self) -> None:
        actor = make_studio_user(username="deleted-actor", roles=())
        event = make_event(actor=actor, actor_ref=f"user:{actor.pk}")
        actor.delete()
        event.refresh_from_db()
        self.assertIsNone(event.actor)

        response = self.client.get(reverse("studio:audit-detail", args=(event.id,)))
        self.assert_private(response, 200)
        self.assertContains(response, event.actor_ref)

    def test_audit_browser_and_model_expose_no_mutation_path(self) -> None:
        event = make_event()
        for method, path in (
            ("post", reverse("studio:audit-list")),
            ("put", reverse("studio:audit-detail", args=(event.id,))),
            ("patch", reverse("studio:audit-detail", args=(event.id,))),
            ("delete", reverse("studio:audit-detail", args=(event.id,))),
        ):
            response = getattr(self.client, method)(path)
            self.assert_private(response, 405)
        event.action = "tests.audit.changed"
        with self.assertRaises(AppendOnlyViolation):
            event.save()
        with self.assertRaises(AppendOnlyViolation):
            AuditEvent.objects.filter(id=event.id).update(action="tests.audit.changed")
        with self.assertRaises(AppendOnlyViolation):
            AuditEvent.objects.filter(id=event.id).delete()

    def test_field_policy_error_is_safe_403(self) -> None:
        with patch(
            "studio.views.AUDIT_DISPLAY_FIELDS",
            ("not_allowlisted",),
        ):
            response = self.client.get(reverse("studio:audit-list"))
        self.assert_private(response, 403)
        self.assertEqual(response.content, b"Studio access denied")

    def test_csrf_and_method_policy_are_enforced_after_authorization(self) -> None:
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.auditor)
        missing = csrf_client.post(reverse("studio:audit-list"))
        self.assert_private(missing, 403)

        page = csrf_client.get(reverse("studio:home"))
        token = page.cookies["csrftoken"].value
        valid = csrf_client.post(
            reverse("studio:audit-list"),
            HTTP_X_CSRFTOKEN=token,
        )
        self.assert_private(valid, 405)

        anonymous_method = Client().post(reverse("studio:audit-list"))
        self.assert_private(anonymous_method, 302)


class PostgreSQLAuditGuardTests(TestCase):
    def test_database_role_cannot_update_or_delete_audit_event(self) -> None:
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL trigger contract")
        event = make_event()
        for statement in (
            "UPDATE core_auditevent SET action = 'tests.audit.changed' WHERE id = %s",
            "DELETE FROM core_auditevent WHERE id = %s",
        ):
            with self.subTest(statement=statement), self.assertRaises(DatabaseError):
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(statement, (event.id,))


@override_settings(ROOT_URLCONF="studio.tests.fixture_urls")
class TestOnlyFixtureCsrfTests(TestCase):
    def test_state_changing_fixture_requires_valid_csrf(self) -> None:
        client = Client(enforce_csrf_checks=True)
        missing = client.post("/studio/_fixtures/high-risk/")
        self.assertEqual(missing.status_code, 403)
        invalid = client.post("/studio/_fixtures/high-risk/", HTTP_X_CSRFTOKEN="invalid")
        self.assertEqual(invalid.status_code, 403)

        seed = client.get("/studio/_fixtures/csrf/")
        token = seed.content.decode()
        valid = client.post("/studio/_fixtures/high-risk/", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(valid.content, b"fixture reached")
