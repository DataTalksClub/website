from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db import transaction

from core.audit import AuditWriteContext, record_audit_event
from core.idempotency import JsonObject, JsonValue, canonical_json
from core.models import (
    AuditEvent,
    OperationalSetting,
    OperationalSettingRevision,
    RevisionConflict,
)

_SETTING_KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SETTING_SOURCE = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_SECRET_KEY_FRAGMENTS = frozenset(
    {
        "apikey",
        "authorization",
        "cookie",
        "credential",
        "csrf",
        "password",
        "passwd",
        "privatekey",
        "secret",
        "sessionid",
        "token",
    }
)


class UnknownOperationalSetting(LookupError):
    """The setting was not registered by application code."""


class InvalidOperationalSetting(ValueError):
    """A setting key, type, source, or value is not safe and valid."""


class OperationalSettingDefinitionConflict(RuntimeError):
    """Two code paths attempted to register different definitions for one key."""


SettingValidator = Callable[[JsonValue], None]


@dataclass(frozen=True, slots=True)
class OperationalSettingDefinition:
    key: str
    value_type: str
    default: JsonValue
    description: str
    version: int = 1
    validator: SettingValidator | None = None


@dataclass(frozen=True, slots=True)
class ResolvedOperationalSetting:
    key: str
    value: JsonValue
    value_type: str
    source: str
    definition_version: int
    revision: int


_registry: dict[str, OperationalSettingDefinition] = {}


def _normalized_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _contains_secret_name(value: str) -> bool:
    normalized = _normalized_name(value)
    return any(fragment in normalized for fragment in _SECRET_KEY_FRAGMENTS)


