"""Deployment check: production must have a way for people to get in.

Production closes email/password signup (``accounts.auth.AccountAdapter``), so
the site's ``SocialApp`` rows are the only way to sign in *and* the only way to
create an account.  With none, every "Create account" and "Sign in" entrance
ends at the contact notice -- the site is up but nobody new can register for a
course.  That is an operator misconfiguration, so it is reported loudly here
(``migrate`` and ``check --database default`` run database checks) and at
request time by the ``account_entry_no_providers`` log event.

It is a Warning, not an Error: an Error would stop ``migrate`` and with it the
deploy that may be needed to fix things.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Tags, Warning, register
from django.db import DatabaseError

NO_PROVIDERS_CHECK_ID = "accounts.W001"


@register(Tags.database)
def check_production_sign_in_providers(app_configs: object, databases=None, **kwargs: object):
    del app_configs, kwargs
    if getattr(settings, "ENVIRONMENT", "local") != "production":
        return []
    if not databases or "default" not in databases:
        return []
    from allauth.socialaccount.models import SocialApp

    try:
        configured = SocialApp.objects.filter(sites__id__exact=settings.SITE_ID).exists()
    except DatabaseError:
        # Before the first migrate the table does not exist yet; there is
        # nothing to report until it does.
        return []
    if configured:
        return []
    return [
        Warning(
            "ENVIRONMENT=production has no SocialApp (Google, GitHub or Slack) "
            f"configured for SITE_ID={settings.SITE_ID}. Email signup is closed in "
            "production, so nobody can sign in or create an account.",
            id=NO_PROVIDERS_CHECK_ID,
            hint=(
                "Configure at least one sign-in provider through the management "
                "API's auth/providers endpoint (accounts.services.oauth_providers)."
            ),
        )
    ]
