"""Public-safe site settings shared by public views, Studio, and the admin API."""

from __future__ import annotations

import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction

from core.audit import AuditWriteContext, record_audit_event
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
    registered_operational_settings,
    validate_operational_setting_value,
)
from core.idempotency import (
    IdempotencyResult,
    JsonObject,
    JsonValue,
    execute_idempotent,
    hash_idempotency_key,
)
from core.models import (
    AuditEvent,
    OperationalSetting,
    OperationalSettingRevision,
    RevisionConflict,
)
from core.services import ServiceContext, validate_actor_ref

ANNOUNCEMENT_ENABLED_KEY = "site.announcement.enabled"
ANNOUNCEMENT_MESSAGE_KEY = "site.announcement.message"
SITE_ANNOUNCEMENT_GROUP = "site.announcement"
SITE_SETTINGS_DOCS_REFERENCE = "_docs/specs/01-platform-architecture.md"
SITE_SETTINGS_READ_PERMISSION = "core.read_operational_settings"
SITE_SETTINGS_WRITE_PERMISSION = "core.change_operational_settings"
SITE_SETTINGS_KEYS = (ANNOUNCEMENT_ENABLED_KEY, ANNOUNCEMENT_MESSAGE_KEY)
SITE_SETTINGS_SOURCES = frozenset({"studio", "admin_api"})


class InvalidSiteSettingsBatch(ValueError):
    """A complete settings batch failed before mutation."""


class SiteSettingsRevisionConflict(RuntimeError):
    """One submitted key no longer has the expected revision."""

    def __init__(self, *, key: str, expected: int, actual: int) -> None:
        self.key = key
        self.expected = expected
        self.actual = actual
        super().__init__(f"setting {key} expected revision {expected}, found {actual}")


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


@dataclass(frozen=True, slots=True)
class ResolvedSiteSetting:
    definition: OperationalSettingDefinition
    value: JsonValue
    source: str
    revision: int

    def as_dict(self, *, changed: bool | None = None) -> JsonObject:
        payload: JsonObject = {
            "key": self.definition.key,
            "group": self.definition.group,
            "label": self.definition.label,
            "description": self.definition.description,
            "value_type": self.definition.value_type,
            "default": self.definition.default,
            "validation": self.definition.validation,
            "docs_reference": self.definition.docs_reference,
            "lifecycle": self.definition.lifecycle,
            "cache_policy": self.definition.cache_policy,
            "sensitivity": self.definition.sensitivity,
            "value": self.value,
            "source": self.source,
            "definition_version": self.definition.version,
            "revision": self.revision,
        }
        if changed is not None:
            payload["changed"] = changed
        return payload


@dataclass(frozen=True, slots=True)
class NormalizedSiteSettingUpdate:
    key: str
    value: JsonValue
    expected_revision: int


@dataclass(frozen=True, slots=True)
class SiteSettingsBatchResult:
    settings: tuple[JsonObject, ...]
    replayed: bool

    def as_dict(self) -> JsonObject:
        return {"settings": list(self.settings), "replayed": self.replayed}


def _site_definitions() -> tuple[OperationalSettingDefinition, ...]:
    definitions = tuple(
        definition
        for definition in registered_operational_settings()
        if definition.key in SITE_SETTINGS_KEYS
        and definition.lifecycle == "active"
        and definition.sensitivity == "public"
    )
    if tuple(definition.key for definition in definitions) != SITE_SETTINGS_KEYS:
        raise InvalidOperationalSetting("site setting definitions are incomplete")
    return definitions


def query_site_settings(
    _query: object = None,
    *,
    context: ServiceContext | None = None,
    using: str = "default",
) -> JsonObject:
    """Resolve all public site settings with one bounded database query."""

    del context
    definitions = _site_definitions()
    stored = {
        setting.key: setting
        for setting in OperationalSetting.objects.using(using).filter(
            key__in=tuple(definition.key for definition in definitions)
        )
    }
    resolved: list[JsonValue] = []
    for definition in definitions:
        setting = stored.get(definition.key)
        if setting is None:
            item = ResolvedSiteSetting(
                definition=definition,
                value=validate_operational_setting_value(definition.key, definition.default),
                source="code_default",
                revision=0,
            )
        else:
            if setting.value_type != definition.value_type:
                raise InvalidOperationalSetting(
                    f"stored setting {definition.key} has an invalid type"
                )
            if setting.source not in SITE_SETTINGS_SOURCES:
                raise InvalidOperationalSetting(
                    f"stored setting {definition.key} has an invalid source"
                )
            item = ResolvedSiteSetting(
                definition=definition,
                value=validate_operational_setting_value(definition.key, setting.value),
                source=setting.source,
                revision=setting.revision,
            )
        resolved.append(item.as_dict())
    return {"settings": [item for item in resolved]}


