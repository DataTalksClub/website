from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings

from accounts.studio_test_support import authenticated_studio_client, make_studio_user
from core.idempotency import hash_idempotency_key
from core.models import AuditEvent
from courses.models import Cohort, CourseRegistrationCountSourceRun, RegistrationCampaign
from courses.registration_count_importer import ADAPTER_VERSION, schema_contract_checksum
from management_api.concurrency import revision_etag
from management_auth.models import APIPrincipal
from management_auth.services import create_principal, issue_credential_once

UTC = datetime_timezone.utc


class CourseRegistrationCountManagementTests(TestCase):
    def setUp(self) -> None:
        scratch = Path(settings.BASE_DIR) / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.source = Path(self.temporary.name) / "synthetic-management.sqlite3"
        self.course = Cohort.objects.create(
            slug="synthetic-managed-cohort",
            title="Synthetic managed cohort",
            description="Deterministic management fixture.",
        )
        self.campaign = RegistrationCampaign.objects.create(
            slug="synthetic-managed-campaign",
            title="Synthetic managed campaign",
            edition_label="2026 cohort",
            current_course=self.course,
        )
        connection = sqlite3.connect(self.source)
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE courses_course (
                id INTEGER PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE
            );
            CREATE TABLE courses_registrationcampaign (
                id INTEGER PRIMARY KEY,
                slug TEXT NOT NULL UNIQUE,
                current_course_id INTEGER REFERENCES courses_course(id)
            );
            CREATE TABLE courses_courseregistration (
                id INTEGER PRIMARY KEY,
                campaign_id INTEGER NOT NULL REFERENCES courses_registrationcampaign(id),
                course_id INTEGER REFERENCES courses_course(id),
                created_at TEXT NOT NULL,
                email_normalized TEXT NOT NULL,
                UNIQUE(campaign_id, email_normalized)
            );
            """
        )
        connection.execute(
            "INSERT INTO courses_course(id, slug) VALUES (?, ?)", (1, self.course.slug)
        )
        connection.execute(
            "INSERT INTO courses_registrationcampaign(id, slug, current_course_id) "
            "VALUES (?, ?, ?)",
            (1, self.campaign.slug, 1),
        )
        connection.execute(
            "INSERT INTO courses_courseregistration"
            "(id, campaign_id, course_id, created_at, email_normalized) "
            "VALUES (?, ?, ?, ?, ?)",
            (1, 1, 1, "2026-01-02T10:00:00+00:00", "synthetic-management"),
        )
        connection.commit()
        connection.close()
        payload = self.source.read_bytes()
        self.registry = {
            "synthetic-management-source": {
                "adapter": ADAPTER_VERSION,
                "path": str(self.source),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "byte_size": len(payload),
                "schema_version": "synthetic-management-v1",
                "schema_contract_checksum": schema_contract_checksum(self.source),
                "captured_at": "2026-01-11T00:00:00+00:00",
                "source_frozen_at": "2026-01-10T00:00:00+00:00",
                "coverage_cutoff_at": "2026-01-10T00:00:00+00:00",
                "native_start_at": "2026-02-01T00:00:00+00:00",
            }
        }
        permission = Permission.objects.get(
            content_type__app_label="courses",
            codename="registration_count_baseline_manage",
        )
        self.principal = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="Synthetic course count automation",
            identity_snapshot="service:synthetic-course-count",
            permissions=(permission,),
        )
        scopes = tuple(
            f"courses.registration_count_baseline.{suffix}"
            for suffix in (
                "manage",
                "create",
                "detail",
                "dry-run",
                "validate",
                "activate",
                "cancel",
                "rollback",
                "total",
            )
        )
        issued = issue_credential_once(
            actor_principal=self.principal,
            target_principal_id=self.principal.id,
            name="Synthetic course count credential",
            scopes=scopes,
            idempotency_key="synthetic-course-count-credential",
            actor_permission="courses.registration_count_baseline_manage",
        )
        self.token = str(issued.response["token"])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _headers(self, *, key: str | None = None, revision: int | None = None) -> dict:
        headers = {"HTTP_AUTHORIZATION": f"Bearer {self.token}"}
        if key is not None:
            headers["HTTP_IDEMPOTENCY_KEY"] = key
        if revision is not None:
            headers["HTTP_IF_MATCH"] = revision_etag(revision)
        return headers

    def test_api_stage_transition_preview_and_replay_are_safe(self) -> None:
        request = {
            "source_reference": "synthetic-management-source",
            "confirmed": True,
            "reason_code": "synthetic_stage",
        }
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            created = self.client.post(
                "/api/v1/admin/course-registration-count-imports",
                data=json.dumps(request),
                content_type="application/json",
                **self._headers(key="synthetic-course-stage"),
            )
            self.assertEqual(created.status_code, 201, created.content)
            self.assertFalse(created.json()["replayed"])
            run_id = created.json()["id"]
            audit_count = AuditEvent.objects.count()
            serialized = json.dumps(created.json())
            self.assertNotIn("source_reference", serialized)
            self.assertNotIn(str(self.source), serialized)
            replay = self.client.post(
                "/api/v1/admin/course-registration-count-imports",
                data=json.dumps(request),
                content_type="application/json",
                **self._headers(key="synthetic-course-stage"),
            )
            self.assertEqual(replay.status_code, 201)
            self.assertTrue(replay.json()["replayed"])
            self.assertEqual(AuditEvent.objects.count(), audit_count)
            revision = created.json()["revision"]
            for action in ("validate", "activate"):
                response = self.client.post(
                    f"/api/v1/admin/course-registration-count-imports/{run_id}/{action}",
                    data=json.dumps({"confirmed": True, "reason_code": f"synthetic_{action}"}),
                    content_type="application/json",
                    **self._headers(key=f"synthetic-{action}", revision=revision),
                )
                self.assertEqual(response.status_code, 200, response.content)
                revision = response.json()["revision"]
            preview = self.client.get(
                f"/api/v1/admin/registration-campaigns/{self.campaign.slug}/public-count",
                **self._headers(),
            )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["count"], 1)
        self.assertEqual(preview.json()["mode"], "baseline_plus_native")
        self.assertIn("private", preview.headers["Cache-Control"])
        self.assertIn("no-store", preview.headers["Cache-Control"])
        self.assertEqual(preview.headers["X-Robots-Tag"], "noindex, nofollow")
        expected_revisions = {
            "courses.registration_count_baseline.staged": (
                None,
                1,
                "courses.registration_count_baseline.create",
                "synthetic-course-stage",
            ),
            "courses.registration_count_baseline.validated": (
                1,
                2,
                "courses.registration_count_baseline.validate",
                "synthetic-validate",
            ),
            "courses.registration_count_baseline.activated": (
                2,
                3,
                "courses.registration_count_baseline.activate",
                "synthetic-activate",
            ),
        }
        for action, (before, after, scope, raw_key) in expected_revisions.items():
            audit = AuditEvent.objects.get(action=action)
            self.assertEqual(
                audit.changes["revision"],
                {"before": before, "after": after},
            )
            self.assertEqual(
                audit.idempotency_key_hash,
                hash_idempotency_key(scope, raw_key),
            )
            self.assertTrue(audit.correlation_id)
            self.assertEqual(audit.target_id, uuid.UUID(run_id))
            self.assertEqual(audit.target_type, "courses.registration_count_source_run")
            self.assertEqual(audit.target_label, "course-registration-count-source")
            self.assertEqual(audit.outcome, AuditEvent.Outcome.SUCCEEDED)
        for audit in AuditEvent.objects.filter(
            action__startswith="courses.registration_count_baseline"
        ):
            safe = json.dumps({"changes": audit.changes, "metadata": audit.metadata})
            self.assertNotIn(str(self.source), safe)
            self.assertNotIn("synthetic-management-source", safe)

    def test_api_requires_scope_idempotency_confirmation_and_if_match(self) -> None:
        self.assertEqual(
            self.client.get("/api/v1/admin/course-registration-count-imports").status_code,
            401,
        )
        request = {
            "source_reference": "synthetic-management-source",
            "confirmed": True,
            "reason_code": "synthetic_stage",
        }
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            missing_key = self.client.post(
                "/api/v1/admin/course-registration-count-imports",
                data=json.dumps(request),
                content_type="application/json",
                **self._headers(),
            )
            self.assertEqual(missing_key.status_code, 400)
            created = self.client.post(
                "/api/v1/admin/course-registration-count-imports",
                data=json.dumps(request),
                content_type="application/json",
                **self._headers(key="precondition-stage"),
            )
            missing_match = self.client.post(
                f"/api/v1/admin/course-registration-count-imports/{created.json()['id']}/validate",
                data=json.dumps({"confirmed": True, "reason_code": "synthetic_validate"}),
                content_type="application/json",
                **self._headers(key="missing-match"),
            )
        self.assertEqual(missing_match.status_code, 428)
        self.assertEqual(missing_match.json()["error"]["code"], "precondition_required")

    def test_denied_api_action_audits_route_target_without_lookup(self) -> None:
        hidden_target = uuid.uuid4()
        response = self.client.post(
            f"/api/v1/admin/course-registration-count-imports/{hidden_target}/validate",
            data=json.dumps({"confirmed": False, "reason_code": "synthetic_denied"}),
            content_type="application/json",
            **self._headers(key="synthetic-api-denied"),
        )
        self.assertEqual(response.status_code, 400, response.content)
        denied = AuditEvent.objects.get(
            action="courses.registration_count_baseline.validate",
            outcome=AuditEvent.Outcome.DENIED,
        )
        self.assertEqual(denied.target_id, hidden_target)
        self.assertEqual(denied.target_type, "courses.registration_count_source_run")
        self.assertEqual(denied.target_label, "course-registration-count-source")
        self.assertEqual(
            denied.changes,
            {
                "state": {"before": None, "after": None},
                "revision": {"before": None, "after": None},
            },
        )
        self.assertEqual(
            denied.idempotency_key_hash,
            hash_idempotency_key(
                "courses.registration_count_baseline.validate",
                "synthetic-api-denied",
            ),
        )
        self.assertTrue(denied.correlation_id)
        self.assertEqual(denied.metadata, {"reason": "confirmation_required", "state": "denied"})

    def test_course_operator_has_studio_parity_and_other_operator_is_denied(self) -> None:
        course_operator = make_studio_user(
            username="synthetic-course-count-operator",
            roles=("course_operator",),
        )
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            response = authenticated_studio_client(course_operator).get(
                "/studio/courses/registration-count-baselines/"
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Course registration totals")
        self.assertContains(response, 'type="password"')
        self.assertNotContains(response, "synthetic-management-source")
        self.assertNotContains(response, str(self.source))
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        content_operator = make_studio_user(
            username="synthetic-course-count-denied",
            roles=("content_operator",),
        )
        denied = authenticated_studio_client(content_operator).get(
            "/studio/courses/registration-count-baselines/"
        )
        self.assertEqual(denied.status_code, 403)
        hidden_target = uuid.uuid4()
        denied_action = authenticated_studio_client(content_operator).post(
            f"/studio/courses/registration-count-baselines/{hidden_target}/validate/",
            {
                "confirmed": "true",
                "expected_revision": "1",
                "reason_code": "synthetic_denied",
                "idempotency_key": "synthetic-denied-action",
            },
        )
        self.assertEqual(denied_action.status_code, 403)
        denied_audit = AuditEvent.objects.get(
            action="courses.registration_count_baseline.validate",
            outcome=AuditEvent.Outcome.DENIED,
        )
        self.assertEqual(denied_audit.target_id, hidden_target)
        self.assertEqual(
            denied_audit.target_type,
            "courses.registration_count_source_run",
        )
        self.assertNotEqual(denied_audit.idempotency_key_hash, "")
        self.assertEqual(denied_audit.metadata["reason"], "permission_denied")

    def test_studio_uses_the_shared_lifecycle_and_safe_preview(self) -> None:
        operator = make_studio_user(
            username="synthetic-course-count-lifecycle",
            roles=("course_operator",),
        )
        client = authenticated_studio_client(operator)
        with override_settings(COURSE_REGISTRATION_COUNT_SOURCES=self.registry):
            staged = client.post(
                "/studio/courses/registration-count-baselines/",
                {
                    "source_reference": "synthetic-management-source",
                    "confirmed": "true",
                    "reason_code": "synthetic_stage",
                    "idempotency_key": "synthetic-studio-stage",
                },
            )
            self.assertEqual(staged.status_code, 302, staged.content)
            run = CourseRegistrationCountSourceRun.objects.get()
            self.assertNotIn("synthetic-management-source", staged.headers["Location"])
            detail_url = f"/studio/courses/registration-count-baselines/{run.id}/"
            detail = client.get(detail_url)
            self.assertEqual(detail.status_code, 200)
            self.assertNotContains(detail, "synthetic-management-source")
            self.assertNotContains(detail, str(self.source))

            for action in ("dry-run", "validate", "activate"):
                response = client.post(
                    f"{detail_url}{action}/",
                    {
                        "confirmed": "true",
                        "expected_revision": str(run.revision),
                        "reason_code": f"synthetic_{action.replace('-', '_')}",
                        "idempotency_key": f"synthetic-studio-{action}",
                    },
                )
                self.assertEqual(response.status_code, 302, response.content)
                run.refresh_from_db()

            preview = client.get(
                f"/studio/courses/registration-campaigns/{self.campaign.slug}/public-count/"
            )
            self.assertEqual(preview.status_code, 200)
            self.assertContains(preview, "<dt>Total</dt><dd>1</dd>", html=True)
            self.assertContains(preview, "<dt>Complete</dt><dd>Yes</dd>", html=True)
            self.assertIn("private", preview.headers["Cache-Control"])

            rolled_back = client.post(
                f"{detail_url}rollback/",
                {
                    "confirmed": "true",
                    "expected_revision": str(run.revision),
                    "reason_code": "synthetic_rollback",
                    "idempotency_key": "synthetic-studio-rollback",
                },
            )
            self.assertEqual(rolled_back.status_code, 302, rolled_back.content)
            run.refresh_from_db()
        self.assertEqual(run.state, CourseRegistrationCountSourceRun.State.ROLLED_BACK)
        expected_revisions = {
            "courses.registration_count_baseline.staged": (
                None,
                1,
                "courses.registration_count_baseline.create",
                "synthetic-studio-stage",
            ),
            "courses.registration_count_baseline.dry_run": (
                1,
                1,
                "courses.registration_count_baseline.dry-run",
                "synthetic-studio-dry-run",
            ),
            "courses.registration_count_baseline.validated": (
                1,
                2,
                "courses.registration_count_baseline.validate",
                "synthetic-studio-validate",
            ),
            "courses.registration_count_baseline.activated": (
                2,
                3,
                "courses.registration_count_baseline.activate",
                "synthetic-studio-activate",
            ),
            "courses.registration_count_baseline.rolled_back": (
                3,
                4,
                "courses.registration_count_baseline.rollback",
                "synthetic-studio-rollback",
            ),
        }
        for action, (before, after, scope, raw_key) in expected_revisions.items():
            audit = AuditEvent.objects.get(action=action)
            self.assertEqual(
                audit.changes["revision"],
                {"before": before, "after": after},
            )
            self.assertEqual(
                audit.idempotency_key_hash,
                hash_idempotency_key(scope, raw_key),
            )
