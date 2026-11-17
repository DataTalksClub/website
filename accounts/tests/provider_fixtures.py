"""Site-bound ``SocialApp`` rows for tests, the way production configures sign-in."""

from __future__ import annotations

from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.sites.models import Site

ALL_PROVIDERS = (("google", "Google"), ("github", "GitHub"), ("slack", "Slack"))


def configure_site_providers(*providers: tuple[str, str]) -> None:
    site, _ = Site.objects.get_or_create(
        pk=settings.SITE_ID,
        defaults={"domain": "testserver", "name": "testserver"},
    )
    for provider, name in providers:
        app = SocialApp.objects.create(
            provider=provider,
            name=name,
            client_id=f"test-{provider}-client-id",
            secret="test-not-a-secret",
        )
        app.sites.add(site)
