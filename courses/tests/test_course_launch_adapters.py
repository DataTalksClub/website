"""Permissions, API concurrency, Studio forms and CLI credential boundaries."""

import json
import uuid
from datetime import timedelta

from community_base.mail.models import EmailDelivery
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone

from accounts.studio_test_support import authenticated_studio_client, make_studio_user
from core.models import AuditEvent
from courses.models import Announcement, Cohort, Course
from management_auth.models import APICredential, APIPrincipal
from management_auth.tokens import encode_secret, generate_token


class CourseLaunchAdapterTests(TestCase):
    def setUp(self):
        self.user = make_studio_user(username="launch-operator", roles=("site_admin",))
        self.studio = authenticated_studio_client(self.user)
        self.principal = APIPrincipal.objects.create(
            kind="human", name="Launch test", identity_snapshot="user:launch-test", user=self.user
        )
        self.principal.permissions.set(
            Permission.objects.filter(
                content_type__app_label="courses",
                codename__in=["read_course_launch", "manage_course_launch", "send_course_launch"],
            )
        )
        token = generate_token()
        self.token = token.raw
        self.credential = APICredential.objects.create(
            principal=self.principal,
            name="Launch test",
            prefix=token.prefix,
            secret_digest=encode_secret(token.secret),
            digest_algorithm="pbkdf2_sha256",
            digest_version=1,
            scopes=["courses.launch.read", "courses.launch.write", "courses.launch.send"],
            expires_at=timezone.now() + timedelta(days=1),
        )
        self.url = "/api/v1/admin/course-launch"

    def post(self, payload, revision=None):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Idempotency-Key": str(uuid.uuid4()),
        }
        if revision is not None:
            headers["If-Match"] = f'"rev-{revision}"'
        return self.client.post(
            self.url + "/commands",
            json.dumps(payload),
            content_type="application/json",
            headers=headers,
        )

    def test_no_token_wrong_scope_and_disabled_principal_are_denied(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)
        APICredential.objects.filter(pk=self.credential.pk).update(scopes=[])
        self.assertEqual(
            self.client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {self.token}").status_code, 403
        )
        self.principal.is_active = False
        self.principal.save()
        self.assertEqual(
            self.client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {self.token}").status_code, 401
        )

    def test_studio_composer_renders_and_support_cannot_access(self):
        response = self.studio.get("/studio/course-launch/")
        self.assertContains(response, "Compose announcement")
        self.assertContains(response, "Launch a live cohort")
        self.assertIn("no-store", response.headers["Cache-Control"])
        support = make_studio_user(username="launch-support", roles=("support_operator",))
        self.assertEqual(
            authenticated_studio_client(support).get("/studio/course-launch/").status_code, 403
        )
        self.assertEqual(self.client.get("/studio/course-launch/").status_code, 302)

    def test_template_save_publish_and_stale_revision_via_api(self):
        response = self.post(
            {
                "action": "template_save",
                "name": "Email",
                "channel": "email",
                "kind": "module",
                "subject": "Subject",
                "body": "Private operator message",
            }
        )
        self.assertEqual(response.status_code, 200, response.content)
        identity = response.json()["id"]
        result = self.post(
            {"action": "template_publish", "id": identity, "expected_revision": 1}, 1
        )
        self.assertEqual(result.status_code, 200, result.content)
        stale = self.post(
            {
                "action": "template_archive",
                "id": identity,
                "expected_revision": 2,
                "archived": True,
            },
            2,
        )
        self.assertEqual(stale.status_code, 409)
        self.assertNotIn("Private operator message", str(list(AuditEvent.objects.values())))

    def test_api_naive_start_returns_400_and_if_match_is_required(self):
        course = Course.objects.create(slug="api-test", title="API test")
        cohort = Cohort.objects.create(course=course, slug="api-2027", title="API cohort")
        payload = {
            "action": "timing_update",
            "id": str(cohort.pk),
            "target_kind": "cohort",
            "expected_revision": 0,
            "starts_at": "2027-01-01T12:00:00",
            "provenance": "Confirmed",
        }
        self.assertEqual(self.post(payload, 0).status_code, 400)
        payload["starts_at"] = None
        self.assertEqual(self.post(payload).status_code, 428)

    def test_course_operator_requires_email_authority_for_dispatch(self):
        user = make_studio_user(username="launch-course-only", roles=("course_operator",))
        response = authenticated_studio_client(user).post(
            "/studio/course-launch/dispatch/",
            {"action": "send", "id": str(uuid.uuid4()), "confirmed": "true"},
        )
        self.assertEqual(response.status_code, 403)

    def test_course_and_cohort_creation_use_shared_api_services(self):
        course = self.post(
            {
                "action": "course_create",
                "slug": "new-course",
                "title": "New course",
                "version_ids": [],
            }
        )
        self.assertEqual(course.status_code, 200, course.content)
        cohort = self.post(
            {
                "action": "cohort_create",
                "course_id": course.json()["id"],
                "slug": "new-2027",
                "identifier": "2027",
                "title": "New cohort",
                "description": "Explicit cohort description",
                "year": 2027,
            }
        )
        self.assertEqual(cohort.status_code, 200, cohort.content)

    def test_invalid_idempotency_keys_are_safe_in_api_and_studio(self):
        for key in ("", "x" * 129):
            response = self.client.post(
                self.url + "/commands",
                json.dumps({"action": "launch"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.token}",
                HTTP_IDEMPOTENCY_KEY=key,
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["code"], "invalid_idempotency_key")
            response = self.studio.post(
                "/studio/course-launch/commands/",
                {"action": "launch", "idempotency_key": key},
                follow=True,
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Changes were not saved")

    def test_combined_course_and_email_roles_can_request_a_controlled_simulation(self):
        user = make_studio_user(
            username="combined-launch-operator", roles=("course_operator", "email_operator")
        )
        course = Course.objects.create(slug="combined-course", title="Combined course")
        cohort = Cohort.objects.create(course=course, slug="combined-2027", title="Combined cohort")
        row = Announcement.objects.create(
            cohort=cohort,
            channel="email",
            target_id=str(cohort.pk),
            subject="Synthetic preview",
            body="Reviewed copy",
        )
        response = authenticated_studio_client(user).post(
            "/studio/course-launch/dispatch/",
            {
                "action": "test_send",
                "id": str(row.pk),
                "destination": "operator@example.invalid",
                "confirmed": "true",
                "idempotency_key": str(uuid.uuid4()),
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Saved test_send")
        self.assertFalse(EmailDelivery.objects.filter(related_object_id=str(row.pk)).exists())

    def test_email_authority_alone_cannot_dispatch_course_messages(self):
        user = make_studio_user(username="launch-email-only", roles=("email_operator",))
        response = authenticated_studio_client(user).post(
            "/studio/course-launch/dispatch/",
            {"action": "test_send", "id": str(uuid.uuid4()), "confirmed": "true"},
        )
        self.assertEqual(response.status_code, 403)
