"""Device login on the management credential surface."""

import json
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase

from management_auth.device_login import LAUNCH_SCOPES, is_device_login_admin

User = get_user_model()

_LAUNCH_PERMISSIONS = (
    "read_course_launch",
    "manage_course_launch",
    "send_course_launch",
)


def make_user(email, *, launch=False):
    user = User.objects.create_user(username=email, email=email)
    if launch:
        permissions = Permission.objects.filter(
            content_type__app_label="courses", codename__in=_LAUNCH_PERMISSIONS
        )
        assert permissions.count() == 3, "launch permissions are missing"
        user.user_permissions.add(*permissions)
    return user


class DeviceLoginAdminCheckTests(TestCase):
    def test_launch_manager_is_admin_plain_user_is_not(self):
        manager = make_user("manager@example.com", launch=True)
        member = make_user("member@example.com")
        self.assertTrue(is_device_login_admin(manager))
        self.assertFalse(is_device_login_admin(member))

    def test_inactive_manager_is_not_admin(self):
        manager = make_user("former@example.com", launch=True)
        manager.is_active = False
        manager.save(update_fields=("is_active",))
        self.assertFalse(is_device_login_admin(manager))


class DeviceLoginFlowTests(TestCase):
    manager: Any
    member: Any

    @classmethod
    def setUpTestData(cls):
        cls.manager = make_user("device-manager@example.com", launch=True)
        cls.member = make_user("device-member@example.com")

    def _start(self):
        response = self.client.post(
            "/api/auth/device",
            data=json.dumps({"label": "agent@box"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _poll(self, device_code):
        from community_base.device_login.codes import sha256_hex
        from community_base.device_login.models import DeviceGrant

        DeviceGrant.objects.filter(device_code_hash=sha256_hex(device_code)).update(
            last_poll_at=None
        )
        return self.client.post(
            "/api/auth/device/token",
            data=json.dumps({"device_code": device_code}),
            content_type="application/json",
        )

    def _approve(self, user_code, approve=True):
        return self.client.post(
            "/api/auth/device/approve",
            data=json.dumps({"user_code": user_code, "approve": approve}),
            content_type="application/json",
        )

    def test_full_flow_mints_credential_usable_on_the_launch_api(self):
        offer = self._start()
        self.client.force_login(self.manager)
        approved = self._approve(offer["user_code"])
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["status"], "approved")

        token_response = self._poll(offer["device_code"])
        self.assertEqual(token_response.status_code, 200)
        credential = token_response.json()
        self.assertIn("token", credential)
        self.assertEqual(set(credential["user"]), {"email", "name"})

        identity = self.client.get(
            "/api/v1/admin/course-launch?resource=identity",
            HTTP_AUTHORIZATION=f"Bearer {credential['token']}",
        )
        self.assertEqual(identity.status_code, 200)
        self.assertEqual(identity.json()["scopes"], sorted(LAUNCH_SCOPES))

        from core.models import AuditEvent

        audit_event = AuditEvent.objects.filter(
            action="management.credential.created",
            target_type="management.credential",
        ).first()
        self.assertIsNotNone(audit_event)
        self.assertEqual(audit_event.actor_id, self.manager.pk)
        self.assertEqual(audit_event.outcome, AuditEvent.Outcome.SUCCEEDED)
        self.assertEqual(audit_event.metadata.get("source"), "device_login")

        again = self._poll(offer["device_code"])
        self.assertEqual(again.json()["error"], "expired_token")

    def test_non_admin_approval_is_refused_and_mints_nothing(self):
        offer = self._start()
        self.client.force_login(self.member)
        refused = self._approve(offer["user_code"])
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["error"], "admin_only")

        from core.models import AuditEvent

        self.assertFalse(
            AuditEvent.objects.filter(
                action="management.credential.created",
                target_type="management.credential",
            ).exists()
        )

        pending = self._poll(offer["device_code"])
        self.assertEqual(pending.json()["error"], "authorization_pending")

        self.client.force_login(self.manager)
        self.assertEqual(self._approve(offer["user_code"]).status_code, 200)
        self.assertEqual(self._poll(offer["device_code"]).status_code, 200)

    def test_unknown_bearer_stays_unauthorized(self):
        response = self.client.get(
            "/api/v1/admin/course-launch?resource=identity",
            HTTP_AUTHORIZATION="Bearer synthetic-invalid",
        )
        self.assertEqual(response.status_code, 401)

    def test_verification_page_renders_review_for_manager(self):
        offer = self._start()
        self.client.force_login(self.manager)
        response = self.client.get(f"/device?code={offer['user_code']}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Authorize this machine?")

    def test_verification_page_requires_login(self):
        response = self.client.get("/device")
        self.assertEqual(response.status_code, 302)
