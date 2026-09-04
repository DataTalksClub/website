from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.studio_test_support import authenticated_studio_client, make_studio_user
from core.models import StaffSession
from management_auth.models import APICredential, APIPrincipal
from management_auth.services import create_principal


class CredentialBrowserFreshAuthenticationTests(TestCase):
    def setUp(self) -> None:
        self.manage_permission = Permission.objects.get(
            content_type__app_label="management_auth",
            codename="manage_api_credentials",
        )
        self.service_permission = Permission.objects.get(
            content_type__app_label="core",
            codename="access_studio",
        )
        self.admin = make_studio_user(username="credential-admin", roles=("site_admin",))
        self.actor = create_principal(
            kind=APIPrincipal.Kind.HUMAN,
            name="Browser administrator",
            identity_snapshot=f"human:{self.admin.pk}",
            user=self.admin,
            permissions=(self.manage_permission,),
        )
        self.target = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="Fixture service",
            identity_snapshot="service:development-automation",
            permissions=(self.service_permission,),
        )
        self.client = authenticated_studio_client(self.admin)
        self.url = reverse("studio:credential-list")

    def payload(self, **overrides: object) -> dict[str, object]:
        data: dict[str, object] = {
            "confirmed": "true",
            "name": "Fixture credential",
            "scopes": ["studio.home.read"],
            "target_principal_id": str(self.target.id),
            "expires_at": (timezone.now() + timedelta(days=30)).isoformat(),
            "idempotency_key": "credential-browser-create",
        }
        data.update(overrides)
        return data

    def test_fresh_session_can_create_a_credential(self) -> None:
        response = self.client.post(self.url, self.payload())

        self.assertEqual(response.status_code, 201)
        self.assertEqual(APICredential.objects.count(), 1)

    def test_stale_session_authentication_denies_creation(self) -> None:
        now = timezone.now()
        StaffSession.objects.update(
            authenticated_at=now - timedelta(seconds=901),
            last_seen_at=now,
        )

        response = self.client.post(self.url, self.payload())

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "Confirm credential creation before continuing.",
            status_code=400,
        )
        self.assertFalse(APICredential.objects.exists())
