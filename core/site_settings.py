"""Public-safe site settings shared by public views, Studio, and the admin API."""

from __future__ import annotations

import unicodedata
import uuid
from typing import Any

from core.capabilities import (
    AdapterMetadata,
    Capability,
    ConcurrencyPolicy,
    IdempotencyPolicy,
    ServiceKind,
)
from core.configuration import (
    InvalidOperationalSetting,
    OperationalSettingDefinition,
    register_operational_setting,
)
from core.idempotency import JsonObject, JsonValue
from core.models import OperationalSetting
from core.services import ServiceContext
from core.settings_batch import (
    SETTINGS_SOURCES,
    InvalidSettingsBatch,
    NormalizedSettingUpdate,
    ResolvedSetting,
    SettingsBatchResult,
    SettingsRevisionConflict,
    SettingsScope,
    normalize_updates,
    query_settings,
    scope_definitions,
    scope_settings_factory,
    update_settings,
)

#: Kept under its announcement-era name for the adapters that import it.
SITE_SETTINGS_SOURCES = SETTINGS_SOURCES

ANNOUNCEMENT_ENABLED_KEY = "site.announcement.enabled"
ANNOUNCEMENT_MESSAGE_KEY = "site.announcement.message"
SITE_ANNOUNCEMENT_GROUP = "site.announcement"
SITE_SETTINGS_DOCS_REFERENCE = "_docs/specs/01-platform-architecture.md"
SITE_SETTINGS_READ_PERMISSION = "core.read_operational_settings"
SITE_SETTINGS_WRITE_PERMISSION = "core.change_operational_settings"
SITE_SETTINGS_KEYS = (ANNOUNCEMENT_ENABLED_KEY, ANNOUNCEMENT_MESSAGE_KEY)
SITE_SETTINGS_AUDIT_READ = "core.operational_settings.read"
SITE_SETTINGS_AUDIT_WRITE = "core.operational_settings.batch_updated"

#: The two batch errors keep their announcement-era names because Studio and the
#: admin API import them; they are the shared errors from ``core.settings_batch``.
InvalidSiteSettingsBatch = InvalidSettingsBatch
SiteSettingsRevisionConflict = SettingsRevisionConflict
ResolvedSiteSetting = ResolvedSetting
NormalizedSiteSettingUpdate = NormalizedSettingUpdate
SiteSettingsBatchResult = SettingsBatchResult


def _normalize_announcement_message(value: JsonValue) -> JsonValue:
    if not isinstance(value, str):
        raise InvalidOperationalSetting("announcement message must be a string")
    if any(
        character in "\r\n\t"
        or unicodedata.category(character).startswith("C")
        or unicodedata.category(character) in {"Zl", "Zp"}
        for character in value
    ):
        raise InvalidOperationalSetting("announcement message must be one safe line")
    normalized = value.strip()
    if len(normalized) > 500:
        raise InvalidOperationalSetting("announcement message must not exceed 500 characters")
    if "<" in normalized or ">" in normalized:
        raise InvalidOperationalSetting("announcement message cannot contain markup")
    return normalized


ANNOUNCEMENT_ENABLED = register_operational_setting(
    OperationalSettingDefinition(
        key=ANNOUNCEMENT_ENABLED_KEY,
        group=SITE_ANNOUNCEMENT_GROUP,
        label="Show site announcement",
        description=(
            "Show the public site announcement only when it is enabled and its trimmed message "
            "is not empty."
        ),
        value_type=OperationalSetting.ValueType.BOOLEAN,
        default=False,
        validation={},
        docs_reference=SITE_SETTINGS_DOCS_REFERENCE,
        lifecycle="active",
        cache_policy="uncached",
        sensitivity="public",
    )
)

ANNOUNCEMENT_MESSAGE = register_operational_setting(
    OperationalSettingDefinition(
        key=ANNOUNCEMENT_MESSAGE_KEY,
        group=SITE_ANNOUNCEMENT_GROUP,
        label="Announcement message",
        description=(
            "Public plain text shown only when the site announcement is enabled and this "
            "trimmed message is not empty."
        ),
        value_type=OperationalSetting.ValueType.STRING,
        default="",
        validation={
            "max_length": 500,
            "single_line": True,
            "trim": True,
            "markup": False,
        },
        docs_reference=SITE_SETTINGS_DOCS_REFERENCE,
        lifecycle="active",
        cache_policy="uncached",
        sensitivity="public",
        validator=_normalize_announcement_message,
    )
)


SITE_SETTINGS_SCOPE = SettingsScope(
    keys=SITE_SETTINGS_KEYS,
    sensitivity="public",
    audit_label="site.announcement",
    audit_action_write=SITE_SETTINGS_AUDIT_WRITE,
    idempotency_prefix="site.settings.write",
)


