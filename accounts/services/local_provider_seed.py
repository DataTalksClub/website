"""Fail-closed local-development seed for the social sign-in providers.

Why this exists
---------------
``/accounts/signup/`` and ``/accounts/login/`` draw one "Continue with …"
button per ``allauth`` :class:`~allauth.socialaccount.models.SocialApp` bound to
the current site.  A fresh local database has none, so both pages render with
that block missing entirely — and on the deployed site it is the most prominent
thing on the page.  A design reviewed against the empty region is a design
reviewed against a page this site does not serve.

This seed writes one placeholder ``SocialApp`` per installed provider so those
buttons render locally, and nothing else.

The credentials are inert placeholders, not secrets
---------------------------------------------------
Every value written here is an obviously fake, checked-in string: it contains
the word ``placeholder``, it authenticates against nothing, and it is not read
from the environment.  Clicking a seeded button reaches the real provider and
fails there, which is the expected outcome — the OAuth flow itself is untouched
and is not stubbed.

Like :mod:`courses.services.local_course_seed`, this refuses to run anywhere but
a local or test SQLite database, so a placeholder credential cannot reach a
deployed one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from allauth.socialaccount.models import SocialApp
from allauth.socialaccount.providers import registry
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.db import transaction

from core.bootstrap import RuntimeEnvironment

ALLOWED_ENVIRONMENTS = frozenset({RuntimeEnvironment.LOCAL, RuntimeEnvironment.TEST})
SQLITE_ENGINE = "django.db.backends.sqlite3"

# The three providers `website.settings.base` installs.  The name is what the
# entrance pages render inside "Continue with …", so it is the provider's own
# capitalisation rather than its id.
PLACEHOLDER_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("google", "Google"),
    ("github", "GitHub"),
    ("slack", "Slack"),
)

# Deliberately unmistakable. Nothing here is a credential; a reader who finds
# one of these strings in a database is meant to know instantly that it is not.
PLACEHOLDER_MARKER = "placeholder"
PLACEHOLDER_SECRET = "local-development-placeholder-not-a-secret"

# `accounts.views.login.get_available_providers` caches the provider list for an
# hour under this key, so a seed run that did not clear it would leave the login
# page showing the empty state it was run to fix.
PROVIDER_CACHE_KEY = "available_providers"


class LocalProviderSeedError(RuntimeError):
    """A fail-closed refusal: wrong environment, wrong database, or wrong site."""


@dataclass(frozen=True, slots=True)
class SeededProvider:
    """One provider row and what the seed did to it."""

    provider: str
    name: str
    created: bool


@dataclass(frozen=True, slots=True)
class LocalProviderSeedResult:
    """Everything a caller needs to report the run without reading the database."""

    providers: tuple[SeededProvider, ...]
    site_id: int

    def summary(self) -> dict[str, Any]:
        return {
            "providers": [provider.provider for provider in self.providers],
            "providers_created": sum(provider.created for provider in self.providers),
            "site_id": self.site_id,
            "credentials": "placeholder",
        }


def placeholder_client_id(provider: str) -> str:
    """The fake client id one provider gets, named so it cannot be mistaken."""

    return f"local-development-{PLACEHOLDER_MARKER}-{provider}-client-id"


def assert_local_database() -> None:
    """Refuse to touch anything but a local/test SQLite database."""

    environment = getattr(settings, "RUNTIME_ENVIRONMENT", None)
    if environment not in ALLOWED_ENVIRONMENTS:
        raise LocalProviderSeedError("environment-not-local")
    engine = settings.DATABASES["default"].get("ENGINE")
    if engine != SQLITE_ENGINE:
        raise LocalProviderSeedError("database-not-local-sqlite")


def assert_providers_installed() -> None:
    """Fail loudly when a provider this seed names is not installed."""

    installed = {provider for provider, _name in registry.as_choices()}
    missing = sorted(
        provider for provider, _name in PLACEHOLDER_PROVIDERS if provider not in installed
    )
    if missing:
        raise LocalProviderSeedError(f"provider-not-installed:{','.join(missing)}")


def _current_site() -> Site:
    site = Site.objects.filter(pk=settings.SITE_ID).first()
    if site is None:
        raise LocalProviderSeedError("site-not-configured")
    return site


def _seed_provider(provider: str, name: str, site: Site) -> SeededProvider:
    app, created = SocialApp.objects.update_or_create(
        provider=provider,
        defaults={
            "name": name,
            "client_id": placeholder_client_id(provider),
            "secret": PLACEHOLDER_SECRET,
            "key": "",
        },
    )
    app.sites.add(site)
    return SeededProvider(provider=provider, name=name, created=created)


def seed_local_social_providers() -> LocalProviderSeedResult:
    """Give the local database one placeholder app per installed provider."""

    assert_local_database()
    assert_providers_installed()
    site = _current_site()
    with transaction.atomic():
        seeded = tuple(
            _seed_provider(provider, name, site) for provider, name in PLACEHOLDER_PROVIDERS
        )
    cache.delete(PROVIDER_CACHE_KEY)
    return LocalProviderSeedResult(providers=seeded, site_id=site.pk)
