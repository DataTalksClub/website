from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, cast

from django.db import transaction

from core.audit import AuditWriteContext, record_audit_event
from core.idempotency import JsonObject, JsonValue, UnsafeJsonValue, canonical_json
from core.models import (
    AuditEvent,
    OperationalSetting,
    RevisionConflict,
)
from core.redaction import is_sensitive_text

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


SettingValidator = Callable[[JsonValue], JsonValue | None]

_SETTING_GROUP = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_DOCS_REFERENCE = re.compile(r"^_docs/[A-Za-z0-9_./#-]{1,240}$")
_SETTING_LIFECYCLES = frozenset({"active"})
# ``uncached`` settings are read straight from the database on every use, which
# is what a page-rendering read wants.  ``stamped`` settings are resolved
# through ``core.runtime_config``, which caches them per process and drops the
# cache when the settings table's stamp moves, so an operator's write reaches
# every running task without a restart.
_SETTING_CACHE_POLICIES = frozenset({"uncached", "stamped"})
# ``public`` values may be rendered on a page anyone can open.  ``operational``
# values are not secret -- the registry still refuses a secret-bearing key, env
# var or settings attribute outright -- but they name our infrastructure
# (buckets, endpoints, sender addresses), so only the settings read permission
# may see them.
_SETTING_SENSITIVITIES = frozenset({"public", "operational"})
_ENV_VAR = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SETTINGS_ATTR = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


@dataclass(frozen=True, slots=True)
class OperationalSettingDefinition:
    key: str
    group: str
    label: str
    description: str
    value_type: str
    default: JsonValue
    validation: JsonObject
    docs_reference: str
    lifecycle: str
    cache_policy: str
    sensitivity: str
    version: int = 1
    validator: SettingValidator | None = None
    #: Environment variable consulted when the database holds no row.  Naming it
    #: here is what lets a deployment keep booting from its task definition
    #: while an operator moves the value into the database at runtime.
    env_var: str = ""
    #: ``django.conf.settings`` attribute consulted after the environment.  It
    #: is the value the process started with, so it is the honest last stop
    #: before the definition default.
    settings_attr: str = ""


@dataclass(frozen=True, slots=True)
class ResolvedOperationalSetting:
    key: str
    value: JsonValue
    value_type: str
    source: str
    definition_version: int
    revision: int


_registry: dict[str, OperationalSettingDefinition] = {}


def _definition_copy(
    definition: OperationalSettingDefinition,
) -> OperationalSettingDefinition:
    validation = canonical_json(definition.validation)
    if not isinstance(validation, dict):
        raise InvalidOperationalSetting("setting validation metadata must be an object")
    return replace(
        definition,
        default=canonical_json(definition.default),
        validation=cast(JsonObject, validation),
    )


def _normalized_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _contains_secret_name(value: str) -> bool:
    normalized = _normalized_name(value)
    return any(fragment in normalized for fragment in _SECRET_KEY_FRAGMENTS)


