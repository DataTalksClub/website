"""Capability declarations for the OAuth provider credentials surface."""

from __future__ import annotations

from core.capabilities import (
    AdapterMetadata,
    Capability,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
)

from .services.oauth_providers import (
    OAUTH_PROVIDER_AUDIT_READ,
    OAUTH_PROVIDER_AUDIT_WRITE,
    OAUTH_PROVIDER_READ_PERMISSION,
    OAUTH_PROVIDER_WRITE_PERMISSION,
    list_oauth_providers,
    oauth_provider_factory,
    set_oauth_provider,
)

#: ``secret`` is listed on both capabilities even though the read never produces
#: one: the redaction list is what strips a field out of an audit payload or a
#: logged request body, and a write body does carry it.
_REDACTED_FIELDS = (
    "authorization",
    "body",
    "cookie",
    "credential",
    "csrfmiddlewaretoken",
    "password",
    "secret",
    "token",
)


def _field_policy(_actor: object, field: str) -> bool:
    return field in {"client_id", "secret"}


OAUTH_PROVIDERS_READ = Capability(
    key="accounts.oauth_providers.read",
    description="Read OAuth sign-in provider configuration without any client secret",
    service_kind=ServiceKind.QUERY,
    service=list_oauth_providers,
    django_permission=OAUTH_PROVIDER_READ_PERMISSION,
    studio=AdapterMetadata(
        # No Studio page yet; the admin API is the runtime adapter.  Declaring
        # the Studio side test-only records that without pointing the Studio
        # navigation at a route that does not exist.
        route="/studio/settings/auth-providers",
        method="GET",
        operation_id="accounts.oauth_providers.read.html",
        test_only=True,
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/auth/providers",
        method="GET",
        operation_id="accounts.oauth_providers.read",
        scopes=("accounts.oauth_providers.read",),
        result_schema="OAuthProviders",
        rate_class="read",
        rate_cost=1,
    ),
    idempotency=IdempotencyPolicy.NONE,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action=OAUTH_PROVIDER_AUDIT_READ,
    redacted_fields=_REDACTED_FIELDS,
    test_factory=oauth_provider_factory,
)

OAUTH_PROVIDERS_WRITE = Capability(
    key="accounts.oauth_providers.write",
    description="Set one OAuth sign-in provider's client credentials",
    service_kind=ServiceKind.COMMAND,
    service=set_oauth_provider,
    django_permission=OAUTH_PROVIDER_WRITE_PERMISSION,
    studio=AdapterMetadata(
        route="/studio/settings/auth-providers/{provider}",
        method="PUT",
        operation_id="accounts.oauth_providers.write.html",
        writable_fields=("client_id", "secret"),
        test_only=True,
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/auth/providers/{provider}",
        method="PUT",
        operation_id="accounts.oauth_providers.write",
        scopes=("accounts.oauth_providers.write",),
        request_schema="OAuthProviderUpdate",
        result_schema="OAuthProvider",
        writable_fields=("client_id", "secret"),
        rate_class="write",
        rate_cost=1,
    ),
    # A PUT of one provider's credentials is naturally idempotent: the same body
    # applied twice leaves the same row, so a replay needs no stored result.
    idempotency=IdempotencyPolicy.OPTIONAL,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action=OAUTH_PROVIDER_AUDIT_WRITE,
    redacted_fields=_REDACTED_FIELDS,
    test_factory=oauth_provider_factory,
    field_policy=_field_policy,
)

OAUTH_PROVIDER_CAPABILITIES = (OAUTH_PROVIDERS_READ, OAUTH_PROVIDERS_WRITE)