def _validate_no_secret_keys(value: JsonValue, *, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _contains_secret_name(key):
                raise InvalidOperationalSetting(
                    f"{path}.{key} looks secret-bearing and cannot be database-backed"
                )
            _validate_no_secret_keys(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_no_secret_keys(child, path=f"{path}[{index}]")


def _validated_value(
    definition: OperationalSettingDefinition,
    value: Any,
) -> JsonValue:
    normalized = canonical_json(value)
    value_type = definition.value_type

    if value_type == OperationalSetting.ValueType.BOOLEAN:
        valid = isinstance(normalized, bool)
    elif value_type == OperationalSetting.ValueType.INTEGER:
        valid = isinstance(normalized, int) and not isinstance(normalized, bool)
    elif value_type == OperationalSetting.ValueType.STRING:
        valid = isinstance(normalized, str) and len(normalized) <= 4_096
    elif value_type == OperationalSetting.ValueType.STRING_LIST:
        valid = (
            isinstance(normalized, list)
            and len(normalized) <= 100
            and all(isinstance(item, str) and len(item) <= 512 for item in normalized)
        )
    elif value_type == OperationalSetting.ValueType.JSON_OBJECT:
        valid = isinstance(normalized, dict)
    else:
        raise InvalidOperationalSetting(f"unsupported setting type {value_type}")

    if not valid:
        raise InvalidOperationalSetting(f"setting {definition.key} requires a {value_type} value")
    _validate_no_secret_keys(normalized, path=definition.key)
    if definition.validator is not None:
        definition.validator(normalized)
    return normalized


def register_operational_setting(
    definition: OperationalSettingDefinition,
) -> OperationalSettingDefinition:
    if not _SETTING_KEY.fullmatch(definition.key):
        raise InvalidOperationalSetting("setting key must be a stable lowercase identifier")
    if _contains_secret_name(definition.key):
        raise InvalidOperationalSetting("secret-bearing settings must remain in the secret store")
    if not definition.description.strip():
        raise InvalidOperationalSetting("setting definitions require a description")
    if definition.version < 1:
        raise InvalidOperationalSetting("setting definition version must be positive")
    _validated_value(definition, definition.default)

    registered = _registry.get(definition.key)
    if registered is not None and registered != definition:
        raise OperationalSettingDefinitionConflict(
            f"setting {definition.key} already has a different definition"
        )
    _registry[definition.key] = definition
    return definition


def registered_operational_settings() -> tuple[OperationalSettingDefinition, ...]:
    return tuple(_registry[key] for key in sorted(_registry))


def _definition(key: str) -> OperationalSettingDefinition:
    try:
        return _registry[key]
    except KeyError as error:
        raise UnknownOperationalSetting(f"operational setting {key} is not registered") from error


def resolve_operational_setting(
    key: str,
    *,
    using: str = "default",
) -> ResolvedOperationalSetting:
    definition = _definition(key)
    stored = OperationalSetting.objects.using(using).filter(key=key).first()
    if stored is None:
        return ResolvedOperationalSetting(
            key=key,
            value=_validated_value(definition, definition.default),
            value_type=definition.value_type,
            source="code_default",
            definition_version=definition.version,
            revision=0,
        )
    if stored.value_type != definition.value_type:
        raise InvalidOperationalSetting(
            f"stored setting {key} has type {stored.value_type}, expected {definition.value_type}"
        )
    return ResolvedOperationalSetting(
        key=key,
        value=_validated_value(definition, stored.value),
        value_type=stored.value_type,
        source=stored.source,
        definition_version=stored.definition_version,
        revision=stored.revision,
    )


def set_operational_setting(
    *,
    key: str,
    value: JsonValue,
    source: str,
    expected_revision: int,
    context: AuditWriteContext | None = None,
    using: str = "default",
) -> ResolvedOperationalSetting:
    """Compare-and-swap one typed setting, immutable revision, and audit event."""

    definition = _definition(key)
    context = context or AuditWriteContext()
    validated_value = _validated_value(definition, value)
    if not _SETTING_SOURCE.fullmatch(source):
        raise InvalidOperationalSetting("setting source must be a stable lowercase identifier")
    if expected_revision < 0:
        raise InvalidOperationalSetting("expected revision cannot be negative")

    with transaction.atomic(using=using):
        setting = OperationalSetting.objects.using(using).filter(key=key).first()
        actual_revision = setting.revision if setting is not None else 0
        if expected_revision != actual_revision:
            raise RevisionConflict(expected=expected_revision, actual=actual_revision)

        if setting is None:
            setting, created = OperationalSetting.objects.using(using).get_or_create(
                key=key,
                defaults={
                    "value_type": definition.value_type,
                    "value": validated_value,
                    "source": source,
                    "definition_version": definition.version,
                    "revision": 1,
                },
            )
            if not created:
                raise RevisionConflict(expected=0, actual=setting.revision)
            before_value = _validated_value(definition, definition.default)
            before_source = "code_default"
        else:
            if setting.value_type != definition.value_type:
                raise InvalidOperationalSetting(
                    f"stored setting {key} has type {setting.value_type}, "
                    f"expected {definition.value_type}"
                )
            before_value = _validated_value(definition, setting.value)
            before_source = setting.source
            setting.value = validated_value
            setting.source = source
            setting.definition_version = definition.version
            setting.revision += 1
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

        audit_event = record_audit_event(
            action="core.operational_setting.set",
            target_type="core.operational_setting",
            target_id=setting.id,
            target_label=key,
            outcome=AuditEvent.Outcome.SUCCEEDED,
            context=context,
            changes={
                "revision": {"before": actual_revision, "after": setting.revision},
                "source": {"before": before_source, "after": source},
                "value": {"before": before_value, "after": validated_value},
            },
            metadata={"definition_version": definition.version},
            using=using,
        )
        OperationalSettingRevision.objects.using(using).create(
            setting=setting,
            key=setting.key,
            value_type=setting.value_type,
            value=validated_value,
            source=source,
            definition_version=definition.version,
            revision=setting.revision,
            changed_by_id=context.actor_id,
            changed_by_ref=context.actor_ref,
            audit_event=audit_event,
        )

        return ResolvedOperationalSetting(
            key=key,
            value=validated_value,
            value_type=definition.value_type,
            source=source,
            definition_version=definition.version,
            revision=setting.revision,
        )


def operational_setting_snapshot(*, using: str = "default") -> JsonObject:
    """Resolve every registered safe setting with its visible source and revision."""

    return {
        definition.key: {
            "value": resolved.value,
            "value_type": resolved.value_type,
            "source": resolved.source,
            "definition_version": resolved.definition_version,
            "revision": resolved.revision,
        }
        for definition in registered_operational_settings()
        for resolved in [resolve_operational_setting(definition.key, using=using)]
    }