def _validate_no_secret_bearing_names(value: JsonValue, *, path: str) -> None:
    """Refuse a value whose own field names announce a credential.

    This checks *names*, not text.  A value is not refused for looking like a
    URL or an address: those are the ordinary shapes of the things an operator
    configures -- the canonical origin, the mailer endpoint, the sender address
    -- and refusing them only pushed the same value into the table in a
    mutilated shape.  What each setting may hold is the business of its own
    validator in ``core.operational_settings``, which is strict about the shape
    it declares.  Keeping secrets out of the logs is the logging boundary's job
    and stays with ``core.redaction``.
    """

    if isinstance(value, dict):
        for key, child in value.items():
            if _contains_secret_name(key):
                raise InvalidOperationalSetting(
                    f"{path}.{key} looks secret-bearing and cannot be database-backed"
                )
            _validate_no_secret_bearing_names(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_no_secret_bearing_names(child, path=f"{path}[{index}]")


def _validated_value(
    definition: OperationalSettingDefinition,
    value: Any,
) -> JsonValue:
    try:
        normalized = canonical_json(value)
    except UnsafeJsonValue as error:
        raise InvalidOperationalSetting(
            f"setting {definition.key} contains an unsafe value"
        ) from error

    def validate_shape(candidate: JsonValue) -> None:
        value_type = definition.value_type

        if value_type == OperationalSetting.ValueType.BOOLEAN:
            valid = isinstance(candidate, bool)
        elif value_type == OperationalSetting.ValueType.INTEGER:
            valid = isinstance(candidate, int) and not isinstance(candidate, bool)
        elif value_type == OperationalSetting.ValueType.STRING:
            valid = isinstance(candidate, str) and len(candidate) <= 4_096
        elif value_type == OperationalSetting.ValueType.STRING_LIST:
            valid = (
                isinstance(candidate, list)
                and len(candidate) <= 100
                and all(isinstance(item, str) and len(item) <= 512 for item in candidate)
            )
        elif value_type == OperationalSetting.ValueType.JSON_OBJECT:
            valid = isinstance(candidate, dict)
        else:
            raise InvalidOperationalSetting(f"unsupported setting type {value_type}")

        if not valid:
            raise InvalidOperationalSetting(
                f"setting {definition.key} requires a {value_type} value"
            )

    validate_shape(normalized)
    _validate_no_secret_bearing_names(normalized, path=definition.key)
    if definition.validator is not None:
        validated = definition.validator(normalized)
        if validated is not None:
            try:
                normalized = canonical_json(validated)
            except UnsafeJsonValue as error:
                raise InvalidOperationalSetting(
                    f"setting {definition.key} normalized to an unsafe value"
                ) from error
            validate_shape(normalized)
            _validate_no_secret_bearing_names(normalized, path=definition.key)
    return normalized


def register_operational_setting(
    definition: OperationalSettingDefinition,
) -> OperationalSettingDefinition:
    if not _SETTING_KEY.fullmatch(definition.key):
        raise InvalidOperationalSetting("setting key must be a stable lowercase identifier")
    if _contains_secret_name(definition.key):
        raise InvalidOperationalSetting("secret-bearing settings must remain in the secret store")
    if not _SETTING_GROUP.fullmatch(definition.group):
        raise InvalidOperationalSetting("setting definitions require a stable lowercase group")
    if not definition.label.strip():
        raise InvalidOperationalSetting("setting definitions require a label")
    if not definition.description.strip():
        raise InvalidOperationalSetting("setting definitions require a description")
    if is_sensitive_text(definition.label) or is_sensitive_text(definition.description):
        raise InvalidOperationalSetting("setting definition text must be public-safe")
    if not _DOCS_REFERENCE.fullmatch(definition.docs_reference):
        raise InvalidOperationalSetting("setting definitions require a repository docs reference")
    if definition.lifecycle not in _SETTING_LIFECYCLES:
        raise InvalidOperationalSetting("setting lifecycle is unsupported")
    if definition.cache_policy not in _SETTING_CACHE_POLICIES:
        raise InvalidOperationalSetting("setting cache policy is unsupported")
    if definition.sensitivity not in _SETTING_SENSITIVITIES:
        raise InvalidOperationalSetting("setting sensitivity is unsupported")
    if definition.env_var and not _ENV_VAR.fullmatch(definition.env_var):
        raise InvalidOperationalSetting("setting env var must be a stable uppercase identifier")
    if definition.env_var and _contains_secret_name(definition.env_var):
        raise InvalidOperationalSetting("secret-bearing settings must remain in the secret store")
    if definition.settings_attr and not _SETTINGS_ATTR.fullmatch(definition.settings_attr):
        raise InvalidOperationalSetting("setting settings attr must be a stable uppercase name")
    if definition.settings_attr and _contains_secret_name(definition.settings_attr):
        raise InvalidOperationalSetting("secret-bearing settings must remain in the secret store")
    if definition.version < 1:
        raise InvalidOperationalSetting("setting definition version must be positive")
    try:
        validation = canonical_json(definition.validation)
    except UnsafeJsonValue as error:
        raise InvalidOperationalSetting("setting validation metadata is unsafe") from error
    if not isinstance(validation, dict):
        raise InvalidOperationalSetting("setting validation metadata must be an object")
    _validate_no_secret_bearing_names(validation, path=f"{definition.key}.validation")
    normalized_default = _validated_value(definition, definition.default)

    registered = _registry.get(definition.key)
    if registered is not None:
        raise OperationalSettingDefinitionConflict(
            f"setting {definition.key} is already registered"
        )
    normalized_definition = replace(
        definition,
        default=normalized_default,
        validation=cast(JsonObject, validation),
    )
    _registry[definition.key] = normalized_definition
    return _definition_copy(normalized_definition)


def registered_operational_settings() -> tuple[OperationalSettingDefinition, ...]:
    return tuple(_definition_copy(_registry[key]) for key in sorted(_registry))


def _definition(key: str) -> OperationalSettingDefinition:
    try:
        return _registry[key]
    except KeyError as error:
        raise UnknownOperationalSetting(f"operational setting {key} is not registered") from error


def validate_operational_setting_value(key: str, value: Any) -> JsonValue:
    """Normalize one value through its code-owned definition."""

    return _validated_value(_definition(key), value)


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

        record_audit_event(
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