def _normalize_updates(updates: object) -> tuple[NormalizedSiteSettingUpdate, ...]:
    if not isinstance(updates, list) or not 1 <= len(updates) <= len(SITE_SETTINGS_KEYS):
        raise InvalidSiteSettingsBatch("updates must contain one or two settings")
    normalized: list[NormalizedSiteSettingUpdate] = []
    seen: set[str] = set()
    for item in updates:
        if not isinstance(item, dict) or set(item) != {"key", "value", "expected_revision"}:
            raise InvalidSiteSettingsBatch("each update must contain exact setting fields")
        key = item.get("key")
        revision = item.get("expected_revision")
        if not isinstance(key, str) or key not in SITE_SETTINGS_KEYS or key in seen:
            raise InvalidSiteSettingsBatch("setting keys must be unique and registered")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise InvalidSiteSettingsBatch("expected revisions must be nonnegative integers")
        try:
            value = validate_operational_setting_value(key, item.get("value"))
        except (InvalidOperationalSetting, TypeError, ValueError) as error:
            raise InvalidSiteSettingsBatch("a setting value is invalid") from error
        seen.add(key)
        normalized.append(
            NormalizedSiteSettingUpdate(
                key=key,
                value=value,
                expected_revision=revision,
            )
        )
    return tuple(sorted(normalized, key=lambda update: update.key))


def _idempotency_scope(actor_ref: str) -> str:
    scope = f"site.settings.write:{actor_ref}"
    if len(scope) > 128:
        raise InvalidSiteSettingsBatch("settings actor scope is invalid")
    return scope


