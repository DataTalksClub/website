"""Read and write the OAuth client credentials the sign-in buttons run on.

Google, GitHub and Slack are ``allauth`` :class:`SocialApp` rows, and allauth
reads them per request, so the *values* were already restart-free: what was
missing was any way to change them without a database console.  This is that
way, and it is deliberately the whole surface -- there is no second copy of
these credentials in the operational settings table, because that table is
readable by anything that can read the database and writes its values into an
audit trail in the clear.

The client secret is write-only, everywhere
-------------------------------------------

A read never returns the secret.  It returns whether one is set, which is all an
operator needs to answer "is this provider configured?", and it is the only
thing about a secret that is safe to put in a JSON response, a log line, or a
page.  A write may set a secret; omitting the field leaves the stored one
untouched, so an operator can correct a client ID without re-typing the secret,
and sending an empty string clears it.  The audit trail records whether the
provider ended up fully configured, never the secret and never its length.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from allauth.socialaccount.models import SocialApp
from django.conf import settings as django_settings
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.db import transaction

from core.audit import AuditWriteContext, record_audit_event
from core.idempotency import JsonObject, JsonValue
from core.models import AuditEvent
from core.runtime_endpoints import canonical_origin
from core.services import ServiceContext, validate_actor_ref

#: The three providers ``website.settings.base`` installs.  A provider that is
#: not installed cannot be configured here: allauth would have no login view for
#: it and the row would be an inert credential sitting in the database.
SUPPORTED_PROVIDERS: tuple[str, ...] = ("google", "github", "slack")

#: Static, checked-in metadata: what the operator sees, where they go to create
#: the application, and the path allauth registers for the callback.  None of it
#: is a credential and none of it comes from the database.
PROVIDER_META: dict[str, dict[str, str]] = {
    "google": {
        "label": "Google OAuth",
        "name": "Google",
        "configure_url": "https://console.cloud.google.com/apis/credentials",
        "callback_path": "/accounts/google/login/callback/",
    },
    "github": {
        "label": "GitHub OAuth",
        "name": "GitHub",
        "configure_url": "https://github.com/settings/developers",
        "callback_path": "/accounts/github/login/callback/",
    },
    "slack": {
        "label": "Slack OAuth",
        "name": "Slack",
        "configure_url": "https://api.slack.com/apps",
        "callback_path": "/accounts/slack/login/callback/",
    },
}

#: ``accounts.views.login.get_available_providers`` caches the rendered provider
#: list for an hour.  A write that did not clear it would leave the sign-in page
#: showing the state the operator just changed.
PROVIDER_CACHE_KEY = "available_providers"

OAUTH_PROVIDER_READ_PERMISSION = "core.read_operational_settings"
OAUTH_PROVIDER_WRITE_PERMISSION = "core.change_operational_settings"
OAUTH_PROVIDER_AUDIT_READ = "accounts.oauth_provider.read"
OAUTH_PROVIDER_AUDIT_WRITE = "accounts.oauth_provider.updated"

_CLIENT_ID = re.compile(r"^[A-Za-z0-9._:~+/=-]{1,191}$")
_SECRET_MAX_LENGTH = 191


class InvalidOAuthProvider(ValueError):
    """The provider, client ID, or secret is not something we will store."""


class OAuthProviderNotFound(LookupError):
    """The provider is not one this deployment installs."""


@dataclass(frozen=True, slots=True)
class OAuthProviderUpdate:
    provider: str
    client_id: str
    #: ``None`` means "leave the stored secret alone"; ``""`` means "clear it".
    secret: str | None


def is_supported_provider(provider: object) -> bool:
    return isinstance(provider, str) and provider in SUPPORTED_PROVIDERS


def _scopes(provider: str) -> list[str]:
    configured = getattr(django_settings, "SOCIALACCOUNT_PROVIDERS", {}) or {}
    entry = configured.get(provider) or {}
    scopes = entry.get("SCOPE") or []
    return [str(scope) for scope in scopes]


def _present(app: SocialApp | None, *, provider: str, site_id: int) -> JsonObject:
    """The safe projection of one provider.  The secret is never in it."""

    meta = PROVIDER_META[provider]
    client_id = (app.client_id if app is not None else "") or ""
    has_secret = bool(app is not None and app.secret)
    return {
        "provider": provider,
        "name": meta["name"],
        "label": meta["label"],
        "configure_url": meta["configure_url"],
        "callback_path": meta["callback_path"],
        "callback_url": f"{canonical_origin().rstrip('/')}{meta['callback_path']}",
        "scopes": _scopes(provider),
        "client_id": client_id,
        "has_secret": has_secret,
        "is_configured": bool(client_id) and has_secret,
        "is_enabled": bool(
            app is not None and app.sites.filter(id=site_id).exists() and client_id
        ),
    }


def _site_id() -> int:
    return int(getattr(django_settings, "SITE_ID", 1))


def list_oauth_providers(
    _query: object = None,
    *,
    context: ServiceContext | None = None,
    using: str = "default",
) -> JsonObject:
    """Every installed provider and whether it is configured.  No secrets."""

    del context
    site_id = _site_id()
    apps = {
        app.provider: app
        for app in SocialApp.objects.using(using).filter(provider__in=SUPPORTED_PROVIDERS)
    }
    providers: list[JsonValue] = [
        _present(apps.get(provider), provider=provider, site_id=site_id)
        for provider in SUPPORTED_PROVIDERS
    ]
    return {"providers": providers}


def _normalized_update(provider: object, payload: object) -> OAuthProviderUpdate:
    if not is_supported_provider(provider):
        raise OAuthProviderNotFound("the OAuth provider is not installed")
    if not isinstance(payload, dict) or set(payload) - {"client_id", "secret"}:
        raise InvalidOAuthProvider("the OAuth provider request fields are invalid")
    if "client_id" not in payload:
        raise InvalidOAuthProvider("the OAuth provider request fields are invalid")
    client_id = payload["client_id"]
    if not isinstance(client_id, str):
        raise InvalidOAuthProvider("the OAuth client identifier is invalid")
    client_id = client_id.strip()
    if client_id and not _CLIENT_ID.fullmatch(client_id):
        raise InvalidOAuthProvider("the OAuth client identifier is invalid")
    secret: str | None = None
    if "secret" in payload:
        raw_secret = payload["secret"]
        if not isinstance(raw_secret, str) or len(raw_secret) > _SECRET_MAX_LENGTH:
            raise InvalidOAuthProvider("the OAuth client secret is invalid")
        secret = raw_secret.strip()
        if secret and any(character.isspace() for character in secret):
            raise InvalidOAuthProvider("the OAuth client secret is invalid")
    return OAuthProviderUpdate(
        provider=str(provider),
        client_id=client_id,
        secret=secret,
    )


def set_oauth_provider(
    *,
    provider: object,
    payload: object,
    actor_ref: str,
    actor_id: Any | None = None,
    api_principal_id: uuid.UUID | None = None,
    context: ServiceContext | None = None,
    using: str = "default",
) -> JsonObject:
    """Upsert one provider's client credentials and return the safe projection."""

    update = _normalized_update(provider, payload)
    if not isinstance(actor_ref, str) or not actor_ref:
        raise InvalidOAuthProvider("the OAuth provider actor is invalid")
    try:
        validate_actor_ref(actor_ref)
    except ValueError as error:
        raise InvalidOAuthProvider("the OAuth provider actor is invalid") from error
    if context is not None and context.actor_ref != actor_ref:
        raise InvalidOAuthProvider("the OAuth provider actor context is invalid")

    meta = PROVIDER_META[update.provider]
    site_id = _site_id()
    audit_context = AuditWriteContext.from_service_context(
        context or ServiceContext.from_current(actor_ref=actor_ref),
        actor_id=actor_id,
        api_principal_id=api_principal_id,
    )
    with transaction.atomic(using=using):
        app = SocialApp.objects.using(using).filter(provider=update.provider).first()
        before_client_id = (app.client_id if app is not None else "") or ""
        before_has_secret = bool(app is not None and app.secret)
        secret = (app.secret if app is not None else "") or ""
        if update.secret is not None:
            secret = update.secret
        if app is None:
            app = SocialApp.objects.using(using).create(
                provider=update.provider,
                name=meta["name"],
                client_id=update.client_id,
                secret=secret,
            )
        else:
            app.name = meta["name"]
            app.client_id = update.client_id
            app.secret = secret
            app.save(using=using, update_fields=("name", "client_id", "secret"))
        site = Site.objects.using(using).filter(id=site_id).first()
        if site is None:
            raise InvalidOAuthProvider("the configured site does not exist")
        app.sites.add(site)

        record_audit_event(
            action=OAUTH_PROVIDER_AUDIT_WRITE,
            target_type="socialaccount.socialapp",
            target_id=None,
            target_label=update.provider,
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=audit_context,
            # Only the shape of the change is recorded: whether a client ID is
            # present, and whether the provider is now usable.  The secret never
            # reaches the trail, and neither does its length.  The field names
            # avoid the word "secret" on purpose -- ``core.redaction`` would
            # blank a field named for one, leaving a trail that records that
            # something changed without saying what.
            changes={
                "client_id_present": {
                    "before": bool(before_client_id),
                    "after": bool(update.client_id),
                },
                "configuration_complete": {
                    "before": bool(before_client_id) and before_has_secret,
                    "after": bool(update.client_id) and bool(secret),
                },
            },
            metadata={"provider": update.provider, "surface": "admin_api"},
            using=using,
        )
        transaction.on_commit(lambda: cache.delete(PROVIDER_CACHE_KEY), using=using)
    app.refresh_from_db(using=using)
    return _present(app, provider=update.provider, site_id=site_id)


def oauth_provider_factory() -> JsonObject:
    """The code-default shape, used by the capability contract tests."""

    return {
        "providers": [
            {
                "provider": provider,
                "name": PROVIDER_META[provider]["name"],
                "label": PROVIDER_META[provider]["label"],
                "configure_url": PROVIDER_META[provider]["configure_url"],
                "callback_path": PROVIDER_META[provider]["callback_path"],
                "callback_url": PROVIDER_META[provider]["callback_path"],
                "scopes": [],
                "client_id": "",
                "has_secret": False,
                "is_configured": False,
                "is_enabled": False,
            }
            for provider in SUPPORTED_PROVIDERS
        ]
    }
