"""The admin API surface for the OAuth sign-in provider credentials.

The load-bearing assertion in this file is the negative one: a read must not
contain the client secret, in any field, anywhere in the body.
"""

from __future__ import annotations

import json

from allauth.socialaccount.models import SocialApp  # type: ignore[import-untyped]
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.development_owner import bootstrap_development_owner
from accounts.services.oauth_providers import PROVIDER_CACHE_KEY, SUPPORTED_PROVIDERS
from accounts.studio_roles import MANAGE_API_CREDENTIALS
from core.bootstrap import RuntimeEnvironment
from core.models import AuditEvent
from management_api.openapi import generate_document
from management_auth.models import APIPrincipal, APIRateAdmission
from management_auth.services import issue_credential_once

OWNER_EMAIL = "oauth-provider-owner@example.test"
OWNER_PASSWORD = "oauth-provider-owner-password-114"
GITHUB_SECRET = "github-client-secret-value-114"


@override_settings(RUNTIME_ENVIRONMENT=RuntimeEnvironment.TEST)
class AdminOAuthProviderTests(TestCase):
    def setUp(self) -> None:
        bootstrap_development_owner(
            email=OWNER_EMAIL,
            password=OWNER_PASSWORD,
            reset_password=False,
            allow_test=True,
        )
        self.human = APIPrincipal.objects.get(kind=APIPrincipal.Kind.HUMAN)
        issued = issue_credential_once(
            actor_principal=self.human,
            target_principal_id=self.human.id,
            name="OAuth provider actor",
            scopes=("accounts.oauth_providers.read", "accounts.oauth_providers.write"),
            idempotency_key="oauth-provider-actor",
            actor_permission=MANAGE_API_CREDENTIALS,
            created_by=self.human.user,
        )
        self.token = str(issued.response["token"])
        self.list_url = "/api/v1/admin/auth/providers"
        Site.objects.get_or_create(
            id=2,
            defaults={"domain": "testserver", "name": "testserver"},
        )

    def get(self, *, token: str | None = None):
        return self.client.get(self.list_url, HTTP_AUTHORIZATION=f"Bearer {token or self.token}")

    def put(self, provider: str, payload: object, *, token: str | None = None):
        return self.client.put(
            f"{self.list_url}/{provider}",
            data=json.dumps(payload),
            content_type="application/json; charset=utf-8",
            HTTP_AUTHORIZATION=f"Bearer {token or self.token}",
        )

    def clear_rate_admissions(self) -> None:
        APIRateAdmission.objects.all().delete()

    def test_get_lists_every_installed_provider_with_no_secret_anywhere(self) -> None:
        SocialApp.objects.create(
            provider="github",
            name="GitHub",
            client_id="github-client-id",
            secret=GITHUB_SECRET,
        )
        response = self.get()
        self.assertEqual(response.status_code, 200)
        raw = response.content.decode("utf-8")
        # The whole body, not one field: a secret must not survive anywhere.
        self.assertNotIn(GITHUB_SECRET, raw)

        providers = response.json()["providers"]
        self.assertEqual([item["provider"] for item in providers], list(SUPPORTED_PROVIDERS))
        by_provider = {item["provider"]: item for item in providers}
        self.assertNotIn("secret", by_provider["github"])
        self.assertTrue(by_provider["github"]["has_secret"])
        self.assertTrue(by_provider["github"]["is_configured"])
        self.assertEqual(by_provider["github"]["client_id"], "github-client-id")
        self.assertFalse(by_provider["google"]["has_secret"])
        self.assertFalse(by_provider["google"]["is_configured"])
        self.assertEqual(by_provider["google"]["client_id"], "")
        self.assertTrue(
            by_provider["google"]["callback_url"].endswith("/accounts/google/login/callback/")
        )

    def test_put_creates_the_row_binds_the_site_and_returns_no_secret(self) -> None:
        cache.set(PROVIDER_CACHE_KEY, [{"id": "stale"}], 3600)
        self.clear_rate_admissions()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.put(
                "google",
                {"client_id": "google-client-id", "secret": "google-client-secret-114"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("google-client-secret-114", response.content.decode("utf-8"))
        body = response.json()
        self.assertTrue(body["has_secret"])
        self.assertTrue(body["is_configured"])
        self.assertNotIn("secret", body)

        app = SocialApp.objects.get(provider="google")
        self.assertEqual(app.client_id, "google-client-id")
        self.assertEqual(app.secret, "google-client-secret-114")
        self.assertTrue(app.sites.filter(id=2).exists())
        self.assertIsNone(cache.get(PROVIDER_CACHE_KEY))

    def test_omitting_the_secret_leaves_the_stored_one_untouched(self) -> None:
        SocialApp.objects.create(
            provider="github",
            name="GitHub",
            client_id="old-client-id",
            secret=GITHUB_SECRET,
        )
        self.clear_rate_admissions()
        response = self.put("github", {"client_id": "new-client-id"})
        self.assertEqual(response.status_code, 200)
        app = SocialApp.objects.get(provider="github")
        self.assertEqual(app.client_id, "new-client-id")
        self.assertEqual(app.secret, GITHUB_SECRET)
        self.assertTrue(response.json()["has_secret"])

    def test_an_empty_secret_clears_it(self) -> None:
        SocialApp.objects.create(
            provider="slack",
            name="Slack",
            client_id="slack-client-id",
            secret="slack-secret-114",
        )
        self.clear_rate_admissions()
        response = self.put("slack", {"client_id": "slack-client-id", "secret": ""})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["has_secret"])
        self.assertEqual(SocialApp.objects.get(provider="slack").secret, "")

    def test_the_audit_trail_records_the_change_without_the_secret(self) -> None:
        self.clear_rate_admissions()
        self.put("google", {"client_id": "google-client-id", "secret": "audited-secret-114"})
        event = AuditEvent.objects.get(action="accounts.oauth_provider.updated")
        serialized = json.dumps(
            {"changes": event.changes, "metadata": event.metadata},
            sort_keys=True,
            default=str,
        )
        self.assertNotIn("audited-secret-114", serialized)
        self.assertEqual(event.target_label, "google")
        self.assertEqual(
            event.changes["configuration_complete"],
            {"before": False, "after": True},
        )
        self.assertEqual(
            event.changes["client_id_present"],
            {"before": False, "after": True},
        )

    def test_an_uninstalled_provider_is_not_found(self) -> None:
        self.clear_rate_admissions()
        response = self.put("facebook", {"client_id": "x"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(SocialApp.objects.filter(provider="facebook").exists())

    def test_an_undeclared_field_is_refused(self) -> None:
        self.clear_rate_admissions()
        response = self.put("google", {"client_id": "x", "provider": "google"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SocialApp.objects.exists())

    def test_an_unauthenticated_read_is_refused(self) -> None:
        self.assertEqual(self.client.get(self.list_url).status_code, 401)

    def test_a_read_only_credential_cannot_write(self) -> None:
        issued = issue_credential_once(
            actor_principal=self.human,
            target_principal_id=self.human.id,
            name="OAuth provider reader",
            scopes=("accounts.oauth_providers.read",),
            idempotency_key="oauth-provider-reader",
            actor_permission=MANAGE_API_CREDENTIALS,
            created_by=self.human.user,
        )
        self.clear_rate_admissions()
        response = self.put(
            "google",
            {"client_id": "denied"},
            token=str(issued.response["token"]),
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(SocialApp.objects.exists())

    def test_the_openapi_document_never_returns_a_secret(self) -> None:
        document = generate_document()
        provider_schema = document["components"]["schemas"]["OAuthProvider"]
        self.assertNotIn("secret", provider_schema["properties"])
        self.assertIn("has_secret", provider_schema["properties"])
        update_schema = document["components"]["schemas"]["OAuthProviderUpdate"]
        self.assertTrue(update_schema["properties"]["secret"]["writeOnly"])
        operations = document["paths"]["/auth/providers"]
        self.assertEqual(operations["get"]["operationId"], "accounts.oauth_providers.read")
        item = document["paths"]["/auth/providers/{provider}"]
        self.assertEqual(item["put"]["operationId"], "accounts.oauth_providers.write")
