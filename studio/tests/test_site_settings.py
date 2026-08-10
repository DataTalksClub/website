from __future__ import annotations

import uuid

from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, resolve, reverse

from accounts.studio_test_support import authenticated_studio_client, make_studio_user
from core.models import AuditEvent, OperationalSetting, OperationalSettingRevision
from core.site_settings import ANNOUNCEMENT_ENABLED_KEY, ANNOUNCEMENT_MESSAGE_KEY


class StudioSiteSettingsTests(TestCase):
    def setUp(self) -> None:
        self.admin = make_studio_user(username="settings-admin", roles=("site_admin",))
        self.client = authenticated_studio_client(self.admin)
        self.url = reverse("studio:settings")

    def assert_private(self, response, status: int) -> None:
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.headers["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])

    def post_settings(
        self,
        client: Client,
        *,
        enabled: bool,
        message: str,
        enabled_revision: int,
        message_revision: int,
        idempotency_key: str | None = None,
    ):
        data = {
            "idempotency_key": idempotency_key or str(uuid.uuid4()),
            "enabled_expected_revision": str(enabled_revision),
            "message_expected_revision": str(message_revision),
            "announcement_message": message,
        }
        if enabled:
            data["announcement_enabled"] = "true"
        return client.post(self.url, data)

    def test_canonical_get_head_navigation_and_read_only_contract(self) -> None:
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(self.url)
        self.assert_private(response, 200)
        settings_queries = [
            query
            for query in captured.captured_queries
            if "core_operationalsetting" in query["sql"].casefold()
        ]
        self.assertEqual(len(settings_queries), 1)
        self.assertEqual(self.url, "/studio/settings")
        self.assertContains(response, "Site settings")
        self.assertContains(response, "Site announcement")
        self.assertContains(response, "Type: Boolean. Default: Off.")
        self.assertContains(response, "Type: String. Default: empty.")
        self.assertContains(response, 'maxlength="500"')
        self.assertContains(response, "Save site settings")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'name="enabled_expected_revision"')
        self.assertContains(response, 'name="message_expected_revision"')
        self.assertContains(response, 'name="idempotency_key"')
        self.assertContains(response, 'aria-describedby="announcement-enabled-help"')
        self.assertNotContains(response, 'aria-label="Site announcement"')

        head = self.client.head(self.url)
        self.assert_private(head, 200)
        self.assertEqual(head.content, b"")
        with self.assertRaises(Resolver404):
            resolve(f"{self.url}/")

        auditor = make_studio_user(username="settings-auditor", roles=("auditor",))
        auditor_client = authenticated_studio_client(auditor)
        read_only = auditor_client.get(self.url)
        self.assert_private(read_only, 200)
        self.assertContains(read_only, "Site settings")
        self.assertContains(read_only, "disabled")
        self.assertContains(read_only, "readonly")
        self.assertNotContains(read_only, "Save site settings")
        denied_post = self.post_settings(
            auditor_client,
            enabled=True,
            message="Denied",
            enabled_revision=0,
            message_revision=0,
        )
        self.assert_private(denied_post, 403)
        self.assertFalse(OperationalSetting.objects.exists())

        outsider = make_studio_user(username="settings-outsider", roles=("course_operator",))
        outsider_client = authenticated_studio_client(outsider)
        self.assert_private(outsider_client.get(self.url), 403)
        home = outsider_client.get(reverse("studio:home"))
        self.assertNotContains(home, self.url)
        self.assertContains(self.client.get(reverse("studio:home")), self.url)

    def test_post_uses_prg_and_refresh_does_not_duplicate_change_evidence(self) -> None:
        response = self.post_settings(
            self.client,
            enabled=True,
            message="  Deterministic announcement  ",
            enabled_revision=0,
            message_revision=0,
        )
        self.assert_private(response, 302)
        self.assertEqual(response.headers["Location"], f"{self.url}?saved=1")
        self.assertEqual(
            OperationalSetting.objects.get(key=ANNOUNCEMENT_MESSAGE_KEY).value,
            "Deterministic announcement",
        )
        self.assertIs(
            OperationalSetting.objects.get(key=ANNOUNCEMENT_ENABLED_KEY).value,
            True,
        )
        self.assertEqual(OperationalSettingRevision.objects.count(), 2)
        self.assertEqual(
            AuditEvent.objects.filter(action="core.operational_settings.batch_updated").count(),
            1,
        )

        success = self.client.get(response.headers["Location"])
        self.assert_private(success, 200)
        self.assertContains(success, "Site settings saved.")
        refresh = self.client.get(response.headers["Location"])
        self.assert_private(refresh, 200)
        self.assertEqual(OperationalSettingRevision.objects.count(), 2)

    def test_validation_and_stale_conflict_preserve_safe_form_state(self) -> None:
        invalid_checkbox = self.client.post(
            self.url,
            {
                "idempotency_key": str(uuid.uuid4()),
                "enabled_expected_revision": "0",
                "message_expected_revision": "0",
                "announcement_enabled": "yes",
                "announcement_message": "Must not be coerced",
            },
        )
        self.assert_private(invalid_checkbox, 400)
        self.assertFalse(OperationalSetting.objects.exists())

        missing_message = self.client.post(
            self.url,
            {
                "idempotency_key": str(uuid.uuid4()),
                "enabled_expected_revision": "0",
                "message_expected_revision": "0",
            },
        )
        self.assert_private(missing_message, 400)
        self.assertFalse(OperationalSetting.objects.exists())

        invalid_key = str(uuid.uuid4())
        invalid = self.post_settings(
            self.client,
            enabled=True,
            message="unsafe\nmessage",
            enabled_revision=0,
            message_revision=0,
            idempotency_key=invalid_key,
        )
        self.assert_private(invalid, 400)
        self.assertContains(invalid, "There is a problem", status_code=400)
        self.assertContains(invalid, 'id="settings-errors"', status_code=400)
        self.assertContains(invalid, 'tabindex="-1"', status_code=400)
        self.assertContains(invalid, invalid_key, status_code=400)
        self.assertContains(invalid, "unsafe\nmessage", status_code=400)
        self.assertFalse(OperationalSetting.objects.exists())

        first_client = authenticated_studio_client(self.admin)
        second_client = authenticated_studio_client(self.admin)
        self.assert_private(first_client.get(self.url), 200)
        self.assert_private(second_client.get(self.url), 200)
        first = self.post_settings(
            first_client,
            enabled=True,
            message="First save",
            enabled_revision=0,
            message_revision=0,
        )
        self.assert_private(first, 302)
        stale_key = str(uuid.uuid4())
        stale = self.post_settings(
            second_client,
            enabled=False,
            message="Second proposed save",
            enabled_revision=0,
            message_revision=0,
            idempotency_key=stale_key,
        )
        self.assert_private(stale, 409)
        self.assertContains(stale, "changed in another session", status_code=409)
        self.assertContains(stale, "Second proposed save", status_code=409)
        self.assertContains(stale, stale_key, status_code=409)
        self.assertContains(
            stale,
            'href="#site-announcement-fields"',
            status_code=409,
        )
        self.assertContains(stale, 'value="1"', status_code=409)
        self.assertEqual(
            OperationalSetting.objects.get(key=ANNOUNCEMENT_MESSAGE_KEY).value,
            "First save",
        )
        self.assertEqual(OperationalSettingRevision.objects.count(), 2)

    def test_csrf_method_anonymous_and_permission_removal_fail_before_mutation(self) -> None:
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        missing_csrf = self.post_settings(
            csrf_client,
            enabled=True,
            message="CSRF denied",
            enabled_revision=0,
            message_revision=0,
        )
        self.assert_private(missing_csrf, 403)
        self.assertFalse(OperationalSetting.objects.exists())

        page = csrf_client.get(self.url)
        token = page.cookies["csrftoken"].value
        valid = csrf_client.post(
            self.url,
            {
                "csrfmiddlewaretoken": token,
                "idempotency_key": str(uuid.uuid4()),
                "enabled_expected_revision": "0",
                "message_expected_revision": "0",
                "announcement_enabled": "true",
                "announcement_message": "CSRF accepted",
            },
        )
        self.assert_private(valid, 302)

        self.assert_private(self.client.put(self.url), 405)
        anonymous = Client().get(self.url)
        self.assert_private(anonymous, 302)

        write_permission = Permission.objects.get(
            content_type__app_label="core",
            codename="change_operational_settings",
        )
        Group.objects.get(name="site_admin").permissions.remove(write_permission)
        removed = self.post_settings(
            self.client,
            enabled=False,
            message="Must not apply",
            enabled_revision=1,
            message_revision=1,
        )
        self.assert_private(removed, 403)
        self.assertEqual(
            OperationalSetting.objects.get(key=ANNOUNCEMENT_MESSAGE_KEY).value,
            "CSRF accepted",
        )