def _apply_site_settings_batch(
    updates: tuple[NormalizedSiteSettingUpdate, ...],
    *,
    source: str,
    context: AuditWriteContext,
    using: str,
) -> JsonObject:
    definitions = {definition.key: definition for definition in _site_definitions()}
    keys = tuple(update.key for update in updates)
    rows = {
        setting.key: setting
        for setting in OperationalSetting.objects.using(using).filter(key__in=keys)
    }
    for update in updates:
        current = rows.get(update.key)
        actual_revision = current.revision if current is not None else 0
        if update.expected_revision != actual_revision:
            raise SiteSettingsRevisionConflict(
                key=update.key,
                expected=update.expected_revision,
                actual=actual_revision,
            )

    changed_rows: list[tuple[OperationalSetting, int, int]] = []
    result_items: list[JsonValue] = []
    for update in updates:
        definition = definitions[update.key]
        setting = rows.get(update.key)
        previous_revision = setting.revision if setting is not None else 0
        previous_value = (
            validate_operational_setting_value(update.key, setting.value)
            if setting is not None
            else validate_operational_setting_value(update.key, definition.default)
        )
        changed = previous_value != update.value
        if changed and setting is None:
            try:
                with transaction.atomic(using=using):
                    setting = OperationalSetting.objects.using(using).create(
                        key=update.key,
                        value_type=definition.value_type,
                        value=update.value,
                        source=source,
                        definition_version=definition.version,
                        revision=1,
                    )
            except IntegrityError as error:
                current = OperationalSetting.objects.using(using).get(key=update.key)
                raise SiteSettingsRevisionConflict(
                    key=update.key,
                    expected=0,
                    actual=current.revision,
                ) from error
            rows[update.key] = setting
        elif changed and setting is not None:
            setting.value = update.value
            setting.source = source
            setting.definition_version = definition.version
            setting.revision += 1
            try:
                setting.save(
                    using=using,
                    update_fields=(
                        "value",
                        "source",
                        "definition_version",
                        "revision",
                        "updated_at",
                    ),
                )
            except RevisionConflict as error:
                raise SiteSettingsRevisionConflict(
                    key=update.key,
                    expected=error.expected,
                    actual=error.actual,
                ) from error
        if changed and setting is not None:
            changed_rows.append((setting, previous_revision, setting.revision))
        result_items.append(
            {
                "key": update.key,
                "source": setting.source if setting is not None else "code_default",
                "definition_version": definition.version,
                "revision": setting.revision if setting is not None else 0,
                "changed": changed,
            }
        )

    audit_event = None
    if changed_rows:
        audit_event = record_audit_event(
            action="core.operational_settings.batch_updated",
            target_type="core.operational_settings",
            target_label="site.announcement",
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=context,
            changes={
                setting.key: {"revision": {"before": before, "after": after}}
                for setting, before, after in changed_rows
            },
            metadata={
                "affected_keys": [setting.key for setting, _before, _after in changed_rows],
                "source": source,
            },
            using=using,
        )
    for setting, _before, _after in changed_rows:
        OperationalSettingRevision.objects.using(using).create(
            setting=setting,
            key=setting.key,
            value_type=setting.value_type,
            value=setting.value,
            source=setting.source,
            definition_version=setting.definition_version,
            revision=setting.revision,
            changed_by_id=context.actor_id,
            changed_by_ref=context.actor_ref,
            audit_event=audit_event,
        )
    return {"items": [item for item in result_items]}


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
) -> SiteSettingsBatchResult:
    """Normalize and atomically apply one actor-scoped idempotent batch."""

    if source not in SITE_SETTINGS_SOURCES:
        raise InvalidSiteSettingsBatch("settings source is invalid")
    if not isinstance(actor_ref, str) or not actor_ref:
        raise InvalidSiteSettingsBatch("settings actor is invalid")
    try:
        validate_actor_ref(actor_ref)
    except ValueError as error:
        raise InvalidSiteSettingsBatch("settings actor is invalid") from error
    if context is not None and context.actor_ref != actor_ref:
        raise InvalidSiteSettingsBatch("settings actor context is invalid")
    normalized = _normalize_updates(updates)
    scope = _idempotency_scope(actor_ref)
    service_context = context or ServiceContext.from_current(actor_ref=actor_ref)
    audit_context = AuditWriteContext.from_service_context(
        service_context,
        actor_id=actor_id,
        api_principal_id=api_principal_id,
        idempotency_key_hash=hash_idempotency_key(scope, idempotency_key),
    )
    request_updates: list[JsonValue] = [
        {
            "key": update.key,
            "value": update.value,
            "expected_revision": update.expected_revision,
        }
        for update in normalized
    ]
    request: JsonObject = {
        "updates": request_updates,
    }
    result: IdempotencyResult = execute_idempotent(
        scope=scope,
        key=idempotency_key,
        request=request,
        command=lambda: _apply_site_settings_batch(
            normalized,
            source=source,
            context=audit_context,
            using=using,
        ),
        using=using,
    )
    persisted_items = result.value.get("items")
    if not isinstance(persisted_items, list) or len(persisted_items) != len(normalized):
        raise InvalidSiteSettingsBatch("settings replay result is invalid")
    update_by_key = {update.key: update for update in normalized}
    definitions = {definition.key: definition for definition in _site_definitions()}
    response_items: list[JsonObject] = []
    for item in persisted_items:
        if not isinstance(item, dict):
            raise InvalidSiteSettingsBatch("settings replay result is invalid")
        key = item.get("key")
        source_value = item.get("source")
        revision_value = item.get("revision")
        changed_value = item.get("changed")
        definition_version_value = item.get("definition_version")
        if (
            not isinstance(key, str)
            or source_value not in SITE_SETTINGS_SOURCES | {"code_default"}
            or not isinstance(source_value, str)
            or isinstance(revision_value, bool)
            or not isinstance(revision_value, int)
            or revision_value < 0
            or not isinstance(changed_value, bool)
            or isinstance(definition_version_value, bool)
            or not isinstance(definition_version_value, int)
            or definition_version_value < 1
        ):
            raise InvalidSiteSettingsBatch("settings replay result is invalid")
        update = update_by_key.get(key)
        definition = definitions.get(key)
        if update is None or definition is None or definition.version != definition_version_value:
            raise InvalidSiteSettingsBatch("settings replay result is invalid")
        response_items.append(
            ResolvedSiteSetting(
                definition=definition,
                value=update.value,
                source=source_value,
                revision=revision_value,
            ).as_dict(changed=changed_value)
        )
    return SiteSettingsBatchResult(settings=tuple(response_items), replayed=result.replayed)


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
    settings: list[JsonValue] = [
        {
            "key": definition.key,
            "group": definition.group,
            "label": definition.label,
            "description": definition.description,
            "value_type": definition.value_type,
            "default": definition.default,
            "validation": definition.validation,
            "docs_reference": definition.docs_reference,
            "lifecycle": definition.lifecycle,
            "cache_policy": definition.cache_policy,
            "sensitivity": definition.sensitivity,
            "value": definition.default,
            "source": "code_default",
            "definition_version": definition.version,
            "revision": 0,
        }
        for definition in _site_definitions()
    ]
    return {
        "settings": settings,
    }


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
    audit_action="core.operational_settings.read",
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
    audit_action="core.operational_settings.batch_updated",
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
