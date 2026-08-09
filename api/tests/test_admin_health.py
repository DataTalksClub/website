from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from management_auth.models import APIPrincipal
from management_auth.services import create_principal, issue_credential_once


class AdminHealthIntegrationTests(TestCase):
    def test_anonymous_and_session_staff_are_denied_as_bearer_only(self) -> None:
        url = reverse("api:admin-health")
        anonymous = self.client.get(url)
        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(anonymous.json()["error"]["code"], "authentication_required")
        self.assertEqual(anonymous.headers["WWW-Authenticate"], "Bearer")

        user = get_user_model().objects.create_user(username="legacy-session", is_staff=True)
        self.client.force_login(user)
        session = self.client.get(url)
        self.assertEqual(session.status_code, 401)
        self.assertEqual(session.json()["error"]["code"], "authentication_required")
        self.assertEqual(session.headers["WWW-Authenticate"], "Bearer")

    def test_scoped_service_bearer_returns_non_pii_health(self) -> None:
        permission = Permission.objects.get(
            content_type__app_label="core",
            codename="access_studio",
        )
        principal = create_principal(
            kind=APIPrincipal.Kind.SERVICE,
            name="integration health",
            identity_snapshot="service:integration-health",
            permissions=(permission,),
        )
        issued = issue_credential_once(
            actor_principal=principal,
            target_principal_id=principal.id,
            name="integration health credential",
            scopes=("studio.home.read",),
            idempotency_key="integration-health",
            actor_permission="core.access_studio",
        )
        response = self.client.get(
            reverse("api:admin-health"),
            HTTP_AUTHORIZATION=f"Bearer {issued.response['token']}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "version": "local-development-build-version-not-configured",
                "source_sha": None,
                "image_digest": None,
            },
        )
        self.assertNotIn("actor", response.json())
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-store", response.headers["Cache-Control"])
