"""One compare-and-swap batch writer shared by every settings scope.

``core.site_settings`` grew this machinery for the two public announcement keys:
normalize a whole batch before touching anything, refuse the batch when any
submitted ``expected_revision`` no longer matches, write the changed rows, and
record one audit event plus one immutable revision per changed row.  None of
that is specific to announcements, so it lives here and is parameterized by a
:class:`SettingsScope` -- a key tuple, the sensitivity those keys must declare,
and the labels the audit trail and the idempotency scope are built from.

Two scopes exist today: the public announcement pair, and the operational
tunables in ``core.operational_settings``.  A third would be a scope object, not
a second copy of this file.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction

from core.audit import AuditWriteContext, record_audit_event
from core.configuration import (
    InvalidOperationalSetting,
    OperationalSettingDefinition,
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

#: The two surfaces allowed to author a settings row.  ``code_default`` is not
#: here: it is what a *missing* row resolves to, never something we store.
SETTINGS_SOURCES = frozenset({"studio", "admin_api"})


class InvalidSettingsBatch(ValueError):
    """A complete settings batch failed before mutation."""


class SettingsRevisionConflict(RuntimeError):
    """One submitted key no longer has the expected revision."""

    def __init__(self, *, key: str, expected: int, actual: int) -> None:
        self.key = key
        self.expected = expected
        self.actual = actual
        super().__init__(f"setting {key} expected revision {expected}, found {actual}")


@dataclass(frozen=True, slots=True)
class SettingsScope:
    """The keys one adapter may read and write, and how they are labelled."""

    #: Every key in the scope, in the order an operator reads them.
    keys: tuple[str, ...]
    #: The sensitivity every key in the scope must declare.  A key that changed
    #: sensitivity drops out of the scope and fails the completeness check
    #: rather than leaking through an adapter that was not written for it.
    sensitivity: str
    #: The audit event's ``target_label`` for a batch in this scope.
    audit_label: str
    #: The audit action recorded when a batch in this scope changes a row.  It
    #: must equal the write capability's ``audit_action`` so the adapter and the
    #: service agree on what the trail is called.
    audit_action_write: str
    #: Prefix of the actor-scoped idempotency scope.
    idempotency_prefix: str


@dataclass(frozen=True, slots=True)
class ResolvedSetting:
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
class NormalizedSettingUpdate:
    key: str
    value: JsonValue
    expected_revision: int


@dataclass(frozen=True, slots=True)
class SettingsBatchResult:
    settings: tuple[JsonObject, ...]
    replayed: bool

    def as_dict(self) -> JsonObject:
        return {"settings": list(self.settings), "replayed": self.replayed}


def scope_definitions(scope: SettingsScope) -> tuple[OperationalSettingDefinition, ...]:
    """Every definition the scope names, in the scope's own order.

    The registry is the only source: a key the scope names but code never
    registered, or one whose lifecycle or sensitivity drifted, makes the scope
    incomplete and every read and write in it fail closed.
    """

    registered = {
        definition.key: definition
        for definition in registered_operational_settings()
        if definition.lifecycle == "active" and definition.sensitivity == scope.sensitivity
    }
    definitions = tuple(registered[key] for key in scope.keys if key in registered)
    if tuple(definition.key for definition in definitions) != scope.keys:
        raise InvalidOperationalSetting("scoped setting definitions are incomplete")
    return definitions


def query_settings(scope: SettingsScope, *, using: str = "default") -> JsonObject:
    """Resolve every setting in the scope with one bounded database query."""

    definitions = scope_definitions(scope)
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
            item = ResolvedSetting(
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
            if setting.source not in SETTINGS_SOURCES:
                raise InvalidOperationalSetting(
                    f"stored setting {definition.key} has an invalid source"
                )
            item = ResolvedSetting(
                definition=definition,
                value=validate_operational_setting_value(definition.key, setting.value),
                source=setting.source,
                revision=setting.revision,
            )
        resolved.append(item.as_dict())
    return {"settings": [item for item in resolved]}


def normalize_updates(
    scope: SettingsScope,
    updates: object,
) -> tuple[NormalizedSettingUpdate, ...]:
    if not isinstance(updates, list) or not 1 <= len(updates) <= len(scope.keys):
        raise InvalidSettingsBatch("updates must contain between one setting and the whole scope")
    normalized: list[NormalizedSettingUpdate] = []
    seen: set[str] = set()
    for item in updates:
        if not isinstance(item, dict) or set(item) != {"key", "value", "expected_revision"}:
            raise InvalidSettingsBatch("each update must contain exact setting fields")
        key = item.get("key")
        revision = item.get("expected_revision")
        if not isinstance(key, str) or key not in scope.keys or key in seen:
            raise InvalidSettingsBatch("setting keys must be unique and registered")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise InvalidSettingsBatch("expected revisions must be nonnegative integers")
        try:
            value = validate_operational_setting_value(key, item.get("value"))
        except (InvalidOperationalSetting, TypeError, ValueError) as error:
            raise InvalidSettingsBatch("a setting value is invalid") from error
        seen.add(key)
        normalized.append(
            NormalizedSettingUpdate(
                key=key,
                value=value,
                expected_revision=revision,
            )
        )
    return tuple(sorted(normalized, key=lambda update: update.key))


def _idempotency_scope(scope: SettingsScope, actor_ref: str) -> str:
    key = f"{scope.idempotency_prefix}:{actor_ref}"
    if len(key) > 128:
        raise InvalidSettingsBatch("settings actor scope is invalid")
    return key


def _apply_settings_batch(
    scope: SettingsScope,
    updates: tuple[NormalizedSettingUpdate, ...],
    *,
    source: str,
    context: AuditWriteContext,
    using: str,
) -> JsonObject:
    definitions = {definition.key: definition for definition in scope_definitions(scope)}
    keys = tuple(update.key for update in updates)
    rows = {
        setting.key: setting
        for setting in OperationalSetting.objects.using(using).filter(key__in=keys)
    }
    for update in updates:
        current = rows.get(update.key)
        actual_revision = current.revision if current is not None else 0
        if update.expected_revision != actual_revision:
            raise SettingsRevisionConflict(
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
                raise SettingsRevisionConflict(
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
                raise SettingsRevisionConflict(
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
            action=scope.audit_action_write,
            target_type="core.operational_settings",
            target_label=scope.audit_label,
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
    if changed_rows:
        # A stamped value this process already resolved is now stale in its own
        # cache.  Other processes pick the write up from the table's stamp; this
        # one would otherwise keep serving the old value for the stamp TTL.
        from core.runtime_config import reset_runtime_settings_cache

        transaction.on_commit(reset_runtime_settings_cache, using=using)
    return {"items": [item for item in result_items]}


def update_settings(
    scope: SettingsScope,
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

    if source not in SETTINGS_SOURCES:
        raise InvalidSettingsBatch("settings source is invalid")
    if not isinstance(actor_ref, str) or not actor_ref:
        raise InvalidSettingsBatch("settings actor is invalid")
    try:
        validate_actor_ref(actor_ref)
    except ValueError as error:
        raise InvalidSettingsBatch("settings actor is invalid") from error
    if context is not None and context.actor_ref != actor_ref:
        raise InvalidSettingsBatch("settings actor context is invalid")
    normalized = normalize_updates(scope, updates)
    idempotency_scope = _idempotency_scope(scope, actor_ref)
    service_context = context or ServiceContext.from_current(actor_ref=actor_ref)
    audit_context = AuditWriteContext.from_service_context(
        service_context,
        actor_id=actor_id,
        api_principal_id=api_principal_id,
        idempotency_key_hash=hash_idempotency_key(idempotency_scope, idempotency_key),
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
        scope=idempotency_scope,
        key=idempotency_key,
        request=request,
        command=lambda: _apply_settings_batch(
            scope,
            normalized,
            source=source,
            context=audit_context,
            using=using,
        ),
        using=using,
    )
    persisted_items = result.value.get("items")
    if not isinstance(persisted_items, list) or len(persisted_items) != len(normalized):
        raise InvalidSettingsBatch("settings replay result is invalid")
    update_by_key = {update.key: update for update in normalized}
    definitions = {definition.key: definition for definition in scope_definitions(scope)}
    response_items: list[JsonObject] = []
    for item in persisted_items:
        if not isinstance(item, dict):
            raise InvalidSettingsBatch("settings replay result is invalid")
        key = item.get("key")
        source_value = item.get("source")
        revision_value = item.get("revision")
        changed_value = item.get("changed")
        definition_version_value = item.get("definition_version")
        if (
            not isinstance(key, str)
            or source_value not in SETTINGS_SOURCES | {"code_default"}
            or not isinstance(source_value, str)
            or isinstance(revision_value, bool)
            or not isinstance(revision_value, int)
            or revision_value < 0
            or not isinstance(changed_value, bool)
            or isinstance(definition_version_value, bool)
            or not isinstance(definition_version_value, int)
            or definition_version_value < 1
        ):
            raise InvalidSettingsBatch("settings replay result is invalid")
        update = update_by_key.get(key)
        definition = definitions.get(key)
        if update is None or definition is None or definition.version != definition_version_value:
            raise InvalidSettingsBatch("settings replay result is invalid")
        response_items.append(
            ResolvedSetting(
                definition=definition,
                value=update.value,
                source=source_value,
                revision=revision_value,
            ).as_dict(changed=changed_value)
        )
    return SettingsBatchResult(settings=tuple(response_items), replayed=result.replayed)


def scope_settings_factory(scope: SettingsScope) -> JsonObject:
    """The code-default shape of the scope, used by capability contract tests."""

    return {
        "settings": [
            ResolvedSetting(
                definition=definition,
                value=definition.default,
                source="code_default",
                revision=0,
            ).as_dict()
            for definition in scope_definitions(scope)
        ]
    }
