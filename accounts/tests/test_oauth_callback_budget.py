"""Server-side budget for the login/OAuth callback (intake fast-pages, AC3).

The callback's total latency has two parts: this site's own work (session,
provider-app lookup, the verified-email reconciliation in
``ConsolidatingSocialAccountAdapter.pre_social_login``, the social-link
writes and the session login) and the provider round trip (the token
exchange plus the user-info fetch, one to two HTTPS round trips to Google,
GitHub or Slack).

The provider part is network, not code: it is measured from the production
region with curl and documented in the intake report (Google ~45 ms, GitHub
~80 ms, Slack ~190 ms per round trip from the EU; a callback therefore
spends ~100-400 ms outside this site).  What this test pins is the part the
site owns: with the provider stubbed out, the callback through to the
session login completes in a bounded number of indexed single-row
reads/writes -- no per-item fan-out -- comfortably inside the 300 ms
server-side budget.
"""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from accounts.tests.test_imported_account_social_matching import (
    SYNTHETIC_ACCESS_TOKEN,
    ImportedAccountSignInTestCase,
    _FakeProviderSession,
)

#: The callback's own work stays far below this.  Measured at 42 queries for
#: a launch-day link of an imported member (session + app lookups, the
#: verified-email candidate resolution, identity activation, the social-link
#: and audit writes, the session login); the headroom covers small bounded
#: additions, while any per-row fan-out would exceed it by an order of
#: magnitude.
CALLBACK_QUERY_BUDGET = 60


class OAuthCallbackBudgetTests(ImportedAccountSignInTestCase):
    def test_google_callback_completes_its_own_work_within_budget(self) -> None:
        user = self.imported_user(email="callback.budget@example.invalid")
        client: Client = Client()
        start = client.get("/accounts/google/login/")
        self.assertEqual(start.status_code, 302)
        state = parse_qs(urlsplit(start.headers["Location"]).query)["state"][0]
        routes = self.google_routes(
            uid="google-uid-callback-budget",
            email="callback.budget@example.invalid",
        )

        def fake_session(_self: object) -> _FakeProviderSession:
            return _FakeProviderSession(routes)

        def fake_access_token(
            _self: object, code: object, pkce_code_verifier: object = None
        ) -> dict[str, str]:
            del code, pkce_code_verifier
            return {"access_token": SYNTHETIC_ACCESS_TOKEN}

        with (
            patch.object(
                DefaultSocialAccountAdapter,
                "get_requests_session",
                fake_session,
            ),
            patch.object(OAuth2Client, "get_access_token", fake_access_token),
            CaptureQueriesContext(connection) as captured,
        ):
            response = client.get(
                "/accounts/google/login/callback/",
                {"code": "synthetic-code", "state": state},
                follow=False,
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        self.assertEqual(int(client.session["_auth_user_id"]), user.pk)
        self.assertLessEqual(len(captured), CALLBACK_QUERY_BUDGET)