def _site_definitions() -> tuple[OperationalSettingDefinition, ...]:
    return scope_definitions(SITE_SETTINGS_SCOPE)


def _normalize_updates(updates: object) -> tuple[NormalizedSettingUpdate, ...]:
    return normalize_updates(SITE_SETTINGS_SCOPE, updates)


def query_site_settings(
    _query: object = None,
    *,
    context: ServiceContext | None = None,
    using: str = "default",
) -> JsonObject:
    """Resolve all public site settings with one bounded database query."""

    del context
    return query_settings(SITE_SETTINGS_SCOPE, using=using)


def update_site_settings(
    *,
    updates: object,
    source: str,
    idempotency_key: str,
    actor_ref: str,
    actor_id: Any | None = None,
    api_principal_id: uuid.UUID | None = None,
    context: ServiceContext | None = None,
    using: str = "default",
) -> SettingsBatchResult:
    """Normalize and atomically apply one actor-scoped idempotent batch."""

    return update_settings(
        SITE_SETTINGS_SCOPE,
        updates=updates,
        source=source,
        idempotency_key=idempotency_key,
        actor_ref=actor_ref,
        actor_id=actor_id,
        api_principal_id=api_principal_id,
        context=context,
        using=using,
    )


def public_announcement(*, using: str = "default") -> JsonObject | None:
    """Return the uncached public banner model, or fail closed to no banner."""

    settings = query_site_settings(using=using).get("settings")
    if not isinstance(settings, list):
        raise InvalidOperationalSetting("site setting query result is invalid")
    by_key: dict[str, JsonObject] = {}
    for item in settings:
        if not isinstance(item, dict):
            raise InvalidOperationalSetting("site setting query result is invalid")
        key = item.get("key")
        if not isinstance(key, str):
            raise InvalidOperationalSetting("site setting query result is invalid")
        by_key[key] = item
    if any(key not in by_key for key in SITE_SETTINGS_KEYS):
        raise InvalidOperationalSetting("site setting query result is incomplete")
    enabled = by_key[ANNOUNCEMENT_ENABLED_KEY]["value"]
    message = by_key[ANNOUNCEMENT_MESSAGE_KEY]["value"]
    if enabled is not True or not isinstance(message, str) or not message:
        return None
    return {"message": message}


def _settings_factory() -> JsonObject:
    return scope_settings_factory(SITE_SETTINGS_SCOPE)


def _settings_field_policy(_actor: object, field: str) -> bool:
    return field == "updates"


SITE_SETTINGS_READ = Capability(
    key="site.settings.read",
    description="Read public-safe site settings",
    service_kind=ServiceKind.QUERY,
    service=query_site_settings,
    django_permission=SITE_SETTINGS_READ_PERMISSION,
    studio=AdapterMetadata(
        route="studio:settings",
        method="GET",
        operation_id="site.settings.read.html",
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/settings",
        method="GET",
        operation_id="site.settings.read",
        scopes=("site.settings.read",),
        result_schema="SiteSettings",
        rate_class="read",
        rate_cost=1,
    ),
    idempotency=IdempotencyPolicy.NONE,
    concurrency=ConcurrencyPolicy.NONE,
    audit_action=SITE_SETTINGS_AUDIT_READ,
    redacted_fields=("authorization", "cookie", "default", "token", "value"),
    test_factory=_settings_factory,
)

SITE_SETTINGS_WRITE = Capability(
    key="site.settings.write",
    description="Update public-safe site settings",
    service_kind=ServiceKind.COMMAND,
    service=update_site_settings,
    django_permission=SITE_SETTINGS_WRITE_PERMISSION,
    studio=AdapterMetadata(
        route="studio:settings",
        method="POST",
        operation_id="site.settings.write.html",
        writable_fields=("updates",),
    ),
    admin_api=AdapterMetadata(
        route="/api/v1/admin/settings",
        method="POST",
        operation_id="site.settings.write",
        scopes=("site.settings.write",),
        request_schema="SiteSettingsBatchRequest",
        result_schema="SiteSettingsBatchResult",
        writable_fields=("updates",),
        rate_class="write",
        rate_cost=1,
    ),
    idempotency=IdempotencyPolicy.REQUIRED,
    concurrency=ConcurrencyPolicy.REVISION,
    audit_action=SITE_SETTINGS_AUDIT_WRITE,
    redacted_fields=(
        "authorization",
        "body",
        "cookie",
        "csrfmiddlewaretoken",
        "default",
        "token",
        "updates",
        "value",
    ),
    test_factory=_settings_factory,
    field_policy=_settings_field_policy,
)

SITE_SETTING_CAPABILITIES = (SITE_SETTINGS_READ, SITE_SETTINGS_WRITE)
